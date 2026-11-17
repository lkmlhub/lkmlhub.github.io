"""D3: only series with a stored diff are shown, everywhere, and the Files changed tab can always be built."""

import tempfile
import unittest
from pathlib import Path

from lorehub.build import build_site

from . import invariants, ipe_fixture
from .helpers import NOW, diff_shard, jload, make_state, patch_body, raw, thread_detail
from .test_complete import series

quiet = lambda m: None  # noqa: E731
DAY = 86400


def noise(seed, lines):
    """Poorly compressible diff lines (random letters), so that sizes in the tests are real."""
    import random
    rnd = random.Random(seed)
    return "\n+".join("".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(60)) for _ in range(lines))


def mixed():
    ents = []
    ents += series("good", 3, replies=[(1, "Reviewed-by: Rev One <one@example.test>\n")], name="Gina", title="good: has diffs", t=NOW - 3600)
    # a series whose patches carry no diff at all (a mail client dropped it): must be hidden everywhere
    nodiff = [raw("nd-0", "[PATCH 0/2] hidden: no diff series", name="Hank", t=NOW - 7000, body="cover\n"),
              raw("nd-1", "[PATCH 1/2] hidden: no diff series part 1", name="Hank", parent="nd-0", t=NOW - 6999, body="no diff\n"),
              raw("nd-2", "[PATCH 2/2] hidden: no diff series part 2", name="Hank", parent="nd-0", t=NOW - 6998, body="no diff\n"),
              raw("nd-r", "Re: [PATCH 1/2] hidden: no diff series part 1", name="Hidden Reviewer", parent="nd-1", t=NOW - 6000,
                  body="Reviewed-by: Hidden Reviewer <hidden@example.test>\n")]
    ents += nodiff
    # only some patches have a diff: shown, with the notice
    part = series("part", 4, name="Pia", title="part: some diffs", t=NOW - 5000)
    for e in part:
        if e["mid"] in ("part-3", "part-4"):
            e["body"] = "no diff in this mail\n"
    ents += part
    ents += [raw("pull", "[GIT PULL] driver updates", name="Greg", t=NOW - 4000, body="please pull\n"),
             raw("pull-r", "Re: [GIT PULL] driver updates", name="Linus", parent="pull", t=NOW - 3900, body="Pulled, thanks.\n"),
             raw("talk", "a plain discussion", name="Tom", t=NOW - 2000, body="hello\n")]
    return ents


class InvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        st, cls.tmp2 = make_state({"lkml": mixed()})
        cls.out = Path(cls.tmp.name) / "public"
        cls.report = build_site(st, cls.out, NOW, log=quiet)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.tmp2.cleanup()

    def test_every_invariant_holds_on_a_mixed_state(self):
        prs = invariants.check(self, self.out)
        self.assertEqual(prs, 2)

    def test_series_without_a_stored_diff_is_nowhere(self):
        meta, rows = invariants.rows_of(self.out, "lkml")
        self.assertEqual(sorted(r["t"] for r in rows if r["k"] == "pr"), ["good: has diffs", "part: some diffs"])
        blob = "".join(p.read_text() for p in (self.out / "data").rglob("*.json"))
        self.assertNotIn("hidden: no diff series", blob)
        self.assertNotIn("Hidden Reviewer", blob)
        self.assertNotIn("Hank", blob)
        authors = "".join(jload(p).__repr__() for p in (self.out / "data/authors").glob("*.json"))
        self.assertNotIn("Hank", authors)
        lists = jload(self.out / "data/lists.json")
        self.assertEqual(lists["totals"]["threads"], 4)  # 2 series, the pull request and the discussion
        self.assertEqual(meta["counts"]["pr"]["open"] + meta["counts"]["pr"]["merged"] + meta["counts"]["pr"]["closed"], 2)

    def test_partly_stored_diffs_show_the_notice_data(self):
        _, rows = invariants.rows_of(self.out, "lkml")
        row = next(r for r in rows if r["t"] == "part: some diffs")
        d = thread_detail(self.out, "lkml", row["n"])
        self.assertEqual([p["i"] for p in d["patches"] if p.get("nd")], [3, 4])  # "diffs for patches 3-4 are not stored"
        self.assertEqual(set(diff_shard(self.out, "lkml", row["n"])), {p["mid"] for p in d["patches"] if not p.get("nd")})
        self.assertEqual(d["df"], 1)

    def test_a_git_pull_request_is_a_discussion(self):
        _, rows = invariants.rows_of(self.out, "lkml")
        pull = next(r for r in rows if r["t"] == "driver updates")
        self.assertEqual((pull["k"], pull.get("pu"), pull["l"]), ("issue", 1, "merged"))

    def test_empty_state_and_only_hidden_series_build_an_empty_site(self):
        st, tmp = make_state({"lkml": [e for e in mixed() if e["mid"].startswith("nd")]})
        self.addCleanup(tmp.cleanup)
        out = Path(self.tmp.name) / "empty"
        report = build_site(st, out, NOW, log=quiet)
        self.assertEqual((report["series"], report["threads"]), (0, 0))
        self.assertEqual(invariants.check(self, out), 0)


