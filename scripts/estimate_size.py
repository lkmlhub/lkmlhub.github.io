"""Estimate steady-state data branch and site size from a one-shot crawl.

usage: python3 scripts/estimate_size.py STATE_DIR

The state should come from a single pass over all lists (25 newest messages each). The newest-25
time span gives a messages-per-day rate per list, which is extrapolated over the retention window.
Unit costs are the ones measured on the development lists (see docs/DESIGN.md). Treat the result as
an order of magnitude: peak-hour rates overstate the daily average, quiet weekends understate it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorehub.build import MAX_ENTITIES  # noqa: E402
from lorehub.state import BODY_DAYS, MAX_BODY_MSGS, MAX_MSGS, RETENTION_DAYS, State  # noqa: E402

DAY = 86400
ENTITIES_PER_MESSAGE = 0.41  # threads per message on the development lists
STATE_BYTES_PER_MSG = 100  # gzip, no body
STATE_BYTES_PER_BODY = 440  # gzip
SITE_BYTES_PER_ENTITY = 1650  # rows, detail without bodies, authors, meta
SITE_BYTES_PER_BODY = 1450
MSGS_PER_ENTITY = 2.5
FIXED_PER_LIST = 3000


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


def main(path):
    rate = rates(State(path))
    print("lists: %d, estimated messages/day over all lists: %.0f" % (len(rate), sum(v for v in rate.values() if v < 1e4)))
    print("caps: %d messages, %d bodies, %d published threads per list" % (MAX_MSGS, MAX_BODY_MSGS, MAX_ENTITIES))
    for label, factor in (("low (0.5x rates)", 0.5), ("expected", 1.0), ("high (1.5x rates)", 1.5)):
        s, w, e = estimate(rate, factor)
        print("%-18s data branch %5.0f MB gz, site %5.0f MB, %6.0f published threads" % (label, s, w, e))
    s, w, e = estimate(rate, 1.0, 10**9, 10**9, 10**9)
    print("uncapped (120 days, all bodies): data branch %.0f MB gz, site %.0f MB" % (s, w))


if __name__ == "__main__":
    main(sys.argv[1])
