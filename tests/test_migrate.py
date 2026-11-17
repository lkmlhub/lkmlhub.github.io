"""State format epochs: old state in, new behaviour out."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lorehub import backfill as bf
from lorehub.backfill import Config, run_backfill
from lorehub.build import build_site
from lorehub.crawl import crawl
from lorehub.migrate import migrate
from lorehub.state import STATE_EPOCH, State

from .helpers import NOW, jload, make_state, patch_body, raw, thread_bodies, thread_detail
from .test_backfill import Clock, FakeSource

quiet = lambda m: None  # noqa: E731
DAY = 86400
FULL = "Reviewed the patch.\n\n> quoted line 1\n> quoted line 2\n> quoted line 3\n> quoted line 4\n> quoted line 5\n> quoted line 6\n> quoted line 7\n\nLooks good to me.\n"


def old_entries(prefix, n=3, t=NOW - 10 * DAY):
    out = []
    for i in range(n):
        out.append(raw("%s-p%d" % (prefix, i), "[PATCH] %s change %d" % (prefix, i), name="Dev %s" % prefix, t=t - i * DAY,
                       body=patch_body("f%d.c" % i, "%s%d" % (prefix, i), text="Commit message %d." % i)))
        out.append(raw("%s-r%d" % (prefix, i), "Re: [PATCH] %s change %d" % (prefix, i), name="Rev", t=t - i * DAY + 600,
                       parent="%s-p%d" % (prefix, i), body=FULL))
    return out


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state"
        st, tmp = make_state({n: old_entries(n[0]) for n in ("one", "two")}, now=NOW)
        self.addCleanup(tmp.cleanup)
        self.state = State(self.root)
        meta = {"lists": {}, "backfill": {"requests": 123, "days": 90, "since": NOW - 5 * DAY, "last": NOW - 3600}}
        shutil.copytree(st.root, self.root, dirs_exist_ok=True)
        for n in ("one", "two"):
            data = self.state.load_list(n)
            for rec in data["msgs"].values():
                rec.pop("bv", None)  # the old format knew no body version
                if rec["s"].startswith("Re:"):
                    rec["b"] = "Reviewed the patch.\n\n> quoted line 1\n> [...]\n> quoted line 7\n\nLooks good to me."
            self.state.save_list(n, data)
            meta["lists"][n] = {"fetched": NOW - 3600, "checked": NOW - 3600, "n": len(data["msgs"]), "rt": 5.0,
                                "bf": {"cur": NOW - 90 * DAY, "hz": NOW - 90 * DAY, "done": True, "pg": 3}}
        self.state.save_meta(meta)

    def test_old_state_is_detected_and_every_backfill_cursor_restarts(self):
        before = {n: self.state.load_list(n) for n in ("one", "two")}
        summary = migrate(self.state, NOW, quiet)
        self.assertEqual((summary["from"], summary["to"], summary["reset"]), (0, STATE_EPOCH, 2))
        meta = self.state.load_meta()
        self.assertEqual(meta["epoch"], STATE_EPOCH)
        for n in ("one", "two"):
            bfm = meta["lists"][n]["bf"]
            self.assertFalse(bfm["done"])
            self.assertNotIn("cur", bfm)  # the walk starts from now again
            self.assertEqual(meta["lists"][n]["rt"], 5.0)  # what was learned is kept
            self.assertEqual(self.state.load_list(n)["msgs"], before[n]["msgs"])  # records and diffs are untouched
        self.assertEqual(meta["backfill"]["requests"], 0)
        self.assertEqual(meta["backfill"]["rebuild"]["from"], 0)
        self.assertIsNone(migrate(self.state, NOW, quiet))  # only once

    def test_a_fresh_deployment_is_stamped_without_a_rebuild(self):
        st = State(Path(self.tmp.name) / "fresh")
        self.assertIsNone(migrate(st, NOW, quiet))
        meta = st.load_meta()
        self.assertEqual(meta["epoch"], STATE_EPOCH)
        self.assertNotIn("rebuild", meta.get("backfill", {}))

    def walk_source(self, lists=("one", "two")):
        return FakeSource({n: old_entries(n[0]) for n in lists}, page=4)

    def test_the_walk_replaces_trimmed_records_with_complete_ones_and_converges(self):
        with mock.patch.object(bf, "SEARCH_PAGE", 4):
            s1 = run_backfill(self.state, self.walk_source(("one",)), NOW, Config(days=180, max_requests=100), names=["one", "two"],
                              log=quiet, clock=Clock())
        self.assertEqual(s1["lists_done"], 2)  # "two" gets an empty answer from the fake search, which finishes its walk too
        meta = self.state.load_meta()
        self.assertEqual(meta["epoch"], STATE_EPOCH)
        self.assertTrue(meta["lists"]["one"]["bf"]["done"])
        self.assertEqual(meta["backfill"]["days"], 180)
        data = self.state.load_list("one")
        reply = data["msgs"]["o-r0"]
        self.assertEqual(reply["bv"], 3)
        self.assertIn("quoted line 4", reply["b"])  # the complete text, not the collapsed one
        self.assertNotIn("[...]", reply["b"])
        # nothing came back for "two", so its old records are still the ones we have
        self.assertNotIn("bv", self.state.load_list("two")["msgs"]["t-r0"])

    def test_a_partially_migrated_state_builds_a_valid_site_with_notes(self):
        with mock.patch.object(bf, "SEARCH_PAGE", 4):
            run_backfill(self.state, self.walk_source(("one",)), NOW, Config(days=180, max_requests=1), names=["one", "two"],
                         log=quiet, clock=Clock())
        out = Path(self.tmp.name) / "public"
        build_site(self.state, out, NOW, log=quiet)
        lists = jload(out / "data/lists.json")
        self.assertEqual(lists["backfill"]["rebuild"], 1)  # the home page says history is being rebuilt
        self.assertLess(lists["backfill"]["done"], lists["backfill"]["lists"])
        # series of both lists are shown (they have diffs), the unwalked one with older trimmed bodies flagged
        for name, expect_lb in (("one", False), ("two", True)):
            meta = jload(out / ("data/lists/%s/meta.json" % name))
            self.assertEqual(meta["counts"]["pr"]["open"] + meta["counts"]["pr"]["closed"] + meta["counts"]["pr"]["merged"], 3)
            first = next(k for k in meta["pages"] if k.startswith("pr-"))
            row = jload(out / ("data/lists/%s/%s-0.json" % (name, first)))["rows"][0]
            d = thread_detail(out, name, row["n"])
            self.assertEqual(any(c.get("lb") for c in d["cm"]), expect_lb, name)
            self.assertEqual(d["cp"], 1 if expect_lb else 0, name)  # trimmed bodies: still being completed
            self.assertTrue(all(b is not None for b in thread_bodies(out, name, row["n"])))

    def test_note_disappears_when_every_list_is_walked(self):
        with mock.patch.object(bf, "SEARCH_PAGE", 4):
            run_backfill(self.state, self.walk_source(), NOW, Config(days=180, max_requests=100), names=["one", "two"],
                         log=quiet, clock=Clock())
        out = Path(self.tmp.name) / "public"
        build_site(self.state, out, NOW, log=quiet)
        lists = jload(out / "data/lists.json")
        self.assertEqual(lists["backfill"]["done"], lists["backfill"]["lists"])
        self.assertEqual(lists["backfill"]["rebuild"], 0)

    def test_crawl_also_migrates(self):
        class Src:
            def manifest(self):
                raise OSError("offline")

            def new_atom(self, name, validators):
                raise OSError("offline")

        crawl(self.state, Src(), NOW, names=["one"], log=quiet)
        self.assertEqual(self.state.load_meta()["epoch"], STATE_EPOCH)
        self.assertFalse(self.state.load_meta()["lists"]["two"]["bf"]["done"])


if __name__ == "__main__":
    unittest.main()
