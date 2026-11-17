"""Retention by whole series.

A patch series (pull request) is kept or dropped as a unit: its cover letter, every patch, every
reply, the bodies of all of them and the diffs of its patches. Series are ranked newest first and kept
while their patch messages add up to at most `max_diff_msgs` per list (the first series is always kept,
even when it alone is longer; the per patch and per series size caps of diff.py still apply). Nothing
else decides which diffs or bodies of a series exist, so the site never has half a series.

Other messages (discussions, pull requests, replies whose patch is unknown) are only pruned by age and
by a count cap of their own (`max_msgs`), and only the newest few keep a body.
"""

from dataclasses import dataclass

from .model import add_records, build_entities, finish_entity
from .state import (
    BODY_DAYS, DAY, MAX_BODY_MSGS, MAX_DIFF_MSGS, MAX_MSGS, RETENTION_DAYS, day_of,
)


@dataclass
class Limits:
    retention_days: float = RETENTION_DAYS
    max_msgs: int = MAX_MSGS
    max_body_msgs: int = MAX_BODY_MSGS
    body_days: int = BODY_DAYS
    max_diff_msgs: int = MAX_DIFF_MSGS


def effective_retention(days):
    """Retention follows the configured backfill horizon but is never shorter than the default."""
    return max(RETENTION_DAYS, days or 0)


def is_series(e):
    """A patch series that can have a diff: a pull request entity that is not a ghost or a git pull."""
    return e.kind == "pr" and not e.ghost and not e.pull and bool(e.patches)


def series_of(data, name):
    """(series entities, message map) of the messages in one list state dict."""
    mm = {}
    add_records(mm, name, data["msgs"])
    ents = build_entities(mm)
    for e in ents:
        finish_entity(e)
    return [e for e in ents if is_series(e)], mm


def enforce(data, name, now, lim=None):
    """Apply the limits to one list state dict in place. Returns a summary dict.

    Records are removed here, so the caller must save the list afterwards (State.save_list also writes
    and deletes the diff day files for the days recorded in data["_touched"]).
    """
    lim = lim or Limits()
    msgs = data["msgs"]
    pend = data.get("_diffs") or {}
    touched = data.setdefault("_touched", set())
    horizon = now - lim.retention_days * DAY
    series, _ = series_of(data, name)

    with_diff, without = [], []
    for e in series:
        (with_diff if any("dz" in msgs[m.mid] for m in e.patches) else without).append(e)
    with_diff.sort(key=lambda e: (-e.opened, e.head))
    kept, total = [], 0
    for e in with_diff:
        if e.updated < horizon:
            continue
        if kept and total + len(e.patches) > lim.max_diff_msgs:
            break
        kept.append(e)
        total += len(e.patches)
    protected = {m.mid for e in kept for m in e.members}
    drop = {m.mid for e in with_diff + without for m in e.members} - protected

    def remove(mid):
        rec = msgs.pop(mid, None)
        if rec is not None and "dz" in rec:
            touched.add(day_of(rec["t"]))
        pend.pop(mid, None)

    for mid in drop:
        remove(mid)
    for mid in [m for m, r in msgs.items() if m not in protected and r["t"] < horizon]:
        remove(mid)
    free = [(r["t"], m) for m, r in msgs.items() if m not in protected]
    room = lim.max_msgs
    if len(free) > room:
        free.sort(reverse=True)
        for _, mid in free[room:]:
            remove(mid)
    body_horizon = now - lim.body_days * DAY
    with_body = sorted((r["t"], m) for m, r in msgs.items() if m not in protected and "b" in r)
    keep_body = {m for t, m in with_body[-lim.max_body_msgs:] if t >= body_horizon}
    for _, mid in with_body:
        if mid not in keep_body:
            for k in ("b", "bv", "bt"):
                msgs[mid].pop(k, None)
    ts = [r["t"] for r in msgs.values()]
    data["last_seen"] = max(ts) if ts else 0
    return {"series_kept": len(kept), "series_dropped": len(with_diff) - len(kept) + len(without),
            "patches_kept": total, "messages": len(msgs)}


def finalize_list(state, name, data, now, lim=None):
    """Enforce the limits and save: the one way ingested messages and diffs reach the state."""
    summary = enforce(data, name, now, lim)
    state.save_list(name, data)
    return summary


MIN_KEEP = 25  # the state budget never lowers the per list limit below this


def state_bytes(state):
    """Bytes of the data branch content that grows: message and diff day files."""
    total = 0
    for sub in ("lists", "diffs"):
        d = state.root / sub
        if d.is_dir():
            total += sum(p.stat().st_size for p in d.rglob("*.json.gz"))
    return total


def apply_meta_limits(state, lim):
    """Lower the per list patch limit to the one a previous run had to set to stay inside the state budget."""
    cap = state.load_meta().get("limits", {}).get("max_diff_msgs")
    if cap and cap < lim.max_diff_msgs:
        lim.max_diff_msgs = cap
    return lim


def trim_to_budget(state, now, max_bytes, lim=None, log=print):
    """Keep the data branch below `max_bytes`: when the state is over it, lower the per list limit of patch
    messages that keep a diff (recorded in meta.json, so later runs keep it) and apply it to every list at
    once. Whole series go, newest stay (see enforce). Returns the new limit or None when nothing was needed."""
    lim = lim or Limits()
    size = state_bytes(state)
    if not max_bytes or size <= max_bytes:
        return None
    cap = lim.max_diff_msgs
    for _ in range(4):
        cap = max(MIN_KEEP, int(cap * max_bytes * 0.92 / size))
        log("state is %.0f MB, over its %.0f MB budget: keeping at most %d patch messages with a diff per list" % (
            size / 1e6, max_bytes / 1e6, cap))
        trimmed = Limits(lim.retention_days, lim.max_msgs, lim.max_body_msgs, lim.body_days, cap)
        for name in state.list_names():
            data = state.load_list(name)
            finalize_list(state, name, data, now, trimmed)
        size = state_bytes(state)
        if size <= max_bytes or cap <= MIN_KEEP:
            break
    meta = state.load_meta()
    meta.setdefault("limits", {})["max_diff_msgs"] = cap
    state.save_meta(meta)
    lim.max_diff_msgs = cap
    return cap
