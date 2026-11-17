"""Thread completion: gap detection, the thread Atom, the resumable queue with a fake transport and a fake clock."""

import re
import tempfile
import threading
import unittest
from pathlib import Path

from lorehub import complete
from lorehub.build import build_site
from lorehub.complete import Config, coverage, find_gaps, orphan_keys, root_of, run_completion, scan_list, thread_url
from lorehub.crawl import thread_file
from lorehub.fetch import FetchError
from lorehub.feed import parse_feed
from lorehub.model import build_entities, compute_status, finish_entity, link_versions, load_messages
from lorehub.state import State

from .helpers import FIXTURES, NOW, atom, make_state, patch_body, raw, thread_detail

quiet = lambda m: None  # noqa: E731
DAY = 86400


def series(prefix, total, have=None, cover=True, replies=(), t=NOW - 3 * 3600, name="Dev", title="foo: add bar", ver=""):
    """Messages of a series [PATCH n/total]; `have` limits which patch numbers exist, replies are (n, text) pairs."""
    out = []
    tag = "PATCH%s" % ver
    if cover:
        out.append(raw(prefix + "-0", "[%s 0/%d] %s" % (tag, total, title), name=name, t=t, body="Cover letter.\n"))
    for i in range(1, total + 1):
        if have is not None and i not in have:
            continue
        out.append(raw("%s-%d" % (prefix, i), "[%s %d/%d] %s part %d" % (tag, i, total, title, i), name=name, t=t + i,
                       parent=prefix + "-0" if cover else "", body=patch_body("f%d.c" % i, prefix + str(i))))
    for k, rep in enumerate(replies):
        i, text = rep[0], rep[1]
        parent = rep[2] if len(rep) > 2 else "%s-%d" % (prefix, i)  # a reply to a reply names its parent
        out.append(raw("%s-r%d" % (prefix, k), "Re: [%s %d/%d] %s part %d" % (tag, i, total, title, i), name="Rev%d" % k,
                       t=t + 100 + k, parent=parent, body=text))
    return out


def entities_of(entries):
    st, tmp = make_state({"l": entries})
    try:
        msgs = load_messages(st, ["l"])
        ents = build_entities(msgs)
        for e in ents:
            finish_entity(e)
        link_versions([e for e in ents if e.kind == "pr"])
        for e in ents:
            compute_status(e, NOW)
        return [e for e in ents if e.kind == "pr" and e.patches], msgs
    finally:
        tmp.cleanup()


class GapTests(unittest.TestCase):
    def gaps(self, entries, legacy=False):
        ents, msgs = entities_of(entries)
        self.assertEqual(len(ents), 1)
        return find_gaps(ents[0], msgs, legacy, orphan_keys(msgs))

    def test_a_complete_series_has_no_gap(self):
        self.assertEqual(self.gaps(series("a", 3, replies=[(1, "ok\n")])), "")

    def test_missing_patch_number_in_the_middle(self):
        self.assertEqual(self.gaps(series("a", 4, have={1, 2, 4})), "n")  # 1/4, 2/4, 4/4 present, 3/4 is not

    def test_missing_last_patch_is_compared_with_the_highest_total(self):
        self.assertEqual(self.gaps(series("a", 7, have={1, 2, 3})), "n")

    def test_reply_whose_parent_is_not_in_the_state(self):
        entries = series("a", 2) + [raw("orphan", "Re: [PATCH 1/2] foo: add bar part 1", name="Rev", t=NOW - 100,
                                        parent="not-stored", body="late reply\n")]
        self.assertEqual(self.gaps(entries), "p")

    def test_no_cover_letter_while_patches_point_to_a_missing_parent(self):
        entries = series("a", 3, cover=False)
        for e in entries:
            e["parent"] = "a-0"  # they reply to a cover letter that is not in the state
        self.assertEqual(self.gaps(entries), "cp")

    def test_a_series_without_cover_letter_is_normal_when_nothing_points_at_one(self):
        self.assertEqual(self.gaps(series("a", 3, cover=False)), "")

    def test_single_patch_is_complete(self):
        self.assertEqual(self.gaps([raw("s", "[PATCH] foo", body=patch_body())]), "")

    def test_trimmed_bodies_count_only_when_asked(self):
        ents, msgs = entities_of(series("a", 2))
        for m in msgs.values():
            m.bv = 0  # an older, trimmed copy
        self.assertEqual(find_gaps(ents[0], msgs, legacy=False), "")
        self.assertEqual(find_gaps(ents[0], msgs, legacy=True), "l")


