import gzip
import json
import re
import tempfile
import unittest
from pathlib import Path

from lorehub.build import MAX_SITE_MB, build_site
from lorehub.crawl import ingest
from lorehub.diff import extract_diff
from lorehub.diffshards import cap_series, est_size, pick
from lorehub.feed import to_record
from lorehub.retention import Limits, finalize_list
from lorehub.state import MAX_DIFF_MSGS, State, day_of, merge_records, stash_diffs

from .helpers import NOW, is_address_file, jload, make_state, raw, thread_detail

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
quiet = lambda m: None  # noqa: E731
DAY = 86400


def patch_body(path="drivers/x.c", extra=""):
    return (
        "Commit text.\n\nSigned-off-by: Alice Author <alice@example.test>\n---\n 1 file changed, 1 insertion(+)\n\n"
        "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1,2 +1,3 @@\n a\n+new line %s\n b\n-- \n2.43.0\n" % (path, path, path, path, extra)
    )


def entries(n, base=NOW - 5 * DAY, step=3600):
    return [raw("p%d" % i, "[PATCH] change %d" % i, t=base + i * step, body=patch_body(extra=str(i))) for i in range(n)]


class StoreDiffsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ingest(self, ents, keep=None):
        raws = [e for e in ents]
        diffs = {}
        data = {"v": 2, "name": "l", "msgs": {}}
        if self.state.segments("l"):
            data = self.state.load_list("l")
        records = {e["mid"]: to_record(e, True, diffs) for e in raws}
        merge_records(data, records, NOW)
        stash_diffs(data, diffs)
        finalize_list(self.state, "l", data, NOW, Limits(max_diff_msgs=keep or MAX_DIFF_MSGS))
        return data

    def test_dz_marks_stored_diffs_and_files_are_per_list_and_day(self):
        data = self.ingest(entries(5))
        self.assertTrue(all("dz" in r and "ds" in r for r in data["msgs"].values()))
        days = self.state.diff_days("l")
        self.assertEqual(days, sorted({day_of(r["t"]) for r in data["msgs"].values()}))
        d = self.state.load_diff_day("l", days[0])
        self.assertTrue(all(v["f"][0]["p"] == "drivers/x.c" for v in d.values()))
        raw_bytes = gzip.decompress((self.state.diff_dir("l") / (days[0] + ".json.gz")).read_bytes())
        self.assertIn(b"new line", raw_bytes)

    def test_only_the_newest_keep_diffs_survive(self):
        data = self.ingest(entries(10), keep=4)
        with_dz = sorted(m for m, r in data["msgs"].items() if "dz" in r)
        self.assertEqual(with_dz, ["p6", "p7", "p8", "p9"])
        stored = {}
        for day in self.state.diff_days("l"):
            stored.update(self.state.load_diff_day("l", day))
        self.assertEqual(sorted(stored), with_dz)
        # a series is kept whole or dropped whole: the six older series are gone with their messages
        self.assertEqual(sorted(data["msgs"]), ["p6", "p7", "p8", "p9"])

    def test_new_batch_evicts_older_diffs_and_deletes_empty_day_files(self):
        self.ingest(entries(4, base=NOW - 60 * DAY, step=DAY), keep=4)
        before = self.state.diff_days("l")
        self.assertEqual(len(before), 4)
        newer = [raw("n%d" % i, "[PATCH] newer %d" % i, t=NOW - 100 * (i + 1), body=patch_body(extra="n")) for i in range(4)]
        data = self.ingest(newer, keep=4)
        self.assertEqual(sorted(m for m, r in data["msgs"].items() if "dz" in r), ["n0", "n1", "n2", "n3"])
        self.assertEqual(self.state.diff_days("l"), [day_of(NOW - 100)])

    def test_unchanged_reingest_rewrites_no_files(self):
        self.ingest(entries(6))
        files = {p: p.read_bytes() for p in self.state.root.rglob("*.gz")}
        mt = {p: p.stat().st_mtime_ns for p in files}
        self.ingest(entries(6))
        self.assertEqual({p: p.read_bytes() for p in self.state.root.rglob("*.gz")}, files)
        self.assertEqual({p: p.stat().st_mtime_ns for p in files}, mt)

    def test_default_keep_is_1000(self):
        self.assertEqual(MAX_DIFF_MSGS, 1000)

    def test_replies_and_cover_letters_store_no_diffs(self):
        ents = [raw("c", "[PATCH 0/1] cover", body="cover\n"),
                raw("r", "Re: [PATCH 1/1] x", body="> diff --git a/x b/x\n> +y\nNo\n")]
        data = self.ingest(ents)
        self.assertFalse(any("dz" in r for r in data["msgs"].values()))
        self.assertEqual(self.state.diff_days("l"), [])
        self.assertEqual(sorted(data["msgs"]), ["c", "r"])  # no patch with a diff yet: nothing is dropped

    def test_ingest_helper_roundtrip(self):
        st, tmp = make_state({})
        try:
            from lorehub.feed import parse_feed  # noqa: F401
            added, data = ingest(st, "l", [dict(e, mid=e["mid"]) for e in entries(3)], NOW)
            finalize_list(st, "l", data, NOW)
            self.assertEqual(added, 3)
            self.assertEqual(len(st.diff_days("l")) >= 1, True)
        finally:
            tmp.cleanup()


