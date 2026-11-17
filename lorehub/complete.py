"""Thread completion: find series with missing messages and fetch their whole thread from lore.

The search based backfill finds a reply when its subject still carries the patch tags. Replies with an
edited subject and messages that fell into a gap of the 25 entry feed are not found. For every
displayed series that looks incomplete the whole thread is fetched in ONE request, the thread Atom of
any message of it:

    https://lore.kernel.org/<list>/<message-id>/t.atom

(verified live: it answers for the cover letter, a patch or a reply alike, the percent-encoded message-id
works, it returns every message of the thread in that list's archive, replies with in-reply-to links).

A series looks incomplete when (see find_gaps)
  p  a reply or a non-first patch has a parent that is not in the state,
  n  numbered patches are missing (1/7, 2/7, 4/7 present: 3/7 is not), compared with the highest N,
  c  patches say N > 1, there is no cover letter, and a patch points to a parent that is not known,
  l  a message holds only an older, trimmed body (state format before epoch 3) after the walk of its list
     is done (the walk itself replaces those bodies).

The queue is resumable: after a fetch the series' first message carries "tc" (time) and "tn" (count)
in the state, a series fetched less than a day ago is not fetched again, one that still has gaps after
three fetches is left alone (lore does not have the missing messages). Newest series first, open ones
before closed ones, with a request budget of its own per run.
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from .crawl import ingest, thread_url  # thread_url is re-exported for callers and tests
from .feed import parse_feed
from .fetch import FetchError
from .model import compute_status, link_versions
from .parse import BODY_VERSION, norm_title
from .retention import Limits, effective_retention, finalize_list, series_of
from .state import DAY, StateError

LEGACY = "l"
RETRY_AFTER = DAY  # a series whose thread was fetched is not fetched again within this time
MAX_FETCHES = 3  # per series, after that the gaps are lore's
MAX_QUEUE_PER_LIST = 500
AD_KEEP = 4000  # per list: series whose address fetch is recorded in meta ("ad"), oldest are forgotten first


@dataclass
class Config:
    max_requests: int = 300  # thread requests per run
    max_addr_requests: int = 100  # of them, at most this many only to learn sender addresses (see address_gap)
    budget_s: float = 480  # wall clock budget of one run
    commit_every: int = 40  # fetched threads between saves


def root_of(e):
    """The message that identifies a series in the queue: the cover letter, else the first patch."""
    return e.cover or (e.patches[0] if e.patches else None)


def subject_key(s):
    """What a reply shares with the patch it answers: version, number, total and the normalised title."""
    return (s.version, s.index or 0, s.total or 0, norm_title(s.title))


def orphan_keys(msgs):
    """Subject keys of replies to patches whose parent message is not in the state. Such a reply is not part of
    any thread we can see (it forms an entity of its own), the series it belongs to is found by its subject."""
    return {subject_key(m.subj) for m in msgs.values()
            if m.subj.is_reply and m.subj.kind == "patch" and m.p and m.p not in msgs}


def find_gaps(e, msgs, legacy=False, orphans=None):
    """Reasons ("p", "n", "c", "l" as one string, sorted) why the conversation of series `e` looks incomplete.

    `orphans` is orphan_keys(msgs) (computed once for many series); without it only the series' own messages
    are looked at."""
    gaps = set()
    root = root_of(e)
    for m in e.comments:
        if m.p and m.p not in msgs:
            gaps.add("p")
            break
    for m in e.patches + ([e.cover] if e.cover else []):
        if m is not root and m.p and m.p not in msgs:
            gaps.add("p")
            break
    if orphans and any(subject_key(m.subj) in orphans for m in e.patches + ([e.cover] if e.cover else [])):
        gaps.add("p")
    if e.total > 1:
        have = {m.subj.index for m in e.patches if m.subj.index}
        if any(i not in have for i in range(1, e.total + 1)):
            gaps.add("n")
        if e.cover is None and any(m.p and m.p not in msgs for m in e.patches):
            gaps.add("c")
    if legacy and any(m.bv < BODY_VERSION and m.mid in msgs for m in e.members):
        gaps.add(LEGACY)
    return "".join(sorted(gaps))


def fetched_state(e):
    """(time of the last thread fetch, number of fetches) recorded on the series' messages."""
    tc = max((m.tc for m in e.members), default=0)
    tn = max((m.tn for m in e.members), default=0)
    return tc, tn


