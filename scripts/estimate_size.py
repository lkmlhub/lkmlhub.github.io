"""Estimate steady-state data branch and site size from a one-shot crawl.

usage: python3 scripts/estimate_size.py STATE_DIR

The state should come from a single pass over all lists (25 newest messages each). The newest-25
time span gives a messages-per-day rate per list, which is extrapolated over the retention window.
Unit costs are the ones measured on the development lists (see docs/DESIGN.md). Treat the result as
an order of magnitude: peak-hour rates overstate the daily average, quiet weekends understate it.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorehub.backfill import Config  # noqa: E402
from lorehub.build import DIFF_BUDGET, MAX_ENTITIES  # noqa: E402
from lorehub.state import BODY_DAYS, MAX_BODY_MSGS, MAX_DIFF_MSGS, MAX_MSGS, RETENTION_DAYS, State  # noqa: E402

DAY = 86400
ENTITIES_PER_MESSAGE = 0.41  # threads per message on the development lists
STATE_BYTES_PER_MSG = 100  # gzip, no body
STATE_BYTES_PER_BODY = 440  # gzip
SITE_BYTES_PER_ENTITY = 1650  # rows, detail without bodies, authors, meta
SITE_BYTES_PER_BODY = 1450
MSGS_PER_ENTITY = 2.5
FIXED_PER_LIST = 3000

# Measured on 1 469 patch messages (8 development lists, tests/ docs/DESIGN.md "Diff storage"):
PATCH_FRACTION = 0.43  # share of all messages that are patch messages carrying a diff
STATE_BYTES_PER_DIFF = 1300  # gzip of the day files, bulk (1 229 B) to alone (1 522 B)
SITE_BYTES_PER_DIFF = 5450  # published JSON of one patch, mean (the median is 1.4 KB, a few big ones dominate)
PATCHES_PER_PUBLISHED_PR = 3.5
SEARCH_BYTES_PER_DOC = 190  # index.json, token shards and document shards
# Backfill request math: search pages of 200 entries, patch related share of a list's traffic
BACKFILL_SHARE = 0.87


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


def estimate(rate, factor=1.0, max_msgs=MAX_MSGS, max_body=MAX_BODY_MSGS, max_ent=MAX_ENTITIES):
    state = site = ents = 0.0
    for r in rate.values():
        r *= factor
        v = min(r * RETENTION_DAYS, max_msgs)
        b = min(r * BODY_DAYS, max_body, v)
        e = min(ENTITIES_PER_MESSAGE * v, max_ent)
        state += v * STATE_BYTES_PER_MSG + b * STATE_BYTES_PER_BODY
        site += e * SITE_BYTES_PER_ENTITY + min(b, e * MSGS_PER_ENTITY) * SITE_BYTES_PER_BODY + FIXED_PER_LIST
        ents += e
    return state / 1e6, site / 1e6, ents


def diffs(rate, factor=1.0, keep=MAX_DIFF_MSGS, max_ent=MAX_ENTITIES, budget=DIFF_BUDGET):
    """(state MB gz, site MB, site MB if unlimited, search MB) for the diffs and the search index."""
    st = site = docs = 0.0
    for r in rate.values():
        r *= factor
        v = min(r * RETENTION_DAYS, MAX_MSGS)
        patches = min(PATCH_FRACTION * v, keep)
        e = min(ENTITIES_PER_MESSAGE * v, max_ent)
        st += patches * STATE_BYTES_PER_DIFF
        site += min(patches, e * 0.4 * PATCHES_PER_PUBLISHED_PR) * SITE_BYTES_PER_DIFF  # ~40% of threads are series
        docs += e
    return st / 1e6, min(site, budget) / 1e6, site / 1e6, docs * SEARCH_BYTES_PER_DOC / 1e6


def backfill_requests(rate, days, list_pages):
    """Search pages needed to walk every list back `days` days with a per-list page cap."""
    req = capped = 0
    for r in rate.values():
        r = r if r < 1e4 else 3000  # lists whose 25 newest messages share a second
        pages = max(1, math.ceil(r * BACKFILL_SHARE * days / 200))
        if pages > list_pages:
            pages, capped = list_pages, capped + 1
        req += pages
    return req, capped


def main(path):
    rate = rates(State(path))
    print("lists: %d, estimated messages/day over all lists: %.0f" % (len(rate), sum(v for v in rate.values() if v < 1e4)))
    print("caps: %d messages, %d bodies, %d published threads per list" % (MAX_MSGS, MAX_BODY_MSGS, MAX_ENTITIES))
    for label, factor in (("low (0.5x rates)", 0.5), ("expected", 1.0), ("high (1.5x rates)", 1.5)):
        s, w, e = estimate(rate, factor)
        print("%-18s data branch %5.0f MB gz, site %5.0f MB, %6.0f published threads" % (label, s, w, e))
    s, w, e = estimate(rate, 1.0, 10**9, 10**9, 10**9)
    print("uncapped (120 days, all bodies): data branch %.0f MB gz, site %.0f MB" % (s, w))
    print()
    print("with diffs (newest %d patch diffs per list in state, site budget %d MB) and the search index:" % (MAX_DIFF_MSGS, DIFF_BUDGET // 10**6))
    for label, factor in (("low (0.5x rates)", 0.5), ("expected", 1.0), ("high (1.5x rates)", 1.5)):
        s, w, e = estimate(rate, factor)
        ds, dw, dwu, sx = diffs(rate, factor)
        print("%-18s data branch %4.0f MB (+%3.0f diffs), site %4.0f MB (+%2.0f diffs, +%2.0f search; diffs unlimited %3.0f), index docs %5.0f"
              % (label, s + ds, ds, w + dw + sx, dw, sx, dwu, e))
    print()
    print("backfill requests (one request per 200 search entries, per-list page cap, min one per list; the rest of")
    print("the %d manifest lists have no sample and count one request each):" % 359)
    unknown = max(0, 359 - len(rate))
    print("%5s %9s %9s %9s %9s" % ("days", "cap 50", "cap 25", "cap 10", "no cap"))
    for days in (14, 30, 45, 60, 90):
        row = [backfill_requests(rate, days, c)[0] + unknown for c in (50, 25, 10, 10**6)]
        print("%5d %9d %9d %9d %9d" % ((days,) + tuple(row)))
    d = Config()
    req, capped = backfill_requests(rate, d.days, d.list_pages)
    print("default (%g days, %d pages per list): %d requests, %d lists hit the page cap, about %.1f GB raw (%.1f GB gzip on the wire)"
          % (d.days, d.list_pages, req + unknown, capped, (req + unknown) * 1.2 / 1000, (req + unknown) * 1.2 / 1000 / 4.2))


if __name__ == "__main__":
    main(sys.argv[1])
