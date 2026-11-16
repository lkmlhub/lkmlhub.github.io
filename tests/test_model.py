import unittest

from lorehub.model import STALE_DAYS

from .helpers import NOW, load, raw

H = 3600
D = 86400


def by_title(ents, title, **kw):
    out = [e for e in ents if e.title == title and all(getattr(e, k) == v for k, v in kw.items())]
    assert out, "no entity %r %r in %r" % (title, kw, [(e.title, e.version) for e in ents])
    return out[0]


def thread(prefix, subject, author="Alice Author", t=NOW - 2 * H, n=3, tags="", total=None):
    """A cover letter plus n patches, all replying to the cover (b4 style)."""
    total = total or n
    entries = [raw(prefix + "-0", "[PATCH%s 0/%d] %s" % (tags, total, subject), author, t=t)]
    for i in range(1, n + 1):
        entries.append(raw("%s-%d" % (prefix, i), "[PATCH%s %d/%d] %s part %d" % (tags, i, total, subject, i),
                           author, t=t + i, parent=prefix + "-0"))
    return entries


class ThreadingTests(unittest.TestCase):
    def test_cover_and_patches_make_one_pull_request(self):
        ents, _ = load({"netdev": thread("a", "net: add widget", tags=" net-next v2")})
        prs = [e for e in ents if e.kind == "pr"]
        self.assertEqual(len(prs), 1)
        e = prs[0]
        self.assertEqual((e.title, e.version, e.total, len(e.patches)), ("net: add widget", 2, 3, 3))
        self.assertIsNotNone(e.cover)
        self.assertEqual([m.subj.index for m in e.patches], [1, 2, 3])
        self.assertIn("net-next", e.tags)

    def test_lone_patch_is_a_one_patch_pr(self):
        ents, _ = load({"bpf": [raw("p", "[PATCH] bpf: fix x")]})
        e = ents[0]
        self.assertEqual((e.kind, len(e.patches), e.cover), ("pr", 1, None))
        self.assertEqual(e.label, "open")

    def test_grandchild_replies_attach_to_the_series(self):
        entries = thread("a", "foo: bar", n=1) + [
            raw("r1", "Re: [PATCH 1/1] foo: bar part 1", "Bob", t=NOW - H, parent="a-1"),
            raw("r2", "Re: [PATCH 1/1] foo: bar part 1", "Carol", t=NOW - H + 5, parent="r1"),
        ]
        ents, _ = load({"lkml": entries})
        e = [x for x in ents if x.kind == "pr"][0]
        self.assertEqual(len(e.comments), 2)
        self.assertEqual(len(ents), 1)

    def test_plain_thread_is_a_discussion(self):
        ents, _ = load({"lkml": [raw("q", "Question about locking"),
                                raw("q2", "Re: Question about locking", "Bob", parent="q", t=NOW - H)]})
        e = ents[0]
        self.assertEqual((e.kind, e.label, len(e.comments), e.partial), ("issue", "open", 1, False))

    def test_bug_and_announce_tags(self):
        ents, _ = load({"lkml": [raw("b", "[BUG] crash in foo"), raw("a", "[ANNOUNCE] bar 1.0")]})
        self.assertEqual({tuple(e.tags) for e in ents}, {("BUG",), ("ANNOUNCE",)})
        self.assertTrue(all(e.kind == "issue" for e in ents))

    def test_reply_to_unknown_parent_is_a_partial_ghost(self):
        ents, _ = load({"lkml": [raw("r", "Re: [PATCH v3 3/4] usb: fix", "Bob", parent="gone")]})
        e = ents[0]
        self.assertEqual((e.kind, e.ghost, e.partial, e.version), ("pr", True, True, 3))
        d = load({"lkml": [raw("r", "Re: something", "Bob", parent="gone")]})[0][0]
        self.assertEqual((d.kind, d.partial), ("issue", True))

    def test_unthreaded_series_is_joined(self):
        entries = [raw("s%d" % i, "[PATCH %d/3] x: step %d" % (i, i), t=NOW - 3000 + i) for i in (1, 2, 3)]
        ents, _ = load({"lkml": entries})
        self.assertEqual(len(ents), 1)
        self.assertEqual(len(ents[0].patches), 3)
        far = [raw("t%d" % i, "[PATCH %d/2] y" % i, t=NOW - 50 * H + i * 5 * H) for i in (1, 2)]
        self.assertEqual(len(load({"lkml": far})[0]), 2)

    def test_two_versions_in_one_thread_split_into_revisions(self):
        v1 = thread("v1", "foo: bar", n=1, t=NOW - 5 * D)
        v2 = [raw("v2-0", "[PATCH v2 0/1] foo: bar", t=NOW - D, parent="v1-0"),
              raw("v2-1", "[PATCH v2 1/1] foo: bar part 1", t=NOW - D + 1, parent="v2-0")]
        ents, _ = load({"lkml": v1 + v2})
        self.assertEqual(sorted(e.version for e in ents if e.kind == "pr"), [1, 2])

    def test_git_pull_is_a_pr(self):
        ents, _ = load({"lkml": [raw("g", "[GIT PULL] driver updates for v6.13", "Greg KH")]})
        self.assertEqual((ents[0].kind, ents[0].pull), ("pr", True))


