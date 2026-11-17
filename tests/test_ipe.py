"""Acceptance test on real data: the series "ipe: add the .ipe policy keyring".

RFC v1 was posted 2026-10-19 as [PATCH RFC 1/4], v2 on 2026-10-29 as [PATCH v2 1/3], with about 30
replies, on linux-security-module and cross-posted to keyrings, linux-doc, lkml and kexec. The
fixture is a trimmed copy of the real thread feeds (addresses replaced), replayed through the real
backfill (search windows, cursor, small pages) and the real build.
"""

import gzip
import json
import re
import time
import unittest

from lorehub.feed import parse_feed

from . import ipe_fixture
from .helpers import FIXTURES

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
LISTS = ["kexec", "keyrings", "linux-doc", "linux-security-module", "lkml"]


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class IpeAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()
        cls.rows = {}
        meta = json.loads((cls.out / "data/lists/linux-security-module/meta.json").read_text())
        for key, pages in meta["pages"].items():
            for i in range(pages):
                page = json.loads((cls.out / ("data/lists/linux-security-module/%s-%d.json" % (key, i))).read_text())
                for r in page["rows"]:
                    cls.rows.setdefault(r["t"], []).append(r)
        cls.v1 = next(r for r in cls.rows["IPE: add a dedicated policy keyring"] if r["v"] == 1)
        cls.v2 = next(r for r in cls.rows["IPE: add a dedicated policy keyring"] if r["v"] == 2)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def detail(self, row, list_name="linux-security-module"):
        home = row.get("hl", list_name)
        shard = json.loads((self.out / ("data/threads/%s/%d.json" % (home, row["n"] // 32))).read_text())
        return home, shard[str(row["n"])]

    def test_backfill_walked_every_list_back_through_search_windows(self):
        self.assertEqual(self.report["backfill"]["lists_done"], 5)
        self.assertEqual(self.report["backfill"]["lists_left"], 0)
        self.assertGreater(len(self.source.calls), 5)  # several pages, small ones
        names = {c[0] for c in self.source.calls}
        self.assertEqual(names, set(LISTS))
        meta = self.state.load_meta()["lists"]
        for n in LISTS:
            self.assertTrue(meta[n]["bf"]["done"], n)

    def test_both_versions_are_present_and_linked(self):
        self.assertEqual(day(self.v1["o"]), "2026-10-19")
        self.assertEqual(day(self.v2["o"]), "2026-10-29")
        self.assertEqual(self.v1["l"], "superseded")
        self.assertEqual(self.v1["sb"], 2)
        self.assertEqual(self.v2["nv"], 2)
        self.assertEqual(self.v1["nv"], 2)
        home2, d2 = self.detail(self.v2)
        home1, d1 = self.detail(self.v1)
        self.assertEqual([(v["v"], v["n"], v["home"]) for v in d2["vs"]],
                         [(1, self.v1["n"], home1), (2, self.v2["n"], home2)])
        self.assertEqual(d1["sb"]["v"], 2)
        self.assertEqual((d1["total"], d2["total"]), (4, 3))
        self.assertEqual([p["t"] for p in d2["patches"]],
                         ["ipe: add the .ipe policy keyring", "keys: add KEY_SPEC_IPE_KEYRING",
                          "Documentation: ipe: document the IPE keyring"])

    def test_replies_are_attached_and_counted(self):
        self.assertEqual(self.v1["c"], 26)
        self.assertEqual(self.v2["c"], 6)
        _, d1 = self.detail(self.v1)
        self.assertEqual(len(d1["cm"]), 26)
        self.assertTrue(any(c["a"] == "Paul Moore" for c in d1["cm"]))

    def test_cross_posts_show_all_lists_that_carry_the_series(self):
        _, d2 = self.detail(self.v2)
        self.assertEqual(d2["lists"], LISTS)
        _, d1 = self.detail(self.v1)
        self.assertEqual(d1["lists"], ["keyrings", "linux-doc", "linux-security-module", "lkml"])  # not on kexec

    def test_the_series_is_listed_on_every_carrying_list(self):
        for name in LISTS:
            meta = json.loads((self.out / ("data/lists/%s/meta.json" % name)).read_text())
            self.assertGreaterEqual(meta["counts"]["pr"]["open"] + meta["counts"]["pr"]["closed"], 1, name)

    def test_files_changed_matches_an_independent_count(self):
        entries = {e["mid"]: e for e in parse_feed((FIXTURES / "ipe/entries.atom").read_bytes())[0]}
        _, d2 = self.detail(self.v2)
        files, add, rem = set(), 0, 0
        for p in d2["patches"]:
            body = entries[p["mid"]]["body"]
            diff = body[body.index("diff --git"):]
            files.update(re.findall(r"^diff --git a/(\S+) b/", diff, re.M))
            for line in diff.split("\n"):
                if line.startswith("+") and not line.startswith("+++"):
                    add += 1
                elif line.startswith("-") and not line.startswith("---") and line != "-- ":
                    rem += 1
        self.assertGreaterEqual(len(files), 3)
        self.assertEqual((d2["fc"], d2["fa"], d2["fd"]), (len(files), add, rem))
        self.assertEqual(d2["df"], 1)
        home = d2["home"]
        shard = json.loads((self.out / ("data/diffs/%s/%d.json" % (home, d2["n"]))).read_text())["p"]
        self.assertEqual(set(shard), {p["mid"] for p in d2["patches"]})
        self.assertEqual({f["p"] for d in shard.values() for f in d["f"]}, files)
        self.assertIn("security/ipe/", " ".join(files))
        text = "".join(f.get("x", "") for d in shard.values() for f in d["f"])
        self.assertIn("ipe", text)

    def test_v1_has_its_own_diffs(self):
        _, d1 = self.detail(self.v1)
        self.assertEqual(d1["df"], 1)
        shard = json.loads((self.out / ("data/diffs/%s/%d.json" % (d1["home"], d1["n"]))).read_text())["p"]
        self.assertEqual(len(shard), 4)
        paths = {f["p"] for d in shard.values() for f in d["f"]}
        self.assertTrue(any(p.startswith("tools/testing/selftests/ipe") or "selftests" in p for p in paths))

    def test_no_email_addresses_in_state_or_site(self):
        for p in self.out.rglob("*.json"):
            self.assertFalse(EMAIL.search(p.read_text()), p)
        for p in self.state.root.rglob("*.gz"):
            self.assertFalse(EMAIL.search(gzip.decompress(p.read_bytes()).decode()), p)


if __name__ == "__main__":
    unittest.main()
