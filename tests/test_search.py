import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lorehub import search
from lorehub.search import build_index, delta, fold, key_file, shard_key, tokenize

from . import ipe_fixture

NODE = shutil.which("node")
CLI = Path(__file__).parent / "js" / "query_cli.js"


def doc(n, title, lst="netdev", u=1000, **kw):
    d = {"n": n, "l": lst, "k": "pr", "t": title, "a": "Ann Author", "ah": "abcdef012345", "o": u - 10, "u": u,
         "lb": "open", "c": 0, "v": 1, "pt": [], "x": [], "tg": []}
    d.update(kw)
    return d


class TokenizerTests(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(tokenize("IPE: add the .ipe policy-keyring v2"),
                         ["ipe", "add", "the", "ipe", "policy", "keyring", "v2"])
        self.assertEqual(tokenize("Müller, José"), ["muller", "jose"])
        self.assertEqual(tokenize("net_next/6.12"), ["net", "next", "6", "12"])
        self.assertEqual(tokenize(""), [])
        self.assertEqual(fold("ÅngströM"), "angstrom")

    @unittest.skipUnless(NODE, "node not installed")
    def test_python_and_javascript_tokenizers_agree(self):
        samples = ["IPE: add the .ipe policy keyring", "Müller, José", "net_next/6.12", "李明 wrote", "x86/msr: Drop 32-bit",
                   "[PATCH v2 1/3] drm/amdgpu: fix  tabs\tand\nnewlines", "CAPS lock KEYS", "ǅ ﬁ ① ² ½", "ÀÉÎÕÜ àéîõü", "foo-bar_baz.qux",
                   "Ünïcödé łódź İstanbul", "", "   "]
        out = subprocess.run([NODE, str(CLI), "--tokenize"], input=json.dumps(samples), capture_output=True, text=True,
                             timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [tokenize(s) for s in samples])


class IndexTests(unittest.TestCase):
    def test_docs_are_newest_first_and_postings_decode(self):
        docs = [doc(1, "old ipe keyring", u=100), doc(2, "new ipe thing", u=300), doc(3, "mid other", u=200)]
        files = build_index(docs, ["netdev"], ["open"])
        self.assertEqual([d[0] for d in files["d/0.json"]], [2, 3, 1])
        ids, acc = [], 0
        for x in files["t/ip.json"]["ipe"]:
            acc += x
            ids.append(acc)
        self.assertEqual(ids, [0, 2])  # ids ascend with the document order
        self.assertEqual(files["index.json"]["docs"], 3)
        self.assertEqual(delta([2, 5, 6]), [2, 3, 1])

    def test_author_list_cross_post_tags_and_patch_titles_are_searchable(self):
        d = doc(1, "series title", lst="linux-mm", a="José Müller", x=["kexec"], tg=["RFC"], pt=["subsystem: first patch"])
        files = build_index([d], ["linux-mm"], ["open"])
        toks = set()
        for name, content in files.items():
            if name.startswith("t/"):
                toks.update(content)
        for want in ("series", "jose", "muller", "linux", "mm", "kexec", "rfc", "subsystem", "first", "patch"):
            self.assertIn(want, toks)

    def test_doc_shards_are_small(self):
        docs = [doc(i, "title %d words" % i, u=10000 - i) for i in range(1, 400)]
        files = build_index(docs, ["netdev"], ["open"])
        shards = [k for k in files if k.startswith("d/")]
        self.assertEqual(len(shards), 4)
        self.assertEqual(len(files["d/0.json"]), search.DOC_SHARD)

    def test_big_token_shards_are_split_by_a_third_letter(self):
        docs = [doc(i, "net%s %s" % (chr(97 + i % 26), "nex%d" % i), u=5000 - i) for i in range(1, 300)]
        with mock.patch.object(search, "MAX_TOKEN_SHARD", 400):
            files = build_index(docs, ["netdev"], ["open"])
        split = files["index.json"]["split"]
        self.assertIn("ne", split)
        for child in split["ne"]:
            self.assertIn("t/%s.json" % key_file(child), files)
        self.assertNotIn("t/ne.json", files)
        self.assertEqual(shard_key("neta", split), "net")
        self.assertEqual(shard_key("ne", split), "ne")
        for child, content in ((c, files["t/%s.json" % key_file(c)]) for c in split["ne"]):
            self.assertTrue(all(t.startswith(child) or t == "ne" for t in content))

    def test_key_file_is_safe(self):
        self.assertEqual(key_file("ip"), "ip")
        self.assertEqual(key_file("李明"), "_e69d8ee6988e")
        self.assertNotIn("/", key_file("a/"))


@unittest.skipUnless(NODE, "node not installed")
class IpeSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def query(self, text):
        r = subprocess.run([NODE, str(CLI), str(self.out), text], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def ipe_rows(self, rows):
        return [r for r in rows if r["t"] == "IPE: add a dedicated policy keyring"]

    def test_ipe_keyring_finds_both_versions(self):
        for q in ("ipe keyring", "IPE Keyring", "keyr ipe", ".ipe", "ipe"):
            rows = self.query(q)["rows"]
            self.assertEqual(sorted(r["v"] for r in self.ipe_rows(rows)), [1, 2], q)

    def test_patch_titles_and_authors_and_lists_are_searchable(self):
        self.assertTrue(self.ipe_rows(self.query("KEY_SPEC_IPE_KEYRING")["rows"]))  # a patch subject of the series
        self.assertTrue(self.ipe_rows(self.query("huber")["rows"]))  # the author
        self.assertTrue(self.ipe_rows(self.query("linux-doc")["rows"]))  # a list the series was cross-posted to
        self.assertTrue(self.ipe_rows(self.query("kexec ipe")["rows"]))

    def test_all_words_must_match(self):
        self.assertEqual(self.query("ipe zzzzqqqq")["rows"], [])
        self.assertEqual(self.query("nonexistentwordxyz")["rows"], [])
        self.assertEqual(self.query("a")["rows"], [])  # one letter words are ignored

    def test_rows_look_like_list_rows(self):
        r = self.ipe_rows(self.query("ipe keyring")["rows"])
        v2 = next(x for x in r if x["v"] == 2)
        self.assertEqual(v2["k"], "pr")
        self.assertEqual(v2["hl"], "kexec")
        self.assertEqual(v2["l"], "changes")
        self.assertEqual(v2["c"], 6)
        shard = json.loads((self.out / "data/threads/kexec/0.json").read_text())
        self.assertEqual(shard[str(v2["n"])]["t"], v2["t"])

    def test_a_query_loads_only_a_few_small_shards(self):
        r = self.query("ipe keyring")
        self.assertLessEqual(len(r["shards"]), 2)
        self.assertLess(r["shard_bytes"], 300_000)

    def test_index_size_is_reported(self):
        self.assertGreater(self.report["search_bytes"], 0)
        total = sum(p.stat().st_size for p in (self.out / "data/search").rglob("*.json"))
        self.assertEqual(total, self.report["search_bytes"])
        self.assertEqual(self.report["search_docs"], json.loads((self.out / "data/search/index.json").read_text())["docs"])


if __name__ == "__main__":
    unittest.main()