class ThreadAtomTests(unittest.TestCase):
    def test_url_form_is_the_one_verified_against_lore(self):
        self.assertEqual(thread_url("linux-security-module", "abc%40example.org"),
                         "https://lore.kernel.org/linux-security-module/abc%40example.org/t.atom")
        feed = (FIXTURES / "ipe/thread-v2.atom").read_text()
        self_link = re.search(r'rel="self"\s+href="([^"]+)"', feed).group(1)  # lore's own link of that real thread feed
        mid = re.search(r"/linux-security-module/([^/]+)/t\.atom", self_link).group(1)
        self.assertEqual(thread_url("linux-security-module", mid), self_link)

    def test_real_thread_atom_parses_into_the_whole_thread(self):
        entries, bad, _ = parse_feed((FIXTURES / "ipe/thread-v2.atom").read_bytes())
        self.assertEqual((len(entries), bad), (10, 0))
        by = {e["mid"]: e for e in entries}
        self.assertEqual(sum(1 for e in entries if e["subject"].startswith("Re:")), 6)
        self.assertEqual(sum(1 for e in entries if e["parent"] and e["parent"] in by), 9)  # every message but the first replies to one
        self.assertTrue(all(e["body"] for e in entries))

    def test_thread_file_name_is_safe(self):
        self.assertEqual(thread_file("l", "a/b"), "l.thread-a_b.atom")


class Resp:
    def __init__(self, body):
        self.status, self.body, self.headers = 200, body, {}


