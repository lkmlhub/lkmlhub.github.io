"""How many thread requests does learning the sender addresses of displayed OPEN series take?

usage: python3 scripts/estimate_addr_queue.py STATE_DIR [--now UNIX] [--shown 49000]

Loads a state directory with the real model, counts the displayed series (a stored diff) per status and the distinct people on the
open ones. The number of requests is at most the number of displayed open series (one t.atom each) and is reduced by the global
learning: an address learned once serves every series of that person, and a series is skipped when everybody on it is known.
With --shown N (the expected number of displayed series of the whole deployment, REPORT3: 0.27 per patch message with a diff,
182 000 of them) the share of open series is scaled to it. Prints the numbers, writes nothing.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorehub.model import build_entities, compute_status, finish_entity, link_versions, load_messages  # noqa: E402
from lorehub.state import State  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state")
    ap.add_argument("--now", type=int)
    ap.add_argument("--shown", type=int, default=0, help="displayed series of the whole deployment, to scale to")
    args = ap.parse_args()
    st = State(args.state)
    names = st.list_names()
    msgs = load_messages(st, names)
    ents = build_entities(msgs)
    for e in ents:
        finish_entity(e)
    link_versions([e for e in ents if e.kind == "pr"])
    now = args.now or max(m.t for m in msgs.values()) + 3600
    for e in ents:
        compute_status(e, now)
    states, people, per_person = Counter(), set(), Counter()
    shown = 0
    for e in ents:
        if e.kind != "pr" or e.ghost or not e.patches or not any(msgs[m.mid].dz is not None for m in e.patches):
            continue
        shown += 1
        states[e.state] += 1
        if e.state == "open":
            who = {m.h for m in e.members if m.h}
            people |= who
            per_person.update(who)
    n_open = states["open"]
    print("lists %d, messages %d, displayed series %d: %s" % (len(names), len(msgs), shown, dict(states)))
    print("open series %d (%.0f%% of the displayed), distinct people on them %d (%.2f per open series)" % (
        n_open, 100.0 * n_open / max(1, shown), len(people), len(people) / max(1, n_open)))
    # greedy cover: how many series fetches are needed if every fetch teaches all its people at once and series whose people are
    # all known are skipped (the order of the queue is newest first, so this is the order used here too)
    known, fetched = set(), 0
    open_series = sorted((e for e in ents if e.kind == "pr" and e.state == "open" and not e.ghost and e.patches
                          and any(msgs[m.mid].dz is not None for m in e.patches)), key=lambda e: -e.updated)
    for e in open_series:
        who = {m.h for m in e.members if m.h}
        if who - known:
            fetched += 1
            known |= who
    print("fetches with the skip rule (everybody known): %d of %d open series (%.0f%%)" % (fetched, n_open, 100.0 * fetched / max(1, n_open)))
    if args.shown:
        frac = n_open / max(1, shown)
        print("scaled to %d displayed series: about %d open, at most %d requests, about %d with the skip rule" % (
            args.shown, args.shown * frac, args.shown * frac, args.shown * frac * fetched / max(1, n_open)))
        print("at 100 requests per run: %d runs without the skip rule, %d with it" % (
            args.shown * frac / 100, args.shown * frac * fetched / max(1, n_open) / 100))


if __name__ == "__main__":
    main()
