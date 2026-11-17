import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lorehub.__main__ import main
from lorehub.state import State

from .helpers import FIXTURES


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.cache = base / "cache"
        self.cache.mkdir()
        shutil.copy(FIXTURES / "manifest.js.gz", self.cache / "manifest.js.gz")
        shutil.copy(FIXTURES / "lkml.atom", self.cache / "lkml.atom")
        shutil.copy(FIXTURES / "lkml.atom", self.cache / "lkml.win-0.atom")  # first backfill page of lkml
        self.state, self.out = base / "state", base / "public"

    def run_cli(self, *extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["all", "--state", str(self.state), "--out", str(self.out), "--lists", "lkml",
                         "--from-cache", str(self.cache)] + list(extra))
        self.assertEqual(code, 0)
        return buf.getvalue()

    def test_all_runs_crawl_backfill_and_build(self):
        log = self.run_cli("--backfill-days", "30")
        self.assertIn("backfill summary", log)
        meta = State(self.state).load_meta()
        bf = meta["lists"]["lkml"]["bf"]
        self.assertTrue(bf["done"])
        self.assertEqual(meta["backfill"]["days"], 30)
        lists = json.loads((self.out / "data/lists.json").read_text())
        self.assertEqual(lists["backfill"]["days"], 30)
        self.assertEqual(lists["backfill"]["done"], 1)
        lk = next(l for l in lists["lists"] if l["n"] == "lkml")
        self.assertEqual(lk["hc"], 1)
        self.assertGreater(lk["hs"], 0)
        meta_l = json.loads((self.out / "data/lists/lkml/meta.json").read_text())
        self.assertEqual(meta_l["hc"], 1)
        # a second run has nothing left to ask
        log = self.run_cli("--backfill-days", "30")
        self.assertIn("already reach back", log)

    def test_defaults_are_180_days_60_pages_and_300_thread_requests(self):
        import argparse
        from lorehub import __main__ as m
        ap = argparse.ArgumentParser()
        m.add_crawl_args(ap)
        a = ap.parse_args([])
        self.assertEqual((a.backfill_days, a.backfill_list_pages, a.complete_requests), (180, 60, 300))
        self.assertEqual(a.max_diff_msgs, 1000)
        self.assertLess(a.max_state_mb, 700)
        lim = m.limits_of(a)
        self.assertEqual((lim.retention_days, lim.max_diff_msgs), (180, 1000))
        a = ap.parse_args(["--backfill-days", "365"])
        self.assertEqual(m.limits_of(a).retention_days, 365)  # retention follows the horizon
        self.assertEqual(m.backfill_config(a).days, 365)

    def test_workflow_defaults_match(self):
        wf = (Path(__file__).resolve().parent.parent / ".github/workflows/update.yml").read_text()
        self.assertIn("|| '180' }}", wf)
        self.assertNotIn("'90'", wf)
        self.assertIn("timeout-minutes: 60", wf)
        self.assertIn("LOREHUB_COMPLETE_REQUESTS", wf)

    def test_complete_command_runs_offline(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["complete", "--state", str(self.state), "--from-cache", str(self.cache)])
        self.assertEqual(code, 0)
        self.assertIn("completion:", buf.getvalue())

    def test_backfill_can_be_turned_off(self):
        log = self.run_cli("--backfill-days", "0")
        self.assertNotIn("backfill summary", log)
        self.assertNotIn("bf", State(self.state).load_meta()["lists"]["lkml"])
        lists = json.loads((self.out / "data/lists.json").read_text())
        self.assertEqual(lists["backfill"]["days"], 0)
        self.assertNotIn("hc", next(l for l in lists["lists"] if l["n"] == "lkml"))

    def test_the_crawl_has_priority_over_the_backfill(self):
        log = self.run_cli("--backfill-days", "30", "--budget-min", "1", "--backfill-min", "5")
        self.assertIn("backfill summary", log)  # about 60 s are left of the run
        log = self.run_cli("--backfill-days", "30", "--budget-min", "0", "--backfill-min", "5")
        self.assertIn("backfill skipped", log)


if __name__ == "__main__":
    unittest.main()
