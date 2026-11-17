"""Resumable, budgeted history backfill through the search Atom of each list.

For every list a cursor walks backwards from now to a horizon, one search page (200 entries, newest
first) per request. The query matches patch related messages and their replies (replies keep the
subject, "Re: [PATCH ...]"):

    (dt:<lo>..<hi>) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)

`hi` is the oldest message time reached so far, so each request continues where the previous one
stopped, also across runs: the cursor lives in state/meta.json (per list: oldest time reached, done
flag). Small lists go first, so most lists are complete after a few runs and the huge ones get as far
back as the budget allows. Replies whose subject lost the tag are not found (accepted loss).
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from .crawl import SEARCH_PAGE, feed_rate, ingest, manifest_from_json
from .feed import parse_feed
from .fetch import FetchError
from .migrate import migrate
from .retention import Limits, effective_retention, finalize_list
from .state import MAX_DIFF_MSGS, StateError

DAY = 86400


@dataclass
class Config:
    days: float = 180  # horizon, how far back to fill
    budget_s: float = 720  # wall clock budget of one run
    max_requests: int = 900  # requests per run
    max_total: int = 12000  # requests over all runs, a hard stop
    list_pages: int = 60  # pages per list (12 000 entries), a safety cap
    fill_patches: int = int(MAX_DIFF_MSGS * 1.1)  # a list is full once this many patch diffs are newer than the cursor
    commit_pages: int = 100  # pages between saves
    keep_bodies: bool = True


def horizon_ts(now, days):
    return int(now - days * DAY)


def list_state(meta_list):
    return meta_list.get("bf") or {}


def is_done(bf, hz, list_pages=None):
    """A list is finished when its walk ended and it already reached the (possibly newer) horizon.

    A list that stopped at the page cap is resumed when the cap is raised.
    """
    if not bf.get("done") or (bf.get("hz") or 0) > hz + DAY:
        return False
    return not (bf.get("cap") and list_pages is not None and bf.get("pg", 0) < list_pages)


def stop_reason(bf):
    """Why a finished walk ended: "" (reached the horizon), "fill" (newest patches stored), "cap" (page cap)."""
    return "cap" if bf.get("cap") else "fill" if bf.get("fill") else ""


def history_start(bf, hz):
    """Oldest time the backfill of a list reached, or 0 when it has not run."""
    if not bf:
        return 0
    if bf.get("done") and not bf.get("cap") and not bf.get("fill"):
        return bf.get("hz") or bf.get("cur") or 0
    return bf.get("cur") or 0


def order(names, ml):
    """Smallest lists first (by messages per day seen in the feed), unknown rates and ties by name."""
    def key(n):
        rt = ml.get(n, {}).get("rt")
        return (rt is None, rt or 0, n)
    return sorted(names, key=key)


def stored_rate(state, name):
    """Messages per day over the newest 25 stored messages of a list, like the feed based estimate."""
    try:
        ts = sorted((r["t"] for r in state.load_list(name)["msgs"].values()), reverse=True)[:25]
    except StateError:
        return 0.0
    if len(ts) < 2:
        return 0.0
    span = ts[0] - ts[-1]
    return round((len(ts) - 1) / span * DAY, 1) if span > 0 else 1e5


def next_cursor(bf, entries, hz):
    """Oldest time reached after a page. Always strictly older than the previous cursor."""
    ts = [e["ts"] for e in entries if e["ts"] > 0]
    cur = min(ts) if ts else hz
    prev = bf.get("cur")
    if prev is not None and cur >= prev:
        cur = prev - 1
    return cur


def run_backfill(state, source, now, cfg=None, names=None, log=print, clock=time.monotonic, limits=None):
    """Run one budgeted backfill pass. Returns a summary dict. Never shrinks or corrupts stored state."""
    cfg = cfg or Config()
    limits = limits or Limits(retention_days=effective_retention(cfg.days))
    migrate(state, now, log)
    deadline = clock() + cfg.budget_s
    hz = horizon_ts(now, cfg.days)
    meta = state.load_meta()
    ml = meta.setdefault("lists", {})
    bfm = meta.setdefault("backfill", {})
    summary = {"touched": [], "requests": 0, "pages": 0, "messages": 0, "lists_done": 0, "lists_left": 0, "failed": 0,
               "aborted": False, "stopped": ""}
    manifest = manifest_from_json(state.load_manifest())
    universe = list(names) if names else sorted(set(manifest) | set(ml))
    todo = [n for n in universe if not is_done(list_state(ml.get(n, {})), hz, cfg.list_pages)]
    queue = order(todo, ml)
    if not queue:
        log("backfill: all %d lists already reach back %g days" % (len(universe), cfg.days))
        bfm.update(days=cfg.days, last=now)
        state.save_meta(meta)
        return summary
    for n in queue:  # state written before the backfill existed has no rate: derive it once from the stored messages
        if "rt" not in ml.setdefault(n, {}):
            ml[n]["rt"] = stored_rate(state, n)
    queue = order(queue, ml)
    log("backfill: %d of %d lists to go, horizon %s" % (len(queue), len(universe), time.strftime("%Y-%m-%d", time.gmtime(hz))))
    bfm.setdefault("since", now)
    open_lists = {}  # list name -> in-memory list data not yet saved
    since_commit = 0
    base_total = bfm.get("requests", 0)

    def request(name):
        """Window (lo, hi] and replay key of the next page of a list, computed on the main thread."""
        bf = ml.setdefault(name, {}).setdefault("bf", {})
        hi = bf.get("cur")
        return hz, ((now + 3600) if hi is None else hi), bf.get("pg", 0)

    def commit():
        for nm, data in open_lists.items():
            finalize_list(state, nm, data, now, limits)
        open_lists.clear()
        bfm.update(requests=base_total + summary["requests"], last=now, days=cfg.days)
        state.save_meta(meta)

    def handle(name, fut):
        m = ml[name]
        bf = m.setdefault("bf", {})
        try:
            resp = fut.result()
            entries = parse_feed(resp.body)[0] if resp.body else []
        except FetchError as e:
            bf["err"] = str(e)[:160]
            summary["failed"] += 1
            log("  %-28s backfill FAILED: %s" % (name, e))
            if getattr(e, "backoff", False):
                summary["aborted"] = True
            return False
        bf.pop("err", None)
        try:
            data = open_lists.get(name)
            if entries:
                added, data = ingest(state, name, entries, now, cfg.keep_bodies, data, limits.retention_days)
                open_lists[name] = data
                if name not in summary["touched"]:
                    summary["touched"].append(name)
                summary["messages"] += added
                m["n"] = len(data["msgs"])
            else:
                added = 0
        except StateError as e:
            bf["err"] = str(e)[:160]
            summary["failed"] += 1
            log("  %-28s backfill FAILED: %s" % (name, e))
            return False
        bf["pg"] = bf.get("pg", 0) + 1
        bf["hz"] = hz
        bf["cur"] = next_cursor(bf, entries, hz) if entries else hz
        if "rt" not in m and entries:
            m["rt"] = feed_rate(entries)
        summary["pages"] += 1
        if len(entries) < SEARCH_PAGE or bf["cur"] <= hz:  # the window is exhausted: complete down to hz
            bf.update(done=True, cur=hz)
            bf.pop("cap", None)
            bf.pop("fill", None)
        elif data is not None and sum(1 for r in data["msgs"].values() if "dz" in r and r["t"] >= bf["cur"]) >= cfg.fill_patches:
            bf.update(done=True, fill=1)  # older series would be dropped by the retention anyway
        elif bf["pg"] >= cfg.list_pages:
            bf.update(done=True, cap=1)
        else:
            bf["done"] = False
        if bf.get("done"):
            summary["lists_done"] += 1
            log("  %-28s backfill done after %d pages (%s%s)" % (
                name, bf["pg"], time.strftime("%Y-%m-%d", time.gmtime(history_start(bf, hz))),
                ", page cap" if bf.get("cap") else ", newest %d patches stored" % limits.max_diff_msgs if bf.get("fill") else ""))
        return not bf.get("done")

    pending = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            while queue and len(pending) < 2 and not summary["aborted"] and not summary["stopped"]:
                if clock() >= deadline:
                    summary["stopped"] = "time budget"
                elif summary["requests"] >= cfg.max_requests:
                    summary["stopped"] = "per run request cap"
                elif base_total + summary["requests"] >= cfg.max_total:
                    summary["stopped"] = "total request cap"
                else:
                    name = next((q for q in queue if q not in pending.values()), None)
                    if name is None:
                        break
                    queue.remove(name)
                    summary["requests"] += 1
                    pending[pool.submit(source.search_window, name, *request(name))] = name
                    continue
                break
            if not pending:
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                name = pending.pop(fut)
                if handle(name, fut):
                    queue.insert(0, name)  # keep walking the same list, smallest lists stay first
                since_commit += 1
                if since_commit >= cfg.commit_pages:
                    commit()
                    since_commit = 0
    commit()
    summary["lists_left"] = sum(1 for n in universe if not is_done(list_state(ml.get(n, {})), hz, cfg.list_pages))
    if summary["aborted"]:
        summary["stopped"] = "server asked to back off"
    log("backfill summary: %s" % summary)
    return summary


def progress(meta, now, days):
    """(lists complete, lists total, requests so far) for display and logs."""
    hz = horizon_ts(now, days)
    ml = meta.get("lists", {})
    done = sum(1 for m in ml.values() if is_done(list_state(m), hz))
    return done, len(ml), meta.get("backfill", {}).get("requests", 0)