class PickTests(unittest.TestCase):
    def test_everything_fits(self):
        c = [("a", "open", 1, 10, 1), ("b", "merged", 2, 10, 2)]
        self.assertEqual(pick(c, 100), {1, 2})

    def test_open_first_then_newest_inside_a_list(self):
        c = [("a", "merged", 9, 10, "m"), ("a", "open", 1, 10, "o-old"), ("a", "open", 5, 10, "o-new"),
             ("a", "closed", 20, 10, "c")]
        self.assertEqual(pick(c, 20), {"o-new", "o-old"})
        self.assertEqual(pick(c, 30), {"o-new", "o-old", "m"})

    def test_busy_lists_cannot_starve_quiet_lists(self):
        busy = [("busy", "open", 1000 - i, 100, ("b", i)) for i in range(100)]
        quiet_ = [("quiet", "open", i, 100, ("q", i)) for i in range(3)]
        chosen = pick(busy + quiet_, 2000)
        self.assertEqual({k for k in chosen if k[0] == "q"}, {("q", 0), ("q", 1), ("q", 2)})
        self.assertLessEqual(sum(100 for _ in chosen), 2000)
        self.assertEqual(len(chosen), 20)
        self.assertIn(("b", 0), chosen)  # newest of the busy list wins

    def test_budget_is_never_exceeded_and_is_well_used(self):
        import random
        rnd = random.Random(3)
        c = [(("l%d" % rnd.randint(0, 9)), rnd.choice(["open", "merged", "closed"]), rnd.randint(1, 1000),
              rnd.randint(50, 500), i) for i in range(300)]
        size = {x[4]: x[3] for x in c}
        for budget in (500, 5000, 20000):
            chosen = pick(c, budget)
            used = sum(size[k] for k in chosen)
            self.assertLessEqual(used, budget)
            self.assertGreater(used, budget * 0.8)

    def test_zero_budget(self):
        self.assertEqual(pick([("a", "open", 1, 10, 1)], 0), set())

    def test_est_size(self):
        self.assertGreater(est_size(1000, 2), 1000)


class CapSeriesTests(unittest.TestCase):
    def test_later_patches_lose_text_and_are_flagged(self):
        def d(n):
            return {"f": [{"p": "f", "a": 1, "d": 0, "x": "+" + "y" * n}], "sz": n}
        out = cap_series([d(60), None, d(60), d(10)], cap=100)
        self.assertIn("x", out[0]["f"][0])
        self.assertIsNone(out[1])
        self.assertNotIn("x", out[2]["f"][0])
        self.assertEqual(out[2]["f"][0]["t"], 1)
        self.assertEqual(out[2]["tr"], 1)
        self.assertIn("x", out[3]["f"][0])
        self.assertNotIn("tr", out[0])