def due(e, now):
    """True when a series may be fetched: never fetched, or fetched long enough ago and not too often."""
    tc, tn = fetched_state(e)
    return not tc or (now - tc >= RETRY_AFTER and tn < MAX_FETCHES)


def coverage(e, msgs, now, orphans=None):
    """(code, gap letters) of a displayed series: 0 complete, 1 waiting for a thread fetch (or the walk),
    2 fetched but lore has no more (gaps remain until a retry)."""
    gaps = find_gaps(e, msgs, legacy=True, orphans=orphans)
    if not gaps:
        return 0, ""
    return (1 if due(e, now) else 2), gaps


def address_gap(e, known):
    """True when a participant of series `e` has no stored sender address (`known`: set of author hashes with one)."""
    return any(m.h and m.h not in known for m in e.members)


def scan_list_full(state, name, now, legacy=False, horizon=0, known=None, done=()):
    """(completion items, address items) of one list.

    Completion items are displayed series that look incomplete and are due for a thread fetch:
    [first message id, last activity, open (1/0), gap letters, patch count]. Trimmed bodies of an older state format
    only count once the walk of the list is done (`legacy`): the walk replaces them.

    Address items ([first message id, last activity]) are displayed OPEN series with a participant whose sender
    address is unknown (`known` is the set of author hashes with an address, None turns this off) and that were not
    fetched for addresses before (`done`: first message ids recorded in meta). A thread fetch fills the address map
    from the whole thread, one request per series.
    """
    data = state.load_list(name)
    series, mm = series_of(data, name)
    link_versions(series)
    orphans = orphan_keys(mm)
    out, addr = [], []
    for e in series:
        if e.updated < horizon or not any("dz" in data["msgs"][m.mid] for m in e.patches):
            continue
        gaps = find_gaps(e, mm, legacy, orphans)
        wants = known is not None and root_of(e).mid not in done and address_gap(e, known)
        if not (gaps and due(e, now)) and not wants:
            continue
        compute_status(e, now)
        if gaps and due(e, now):
            out.append([root_of(e).mid, e.updated, int(e.state == "open"), gaps, len(e.patches)])
        elif wants and e.state == "open":
            addr.append([root_of(e).mid, e.updated])
    out.sort(key=lambda x: (-x[2], -x[1], x[0]))
    addr.sort(key=lambda x: (-x[1], x[0]))
    return out[:MAX_QUEUE_PER_LIST], addr[:MAX_QUEUE_PER_LIST]


def scan_list(state, name, now, legacy=False, horizon=0):
    """Completion queue items of one list (see scan_list_full)."""
    return scan_list_full(state, name, now, legacy, horizon)[0]


def known_hashes(state, names):
    """Author hashes that have a stored sender address in any list (hashes are global)."""
    known = set()
    for n in names:
        try:
            known.update(state.load_addrs(n)["a"])
        except StateError:
            pass
    return known


def mark_fetched(data, mid, now, added):
    """Record a thread fetch on the series' first message. A fetch that found nothing new is final: the gaps
    that remain are lore's, fetching again would only repeat the answer."""
    rec = data["msgs"].get(mid)
    if rec is not None:
        rec["tc"] = now
        rec["tn"] = rec.get("tn", 0) + 1 if added else MAX_FETCHES