class ThreadSource:
    """Serves thread Atoms: `threads` maps any message-id of a thread to the entries of the whole thread."""

    def __init__(self, threads, fail=(), clock=None, tick=0.0, backoff=False):
        self.threads, self.fail, self.calls = threads, set(fail), []
        self.clock, self.tick, self.backoff = clock, tick, backoff
        self.active = self.max_active = 0
        self.lock = threading.Lock()

    def thread_atom(self, name, mid):
        with self.lock:
            self.calls.append((name, mid))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.clock and self.tick:
                self.clock.advance(self.tick)
            if mid in self.fail:
                raise FetchError("boom", 503, backoff=self.backoff)
            entries = self.threads.get(mid)
            if entries is None:
                raise FetchError("not found", 404)
            return Resp(atom(entries, name))
        finally:
            with self.lock:
                self.active -= 1


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        return self.t

    def advance(self, dt):
        with self.lock:
            self.t += dt


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name) / "state")

    def put(self, entries, name="l"):
        from lorehub.crawl import ingest
        _, data = ingest(self.state, name, entries, NOW, data={"v": 2, "name": name, "msgs": {}})
        self.state.save_list(name, data)

    def run_q(self, source, cfg=None, **kw):
        kw.setdefault("clock", Clock())
        return run_completion(self.state, source, NOW, cfg or Config(), log=quiet, **kw)

    def mids(self, name="l"):
        return set(self.state.load_list(name)["msgs"])

    def test_a_missing_reply_is_fetched_with_one_request_and_the_series_becomes_complete(self):
        full = series("a", 4, replies=[(3, "Looks fine.\n"), (3, "Thanks.\n", "a-r0"), (4, "Acked-by: Rev <r@example.test>\n")])
        partial = [e for e in full if e["mid"] != "a-r0"]  # the first reply fell into a gap, the reply to the reply did not
        self.put(partial)
        ents, msgs = entities_of(partial)
        self.assertEqual(find_gaps(ents[0], msgs, orphans=orphan_keys(msgs)), "p")
        src = ThreadSource({"a-0": full})
        s = self.run_q(src)
        self.assertEqual(s["queue"], 1)
        self.assertEqual((s["requests"], s["fetched"]), (1, 1))
        self.assertEqual(src.calls, [("l", "a-0")])  # the first message of the series, one request
        self.assertIn("a-r0", self.mids())
        self.assertEqual(self.mids(), {e["mid"] for e in full})
        self.assertEqual(len(self.state.load_list("l")["msgs"]), len(full))
        rec = self.state.load_list("l")["msgs"]["a-0"]
        self.assertEqual((rec["tn"] > 0, rec["tc"]), (True, NOW))
        self.assertEqual(scan_list(self.state, "l", NOW), [])  # nothing left to do
        self.assertEqual(self.state.load_meta()["lists"]["l"]["cq"], [])

    def test_a_missing_patch_arrives_with_its_diff_and_full_body(self):
        full = series("a", 4)
        self.put([e for e in full if e["mid"] != "a-3"])
        self.run_q(ThreadSource({"a-0": full}))
        data = self.state.load_list("l")
        rec = data["msgs"]["a-3"]
        self.assertIn("dz", rec)
        self.assertEqual(rec["bv"], 3)
        stored = {m for d in self.state.diff_days("l") for m in self.state.load_diff_day("l", d)}
        self.assertIn("a-3", stored)
        ents, msgs = entities_of(full)
        self.assertEqual(find_gaps(ents[0], msgs), "")

    def test_second_run_makes_no_request(self):
        partial = series("a", 3, have={1, 3})
        self.put(partial)
        src = ThreadSource({"a-0": partial})  # lore has nothing more either
        self.run_q(src)
        src2 = ThreadSource({"a-0": partial})
        s = self.run_q(src2, touched={"l"})
        self.assertEqual((s["queue"], s["requests"]), (0, 0))
        self.assertEqual(src2.calls, [])

    def test_gaps_that_lore_cannot_fill_are_marked_and_shown_as_such(self):
        partial = series("a", 3, have={1, 3})
        self.put(partial)
        self.run_q(ThreadSource({"a-0": partial}))
        ents, msgs = entities_of(partial)
        rec = self.state.load_list("l")["msgs"]["a-0"]
        ents[0].cover.tc, ents[0].cover.tn = rec["tc"], rec["tn"]
        code, gaps = coverage(ents[0], msgs, NOW)
        self.assertEqual((code, gaps), (2, "n"))  # fetched, still a gap: lore does not have it
        ents[0].cover.tc = 0
        self.assertEqual(coverage(ents[0], msgs, NOW)[0], 1)  # never fetched: waiting

    def test_newest_and_open_series_first_and_the_request_budget(self):
        old = series("old", 3, have={1, 3}, t=NOW - 20 * DAY, name="Dev")
        mid = series("mid", 3, have={1, 3}, t=NOW - 5 * DAY, name="Dev2", title="other: change")
        new = series("new", 3, have={1, 3}, t=NOW - 3600, name="Dev3", title="third: change")
        merged = series("mrg", 3, have={1, 3}, t=NOW - 600, name="Dev4", title="fourth: change", replies=[(1, "Applied, thanks.\n")])
        self.put(old + mid + new + merged)
        src = ThreadSource({"old-0": old, "mid-0": mid, "new-0": new, "mrg-0": merged})
        s = self.run_q(src, Config(max_requests=2))
        self.assertEqual(s["requests"], 2)
        self.assertEqual(s["stopped"], "per run request cap")
        self.assertEqual({c[1] for c in src.calls}, {"new-0", "mid-0"})  # open and newest first, the merged one waits
        self.assertEqual(s["left"], 2)
        src2 = ThreadSource({"old-0": old, "mid-0": mid, "new-0": new, "mrg-0": merged})
        s2 = self.run_q(src2, Config(max_requests=10))  # the next run resumes with what is left
        self.assertEqual({c[1] for c in src2.calls}, {"mrg-0", "old-0"})
        self.assertEqual(src2.calls[0][1], "old-0")  # open before merged, however old
        self.assertEqual(s2["left"], 0)

    def test_time_budget_with_a_fake_clock(self):
        parts = [series("s%d" % i, 3, have={1, 3}, t=NOW - 3600 * (i + 1), name="D%d" % i, title="t%d: x" % i) for i in range(6)]
        self.put([e for p in parts for e in p])
        clock = Clock()
        src = ThreadSource({"s%d-0" % i: p for i, p in enumerate(parts)}, clock=clock, tick=10.0)
        s = self.run_q(src, Config(budget_s=25), clock=clock)
        self.assertEqual(s["stopped"], "time budget")
        self.assertLess(s["requests"], 6)
        self.assertGreaterEqual(s["requests"], 2)

    def test_failures_stay_in_the_queue_and_a_back_off_stops_the_run(self):
        a, b = series("a", 3, have={1, 3}), series("b", 3, have={1, 3}, t=NOW - 7200, name="B", title="b: y")
        self.put(a + b)
        s = self.run_q(ThreadSource({"b-0": b}, fail={"a-0"}))
        self.assertEqual((s["failed"], s["fetched"]), (1, 1))
        self.assertEqual(s["left"], 1)  # a is retried next run
        s = self.run_q(ThreadSource({"a-0": a}, fail={"a-0"}, backoff=True), touched={"l"})
        self.assertEqual(s["stopped"], "server asked to back off")

    def test_unknown_thread_404_is_final(self):
        a = series("a", 3, have={1, 3})
        self.put(a)
        s = self.run_q(ThreadSource({}))  # lore answers 404 for the thread
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["left"], 1)

    def test_at_most_two_requests_in_flight(self):
        parts = [series("s%d" % i, 3, have={1, 3}, t=NOW - 3600 * (i + 1), name="D%d" % i, title="t%d: x" % i) for i in range(8)]
        self.put([e for p in parts for e in p])
        src = ThreadSource({"s%d-0" % i: p for i, p in enumerate(parts)})
        self.run_q(src)
        self.assertLessEqual(src.max_active, 2)
        self.assertEqual(len(src.calls), 8)

    def test_thread_older_than_the_horizon_is_taken_whole(self):
        entries = series("a", 2, t=NOW - 3600, cover=False)
        for e in entries:
            e["parent"] = "a-0"  # the patches answer a cover letter that is not stored
        ancient = raw("a-0", "[PATCH 0/2] foo: add bar", name="Dev", t=NOW - 200 * DAY, body="Cover.\n")  # before the horizon
        self.put(entries)
        full = entries + [ancient]
        run_completion(self.state, ThreadSource({"a-1": full}), NOW, Config(), log=quiet, clock=Clock())
        self.assertIn("a-0", self.mids())  # kept although older than the retention: part of a displayed thread

    def test_build_shows_the_waiting_note_flag_then_clears_it(self):
        full = series("a", 3, replies=[(2, "fine\n"), (2, "more\n", "a-r0")])
        partial = [e for e in full if e["mid"] != "a-r0"]  # a reply fell into a gap, its answer is waiting without a parent
        self.put(partial)
        out = Path(self.tmp.name) / "public"
        build_site(self.state, out, NOW, log=quiet)
        d = thread_detail(out, "l", 1)
        self.assertEqual((d["cp"], d["gp"]), (1, "p"))
        run_completion(self.state, ThreadSource({"a-0": full}), NOW, Config(), log=quiet, clock=Clock())
        build_site(self.state, out, NOW, log=quiet)
        d = thread_detail(out, "l", 1)
        self.assertEqual(d["cp"], 0)
        self.assertNotIn("gp", d)
        self.assertEqual(d["c"], 2)
        self.assertEqual(len(d["patches"]), 3)


if __name__ == "__main__":
    unittest.main()
