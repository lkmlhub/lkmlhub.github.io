"""Acceptance fixture: a real series ("ipe: add the .ipe policy keyring", RFC v1 2026-10-19, v2 2026-10-29)
carried by five lists, replayed through the real backfill and build code.

usage: python3 -m tests.ipe_fixture OUT_DIR     builds the site into OUT_DIR (used for screenshots)
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from lorehub import backfill as bf
from lorehub.backfill import Config, run_backfill
from lorehub.build import build_site
from lorehub.crawl import _Empty
from lorehub.feed import parse_feed
from lorehub.state import State

from .helpers import FIXTURES, NOW, atom

IPE = FIXTURES / "ipe"
PAGE = 10


class Resp:
    def __init__(self, body):
        self.status, self.body, self.headers = 200, body, {}


class IpeSource:
    """Serves the fixture like the search service: newest first, (lo, hi] windows, small pages."""

    def __init__(self):
        entries = {e["mid"]: e for e in parse_feed((IPE / "entries.atom").read_bytes())[0]}
        member = json.loads((IPE / "membership.json").read_text())
        self.lists = {name: [entries[m] for m in mids] for name, mids in member.items()}
        self.calls = []

    def manifest(self):
        raise OSError("not used")

    def search_window(self, name, lo, hi, seq):
        self.calls.append((name, lo, hi, seq))
        hits = sorted((e for e in self.lists.get(name, []) if lo < e["ts"] <= hi), key=lambda e: -e["ts"])
        return Resp(atom(hits[:PAGE], name)) if hits else _Empty()


class Clock:
    def __call__(self):
        return 0.0


def build(out=None, now=NOW, days=40):
    """Backfill all five lists from the fixture and build the site. Returns (state, out dir, source, report)."""
    tmp = tempfile.TemporaryDirectory()
    state = State(Path(tmp.name) / "state")
    source = IpeSource()
    with mock.patch.object(bf, "SEARCH_PAGE", PAGE):
        summary = run_backfill(state, source, now, Config(days=days, max_requests=500, max_total=500),
                               names=sorted(source.lists), log=lambda m: None, clock=Clock())
    out = Path(out) if out else Path(tmp.name) / "public"
    report = build_site(state, out, now, log=lambda m: None)
    return tmp, state, out, source, dict(report, backfill=summary)


if __name__ == "__main__":
    tmp, state, out, source, report = build(sys.argv[1])
    print("built", out, "requests", len(source.calls), "bytes", report["bytes"])