class NewestDisplayedVersionTests(unittest.TestCase):
    def test_older_version_with_a_diff_is_shown_when_the_newer_has_none(self):
        v1 = series("v1", 2, name="Dev", title="feat: thing", t=NOW - 5 * DAY)
        v2 = [raw("v2-0", "[PATCH v2 0/2] feat: thing", name="Dev", t=NOW - DAY, body="cover\n"),
              raw("v2-1", "[PATCH v2 1/2] feat: thing part 1", name="Dev", parent="v2-0", t=NOW - DAY + 1, body="no diff\n"),
              raw("v2-2", "[PATCH v2 2/2] feat: thing part 2", name="Dev", parent="v2-0", t=NOW - DAY + 2, body="no diff\n")]
        st, tmp = make_state({"lkml": v1 + v2})
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, log=quiet)
        invariants.check(self, out)
        _, rows = invariants.rows_of(out, "lkml")
        self.assertEqual([r["v"] for r in rows], [1])
        d = thread_detail(out, "lkml", rows[0]["n"])
        self.assertNotIn("vs", d)  # only displayed versions are listed
        self.assertNotIn("sb", d)
        self.assertEqual(d["sbh"], 2)  # superseded by v2, which is not shown

    def test_both_versions_with_diffs_are_linked(self):
        v1 = series("v1", 2, name="Dev", title="feat: thing", t=NOW - 5 * DAY)
        v2 = series("v2", 2, name="Dev", title="feat: thing", t=NOW - DAY, ver=" v2")
        st, tmp = make_state({"lkml": v1 + v2})
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, log=quiet)
        invariants.check(self, out)
        _, rows = invariants.rows_of(out, "lkml")
        self.assertEqual(sorted(r["v"] for r in rows), [1, 2])


class EvictionTests(unittest.TestCase):
    def test_leaving_series_out_for_size_removes_them_whole_oldest_closed_first(self):
        ents = []
        for i in range(12):
            for e in series("s%d" % i, 2, name="Dev%d" % i, title="t%d: change" % i, t=NOW - i * 6 * 3600 - 3600,
                            replies=[(1, ("reply text %d " % i) * 200 + "\n")]):
                if e["mid"].endswith(("-1", "-2")) and "-r" not in e["mid"]:
                    e["body"] = patch_body("f.c", e["mid"]).replace("+new line", noise(e["mid"], 40))
                ents.append(e)
        # series 3 is merged, series 11 (the oldest) and 5 are superseded by nothing: make 3 merged to test the order
        ents.append(raw("m", "Re: [PATCH 1/2] t3: change part 1", name="Maint", parent="s3-1", t=NOW - 100, body="Applied, thanks.\n"))
        st, tmp = make_state({"lkml": ents})
        self.addCleanup(tmp.cleanup)
        full_out = Path(tmp.name) / "full"
        full = build_site(st, full_out, NOW, log=quiet)
        self.assertEqual(full["series"], 12)
        small = Path(tmp.name) / "small"
        rep = build_site(st, small, NOW, log=quiet, diff_budget=int(full["est_heavy"] * 0.5))
        self.assertTrue(0 < rep["series"] < 12)
        invariants.check(self, small)
        _, rows = invariants.rows_of(small, "lkml")
        titles = {r["t"] for r in rows if r["k"] == "pr"}
        self.assertIn("t0: change", titles)  # the newest stay
        self.assertNotIn("t11: change", titles)  # the oldest go first
        self.assertEqual(rep["series"] + rep["series_left_out"], 12)
        for r in rows:  # whole series: every shown series still has every patch and all replies
            d = thread_detail(small, "lkml", r["n"])
            self.assertEqual((len(d["patches"]), d["c"]), (2, 1) if r["t"] != "t3: change" else (2, 2))
            self.assertFalse(any(p.get("nd") for p in d["patches"]))

    def test_the_site_cap_is_enforced_with_real_sizes(self):
        ents = []
        for i in range(20):
            for e in series("s%d" % i, 2, name="Dev%d" % i, title="t%d: change" % i, t=NOW - i * 3600 - 600):
                if "-r" not in e["mid"] and e["mid"][-1] in "12":
                    e["body"] = patch_body("f.c", e["mid"]).replace("+new line", noise(e["mid"], 300))
                ents.append(e)
        st, tmp = make_state({"lkml": ents})
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "public"
        big = build_site(st, out, NOW, log=quiet)
        from lorehub.build import site_bytes
        cap_mb = (site_bytes() + (big["bytes"] - site_bytes()) * 0.6) / 1e6
        rep = build_site(st, Path(tmp.name) / "capped", NOW, log=quiet, max_site_mb=cap_mb)
        self.assertLessEqual(rep["bytes"], cap_mb * 1e6)
        self.assertFalse(rep["over_cap"])
        self.assertLess(rep["series"], big["series"])
        invariants.check(self, Path(tmp.name) / "capped")


class IpeInvariantTests(unittest.TestCase):
    def test_the_real_series_site_satisfies_every_invariant(self):
        tmp, state, out, source, report = ipe_fixture.build()
        self.addCleanup(tmp.cleanup)
        self.assertGreaterEqual(invariants.check(self, out), 2)


if __name__ == "__main__":
    unittest.main()
