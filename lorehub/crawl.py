"""Resumable, budgeted crawl of list feeds into the accumulated state."""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from urllib.parse import quote

from . import LORE
from .feed import parse_feed, to_record
from .fetch import FetchError
from .manifest import ListInfo, parse_manifest
from .migrate import migrate
from .retention import Limits, finalize_list
from .state import RETENTION_DAYS, StateError, merge_records, stash_diffs

MANIFEST_URL = LORE + "/manifest.js.gz"
SEARCH_PAGE = 200
PATCH_TERMS = "s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE"


def feed_url(name):
    return "%s/%s/new.atom" % (LORE, name)


def stamp(ts):
    return time.strftime("%Y%m%d%H%M%S", time.gmtime(ts))


def window_query(lo_ts, hi_ts):
    """Patch related messages in a time window.

    Verified against lore: the parentheses are required because OR binds weaker than the implicit AND
    (without them the date bound only applies to the last term), dt: needs the 14 digit form, the upper
    bound is inclusive and the lower bound exclusive.
    """
    return "(dt:%s..%s) AND (%s)" % (stamp(lo_ts), stamp(hi_ts), PATCH_TERMS)


def window_url(name, lo_ts, hi_ts, offset=0):
    url = "%s/%s/?x=A&q=%s" % (LORE, name, quote(window_query(lo_ts, hi_ts), safe=":()"))
    if offset:
        url += "&o=%d" % offset
    return url


def thread_url(list_name, mid):
    """Thread Atom of the thread a message belongs to (any message of it works). `mid` is percent-encoded."""
    return "%s/%s/%s/t.atom" % (LORE, list_name, mid)


def thread_file(name, mid):
    return "%s.thread-%s.atom" % (name, mid.replace("/", "_")[:150])


class _Empty:
    status, body, headers = 404, b"", {}


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

    def search_window(self, name, lo_ts, hi_ts, seq):
        """One search Atom page (newest first, 200 entries) of patch related messages in (lo, hi].

        `seq` only names the saved raw response so that an offline replay finds it again.
        """
        try:
            resp = self.fetcher.get(window_url(name, lo_ts, hi_ts))
        except FetchError as e:
            if e.status == 404:  # public-inbox answers an empty result with 404
                return _Empty()
            raise
        self._save("%s.win-%s.atom" % (name, seq), resp.body)
        return resp


    def thread_atom(self, name, mid):
        """The whole thread of a message as Atom (one request). An unknown thread (404) is an empty answer."""
        try:
            resp = self.fetcher.get(thread_url(name, mid))
        except FetchError as e:
            if e.status == 404:
                return _Empty()
            raise
        self._save(thread_file(name, mid), resp.body)
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

    def search_window(self, name, lo_ts, hi_ts, seq):
        p = self.dir / ("%s.win-%s.atom" % (name, seq))
        if not p.exists():
            return _Empty()
        return self._R(p.read_bytes())


def _cache_thread(self, name, mid):
    p = self.dir / thread_file(name, mid)
    return self._R(p.read_bytes()) if p.exists() else _Empty()


CacheSource.thread_atom = _cache_thread


def manifest_to_json(lists):
    return {n: {"d": i.description, "m": i.modified, "e": i.epochs} for n, i in sorted(lists.items())}


def manifest_from_json(stored):
    return {n: ListInfo(n, v.get("d", ""), v.get("m", 0), v.get("e", [])) for n, v in stored.items()}


def ingest(state, name, entries, now, keep_bodies=True, data=None, retention_days=RETENTION_DAYS):
    """Merge parsed feed entries into one list: messages with complete bodies, and their parsed diffs.

    Returns (messages added, list data). Diffs wait in the list data until the caller runs
    retention.finalize_list (which applies the whole series limits and saves), so nothing is written here.
    `retention_days=None` accepts messages of any age (used when a whole thread is completed).
    """
    if data is None:
        data = state.load_list(name)
    diffs = {}
    records = {e["mid"]: to_record(e, keep_bodies, diffs) for e in entries}
    added = merge_records(data, records, now, retention_days)
    stash_diffs(data, diffs)
    return added, data


def feed_rate(entries):
    """Messages per day estimated from the time span of a feed page (0 when unknown)."""
    ts = sorted(e["ts"] for e in entries if e["ts"] > 0)
    if len(ts) < 2:
        return 0.0
    span = ts[-1] - ts[0]
    return round((len(ts) - 1) / span * 86400, 1) if span > 0 else 1e5


def crawl(state, source, now, names=None, budget_s=1500, deep_budget=0, deep_pages=2,
          keep_bodies=True, log=print, clock=time.monotonic, limits=None):
    """Run one crawl pass and return a summary dict.

    Stored state is only ever replaced by a merged superset (plus age pruning), so a failed
    or partial pass cannot corrupt or shrink it.
    """
    deadline = clock() + budget_s
    limits = limits or Limits()
    migrate(state, now, log)
    summary = {"touched": [], "fetched": 0, "skipped": 0, "failed": 0, "deferred": 0, "messages": 0,
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
            added, data = ingest(state, name, entries, now, keep_bodies, data, limits.retention_days)
            finalize_list(state, name, data, now, limits)
            summary["touched"].append(name)
            ts = [e["ts"] for e in entries if e["ts"] > 0]
            if ts and (before_seen == 0 or min(ts) > before_seen):
                deep_candidates.append((max(ts) - min(ts), name, before_seen))
            m.update(
                checked=now, fetched=now, mod=lists[name].modified, err="", n=len(data["msgs"]),
                etag=resp.headers.get("etag", ""), lm=resp.headers.get("last-modified", ""),
                rt=feed_rate(entries),
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
            _gap_fill(state, source, name, since, now, deep_pages, deep_budget, summary, ml, keep_bodies, log, limits)

    state.save_meta(meta)
    return summary


def _gap_fill(state, source, name, since, now, pages, budget, summary, ml, keep_bodies, log, limits):
    """Sweep a busy list for patch related messages posted since `since` (the feed skipped them)."""
    total = 0
    data = None
    changed = False
    try:
        data = state.load_list(name)
        hi = now + 3600
        for page in range(pages):
            if summary["deep_requests"] >= budget:
                break
            resp = source.search_window(name, since, hi, "sweep-%d" % page)
            summary["deep_requests"] += 1
            entries, _, _ = parse_feed(resp.body)
            if not entries:
                break
            added, data = ingest(state, name, entries, now, keep_bodies, data, limits.retention_days)
            total += added
            changed = True
            if len(entries) < SEARCH_PAGE:
                break
            hi = min(e["ts"] for e in entries if e["ts"] > 0)
    except (FetchError, StateError) as e:
        log("  %-28s gap fill stopped: %s" % (name, e))
    if data is not None and changed:
        finalize_list(state, name, data, now, limits)
        if name not in summary["touched"]:
            summary["touched"].append(name)
        summary["messages"] += total
        ml[name]["n"] = len(data["msgs"])
    log("  %-28s gap fill +%d" % (name, total))
