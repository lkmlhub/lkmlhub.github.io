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
from .helpers import FIXTURES, diff_shard, jload, thread_bodies, thread_detail

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
        shard = jload(self.out / ("data/threads/%s/%d.json.gz" % (home, row["n"] // 32)))
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

    def test_conversation_is_complete_and_equals_the_real_thread(self):
        """The reference is real-threads.json: the thread Atom of both cover letters as lore served it on 2026-11-17."""
        real = json.loads((FIXTURES / "ipe/real-threads.json").read_text())
        for key, row in (("v1", self.v1), ("v2", self.v2)):
            ref = real[key]
            home, d = self.detail(row)
            mids = ([d["cover"]["mid"]] if "cover" in d else []) + [p["mid"] for p in d["patches"]] + [c["mid"] for c in d["cm"]]
            self.assertEqual(sorted(mids), ref["mids"], key)  # every message of the thread, nothing else
            self.assertEqual(d["c"], ref["replies"], key)
            self.assertEqual(row["c"], ref["replies"], key)
            self.assertEqual(d["mc"], ref["entries"], key)
            bodies = thread_bodies(self.out, home, d["n"])
            self.assertEqual(len(bodies), ref["entries"])
            self.assertTrue(all(b is not None for b in bodies), key)  # the complete text of every message
        self.assertEqual((real["v1"]["replies"], real["v2"]["replies"]), (26, 6))
        self.assertEqual(real["v1"]["replies"] + real["v2"]["replies"], 32)  # "more than 20 replies across versions"

    def test_the_files_changed_tab_has_diffs_for_all_patches_of_both_versions(self):
        for row, patches in ((self.v1, 4), (self.v2, 3)):
            home, d = self.detail(row)
            shard = diff_shard(self.out, home, d["n"])
            self.assertEqual(len(d["patches"]), patches)
            self.assertEqual(set(shard), {p["mid"] for p in d["patches"]})
            for p in d["patches"]:
                self.assertNotIn("nd", p)
                self.assertGreaterEqual(len(shard[p["mid"]]["f"]), 1)
            self.assertEqual(d["cp"], 0, "no gap is left in the real threads")

    def test_replies_are_nested_under_the_message_they_answer(self):
        _, d = self.detail(self.v2)
        order = [d["cover"]] + d["patches"] + d["cm"]
        by_mid = {m["mid"]: i for i, m in enumerate(order)}
        replies = [c for c in d["cm"] if "pi" in c]
        self.assertEqual(len(replies), 6)
        for c in replies:
            self.assertIn("re", c)  # "in reply to <author>"
            self.assertLess(c["pi"], len(order))
        patch1 = by_mid[d["patches"][0]["mid"]]
        self.assertTrue(any(c["pi"] == patch1 for c in replies))  # replies to patch 1 hang under patch 1

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
        shard = diff_shard(self.out, home, d2["n"])
        self.assertEqual(set(shard), {p["mid"] for p in d2["patches"]})
        self.assertEqual({f["p"] for d in shard.values() for f in d["f"]}, files)
        self.assertIn("security/ipe/", " ".join(files))
        text = "".join(f.get("x", "") for d in shard.values() for f in d["f"])
        self.assertIn("ipe", text)

    def test_v1_has_its_own_diffs(self):
        _, d1 = self.detail(self.v1)
        self.assertEqual(d1["df"], 1)
        shard = diff_shard(self.out, d1["home"], d1["n"])
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
