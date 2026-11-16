import gzip
import json
import re
import unittest

from lorehub.feed import mid_from_href, parse_feed, parse_time, to_record
from lorehub.manifest import parse_manifest

from .helpers import FIXTURES

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.lists = parse_manifest((FIXTURES / "manifest.js.gz").read_bytes())

    def test_sample(self):
        self.assertGreater(len(self.lists), 300)
        for name in ("lkml", "netdev", "linux-mm", "bpf", "linux-fsdevel", "dri-devel", "linux-riscv", "stable"):
            self.assertIn(name, self.lists)
        lk = self.lists["lkml"]
        self.assertEqual(lk.description, "The Linux Kernel Mailing List")
        self.assertEqual(lk.epochs, list(range(21)))
        self.assertGreater(lk.modified, 1_700_000_000)

    def test_modified_is_max_over_epochs_and_description_from_newest(self):
        data = {
            "/x/git/0.git": {"modified": 5, "description": "Old [epoch 0]"},
            "/x/git/1.git": {"modified": 3, "description": "New name [epoch 1]"},
            "/weird/path": {"modified": 9},
        }
        got = parse_manifest(json.dumps(data))
        self.assertEqual(list(got), ["x"])
        self.assertEqual(got["x"].modified, 5)
        self.assertEqual(got["x"].description, "New name")

    def test_accepts_gzip_dict_and_rejects_garbage(self):
        data = {"/a/git/0.git": {"modified": 1, "description": "A"}}
        self.assertIn("a", parse_manifest(gzip.compress(json.dumps(data).encode())))
        self.assertIn("a", parse_manifest(data))
        with self.assertRaises(ValueError):
            parse_manifest("[1, 2]")
        with self.assertRaises(ValueError):
            parse_manifest("not json")


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.data = (FIXTURES / "lkml.atom").read_bytes()
        self.entries, self.bad, self.updated = parse_feed(self.data)

    def test_entries(self):
        self.assertEqual(len(self.entries), 25)
        self.assertEqual(self.bad, 0)
        self.assertEqual(self.updated, parse_time("2026-11-16T22:12:01Z"))
        e = self.entries[0]
        self.assertTrue(e["parent"])
        self.assertTrue(e["body"].startswith("Hi Angel"))
        self.assertEqual(sum(1 for x in self.entries if x["parent"]), 24)

    def test_message_ids_are_encoded(self):
        self.assertEqual(mid_from_href("https://lore.kernel.org/lkml/a%2Fb@x.test/"), "a%2Fb%40x.test")
        self.assertEqual(mid_from_href("https://lore.kernel.org/lkml/a@x.test/"), "a%40x.test")
        self.assertEqual(mid_from_href(""), "")
        for e in self.entries:
            self.assertNotIn("@", e["mid"])
            self.assertNotIn("@", e["parent"])

    def test_record_has_no_email(self):
        for e in self.entries:
            rec = to_record(e)
            blob = json.dumps(rec)
            self.assertFalse(EMAIL.search(blob), blob[:200])
            self.assertNotIn(e["email"], blob)
            self.assertEqual(len(rec["h"]), 12)

    def test_one_broken_entry_does_not_kill_the_feed(self):
        text = self.data.decode()
        broken = text.replace("<entry>", "<entry><broken", 1)
        entries, bad, _ = parse_feed(broken)
        self.assertEqual(bad, 1)
        self.assertEqual(len(entries), 24)

    def test_control_characters_are_tolerated(self):
        text = self.data.decode().replace("Hi Angel", "Hi\x0b Angel", 1)
        entries, bad, _ = parse_feed(text)
        self.assertEqual((len(entries), bad), (25, 0))

    def test_search_feed_has_200_style_pages(self):
        entries, bad, _ = parse_feed((FIXTURES / "netdev.atom").read_bytes())
        self.assertEqual(len(entries), 40)
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