class BuildDiffTests(unittest.TestCase):
    def build(self, ents, **kw):
        st, tmp = make_state({"lkml": ents})
        self.addCleanup(tmp.cleanup)
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        report = build_site(st, Path(out.name) / "public", NOW, log=quiet, **kw)
        return st, Path(out.name) / "public", report

    def series(self):
        return [
            raw("s0", "[PATCH 0/2] cover", t=NOW - 7200, body="Cover\n"),
            raw("s1", "[PATCH 1/2] first", t=NOW - 7100, parent="s0", body=patch_body("a.c", "1")),
            raw("s2", "[PATCH 2/2] second", t=NOW - 7000, parent="s0", body=patch_body("b.c", "2")),
            raw("s3", "Re: [PATCH 1/2] first", t=NOW - 3000, parent="s1", name="Bob", body="Looks good\n"),
        ]

    def test_diff_shard_and_thread_summary(self):
        st, out, report = self.build(self.series())
        shards = list((out / "data/diffs/lkml").glob("*.json.gz"))
        self.assertEqual(len(shards), 1)
        diff = jload(shards[0])["p"]
        self.assertEqual(sorted(diff), ["s1", "s2"])
        self.assertEqual(diff["s1"]["f"][0]["p"], "a.c")
        detail = thread_detail(out, "lkml", 1)
        self.assertEqual((detail["fc"], detail["fa"], detail["fd"], detail["df"]), (2, 2, 0, 1))
        self.assertEqual(detail["patches"][0]["ds"], [1, 1, 0])
        self.assertEqual(report["diff_series"], 1)

    def test_diffs_never_appear_in_list_or_thread_json(self):
        st, out, _ = self.build(self.series())
        for p in list((out / "data/lists").rglob("*.json")) + list((out / "data/threads").rglob("*.json.gz")) + \
                list((out / "data/authors").glob("*.json")) + [out / "data/lists.json", out / "data/recent.json"]:
            t = json.dumps(jload(p))
            self.assertNotIn("new line", t, p)
            self.assertNotIn("@@ -1,2", t, p)

    def test_a_series_that_does_not_fit_is_not_shown_at_all(self):
        st, out, report = self.build(self.series(), diff_budget=0)
        self.assertFalse((out / "data/diffs").exists())
        self.assertFalse((out / "data/threads").exists())
        self.assertFalse(list((out / "data/lists/lkml").glob("pr-*.json")))
        self.assertEqual(report["diff_evicted"], 1)
        self.assertEqual(report["threads"], 0)

    def test_site_cap_lowers_the_diff_budget(self):
        ents = []
        for s in range(6):
            body = ("Big.\n---\ndiff --git a/f%d.c b/f%d.c\n--- a/f%d.c\n+++ b/f%d.c\n@@ -0,0 +1,400 @@\n" % (s, s, s, s)
                    + "".join("+%s line %d\n" % ("z" * 70, i) for i in range(400)))
            ents.append(raw("x%d" % s, "[PATCH] big %d" % s, name="A%d" % s, t=NOW - 100 * s, body=body))
        st, out, report = self.build(ents, diff_budget=10**9)
        out = out
        full = report["diff_bytes"]
        self.assertGreater(full, 6000)
        st2, out2, report2 = self.build(ents, diff_budget=report['est_heavy'] // 2)
        self.assertLess(report2["diff_bytes"], full)
        self.assertGreater(report2["diff_series"], 0)
        self.assertEqual(report2["diff_series"] + report2["diff_evicted"], 6)
        rows = [r for p in (out2 / "data/lists/lkml").glob("pr-*.json") for r in jload(p)["rows"]]
        self.assertEqual(len(rows), report2["diff_series"])  # whole series only: every shown row has its shard

    def test_privacy_diff_emails_are_gone_everywhere(self):
        body = patch_body("MAINTAINERS", "x") + ""
        body = body.replace("+new line x", "+M:\tNew Person <new.person@corp.example.org>\n+R: plain@example.test")
        body = body.replace("@@ -1,2 +1,3 @@", "@@ -1,2 +1,4 @@")
        st, out, _ = self.build([raw("m1", "[PATCH] maintainers", body=body)])
        files = [p for p in list(out.rglob("*.json")) + list(out.rglob("*.gz")) + list(st.root.rglob("*.gz"))
                 if not is_address_file(p)]
        found = False
        for p in files:
            t = gzip.decompress(p.read_bytes()).decode() if p.suffix == ".gz" else p.read_text()
            self.assertFalse(EMAIL.search(t), p)
            found = found or "[email]" in t
        self.assertTrue(found)

    def test_default_site_cap_is_700_mb(self):
        self.assertEqual(MAX_SITE_MB, 700)

    def test_extract_matches_state_roundtrip(self):
        d = extract_diff(patch_body("q.c", "1"))
        st, tmp = make_state({"lkml": [raw("q", "[PATCH] q", body=patch_body("q.c", "1"))]})
        self.addCleanup(tmp.cleanup)
        day = st.diff_days("lkml")[0]
        self.assertEqual(st.load_diff_day("lkml", day)["q"], d)


if __name__ == "__main__":
    unittest.main()
