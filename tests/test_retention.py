"""Retention by whole series, the per list limit of 1000 patch messages, the state budget."""

import tempfile
import unittest
from pathlib import Path

from lorehub.crawl import ingest
from lorehub.retention import (
    Limits, apply_meta_limits, effective_retention, enforce, finalize_list, state_bytes, trim_to_budget,
)
from lorehub.state import MAX_DIFF_MSGS, MAX_MSGS, State

from .helpers import NOW, patch_body, raw
from .test_complete import series

DAY = 86400
quiet = lambda m: None  # noqa: E731


def many(n, patches=3, replies=2, first=NOW - 3600, step=3 * 3600):
    """n series of `patches` patches (author per series, newest first in time order i=0) with replies each."""
    out = []
    for i in range(n):
        out += series("s%d" % i, patches, replies=[(1, "Reply %d.\n> quoted\n" % k) for k in range(replies)],
                      t=first - i * step, name="Dev%d" % i, title="sub%d: change" % i)
    return out


class WholeSeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(self.tmp.name)

    def ingest(self, entries, limit=MAX_DIFF_MSGS, now=NOW, name="l", **kw):
        data = self.state.load_list(name) if self.state.segments(name) else {"v": 2, "name": name, "msgs": {}}
        _, data = ingest(self.state, name, entries, now, data=data, **kw)
        return finalize_list(self.state, name, data, now, Limits(max_diff_msgs=limit)), self.state.load_list(name)

    def stored_diffs(self, name="l"):
        return {m for d in self.state.diff_days(name) for m in self.state.load_diff_day(name, d)}

    def test_newest_series_first_up_to_the_patch_limit_and_never_partial(self):
        entries = many(5)  # 5 series x (cover + 3 patches + 2 replies)
        s, data = self.ingest(entries, limit=7)  # 3 patches each: two series fit (6), the third would make 9
        self.assertEqual((s["series_kept"], s["series_dropped"], s["patches_kept"]), (2, 3, 6))
        kept = {"s0", "s1"}
        want = {e["mid"] for e in entries if e["mid"].split("-")[0] in kept}
        self.assertEqual(set(data["msgs"]), want)  # every message of a kept series, no message of the others
        self.assertEqual(self.stored_diffs(), {"s%d-%d" % (i, k) for i in (0, 1) for k in (1, 2, 3)})
        for rec in data["msgs"].values():
            self.assertEqual(rec.get("bv"), 3)  # complete bodies for the whole series, covers and replies included

    def test_a_series_longer_than_the_limit_is_still_kept_when_it_is_the_newest(self):
        s, data = self.ingest(many(2, patches=5), limit=3)
        self.assertEqual((s["series_kept"], s["patches_kept"]), (1, 5))
        self.assertEqual(len(self.stored_diffs()), 5)

    def test_replies_older_than_the_body_horizon_and_beyond_300_messages_keep_their_bodies(self):
        old = NOW - 150 * DAY
        entries = series("big", 3, t=old, replies=[(1, "Reply %d. %s\n" % (k, "x" * 50)) for k in range(400)])
        _, data = self.ingest(entries, now=NOW)
        self.assertEqual(sum(1 for r in data["msgs"].values() if r.get("b")), 404)  # nothing is dropped by age or count

    def test_a_series_is_alive_while_any_message_is_inside_the_retention(self):
        old = NOW - 190 * DAY
        entries = series("a", 2, t=old, replies=[(1, "late reply\n")])
        entries[-1]["ts"] = NOW - DAY  # a reply a day ago to a series that began 190 days ago
        s, data = self.ingest(entries, now=NOW, retention_days=None)  # as a fetched thread brings it
        self.assertEqual(s["series_kept"], 1)
        self.assertEqual(len(data["msgs"]), 4)  # the old part is kept with it
        dead = series("z", 2, t=NOW - 200 * DAY)
        s, data = self.ingest(dead, now=NOW, retention_days=None)
        self.assertNotIn("z-1", data["msgs"])  # nothing of it is inside the retention

    def test_series_without_any_diff_is_not_kept_but_a_cover_alone_waits_for_its_patches(self):
        nodiff = [raw("n-1", "[PATCH 1/2] x: y", body="no diff here\n", t=NOW - 100), raw("n-r", "Re: [PATCH 1/2] x: y", parent="n-1", t=NOW - 50)]
        cover = [raw("c-0", "[PATCH 0/2] c: only a cover", name="Cov", t=NOW - 60, body="cover\n")]
        _, data = self.ingest(nodiff + cover)
        self.assertNotIn("n-1", data["msgs"])
        self.assertNotIn("n-r", data["msgs"])
        self.assertIn("c-0", data["msgs"])

    def test_other_messages_have_their_own_count_cap_and_bodies_only_for_the_newest(self):
        talk = [raw("d%d" % i, "discussion %d" % i, t=NOW - i * 60, body="text\n") for i in range(3200)]
        data = {"msgs": {}}
        from lorehub.state import merge_records
        from lorehub.feed import to_record
        merge_records(data, {e["mid"]: to_record(e) for e in talk}, NOW)
        enforce(data, "l", NOW)
        self.assertEqual(len(data["msgs"]), MAX_MSGS)
        self.assertEqual(sum(1 for r in data["msgs"].values() if "b" in r), 300)

    def test_defaults(self):
        self.assertEqual(MAX_DIFF_MSGS, 1000)
        self.assertEqual(effective_retention(0), 180)
        self.assertEqual(effective_retention(400), 400)

    def test_diff_day_files_of_dropped_series_are_deleted(self):
        self.ingest(many(4, patches=2, step=2 * DAY), limit=100)
        self.assertEqual(len(self.stored_diffs()), 8)
        days_before = self.state.diff_days("l")
        self.ingest(many(4, patches=2, step=2 * DAY)[:6], limit=2)  # a later pass with a smaller limit drops the older ones
        self.assertEqual(len(self.stored_diffs()), 2)
        self.assertLess(len(self.state.diff_days("l")), len(days_before))

    def test_entities_after_retention_have_no_partial_series(self):
        _, data = self.ingest(many(6), limit=10)
        from lorehub.retention import series_of
        series_list, _ = series_of(data, "l")
        for e in series_list:
            self.assertEqual(len(e.patches), 3)
            self.assertIsNotNone(e.cover)


