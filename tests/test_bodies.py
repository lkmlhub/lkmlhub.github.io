"""Complete message bodies: stored whole (quotes, trailers), capped at 128 KB with a flag, no e-mail addresses,
the thread tree in the detail JSON, texts in the bodies file."""

import re
import tempfile
import unittest
from pathlib import Path

from lorehub.build import build_site
from lorehub.crawl import ingest
from lorehub.feed import to_record
from lorehub.parse import MAX_BODY_BYTES
from lorehub.retention import finalize_list
from lorehub.state import State

from .helpers import NOW, make_state, patch_body, raw, site_texts, thread_bodies, thread_detail

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
quiet = lambda m: None  # noqa: E731

REPLY = ("On Tue, Jane Roe <jane.roe@example.test> wrote:\n> The commit message is wrong, see bob@corp.example.org.\n"
         + "".join("> quoted line %d\n" % i for i in range(1, 80)) + "\nI disagree, please fix it.\n\nAcked-by: Max Mustermann <max@example.test>\n"
         "Reviewed-by: Eve Reviewer <eve@example.test>\n-- \nMax\n")


def entries():
    return [
        raw("c", "[PATCH 0/2] foo: add bar", name="Author", t=NOW - 5000, body="Cover letter text.\n\nSee https://lore.kernel.org/r/x%40example.test/\n"),
        raw("p1", "[PATCH 1/2] foo: add bar part 1", name="Author", parent="c", t=NOW - 4900, body=patch_body("a.c", "1", text="Commit message one.")),
        raw("p2", "[PATCH 2/2] foo: add bar part 2", name="Author", parent="c", t=NOW - 4800, body=patch_body("b.c", "2", text="Commit message two.")),
        raw("r1", "Re: [PATCH 1/2] foo: add bar part 1", name="Max", parent="p1", t=NOW - 3000, body=REPLY),
        raw("r2", "Re: [PATCH 1/2] foo: add bar part 1", name="Author", parent="r1", t=NOW - 2000, body="> I disagree, please fix it.\nOk, will do.\n"),
        raw("r3", "Re: [PATCH 0/2] foo: add bar", name="Eve", parent="c", t=NOW - 1000, body="Nice cover letter.\n"),
    ]


class BodyStorageTests(unittest.TestCase):
    def test_record_keeps_everything_but_addresses_and_the_diff(self):
        rec = to_record(entries()[3])
        self.assertEqual(rec["bv"], 3)
        self.assertEqual(rec["b"].count("> quoted line"), 79)  # all quoted lines, nothing collapsed
        self.assertNotIn("[...]", rec["b"])
        self.assertIn("Acked-by: Max Mustermann", rec["b"])
        self.assertIn("Reviewed-by: Eve Reviewer", rec["b"])
        self.assertTrue(rec["b"].endswith("Max"))  # the signature too
        self.assertFalse(EMAIL.search(rec["b"]))
        self.assertIn("[email]", rec["b"])  # the bare address in quoted text
        self.assertEqual([t[0] for t in rec["tr"]], ["acked", "reviewed"])
        patch = to_record(entries()[1])
        self.assertIn("Commit message one.", patch["b"])
        self.assertNotIn("diff --git", patch["b"])

    def test_per_message_cap_is_128_kb_and_is_flagged(self):
        self.assertEqual(MAX_BODY_BYTES, 128 * 1024)
        big = raw("big", "Re: [PATCH] x", body="".join("> line %05d of a very long quoted mail\n" % i for i in range(6000)) + "my answer\n")
        rec = to_record(big)
        self.assertLessEqual(len(rec["b"].encode()), MAX_BODY_BYTES)
        self.assertEqual(rec["bt"], 1)
        small = to_record(raw("s", "Re: [PATCH] x", body="short\n"))
        self.assertNotIn("bt", small)

    def test_site_has_every_text_in_order_and_the_thread_tree(self):
        st, tmp = make_state({"l": entries()})
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, log=quiet)
        d = thread_detail(out, "l", 1)
        bodies = thread_bodies(out, "l", 1)
        order = [d["cover"]] + d["patches"] + d["cm"]
        self.assertEqual(len(bodies), len(order))
        self.assertEqual(d["mc"], 6)
        self.assertEqual(d["c"], 3)
        self.assertTrue(bodies[0].startswith("Cover letter text."))
        self.assertTrue(bodies[1].startswith("Commit message one."))
        self.assertTrue(bodies[2].startswith("Commit message two."))
        idx = {m["mid"]: i for i, m in enumerate(order)}
        r1, r2, r3 = (next(c for c in d["cm"] if c["mid"] == m) for m in ("r1", "r2", "r3"))
        self.assertEqual((r1["pi"], r1["re"]), (idx["p1"], "Author"))  # a reply to patch 1
        self.assertEqual((r2["pi"], r2["re"]), (idx["r1"], "Max"))  # a reply to a reply
        self.assertEqual((r3["pi"], r3["re"]), (idx["c"], "Author"))  # a reply to the cover letter
        full = bodies[idx["r1"]]
        self.assertEqual(full.count("> quoted line"), 79)
        self.assertIn("Acked-by: Max Mustermann", full)
        self.assertEqual(sorted(x[0] for x in r1["tr"]), ["acked", "reviewed"])
        for p, t in site_texts(out):
            self.assertFalse(EMAIL.search(t), p)

    def test_truncation_flag_reaches_the_detail(self):
        ents = entries()
        ents[3]["body"] = "".join("> line %05d of a very long quoted mail\n" % i for i in range(6000)) + "my answer\n"
        st, tmp = make_state({"l": ents})
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, log=quiet)
        d = thread_detail(out, "l", 1)
        r1 = next(c for c in d["cm"] if c["mid"] == "r1")
        self.assertEqual(r1["bt"], 1)

    def test_a_message_without_a_stored_body_is_null_in_the_bodies_file(self):
        st, tmp = make_state({"l": entries()})
        self.addCleanup(tmp.cleanup)
        data = st.load_list("l")
        for k in ("b", "bv"):
            data["msgs"]["r3"].pop(k)
        st.save_list("l", data)
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, log=quiet)
        d = thread_detail(out, "l", 1)
        bodies = thread_bodies(out, "l", 1)
        order = [d["cover"]] + d["patches"] + d["cm"]
        self.assertIsNone(bodies[[m["mid"] for m in order].index("r3")])
        self.assertEqual(sum(1 for b in bodies if b is None), 1)

    def test_state_and_diffs_hold_no_addresses_after_ingest(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = State(tmp.name)
        _, data = ingest(st, "l", entries(), NOW, data={"v": 2, "name": "l", "msgs": {}})
        finalize_list(st, "l", data, NOW)
        import gzip
        for p in st.root.rglob("*.gz"):
            self.assertFalse(EMAIL.search(gzip.decompress(p.read_bytes()).decode()), p)


if __name__ == "__main__":
    unittest.main()
