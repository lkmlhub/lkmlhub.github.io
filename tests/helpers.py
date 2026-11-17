"""Shared helpers for building synthetic state quickly."""

import tempfile
from pathlib import Path

from lorehub.crawl import ingest
from lorehub.feed import mid_from_href, to_record
from lorehub.state import State

FIXTURES = Path(__file__).parent / "fixtures"
NOW = 1794870000  # 2026-11-17, just after the fixture feeds


def raw(mid, subject, name="Alice Author", email=None, t=NOW - 3600, parent="", body="Hello\n"):
    return {
        "mid": mid, "parent": parent, "ts": t, "name": name,
        "email": email or (name.lower().replace(" ", ".") + "@example.test"),
        "subject": subject, "body": body,
    }


def make_state(lists, now=NOW):
    """lists: {list name: [raw entries]} -> (State, tempdir object)."""
    tmp = tempfile.TemporaryDirectory()
    st = State(tmp.name)
    for name, entries in lists.items():
        _, data = ingest(st, name, entries, now, data={"v": 1, "name": name, "msgs": {}})
        st.save_list(name, data)
    return st, tmp


def load(lists, now=NOW):
    """Build entities straight from raw entries; returns (entities by title, msgs)."""
    from lorehub.model import (
        build_entities, compute_status, finish_entity, link_versions, load_messages,
    )
    from lorehub.build import assign_numbers
    st, tmp = make_state(lists, now)
    msgs = load_messages(st, sorted(lists))
    ents = build_entities(msgs)
    for e in ents:
        finish_entity(e)
    link_versions([e for e in ents if e.kind == "pr"])
    for e in ents:
        compute_status(e, now)
    assign_numbers(ents, msgs, {"keys": {}, "next": {}}, prune=False)
    tmp.cleanup()
    return ents, msgs


__all__ = ["raw", "make_state", "load", "NOW", "FIXTURES", "mid_from_href"]