class BudgetTests(unittest.TestCase):
    def test_state_over_budget_lowers_the_per_list_limit_and_remembers_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = State(tmp.name)
        entries = []
        for i in range(80):
            entries += series("s%d" % i, 2, t=NOW - 600 * i, name="D%d" % i, title="sub%d: change" % i,
                              replies=[(1, "reply %s\n" % ("lorem ipsum %d " % i) * 20)])
        for e in entries:
            if e["subject"].startswith("[PATCH 1/") or e["subject"].startswith("[PATCH 2/"):
                e["body"] = patch_body("f.c", e["mid"], text="Commit %s" % e["mid"]).replace("+new line", "+" + "".join(chr(97 + (hash((e["mid"], j)) % 26)) for j in range(300)) + "\n+new line")
        _, data = ingest(st, "l", entries, NOW, data={"v": 2, "name": "l", "msgs": {}})
        finalize_list(st, "l", data, NOW)
        size = state_bytes(st)
        self.assertGreater(size, 5000)
        self.assertIsNone(trim_to_budget(st, NOW, size + 1, Limits(), quiet))  # inside the budget: nothing happens
        cap = trim_to_budget(st, NOW, size // 2, Limits(), quiet)
        self.assertIsNotNone(cap)
        self.assertLessEqual(state_bytes(st), size // 2)
        self.assertEqual(st.load_meta()["limits"]["max_diff_msgs"], cap)
        lim = apply_meta_limits(st, Limits())
        self.assertEqual(lim.max_diff_msgs, cap)
        data = st.load_list("l")
        kept_series = {mid.split("-")[0] for mid in data["msgs"]}
        for s in kept_series:  # whole series only
            self.assertTrue({s + "-0", s + "-1", s + "-2"} <= set(data["msgs"]))
        self.assertIn("s0", kept_series)  # the newest stay


if __name__ == "__main__":
    unittest.main()
