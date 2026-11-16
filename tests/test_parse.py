import unittest

from lorehub.parse import (
    classify, clean_body, extract_trailers, looks_applied, norm_title, parse_subject,
    person_hash, scrub_emails, split_diff,
)


class SubjectTests(unittest.TestCase):
    def check(self, raw, **want):
        s = parse_subject(raw)
        for k, v in want.items():
            self.assertEqual(getattr(s, k), v, "%s: %s" % (raw, k))
        return s

    def test_series_positions(self):
        self.check("[PATCH v3 2/7] foo: bar", kind="patch", version=3, index=2, total=7, title="foo: bar")
        self.check("[PATCH net-next v2 0/5] x", version=2, index=0, total=5, trees=["net-next"])
        self.check("[PATCH bpf-next v4 03/12] bpf: y", version=4, index=3, total=12, trees=["bpf-next"])
        self.check("[PATCH,v2,1/2] y", version=2, index=1, total=2)

    def test_tags(self):
        self.check("[RFC PATCH] hi", kind="patch", rfc=True)
        self.check("[PATCH RFC] hi", kind="patch", rfc=True)
        self.check("[RESEND PATCH] a", kind="patch", resend=True, version=1)
        self.check("[PATCH RESEND v2] a", resend=True, version=2)
        self.check("[PATCH]\n  [v2] fix", version=2, title="fix")
        self.check("[PATCHv2] x", kind="patch", version=2)

    def test_non_patch_kinds(self):
        self.check("[GIT PULL] updates for v6.12", kind="pull", title="updates for v6.12")
        self.check("[PULL] x", kind="pull")
        self.check("[ANNOUNCE] foo 1.0", kind="announce")
        self.check("[BUG] oops", kind="bug")
        self.check("[REGRESSION] r", kind="regression")
        self.check("[RFC] design", kind="rfc", rfc=True)
        self.check("just a question", kind="other", title="just a question")

    def test_replies(self):
        s = self.check("Re: [PATCH v2] x", is_reply=True, kind="patch", version=2)
        self.assertFalse(s.is_patch_message)
        self.assertTrue(parse_subject("[PATCH] x").is_patch_message)
        self.check("Re: Re: [PATCH] x", is_reply=True)
        self.assertTrue(parse_subject("[PATCH 0/3] c").is_cover)

    def test_norm_title(self):
        self.assertEqual(norm_title("Re: [PATCH v2] Foo: Bar-baz!"), "foo bar baz")
        self.assertEqual(norm_title("foo:  bar baz"), norm_title("Foo: bar-baz"))


class TrailerTests(unittest.TestCase):
    def test_extract(self):
        text = (
            "Looks good.\n\nReviewed-by: Jane Doe <jane@example.test>\n"
            "> Acked-by: Quoted Person <q@example.test>\n"
            "Tested-by: Bob <bob@example.test>\n  Nacked-by: Nina <nina@example.test>\n"
            "Reported-and-tested-by: Rita <rita@example.test>\n"
        )
        got = extract_trailers(text)
        kinds = [t[0] for t in got]
        self.assertEqual(kinds, ["reviewed", "tested", "nacked", "reported", "tested"])
        self.assertNotIn("Quoted Person", [t[1] for t in got])
        self.assertEqual(got[0][1], "Jane Doe")
        self.assertEqual(got[0][2], person_hash("jane@example.test"))
        self.assertFalse(any("@" in str(x) for t in got for x in t))

    def test_hash_is_stable_and_case_insensitive(self):
        self.assertEqual(person_hash("A@B.test"), person_hash("a@b.test "))
        self.assertEqual(len(person_hash("x")), 12)
        self.assertNotIn("a@b.test", person_hash("a@b.test"))


class HeuristicTests(unittest.TestCase):
    def test_applied(self):
        yes = ["Applied, thanks.", "Hi,\nThanks, applied to the net tree.", "Queued for 6.13.",
               "I've applied this to my tree.", "Applied it to #ksmbd-for-next.\nThanks!",
               "pr-tracker-bot: The pull request you sent has been merged into torvalds/linux.git"]
        no = ["Should this be applied to stable?", "This does not apply cleanly on top of -next.",
              "Please resend, it fails to apply.", "If this is applied, the build breaks.",
              "> Applied, thanks\nLooks wrong to me.", "I will test it first."]
        for t in yes:
            self.assertTrue(looks_applied(t), t)
        for t in no:
            self.assertFalse(looks_applied(t), t)

    def test_applied_only_in_first_lines(self):
        body = "\n".join(["line %d of discussion" % i for i in range(12)]) + "\nApplied.\n"
        self.assertFalse(looks_applied(body))

    def test_flags(self):
        self.assertIn("N", classify("NAK, this is wrong.\n"))
        self.assertIn("A", classify("Applied, thanks.\n"))
        inline = "> +  foo = bar;\nWhy not use baz here?\n"
        self.assertIn("I", classify(inline))
        self.assertNotIn("I", classify("Thanks for the patch, looks fine to me overall.\n> quoted\n"))
        self.assertEqual(classify("Just chatting about lunch plans today.\n"), "")


class BodyTests(unittest.TestCase):
    DIFF = (
        "Fix the thing.\n\nSigned-off-by: A <a@example.test>\n---\n"
        " drivers/x.c | 4 ++--\n 1 file changed, 2 insertions(+), 2 deletions(-)\n\n"
        "diff --git a/drivers/x.c b/drivers/x.c\n--- a/drivers/x.c\n+++ b/drivers/x.c\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )

    def test_split_diff(self):
        text, stat = split_diff(self.DIFF)
        self.assertEqual(stat, (1, 2, 2))
        self.assertNotIn("diff --git", text)
        self.assertNotIn("drivers/x.c", text)
        self.assertIn("Fix the thing.", text)

    def test_clean_body_scrubs_and_caps(self):
        body = "Hi\n> From: Someone <someone@example.test>\n" + "word " * 5000
        out = clean_body(body, max_bytes=500)
        self.assertNotIn("someone@example.test", out)
        self.assertLessEqual(len(out.encode()), 500 + 20)
        self.assertTrue(out.endswith("[truncated]"))

    def test_collapse_long_quotes(self):
        body = "\n".join("> line %d" % i for i in range(30)) + "\nmy comment here ok\n"
        out = clean_body(body)
        self.assertIn("> [...]", out)
        self.assertIn("my comment here ok", out)
        self.assertLess(out.count("\n"), 12)

    def test_scrub_emails(self):
        self.assertEqual(scrub_emails("Jane <jane@example.test> wrote"), "Jane wrote")
        self.assertIn("[email]", scrub_emails("mail bob@example.test now"))
        url = scrub_emails("see https://lore.kernel.org/r/abc-1@example.test/ ok")
        self.assertNotIn("@", url)
        self.assertIn("abc-1%40example.test", url)


if __name__ == "__main__":
    unittest.main()
