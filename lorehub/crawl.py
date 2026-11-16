"""Resumable, budgeted crawl of list feeds into the accumulated state."""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from . import LORE
from .feed import parse_feed, to_record
from .fetch import FetchError
from .manifest import ListInfo, parse_manifest
from .state import StateError, merge_records

MANIFEST_URL = LORE + "/manifest.js.gz"
SEARCH_PAGE = 200


def feed_url(name):
    return "%s/%s/new.atom" % (LORE, name)


def search_url(name, since_ts, offset=0):
    q = "dt:%s.." % time.strftime("%Y%m%d%H%M%S", time.gmtime(since_ts))
    url = "%s/%s/?q=%s&x=A" % (LORE, name, q)
    if offset:
        url += "&o=%d" % offset
    return url


class LiveSource:
    """Fetches feeds over HTTP, optionally saving the raw responses for later offline runs."""

    def __init__(self, fetcher, save_raw=None):
        self.fetcher = fetcher
        self.save_raw = Path(save_raw) if save_raw else None

    def _save(self, fname, body):
        if self.save_raw:
            self.save_raw.mkdir(parents=True, exist_ok=True)
            (self.save_raw / fname).write_bytes(body)

    def manifest(self):
        resp = self.fetcher.get(MANIFEST_URL)
        self._save("manifest.js.gz", resp.body)
        return resp.body

    def new_atom(self, name, validators):
        resp = self.fetcher.get(feed_url(name), validators.get("etag"), validators.get("lm"))
        if resp.status == 200:
            self._save(name + ".atom", resp.body)
        return resp

    def search_atom(self, name, since_ts, offset):
        resp = self.fetcher.get(search_url(name, since_ts, offset))
        self._save("%s.search-%d.atom" % (name, offset), resp.body)
        return resp


class CacheSource:
    """Replays raw responses saved by LiveSource (used for development and tests)."""

    class _R:
        def __init__(self, body):
            self.status, self.body, self.headers = 200, body, {}

    def __init__(self, directory):
        self.dir = Path(directory)

    def manifest(self):
        return (self.dir / "manifest.js.gz").read_bytes()

    def new_atom(self, name, validators):
        p = self.dir / (name + ".atom")
        if not p.exists():
            raise FetchError("no cached feed for %s" % name)
        return self._R(p.read_bytes())

    def search_atom(self, name, since_ts, offset):
        p = self.dir / ("%s.search-%d.atom" % (name, offset))
        if not p.exists():
            raise FetchError("no cached search page for %s" % name)
        return self._R(p.read_bytes())


def manifest_to_json(lists):
    return {n: {"d": i.description, "m": i.modified, "e": i.epochs} for n, i in sorted(lists.items())}


def manifest_from_json(stored):
    return {n: ListInfo(n, v.get("d", ""), v.get("m", 0), v.get("e", [])) for n, v in stored.items()}


