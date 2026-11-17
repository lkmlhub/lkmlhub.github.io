import unittest

from lorehub.diff import (
    MAX_LINE_CHARS,
    build_result,
    extract_diff,
    find_diff_start,
    kept_bytes,
    parse_diff,
    stat_of,
)
from lorehub.feed import to_record
from lorehub.parse import parse_subject

from .helpers import raw

COMMIT = "Fix the thing.\n\nSigned-off-by: Alice Author <alice@example.test>\n---\n"
SIG = "-- \n2.43.0\n\n"


def patch(diff, head=COMMIT, sig=SIG):
    return head + " drivers/x.c | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n\n" + diff + sig


MODIFY = (
    "diff --git a/drivers/x.c b/drivers/x.c\nindex 111..222 100644\n--- a/drivers/x.c\n+++ b/drivers/x.c\n"
    "@@ -10,3 +10,3 @@ static int foo(void)\n context\n-old line\n+new line\n context2\n"
)


class ExtractTests(unittest.TestCase):
    def test_modify_counts_and_text(self):
        r = extract_diff(patch(MODIFY))
        self.assertEqual(stat_of(r), [1, 1, 1])
        f = r["f"][0]
        self.assertEqual(f["p"], "drivers/x.c")
        self.assertNotIn("k", f)
        self.assertEqual(f["x"].split("\n")[0], "@@ -10,3 +10,3 @@ static int foo(void)")
        self.assertIn("-old line\n+new line", f["x"])
        self.assertNotIn("2.43.0", f["x"])  # the mail signature is not part of the diff
        self.assertNotIn("tr", r)

    def test_new_deleted_rename_copy_and_mode(self):
        text = patch(
            "diff --git a/new.c b/new.c\nnew file mode 100644\nindex 0000000..1111111\n--- /dev/null\n+++ b/new.c\n"
            "@@ -0,0 +1,2 @@\n+one\n+two\n"
            "diff --git a/gone.c b/gone.c\ndeleted file mode 100644\nindex 1111111..0000000\n--- a/gone.c\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-one\n-two\n"
            "diff --git a/old/name.c b/new/name.c\nsimilarity index 100%\nrename from old/name.c\nrename to new/name.c\n"
            "diff --git a/a.c b/b.c\nsimilarity index 90%\nrename from a.c\nrename to b.c\nindex 1..2 100644\n"
            "--- a/a.c\n+++ b/b.c\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/s.sh b/s.sh\nold mode 100644\nnew mode 100755\n"
            "diff --git a/c.c b/c2.c\nsimilarity index 80%\ncopy from c.c\ncopy to c2.c\n"
        )
        files = {f["p"]: f for f in extract_diff(text)["f"]}
        self.assertEqual(files["new.c"]["k"], "a")
        self.assertEqual((files["new.c"]["a"], files["new.c"]["d"]), (2, 0))
        self.assertEqual(files["gone.c"]["k"], "d")
        self.assertEqual((files["gone.c"]["a"], files["gone.c"]["d"]), (0, 2))
        self.assertEqual(files["new/name.c"]["k"], "r")
        self.assertEqual(files["new/name.c"]["o"], "old/name.c")
        self.assertEqual(files["new/name.c"]["s"], 100)
        self.assertNotIn("x", files["new/name.c"])
        self.assertEqual(files["b.c"]["o"], "a.c")
        self.assertEqual(files["b.c"]["s"], 90)
        self.assertEqual((files["b.c"]["a"], files["b.c"]["d"]), (1, 1))
        self.assertEqual(files["s.sh"]["m"], ["100644", "100755"])
        self.assertEqual(files["c2.c"]["k"], "c")
        self.assertEqual(files["c2.c"]["o"], "c.c")

    def test_binary_files(self):
        text = patch(
            "diff --git a/img.png b/img.png\nindex 1..2 100644\nBinary files a/img.png and b/img.png differ\n"
            "diff --git a/blob.bin b/blob.bin\nnew file mode 100644\nindex 0000000..1234567\nGIT binary patch\n"
            "literal 12\nTcmZ?b@Mq4hlcuY=)\n\nliteral 0\nHcmV?d00001\n\n"
            "diff --git a/after.c b/after.c\n--- a/after.c\n+++ b/after.c\n@@ -1 +1 @@\n-a\n+b\n"
        )
        r = extract_diff(text)
        names = [f["p"] for f in r["f"]]
        self.assertEqual(names, ["img.png", "blob.bin", "after.c"])
        self.assertEqual(r["f"][0]["b"], 1)
        self.assertEqual(r["f"][1]["b"], 1)
        self.assertEqual(r["f"][1]["k"], "a")
        self.assertNotIn("b", r["f"][2])
        self.assertEqual(stat_of(r), [3, 1, 1])  # binary payload lines are never counted
        self.assertNotIn("Tcm", str(r))

    def test_no_newline_marker_is_kept_and_not_counted(self):
        text = patch(
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n+c\n"
            "\\ No newline at end of file\n"
        )
        r = extract_diff(text)
        self.assertEqual(stat_of(r), [1, 1, 1])
        lines = r["f"][0]["x"].split("\n")
        self.assertEqual(lines.count("\\ No newline at end of file"), 2)

    def test_zero_context_hunks(self):
        text = patch(
            "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -5 +5 @@\n-x\n+y\n@@ -9,0 +10,2 @@\n+p\n+q\n@@ -20,2 +21,0 @@\n-r\n-s\n"
        )
        r = extract_diff(text)
        self.assertEqual(stat_of(r), [1, 3, 3])
        self.assertEqual(sum(1 for ln in r["f"][0]["x"].split("\n") if ln.startswith("@@")), 3)

    def test_empty_context_line_without_its_space(self):
        text = patch("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n")
        r = extract_diff(text)
        self.assertEqual(stat_of(r), [1, 1, 1])
        self.assertEqual(r["f"][0]["x"].split("\n")[2], " ")

    def test_plain_unified_diff_without_git_header(self):
        text = "Subject text\n---\n--- a/old.txt\n+++ b/old.txt\n@@ -1 +1 @@\n-a\n+b\n--- a/two.txt\n+++ b/two.txt\n@@ -1 +1,2 @@\n x\n+y\n"
        r = extract_diff(text)
        self.assertEqual([f["p"] for f in r["f"]], ["old.txt", "two.txt"])
        self.assertEqual(stat_of(r), [2, 2, 1])

    def test_paths_with_spaces(self):
        text = patch("diff --git a/dir with space/f x.c b/dir with space/f x.c\n--- a/dir with space/f x.c\n"
                     "+++ b/dir with space/f x.c\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual(extract_diff(text)["f"][0]["p"], "dir with space/f x.c")

    def test_combined_diff_is_ignored_gracefully(self):
        text = patch(
            "diff --cc merged.c\nindex 1,2..3\n--- a/merged.c\n+++ b/merged.c\n@@@ -1,2 -1,2 +1,3 @@@\n  a\n- b\n +c\n++d\n"
            "diff --git a/ok.c b/ok.c\n--- a/ok.c\n+++ b/ok.c\n@@ -1 +1 @@\n-1\n+2\n"
        )
        r = extract_diff(text)
        self.assertEqual([f["p"] for f in r["f"]], ["merged.c", "ok.c"])
        self.assertEqual(r["f"][0]["cb"], 1)
        self.assertNotIn("x", r["f"][0])
        self.assertEqual((r["f"][0]["a"], r["f"][0]["d"]), (0, 0))
        self.assertEqual((r["f"][1]["a"], r["f"][1]["d"]), (1, 1))

    def test_wrong_counts_and_mangled_hunks_do_not_crash(self):
        text = patch("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,50 +1,50 @@\n a\n-b\n+c\n-- \n2.43.0\n\n")
        r = extract_diff(text, )
        self.assertEqual(stat_of(r), [1, 1, 1])
        self.assertNotIn("2.43.0", r["f"][0]["x"])
        wrapped = patch("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@ int foo(struct bar\n*x)\n  a\n")
        self.assertEqual(len(extract_diff(wrapped)["f"]), 1)

    def test_quoted_diff_is_not_a_patch(self):
        reply = "On Monday Bob wrote:\n> diff --git a/f b/f\n> --- a/f\n> +++ b/f\n> @@ -1 +1 @@\n> -a\n> +b\n\nLooks wrong.\n"
        self.assertIsNone(extract_diff(reply))
        self.assertIsNone(find_diff_start(reply.split("\n")))

    def test_sample_diff_inside_the_commit_message_is_not_the_patch(self):
        text = (
            "Fix it.\n\nBefore:\ndiff --git a/sample b/sample\n\nThe real change follows.\n---\n"
            " f | 1 +\n\n" + MODIFY
        )
        r = extract_diff(text)
        self.assertEqual([f["p"] for f in r["f"]], ["drivers/x.c"])

    def test_cover_letter_and_empty(self):
        self.assertIsNone(extract_diff("Cover letter text\n\n drivers/x.c | 2 +-\n 1 file changed\n"))
        self.assertIsNone(extract_diff(""))

    def test_parse_diff_handles_empty_input(self):
        self.assertEqual(parse_diff([]), [])
        self.assertEqual(parse_diff(["no diff here"]), [])


class CapAndScrubTests(unittest.TestCase):
    def big(self, nfiles, nlines, width=40):
        out = []
        for i in range(nfiles):
            out.append("diff --git a/f%d.c b/f%d.c\n--- a/f%d.c\n+++ b/f%d.c\n@@ -1,0 +1,%d @@" % (i, i, i, i, nlines))
            out.extend("+" + ("x" * width) for _ in range(nlines))
        return "---\n" + "\n".join(out) + "\n"

    def test_cap_truncates_hunk_text_but_keeps_stats_and_flags(self):
        r = extract_diff(self.big(4, 100, 100), cap=15000)
        self.assertEqual(r["tr"], 1)
        self.assertEqual(len(r["f"]), 4)  # every file is listed
        self.assertEqual(stat_of(r), [4, 400, 0])  # counts cover the whole diff
        self.assertLessEqual(kept_bytes(r), 15000)
        self.assertEqual(r["f"][0].get("x") is not None, True)
        self.assertEqual(r["f"][-1].get("t"), 1)
        self.assertNotIn("+xxx", r["f"][-1].get("x", ""))
        self.assertGreater(r["sz"], 40000)

    def test_under_the_cap_nothing_is_flagged(self):
        r = extract_diff(self.big(2, 10), cap=15000)
        self.assertNotIn("tr", r)
        self.assertTrue(all("t" not in f for f in r["f"]))

    def test_default_cap_is_120kb(self):
        r = extract_diff(self.big(1, 3000, 100))
        self.assertEqual(r["tr"], 1)
        self.assertLessEqual(kept_bytes(r), 120 * 1024)
        self.assertGreater(kept_bytes(r), 100 * 1024)

    def test_giant_line_is_cut(self):
        text = "---\ndiff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+" + "y" * 100000 + "\n"
        r = extract_diff(text)
        added = [ln for ln in r["f"][0]["x"].split("\n") if ln.startswith("+")][0]
        self.assertLessEqual(len(added), MAX_LINE_CHARS + 20)
        self.assertTrue(added.endswith("[line cut]"))
        self.assertEqual(stat_of(r), [1, 1, 1])

    def test_control_characters_and_binary_junk_are_removed(self):
        text = "---\ndiff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\x00\x01\x1b[31mred\x7f\u2028end\n"
        x = extract_diff(text)["f"][0]["x"]
        for bad in ("\x00", "\x01", "\x1b", "\x7f", "\u2028"):
            self.assertNotIn(bad, x)
        self.assertIn("[31mred", x)

    def test_emails_in_diff_lines_commit_message_and_paths_are_replaced(self):
        text = (
            "---\ndiff --git a/MAINTAINERS b/MAINTAINERS\n--- a/MAINTAINERS\n+++ b/MAINTAINERS\n@@ -1,2 +1,3 @@\n"
            " M:\tOld Person <old@example.test>\n+M:\tNew Person <new.person@corp.example.org>\n"
            "+R:\tplain@example.test\n+ Signed-off-by: A B <ab@example.test>\n"
            "diff --git a/dir/user@example.test/f b/dir/user@example.test/f\n--- a/dir/user@example.test/f\n"
            "+++ b/dir/user@example.test/f\n@@ -1 +1 @@\n-a\n+b\n"
        )
        r = extract_diff(text)
        blob = str(r)
        self.assertNotIn("@example", blob)
        self.assertNotIn("@corp", blob)
        self.assertIn("[email]", blob)
        self.assertIn("New Person", blob)
        self.assertEqual(stat_of(r), [2, 4, 1])

    def test_build_result_flags_when_patch_has_no_text(self):
        r = build_result(parse_diff(["diff --git a/f b/f", "Binary files a/f and b/f differ"]))
        self.assertEqual(r["f"], [{"p": "f", "b": 1, "a": 0, "d": 0}])


class RecordTests(unittest.TestCase):
    def test_patch_message_gets_diff_and_ds(self):
        diffs = {}
        rec = to_record(raw("m1", "[PATCH 1/2] fix", body=patch(MODIFY)), diffs=diffs)
        self.assertEqual(rec["ds"], [1, 1, 1])
        self.assertIn("m1", diffs)
        self.assertNotIn("diff --git", rec["b"])

    def test_reply_with_quoted_or_inline_diff_gets_no_diff(self):
        diffs = {}
        body = "> " + MODIFY.replace("\n", "\n> ") + "\nBut why?\n\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        rec = to_record(raw("r1", "Re: [PATCH 1/2] fix", body=body), diffs=diffs)
        self.assertEqual(diffs, {})
        self.assertNotIn("ds", rec)

    def test_cover_letter_and_pull_request_have_no_diff(self):
        diffs = {}
        to_record(raw("c0", "[PATCH 0/2] cover", body="Cover\n\n a | 1 +\n 1 file changed, 1 insertion(+)\n"), diffs=diffs)
        to_record(raw("p0", "[GIT PULL] stuff", body="please pull\n\n" + MODIFY), diffs=diffs)
        self.assertEqual(diffs, {})

    def test_subject_helper_still_agrees(self):
        self.assertTrue(parse_subject("[PATCH v2 3/7] x").is_patch_message)


if __name__ == "__main__":
    unittest.main()