def run_completion(state, source, now, cfg=None, names=None, touched=None, log=print, clock=time.monotonic,
                   limits=None, days=180):
    """One budgeted pass over the completion queue. Returns a summary dict. Never shrinks stored state.

    `touched` are the lists changed earlier in this run: their queue is scanned again (a list that has
    no stored queue is scanned too, all others reuse the queue kept in state/meta.json).
    """
    cfg = cfg or Config()
    limits = limits or Limits(retention_days=effective_retention(days))
    deadline = clock() + cfg.budget_s
    meta = state.load_meta()
    ml = meta.setdefault("lists", {})
    summary = {"touched": [], "requests": 0, "fetched": 0, "messages": 0, "failed": 0, "queue": 0, "left": 0,
               "stopped": "", "scanned": 0, "addr_requests": 0, "addr_queue": 0, "addr_left": 0}
    horizon = now - days * DAY
    universe = [n for n in (names or state.list_names()) if n in set(state.list_names())]
    touched = set(touched or ())
    addrs_on = bool(getattr(state, "reply_addresses", True))
    known = known_hashes(state, state.list_names()) if addrs_on else None
    done = {mid for m in ml.values() for mid in (m.get("ad") or {})}
    for name in universe:
        m = ml.setdefault(name, {})
        if name in touched or "cq" not in m or (addrs_on and "aq" not in m):
            m["cq"], aq_items = scan_list_full(state, name, now, bool((m.get("bf") or {}).get("done")), horizon, known, done)
            if addrs_on:
                m["aq"] = aq_items
            summary["scanned"] += 1
        elif not addrs_on:
            m.pop("aq", None)
    queue = sorted(((it[2], it[1], name, it) for name in universe for it in ml[name].get("cq", [])),
                   key=lambda x: (-x[0], -x[1], x[2], x[3][0]))
    summary["queue"] = len(queue)
    seen_roots = {it[0] for _, _, _, it in queue}
    aq = []
    for name in universe:
        for it in ml[name].get("aq", []):
            if it[0] not in seen_roots and it[0] not in done:
                seen_roots.add(it[0])
                aq.append((-it[1], name, it))
    aq.sort(key=lambda x: (x[0], x[1], x[2][0]))
    summary["addr_queue"] = len(aq)
    log("completion: %d series wait for their thread, %d open series wait for sender addresses (%d lists scanned)" % (
        len(queue), len(aq), summary["scanned"]))
    queue = [(name, it, "") for _, _, name, it in queue] + [(name, it, "a") for _, name, it in aq]
    open_lists = {}
    since_commit = 0

    def commit():
        for nm, data in open_lists.items():
            finalize_list(state, nm, data, now, limits)
        open_lists.clear()
        state.save_meta(meta)

    def done_item(name, it, kind):
        q = ml[name].get("aq" if kind else "cq", [])
        if it in q:
            q.remove(it)
        if kind or addrs_on:
            ad = ml[name].setdefault("ad", {})
            ad[it[0]] = now
            if len(ad) > AD_KEEP:
                for k in sorted(ad, key=ad.get)[: len(ad) - AD_KEEP]:
                    del ad[k]

    def handle(name, it, kind, fut):
        nonlocal since_commit
        try:
            resp = fut.result()
            entries = parse_feed(resp.body)[0] if resp.body else []
        except (FetchError, OSError, EOFError, ValueError) as e:
            summary["failed"] += 1
            log("  %-28s thread FAILED: %s" % (name, e))
            if getattr(e, "backoff", False):
                summary["stopped"] = "server asked to back off"
            return
        try:
            data = open_lists.get(name)
            if data is None:
                data = open_lists[name] = state.load_list(name)
            added = 0
            if entries:
                added, _ = ingest_all(state, name, entries, now, data)
                summary["messages"] += added
            if not kind:
                mark_fetched(data, it[0], now, added)
        except StateError as e:
            summary["failed"] += 1
            log("  %-28s thread FAILED: %s" % (name, e))
            return
        if name not in summary["touched"]:
            summary["touched"].append(name)
        summary["fetched"] += 1
        done_item(name, it, kind)
        since_commit += 1
        if since_commit >= cfg.commit_every:
            commit()
            since_commit = 0

    pending = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            while queue and len(pending) < 2 and not summary["stopped"]:
                if clock() >= deadline:
                    summary["stopped"] = "time budget"
                elif summary["requests"] >= cfg.max_requests:
                    summary["stopped"] = "per run request cap"
                elif queue[0][2] and summary["addr_requests"] >= cfg.max_addr_requests:
                    summary["stopped"] = "per run cap for address requests"
                else:
                    name, it, kind = queue.pop(0)
                    summary["requests"] += 1
                    summary["addr_requests"] += 1 if kind else 0
                    pending[pool.submit(source.thread_atom, name, it[0])] = (name, it, kind)
                    continue
                break
            if not pending:
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                name, it, kind = pending.pop(fut)
                handle(name, it, kind, fut)
    commit()
    summary["left"] = sum(len(ml[n].get("cq", [])) for n in universe)
    summary["addr_left"] = sum(len(ml[n].get("aq", [])) for n in universe)
    log("completion summary: %s" % {k: v for k, v in summary.items() if k != "touched"})
    return summary


def ingest_all(state, name, entries, now, data):
    """Add the messages of a whole thread to a list; threads may reach back beyond the horizon."""
    return ingest(state, name, entries, now, True, data, retention_days=None)