class VersionTests(unittest.TestCase):
    def test_versions_are_linked_and_only_latest_is_headline(self):
        entries = (thread("v1", "mm: speed up foo", n=2, t=NOW - 9 * D)
                   + thread("v2", "mm: speed up foo", n=2, t=NOW - 5 * D, tags=" v2")
                   + thread("v3", "mm: speed up foo", n=2, t=NOW - 1 * D, tags=" v3"))
        ents, _ = load({"linux-mm": entries})
        prs = sorted([e for e in ents if e.kind == "pr"], key=lambda e: e.version)
        self.assertEqual([e.version for e in prs], [1, 2, 3])
        self.assertEqual([e.label for e in prs], ["superseded", "superseded", "open"])
        self.assertEqual(len(prs[2].versions), 3)
        self.assertIs(prs[0].superseded_by, prs[1])
        self.assertIsNone(prs[2].superseded_by)

    def test_different_author_or_title_is_not_linked(self):
        entries = (thread("a", "mm: speed up foo", "Alice Author", t=NOW - 5 * D)
                   + thread("b", "mm: speed up foo", "Bob Builder", t=NOW - D, tags=" v2")
                   + thread("c", "mm: something else", "Alice Author", t=NOW - D, tags=" v2"))
        ents, _ = load({"linux-mm": entries})
        self.assertTrue(all(e.superseded_by is None for e in ents))

    def test_same_version_without_resend_is_not_superseding(self):
        entries = thread("a", "foo: x", n=1, t=NOW - 5 * D) + thread("b", "foo: x", n=1, t=NOW - D)
        ents, _ = load({"lkml": entries})
        self.assertTrue(all(e.superseded_by is None for e in ents))

    def test_resend_supersedes_the_original(self):
        entries = thread("a", "foo: x", n=1, t=NOW - 5 * D) + thread("b", "foo: x", n=1, t=NOW - D, tags=" RESEND")
        ents, _ = load({"lkml": entries})
        self.assertEqual(sorted(e.label for e in ents), ["open", "superseded"])


class StatusTests(unittest.TestCase):
    def one(self, replies, patch_t=NOW - 2 * H, subject="[PATCH] fs: fix x", author="Alice Author"):
        entries = [raw("p", subject, author, t=patch_t)]
        for i, (who, body, *rest) in enumerate(replies):
            entries.append(raw("r%d" % i, "Re: " + subject, who, parent=rest[0] if rest else "p",
                               t=patch_t + 60 * (i + 1), body=body))
        ents, _ = load({"linux-fsdevel": entries})
        return [e for e in ents if e.kind == "pr"][0]

    def test_open_then_in_review(self):
        self.assertEqual(self.one([]).label, "open")
        self.assertEqual(self.one([("Bob", "Which tree is this for, by the way?\n")]).label, "review")

    def test_author_reply_alone_is_not_review(self):
        self.assertEqual(self.one([("Alice Author", "Ping, anyone?\n")]).label, "open")

    def test_reviewed_acked_tested(self):
        self.assertEqual(self.one([("Bob", "Looks good.\nReviewed-by: Bob <bob@example.test>\n")]).label, "reviewed")
        self.assertEqual(self.one([("Bob", "Fine.\nAcked-by: Bob <bob@example.test>\n")]).label, "acked")
        self.assertEqual(self.one([("Bob", "Works.\nTested-by: Bob <bob@example.test>\n")]).label, "tested")
        both = self.one([("Bob", "x\nTested-by: Bob <bob@example.test>\n"),
                         ("Carol", "x\nReviewed-by: Carol <carol@example.test>\n")])
        self.assertEqual(both.label, "reviewed")

    def test_own_reviewed_by_does_not_count(self):
        e = self.one([("Alice Author", "Reviewed-by: Alice Author <alice.author@example.test>\n")])
        self.assertEqual(e.label, "open")

    def test_quoted_trailers_do_not_count(self):
        e = self.one([("Bob", "> Reviewed-by: Carol <carol@example.test>\nWhat is this about, exactly?\n")])
        self.assertEqual(e.label, "review")

    def test_nak_and_inline_review_request_changes(self):
        self.assertEqual(self.one([("Bob", "NAK, this breaks the ABI.\n")]).label, "changes")
        self.assertEqual(self.one([("Bob", "Nacked-by: Bob <bob@example.test>\n")]).label, "changes")
        inline = "> +\tfoo = bar;\nPlease use baz here instead of bar.\n"
        self.assertEqual(self.one([("Bob", inline)]).label, "changes")

    def test_later_approval_by_same_reviewer_clears_changes(self):
        inline = "> +\tfoo = bar;\nPlease use baz here instead of bar.\n"
        e = self.one([("Bob", inline), ("Bob", "Good now.\nReviewed-by: Bob <bob@example.test>\n")])
        self.assertEqual(e.label, "reviewed")

    def test_merged_by_someone_else(self):
        e = self.one([("Maintainer", "Applied to my tree, thanks.\n")])
        self.assertEqual((e.label, e.state), ("merged", "merged"))

    def test_author_cannot_merge_own_patch(self):
        self.assertNotEqual(self.one([("Alice Author", "Applied to my tree, thanks.\n")]).label, "merged")

    def test_question_about_applying_is_not_merged(self):
        self.assertNotEqual(self.one([("Bob", "Can this be applied to stable?\n")]).label, "merged")

    def test_merged_beats_superseded(self):
        entries = (thread("a", "foo: x", n=1, t=NOW - 5 * D)
                   + [raw("m", "Re: [PATCH 1/1] foo: x part 1", "Maint", parent="a-1", t=NOW - 4 * D,
                          body="Applied, thanks.\n")]
                   + thread("b", "foo: x", n=1, t=NOW - D, tags=" v2"))
        ents, _ = load({"lkml": entries})
        self.assertEqual(sorted(e.label for e in ents), ["merged", "open"])

    def test_pull_request_pulled(self):
        ents, _ = load({"lkml": [raw("g", "[GIT PULL] updates", "Greg"),
                                raw("g2", "Re: [GIT PULL] updates", "Linus", parent="g", t=NOW - H,
                                    body="Pulled, thanks.\n")]})
        self.assertEqual(ents[0].label, "merged")

    def test_stale_after_60_days(self):
        old = NOW - (STALE_DAYS + 5) * D
        e = self.one([], patch_t=old)
        self.assertEqual((e.label, e.state), ("stale", "closed"))
        fresh = self.one([], patch_t=NOW - (STALE_DAYS - 5) * D)
        self.assertEqual(fresh.label, "open")

    def test_stale_discussion_closes(self):
        ents, _ = load({"lkml": [raw("q", "Old question", t=NOW - 80 * D)]})
        self.assertEqual((ents[0].label, ents[0].state), ("stale", "closed"))

    def test_merged_never_goes_stale(self):
        e = self.one([("Maintainer", "Applied, thanks.\n")], patch_t=NOW - 100 * D)
        self.assertEqual(e.label, "merged")


