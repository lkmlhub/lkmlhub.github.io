"""Choosing which series get a published diff, and shaping the per-series diff shard.

Pure functions, no I/O, so the size policy is easy to test.
"""

SERIES_CAP = 600 * 1024  # kept hunk text per series; later patches are cut when a series is huge
RANK_STATE = {"open": 0, "merged": 1, "closed": 2}


def est_size(kept_bytes, patches):
    """Published JSON size estimate for a series: hunk text plus escaping and per-patch overhead."""
    return int(kept_bytes * 1.08) + 160 * patches + 64


def pick(cands, budget):
    """Select candidates within a byte budget, fair across lists.

    cands: list of (list name, state name, updated ts, size, key). Within a list the order is open
    series first, then merged, then closed, newest first. The per-list quota is raised until the
    budget is used (water filling), so quiet lists keep all their diffs and busy lists share the rest
    equally. Left over budget goes to the best candidates not yet taken. Returns the set of keys.
    """
    if sum(c[3] for c in cands) <= budget:
        return {c[4] for c in cands}

    def rank(c):
        return (RANK_STATE.get(c[1], 2), -c[2], str(c[4]))

    by_list = {}
    for c in sorted(cands, key=rank):
        by_list.setdefault(c[0], []).append(c)

    def take(items, quota):
        got, used = [], 0
        for c in items:
            if used + c[3] <= quota:
                got.append(c)
                used += c[3]
        return got, used

    lo, hi = 0, max(sum(c[3] for c in items) for items in by_list.values())
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(take(items, mid)[1] for items in by_list.values()) <= budget:
            lo = mid
        else:
            hi = mid - 1
    chosen, used = set(), 0
    for items in by_list.values():
        got, u = take(items, lo)
        chosen.update(c[4] for c in got)
        used += u
    for c in sorted(cands, key=rank):
        if c[4] not in chosen and used + c[3] <= budget:
            chosen.add(c[4])
            used += c[3]
    return chosen


def cap_series(diffs, cap=SERIES_CAP):
    """Copy of a list of per-patch diff dicts where hunk text beyond `cap` bytes is dropped and flagged."""
    out, used = [], 0
    for d in diffs:
        if d is None:
            out.append(None)
            continue
        files, cut = [], bool(d.get("tr"))
        for f in d["f"]:
            x = f.get("x")
            if x is None:
                files.append(f)
                continue
            size = len(x.encode("utf-8", "replace"))
            if used + size > cap:
                f = {k: v for k, v in f.items() if k != "x"}
                f["t"] = 1
                cut = True
            else:
                used += size
            files.append(f)
        item = dict(d, f=files)
        if cut:
            item["tr"] = 1
        out.append(item)
    return out
