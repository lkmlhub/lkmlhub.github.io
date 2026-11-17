"""Estimate the steady-state size of the data branch and of the site, and the backfill request math.

usage: python3 scripts/estimate_size.py STATE_DIR

STATE_DIR should come from one pass over all lists (the 25 newest messages of each). The time span of those
25 messages gives a message rate per list, which is extrapolated over the retention window. Unit costs are
measured on real data (see docs/DESIGN.md, "Sizes"): the cached search pages of selinux, linux-security-module,
netdev and lkml (7 days each), ingested with the real code (complete bodies, whole series retention) and built.
Peak-hour sample rates overstate the daily average and weekends understate it: treat the result as the right
order of magnitude, hence the low / expected / high columns.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorehub.backfill import Config  # noqa: E402
from lorehub.build import MAX_ENTITIES, MAX_SITE_MB  # noqa: E402
from lorehub.state import MAX_BODY_MSGS, MAX_DIFF_MSGS, MAX_MSGS, RETENTION_DAYS, State  # noqa: E402

DAY = 86400
CEILING_MB = 700  # site and data branch, each

# ---- measured unit costs (bytes). Range = (low, expected, high) over the four measured lists.
PATCH_FRACTION = (0.30, 0.43, 0.50)  # share of all messages that are patch messages with a diff
MSGS_PER_PATCH = (1.7, 2.6, 3.5)  # messages of a shown series (cover, patches, replies) per patch with a diff
SERIES_PER_PATCH = 0.27  # shown series per patch with a diff (3.7 patches per series)
STATE_DIFF = (580, 1170, 1950)  # state/diffs, gzip day files, per patch message with a diff
STATE_MSG = (420, 530, 640)  # state/lists, gzip day files, per message with its complete body
STATE_MSG_NOBODY = 100  # per message without a body
SITE_DIFF = (700, 1260, 2000)  # data/diffs/*.json.gz per patch message with a diff
SITE_BODY = (230, 520, 715)  # data/bodies/*.json.gz per message
SITE_LIGHT_MSG = (264, 350, 430)  # data/threads shard JSON per message (author, time, trailers, parent index)
SITE_LIGHT_THREAD = 900  # list rows, search document and profile share, per thread
SITE_FIXED_PER_LIST = 15000  # meta.json with the daily chart and contributors, per list (measured 15.5 KB)
SEARCH_PER_THREAD = 190
DISCUSSION_MSGS = 2.5  # messages per discussion
FIXED_DATA = 1_500_000  # manifest, meta, numbers
ASSETS = 400_000
GZ_THREADS = 0.30  # gzip ratio of the thread detail shards when they are stored compressed (measured on lkml)
BACKFILL_SHARE = 0.87  # patch related share of a list's traffic (search query)
ENTRIES_PER_PATCH = 3.0  # search entries (patch messages, covers, tagged replies) per patch message with a diff


def rates(state):
    out = {}
    for name in state.list_names():
        ts = sorted(r["t"] for r in state.load_list(name)["msgs"].values())
        k = len(ts)
        if k < 25:
            out[name] = k / RETENTION_DAYS  # the feed reached back past the window: this is everything
        else:
            span = ts[-1] - ts[0]
            out[name] = (k - 1) / span * DAY if span > 0 else 1e5
    return out


def estimate(rate, level=1, rate_factor=1.0, days=RETENTION_DAYS, max_diff=MAX_DIFF_MSGS, max_msgs=MAX_MSGS,
             max_disc=MAX_ENTITIES, gz_threads=False, body_scale=1.0):
    """Sizes in bytes for one scenario. level 0/1/2 picks the low/expected/high unit costs and ratios.

    Returns a dict with the data branch, the site before and after leaving out series, patches and series.
    """
    pf, mpp = PATCH_FRACTION[level], MSGS_PER_PATCH[level]
    d = {"patches": 0.0, "series": 0.0, "data": FIXED_DATA, "site": ASSETS, "lists_at_cap": 0, "msgs": 0.0}
    for r in rate.values():
        r = min(r, 3000.0) * rate_factor
        pp = r * pf * days
        patches = min(pp, max_diff)
        d["lists_at_cap"] += pp >= max_diff
        series = patches * SERIES_PER_PATCH
        members = patches * mpp
        others = min(r * (1 - pf) * days, max_msgs)
        discs = min(others / DISCUSSION_MSGS, max_disc)
        disc_msgs = discs * DISCUSSION_MSGS
        bodies_disc = min(disc_msgs, MAX_BODY_MSGS)
        d["patches"] += patches
        d["series"] += series
        d["msgs"] += members + disc_msgs
        d["data"] += (members * STATE_MSG[level] * body_scale + patches * STATE_DIFF[level]
                      + others * STATE_MSG_NOBODY + bodies_disc * STATE_MSG[level] * body_scale)
        light_msgs = (members + disc_msgs) * SITE_LIGHT_MSG[level] * (GZ_THREADS if gz_threads else 1.0)
        d["site"] += (patches * SITE_DIFF[level] + (members + bodies_disc) * SITE_BODY[level] * body_scale + light_msgs
                      + (series + discs) * (SITE_LIGHT_THREAD + SEARCH_PER_THREAD) + SITE_FIXED_PER_LIST)
    return d


def backfill_requests(rate, days, list_pages, max_diff=MAX_DIFF_MSGS, fill=True):
    """Search pages to walk every list back `days` days. A list stops at the horizon, at the page cap, or (fill)
    when its newest `max_diff` patch diffs are stored, whichever comes first. Returns (requests, capped, filled)."""
    req = capped = filled = 0
    for r in rate.values():
        r = min(r, 3000.0)
        entries = r * BACKFILL_SHARE * days
        if fill:
            patches = r * PATCH_FRACTION[1] * days
            if patches > max_diff * 1.1:
                entries = min(entries, max_diff * 1.1 * ENTRIES_PER_PATCH)
                filled += 1
        pages = max(1, math.ceil(entries / 200))
        if pages > list_pages:
            pages, capped = list_pages, capped + 1
        req += pages
    return req, capped, filled


def mb(x):
    return x / 1e6


def main(path):
    rate = rates(State(path))
    unknown = max(0, 359 - len(rate))
    print("lists: %d with a rate sample, estimated messages/day over all lists: %.0f (peak sample)" % (
        len(rate), sum(v for v in rate.values() if v < 1e4)))
    print("limits: %d days, %d patch messages with a diff per list, %d other messages, %d discussions published per list" % (
        RETENTION_DAYS, MAX_DIFF_MSGS, MAX_MSGS, MAX_ENTITIES))
    print()
    print("%-34s %9s %9s %9s %9s" % ("", "low", "expected", "high", "ceiling"))
    rows = []
    for label, level, factor in (("low (0.5x rates, low costs)", 0, 0.5), ("expected", 1, 1.0), ("high (1.5x rates, high costs)", 2, 1.5)):
        rows.append((label, estimate(rate, level, factor)))
    for key, name in (("data", "data branch MB (gzip files)"), ("site", "site MB before leaving out series")):
        print("%-34s %9.0f %9.0f %9.0f %9d" % (name, *(mb(r[1][key]) for r in rows), CEILING_MB))
    print("%-34s %9.0f %9.0f %9.0f" % ("patch messages with a diff", *(r[1]["patches"] for r in rows)))
    print("%-34s %9.0f %9.0f %9.0f" % ("lists at the 1000 patch cap", *(r[1]["lists_at_cap"] for r in rows)))
    print()
    print("levers (expected, in MB):")
    base = estimate(rate)
    comp = estimate(rate, gz_threads=True)
    print("  thread shards stored gzip:       site %.0f -> %.0f" % (mb(base["site"]), mb(comp["site"])))
    for md in (500, 300):
        e = estimate(rate, max_diff=md, gz_threads=True)
        print("  and %d patch diffs per list:     data %.0f, site %.0f" % (md, mb(e["data"]), mb(e["site"])))
    e = estimate(rate, max_diff=MAX_DIFF_MSGS, gz_threads=True, body_scale=0.83)
    print("  and quoted lines deduplicated:   data %.0f, site %.0f  (bodies x0.83)" % (mb(e["data"]), mb(e["site"])))
    print()
    print("with the shipped levers (thread shards gzip): site MB low / expected / high, ceiling %d:" % MAX_SITE_MB)
    out = [estimate(rate, level, factor, gz_threads=True) for _, level, factor in (("", 0, 0.5), ("", 1, 1.0), ("", 2, 1.5))]
    print("  site %.0f / %.0f / %.0f   data %.0f / %.0f / %.0f" % (
        *(mb(o["site"]) for o in out), *(mb(o["data"]) for o in out)))
    print()
    print("backfill requests for all %d lists (one request per 200 search entries; the %d lists without a sample count 1):" % (359, unknown))
    print("%5s %12s %12s %12s %12s" % ("days", "cap 60+fill", "cap 60", "cap 25", "no cap"))
    for days in (30, 90, 180):
        row = [backfill_requests(rate, days, 60, fill=True)[0], backfill_requests(rate, days, 60, fill=False)[0],
               backfill_requests(rate, days, 25, fill=False)[0], backfill_requests(rate, days, 10**6, fill=False)[0]]
        print("%5d %12d %12d %12d %12d" % ((days,) + tuple(x + unknown for x in row)))
    d = Config()
    req, capped, filled = backfill_requests(rate, d.days, d.list_pages)
    total = req + unknown
    run = 900
    print("default (%g days, page cap %d, stop when the newest %d patch diffs are stored): %d requests, %d lists filled early, %d hit the page cap,"
          % (d.days, d.list_pages, MAX_DIFF_MSGS, total, filled, capped))
    print("  about %.1f GB raw, %.1f GB gzip on the wire; at %d requests per run: %d runs" % (
        total * 1.2 / 1000, total * 1.2 / 1000 / 4.2, run, math.ceil(total / run)))


if __name__ == "__main__":
    main(sys.argv[1])