class CrossPostTests(unittest.TestCase):
    def test_same_message_in_two_lists_is_one_thread_with_chips(self):
        a = thread("x", "net: shared change", n=2)
        ents, msgs = load({"netdev": a, "lkml": a})
        self.assertEqual(len([e for e in ents if e.kind == "pr"]), 1)
        e = ents[0]
        self.assertEqual(e.lists, {"netdev", "lkml"})
        self.assertEqual(e.home, "netdev")  # lkml is never the home when cross-posted
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs["x-0"].lists, {"netdev", "lkml"})

    def test_reply_seen_in_only_one_list_still_joins_the_thread(self):
        a = thread("x", "net: shared change", n=1)
        reply = raw("r", "Re: [PATCH 1/1] net: shared change part 1", "Bob", parent="x-1", t=NOW - H)
        ents, _ = load({"netdev": a, "lkml": a + [reply]})
        self.assertEqual(len(ents), 1)
        self.assertEqual(len(ents[0].comments), 1)


class NumberingTests(unittest.TestCase):
    def test_numbers_are_stable_when_an_older_root_appears(self):
        from lorehub.build import assign_numbers
        from lorehub.model import build_entities, finish_entity, load_messages

        from .helpers import make_state
        numbers = {"keys": {}, "next": {}}
        reply = raw("r", "Re: Topic", "Bob", parent="root", t=NOW - H)
        st, tmp = make_state({"lkml": [reply]})
        msgs = load_messages(st, ["lkml"])
        ents = build_entities(msgs)
        [finish_entity(e) for e in ents]
        assign_numbers(ents, msgs, numbers, prune=False)
        first = (ents[0].home, ents[0].n)
        tmp.cleanup()
        root = raw("root", "Topic", "Alice", t=NOW - 2 * H)
        st, tmp = make_state({"lkml": [reply, root]})
        msgs = load_messages(st, ["lkml"])
        ents = build_entities(msgs)
        [finish_entity(e) for e in ents]
        assign_numbers(ents, msgs, numbers, prune=False)
        tmp.cleanup()
        self.assertEqual(len(ents), 1)
        self.assertEqual((ents[0].home, ents[0].n), first)

    def test_numbers_are_unique_per_home(self):
        ents, _ = load({"lkml": [raw("a", "One"), raw("b", "Two"), raw("c", "[PATCH] three")]})
        self.assertEqual(len({(e.home, e.n) for e in ents}), 3)


if __name__ == "__main__":
    unittest.main()
