import unittest

from lorehub.build import assign_numbers
from lorehub.model import build_entities, finish_entity, load_messages

from .helpers import NOW, load, make_state, raw


class GhostGroupingTests(unittest.TestCase):
    def test_siblings_replying_to_the_same_missing_parent_share_a_thread(self):
        ents, _ = load({"netdev": [
            raw("r1", "Re: [PATCH 2/4] foo: bar", "Bob", parent="gone", t=NOW - 600),
            raw("r2", "Re: [PATCH 2/4] foo: bar", "Carol", parent="gone", t=NOW - 300)]})
        self.assertEqual(len(ents), 1)
        self.assertEqual((ents[0].ghost, len(ents[0].comments)), (True, 2))
        self.assertEqual(ents[0].head, "gone")

    def test_ghost_number_survives_pruning_and_real_root_inherits_it(self):
        numbers = {"keys": {}, "next": {}}
        reply = raw("r", "Re: [PATCH] foo: bar", "Bob", parent="gone", t=NOW - 600)

        def run(entries):
            st, tmp = make_state({"lkml": entries})
            msgs = load_messages(st, ["lkml"])
            ents = build_entities(msgs)
            for e in ents:
                finish_entity(e)
            assign_numbers(ents, msgs, numbers, prune=True)
            tmp.cleanup()
            return ents

        first = run([reply])
        again = run([reply])
        self.assertEqual((first[0].home, first[0].n), (again[0].home, again[0].n))
        root = raw("gone", "[PATCH] foo: bar", "Alice", t=NOW - 3600)
        later = run([root, reply])
        self.assertEqual(len(later), 1)
        self.assertEqual((later[0].home, later[0].n), (first[0].home, first[0].n))
        self.assertFalse(later[0].ghost)
        self.assertEqual(len(later[0].comments), 1)


class UnthreadedSeriesTests(unittest.TestCase):
    def test_unthreaded_series_is_joined_even_when_a_patch_has_replies(self):
        ents, _ = load({"lkml": [
            raw("p1", "[PATCH 1/2] foo: bar", "Alice", t=NOW - 7200),
            raw("p2", "[PATCH 2/2] foo: baz", "Alice", t=NOW - 7190),
            raw("r1", "Re: [PATCH 1/2] foo: bar", "Bob", parent="p1", t=NOW - 3600, body="Reviewed-by: Bob <b@example.test>\n")]})
        self.assertEqual(len(ents), 1)
        self.assertEqual((len(ents[0].patches), len(ents[0].comments)), (2, 1))
        self.assertFalse(ents[0].partial)


if __name__ == "__main__":
    unittest.main()