def crawl(state, source, now, names=None, budget_s=1500, deep_budget=0, deep_pages=2,
          keep_bodies=True, log=print, clock=time.monotonic):
    """Run one crawl pass and return a summary dict.

    Stored state is only ever replaced by a merged superset (plus age pruning), so a failed
    or partial pass cannot corrupt or shrink it.
    """
    deadline = clock() + budget_s
    summary = {"fetched": 0, "skipped": 0, "failed": 0, "deferred": 0, "messages": 0,
               "deep_requests": 0, "aborted": False}
    manifest_ok = True
    try:
        lists = parse_manifest(source.manifest())
        state.save_manifest(manifest_to_json(lists))
    except (FetchError, OSError, ValueError) as e:
        log("manifest fetch failed (%s), using the stored copy" % e)
        manifest_ok = False
        lists = manifest_from_json(state.load_manifest())
    if names:
        missing = [n for n in names if n not in lists]
        if missing:
            log("not in manifest: %s" % ", ".join(missing))
        lists = {n: lists[n] for n in names if n in lists}

    meta = state.load_meta()
    ml = meta.setdefault("lists", {})
    todo = []
    for name, info in lists.items():
        m = ml.setdefault(name, {})
        unchanged = manifest_ok and m.get("fetched") and m.get("mod") == info.modified and not m.get("err")
        if unchanged:
            m["checked"] = now
            summary["skipped"] += 1
        else:
            todo.append(name)
    # least recently fetched first; lists never fetched go before everything else
    todo.sort(key=lambda n: (ml[n].get("fetched") or 0, n))
    log("%d lists to fetch, %d unchanged" % (len(todo), summary["skipped"]))

    deep_candidates = []

    def work(name):
        return source.new_atom(name, {"etag": ml[name].get("etag"), "lm": ml[name].get("lm")})

    def fail(name, e):
        m = ml[name]
        m["err"] = str(e)[:200]
        m["err_ts"] = now
        summary["failed"] += 1
        log("  %-28s FAILED: %s" % (name, e))
        if getattr(e, "backoff", False):
            summary["aborted"] = True

    def handle(name, fut):
        m = ml[name]
        try:
            resp = fut.result()
            if resp.status == 304:
                m.update(checked=now, mod=lists[name].modified, err="")
                summary["fetched"] += 1
                return
            entries, bad, _ = parse_feed(resp.body)
            if not entries:
                raise FetchError("feed for %s parsed to zero entries (%d bad)" % (name, bad))
            data = state.load_list(name)
            before_seen = data.get("last_seen", 0)
            added = merge_records(data, {e["mid"]: to_record(e, keep_bodies) for e in entries}, now)
            state.save_list(name, data)
            ts = [e["ts"] for e in entries if e["ts"] > 0]
            if ts and (before_seen == 0 or min(ts) > before_seen):
                deep_candidates.append((max(ts) - min(ts), name, before_seen))
            m.update(
                checked=now, fetched=now, mod=lists[name].modified, err="", n=len(data["msgs"]),
                etag=resp.headers.get("etag", ""), lm=resp.headers.get("last-modified", ""),
            )
            summary["fetched"] += 1
            summary["messages"] += added
            log("  %-28s +%d new (%d in feed, %d bad)" % (name, added, len(entries), bad))
        except (FetchError, StateError) as e:
            fail(name, e)

    queue = list(todo)
    pending = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            while queue and len(pending) < 2 and clock() < deadline and not summary["aborted"]:
                name = queue.pop(0)
                pending[pool.submit(work, name)] = name
            if not pending:
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                handle(pending.pop(fut), fut)
    summary["deferred"] = len(queue)
    if queue:
        log("budget or backoff reached: %d lists deferred to the next run" % len(queue))

    if deep_budget > 0 and not summary["aborted"]:
        deep_candidates.sort()  # shortest feed span (busiest list) first
        for _, name, before_seen in deep_candidates:
            if summary["deep_requests"] >= deep_budget or clock() >= deadline:
                break
            since = before_seen - 60 if before_seen else now - 6 * 3600
            _gap_fill(state, source, name, since, now, deep_pages, deep_budget, summary, ml, keep_bodies, log)

    state.save_meta(meta)
    return summary


def _gap_fill(state, source, name, since, now, pages, budget, summary, ml, keep_bodies, log):
    total = 0
    data = None
    try:
        data = state.load_list(name)
        offset = 0
        for _ in range(pages):
            if summary["deep_requests"] >= budget:
                break
            resp = source.search_atom(name, since, offset)
            summary["deep_requests"] += 1
            entries, _, _ = parse_feed(resp.body)
            if not entries:
                break
            total += merge_records(data, {e["mid"]: to_record(e, keep_bodies) for e in entries}, now)
            if len(entries) < SEARCH_PAGE:
                break
            offset += SEARCH_PAGE
    except (FetchError, StateError) as e:
        log("  %-28s gap fill stopped: %s" % (name, e))
    if data is not None and total:
        state.save_list(name, data)
        summary["messages"] += total
        ml[name]["n"] = len(data["msgs"])
    log("  %-28s gap fill +%d" % (name, total))


def backfill(state, source, now, names, hours, pages, keep_bodies=True, log=print):
    """Explicit history bootstrap for chosen lists via search atom (200 entries per request)."""
    meta = state.load_meta()
    ml = meta.setdefault("lists", {})
    summary = {"deep_requests": 0, "messages": 0}
    for name in names:
        ml.setdefault(name, {})
        _gap_fill(state, source, name, now - int(hours * 3600), now, pages, pages * len(names),
                  summary, ml, keep_bodies, log)
    state.save_meta(meta)
    return summary
