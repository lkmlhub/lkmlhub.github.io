import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from lorehub import backfill as bf
from lorehub.backfill import Config, history_start, is_done, next_cursor, order, run_backfill
from lorehub.build import build_site
from lorehub.crawl import _Empty, window_query, window_url
from lorehub.fetch import FetchError
from lorehub.state import State

from .helpers import NOW, atom, raw

DAY = 86400
quiet = lambda m: None  # noqa: E731


class Resp:
    def __init__(self, body):
        self.status, self.body, self.headers = 200, body, {}


class FakeSource:
    """In-memory search service: every list holds entries; a window request returns the newest `page`."""

    def __init__(self, lists, page=5, fail=(), clock=None, tick=0.0):
        self.lists = lists
        self.page = page
        self.fail = set(fail)
        self.calls = []
        self.clock, self.tick = clock, tick
        self.active = self.max_active = 0
        self.lock = threading.Lock()

    def manifest(self):
        raise FetchError("not used")

    def search_window(self, name, lo, hi, seq):
        with self.lock:
            self.calls.append((name, lo, hi, seq))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.clock and self.tick:
                self.clock.advance(self.tick)
            if name in self.fail:
                raise FetchError("boom %s" % name)
            hits = sorted((e for e in self.lists.get(name, []) if lo < e["ts"] <= hi), key=lambda e: -e["ts"])
            if not hits:
                return _Empty()
            return Resp(atom(hits[: self.page], name))
        finally:
            with self.lock:
                self.active -= 1


class SyncPool:
    """Runs each task when it is submitted, so the order of requests is deterministic."""

    def __init__(self, max_workers=2):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *args):
        from concurrent.futures import Future
        f = Future()
        try:
            f.set_result(fn(*args))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        return self.t

    def advance(self, dt):
        with self.lock:
            self.t += dt


def diff_body(i):
    return ("Change %d.\n---\n a | 1 +\n\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1,2 @@\n x\n+y%d\n" % (i, i))


def entries(prefix, n, newest=NOW - 3600, step=DAY // 2, same=False):
    return [raw("%s%d" % (prefix, i), "[PATCH] change %s %d" % (prefix, i), name="Dev %s" % prefix,
                t=newest - (0 if same else i * step), body=diff_body(i)) for i in range(n)]


class QueryTests(unittest.TestCase):
    def test_query_has_parentheses_and_14_digit_dates(self):
        q = window_query(NOW - DAY, NOW)
        self.assertTrue(q.startswith("(dt:2026"))
        self.assertIn(") AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)", q)
        lo, hi = q[len("(dt:"):].split(")")[0].split("..")
        self.assertEqual((len(lo), len(hi)), (14, 14))

    def test_url_is_encoded_and_paged_with_o(self):
        u = window_url("linux-mm", NOW - DAY, NOW, offset=200)
        self.assertTrue(u.startswith("https://lore.kernel.org/linux-mm/?x=A&q=%28") or "q=(" in u)
        self.assertNotIn(" ", u)
        self.assertTrue(u.endswith("&o=200"))


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(bf, "SEARCH_PAGE", 5)
        p.start()
        self.addCleanup(p.stop)

    def run_bf(self, source, cfg=None, now=NOW, names=None, clock=None):
        cfg = cfg or Config(days=10)
        return run_backfill(self.state, source, now, cfg, names=names, log=quiet, clock=clock or Clock())

    def mids(self, name):
        return set(self.state.load_list(name)["msgs"])

    def test_cursor_walks_back_in_windows_until_the_horizon(self):
        ents = entries("a", 23)  # 11.5 days of messages, 10 day horizon
        src = FakeSource({"a": ents})
        s = self.run_bf(src, names=["a"])
        wanted = {e["mid"] for e in ents if e["ts"] > NOW - 10 * DAY}
        self.assertEqual(self.mids("a"), wanted)
        calls = src.calls
        self.assertEqual([c[3] for c in calls], list(range(len(calls))))  # replay keys count up
        for prev, cur in zip(calls, calls[1:]):
            self.assertLess(cur[2], prev[2])  # the upper bound only moves back
        self.assertEqual(calls[0][1], NOW - 10 * DAY)
        meta = self.state.load_meta()["lists"]["a"]["bf"]
        self.assertTrue(meta["done"])
        self.assertEqual(history_start(meta, NOW - 10 * DAY), NOW - 10 * DAY)
        self.assertEqual(s["lists_left"], 0)
        self.assertEqual(s["requests"], len(calls))

    def test_resumes_across_runs_and_matches_a_single_run(self):
        ents = entries("a", 23)
        src = FakeSource({"a": ents})
        s1 = self.run_bf(src, Config(days=10, max_requests=2), names=["a"])
        self.assertEqual(s1["stopped"], "per run request cap")
        self.assertEqual(s1["requests"], 2)
        bf1 = self.state.load_meta()["lists"]["a"]["bf"]
        self.assertFalse(bf1["done"])
        part = self.mids("a")
        self.assertEqual(len(part), 9)  # 5 + 4: the inclusive upper bound repeats the oldest entry
        s2 = self.run_bf(src, Config(days=10, max_requests=100), names=["a"])
        self.assertEqual(s2["lists_left"], 0)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        st2 = State(other.name)
        run_backfill(st2, FakeSource({"a": ents}), NOW, Config(days=10), names=["a"], log=quiet, clock=Clock())
        self.assertEqual(self.mids("a"), set(st2.load_list("a")["msgs"]))
        self.assertGreater(len(self.mids("a")), len(part))
        self.assertEqual(self.state.load_meta()["backfill"]["requests"], s1["requests"] + s2["requests"])

    def test_finished_lists_are_not_asked_again(self):
        src = FakeSource({"a": entries("a", 3)})
        self.run_bf(src, names=["a"])
        n = len(src.calls)
        s = self.run_bf(src, names=["a"], now=NOW + 3 * DAY)
        self.assertEqual(len(src.calls), n)
        self.assertEqual(s["requests"], 0)

    def test_time_budget_with_a_fake_clock(self):
        clock = Clock()
        src = FakeSource({"a": entries("a", 60)}, clock=clock, tick=100)
        s = run_backfill(self.state, src, NOW, Config(days=40, budget_s=250, commit_pages=2), names=["a"],
                         log=quiet, clock=clock)
        self.assertEqual(s["stopped"], "time budget")
        self.assertEqual(s["requests"], 3)  # 0 s, 100 s, 200 s start before the 250 s deadline
        self.assertFalse(self.state.load_meta()["lists"]["a"]["bf"].get("done"))
        self.assertEqual(len(self.mids("a")), 13)

    def test_total_cap_across_runs(self):
        src = FakeSource({"a": entries("a", 60)})
        s1 = self.run_bf(src, Config(days=40, max_total=3), names=["a"])
        self.assertEqual(s1["requests"], 3)
        self.assertEqual(s1["stopped"], "total request cap")
        s2 = self.run_bf(src, Config(days=40, max_total=3), names=["a"])
        self.assertEqual(s2["requests"], 0)
        self.assertEqual(s2["stopped"], "total request cap")

    def test_page_cap_marks_the_list_done_but_capped(self):
        src = FakeSource({"a": entries("a", 60)})
        s = self.run_bf(src, Config(days=40, list_pages=2), names=["a"])
        m = self.state.load_meta()["lists"]["a"]["bf"]
        self.assertTrue(m["done"])
        self.assertEqual(m["cap"], 1)
        self.assertEqual(s["requests"], 2)
        self.assertGreater(history_start(m, NOW - 40 * DAY), NOW - 40 * DAY)  # reports where it really got to
        self.assertTrue(is_done(m, NOW - 40 * DAY, 2))
        self.assertFalse(is_done(m, NOW - 40 * DAY, 4))  # raising the cap resumes it

    def test_small_lists_first_and_at_most_two_in_flight(self):
        self.state.save_meta({"lists": {"big": {"rt": 900.0}, "mid": {"rt": 30.0}, "tiny": {"rt": 0.5}, "unknown": {}}})
        src = FakeSource({n: entries(n[0], 12) for n in ("big", "mid", "tiny", "unknown")})
        with mock.patch.object(bf, "ThreadPoolExecutor", SyncPool):
            self.run_bf(src, names=["big", "mid", "tiny", "unknown"])
        first = []
        for c in src.calls:
            if c[0] not in first:
                first.append(c[0])
        self.assertEqual(first, ["unknown", "tiny", "mid", "big"])  # no stored messages and no rate: the cheapest list
        src2 = FakeSource({n: entries(n[0], 12) for n in ("big", "mid", "tiny", "unknown")})
        self.run_bf(src2, Config(days=39), names=["big", "mid", "tiny", "unknown"])  # real pool: never more than 2 in flight
        self.assertLessEqual(src2.max_active, 2)
        self.assertEqual(order(["b", "a", "c"], {"b": {"rt": 5}, "c": {"rt": 1}}), ["c", "b", "a"])

    def test_old_state_without_rates_is_ordered_from_the_stored_messages(self):
        from lorehub.crawl import ingest
        for name, n, step in (("busy", 30, 600), ("calm", 30, DAY // 2), ("empty", 0, 1)):
            _, data = ingest(self.state, name, entries(name[0], n, step=step), NOW) if n else (0, {"msgs": {}})
            if n:
                self.state.save_list(name, data)
        self.state.save_meta({"lists": {"busy": {}, "calm": {}, "empty": {}}})
        src = FakeSource({"busy": entries("b", 3), "calm": entries("c", 3), "empty": []})
        with mock.patch.object(bf, "ThreadPoolExecutor", SyncPool):
            self.run_bf(src, names=["busy", "calm", "empty"])
        first = []
        for c in src.calls:
            if c[0] not in first:
                first.append(c[0])
        self.assertEqual(first, ["empty", "calm", "busy"])
        rt = {n: self.state.load_meta()["lists"][n]["rt"] for n in ("busy", "calm", "empty")}
        self.assertEqual(sorted(rt, key=rt.get), ["empty", "calm", "busy"])  # the rate is derived from stored messages
        self.assertEqual(order(["busy", "calm", "empty"], {n: {"rt": v} for n, v in rt.items()}), ["empty", "calm", "busy"])

    def test_failure_is_recorded_and_other_lists_continue(self):
        self.run_bf(FakeSource({"ok": entries("o", 4), "bad": entries("b", 4)}), names=["ok"])
        before = {p.name: p.read_bytes() for p in self.state.segments("ok")}
        src = FakeSource({"ok": entries("o", 4), "bad": entries("b", 4)}, fail={"bad"})
        s = self.run_bf(src, names=["ok", "bad"])
        self.assertEqual(s["failed"], 1)
        meta = self.state.load_meta()["lists"]
        self.assertIn("boom", meta["bad"]["bf"]["err"])
        self.assertFalse(meta["bad"]["bf"].get("done"))
        self.assertEqual(self.state.list_names(), ["ok"])
        self.assertEqual({p.name: p.read_bytes() for p in self.state.segments("ok")}, before)
        # the next run retries the failed list
        s = self.run_bf(FakeSource({"bad": entries("b", 4)}), names=["ok", "bad"])
        self.assertEqual(s["failed"], 0)
        self.assertEqual(len(self.mids("bad")), 4)

    def test_server_backoff_aborts_the_run(self):
        class Angry(FakeSource):
            def search_window(self, *a):
                raise FetchError("go away", 429, backoff=True)
        s = self.run_bf(Angry({"a": entries("a", 3), "b": entries("b", 3)}), names=["a", "b"])
        self.assertTrue(s["aborted"])
        self.assertEqual(s["stopped"], "server asked to back off")

    def test_empty_window_finishes_the_list(self):
        s = self.run_bf(FakeSource({"a": []}), names=["a"])
        self.assertTrue(self.state.load_meta()["lists"]["a"]["bf"]["done"])
        self.assertEqual(s["requests"], 1)

    def test_burst_at_one_second_cannot_loop_forever(self):
        src = FakeSource({"a": entries("a", 12, same=True)})
        s = self.run_bf(src, Config(days=10, max_requests=50), names=["a"])
        self.assertLessEqual(s["requests"], 5)
        cursors = [c[2] for c in src.calls]
        self.assertEqual(len(cursors), len(set(cursors)))
        self.assertEqual(next_cursor({"cur": 100}, [{"ts": 100}], 0), 99)

    def test_a_longer_horizon_continues_from_where_it_stopped(self):
        ents = entries("a", 40)
        src = FakeSource({"a": ents})
        self.run_bf(src, Config(days=5), names=["a"])
        first = len(src.calls)
        meta = self.state.load_meta()["lists"]["a"]["bf"]
        self.assertEqual(meta["cur"], NOW - 5 * DAY)
        self.run_bf(src, Config(days=10), names=["a"])
        self.assertEqual(src.calls[first][2], NOW - 5 * DAY)  # started at the old horizon, not at now
        self.assertEqual(self.mids("a"), {e["mid"] for e in ents if e["ts"] > NOW - 10 * DAY})

    def test_default_horizon_is_180_days_and_retention_follows_it(self):
        from lorehub.retention import effective_retention
        from lorehub.state import RETENTION_DAYS
        self.assertEqual(Config().days, 180)
        self.assertEqual(RETENTION_DAYS, 180)
        self.assertEqual(bf.horizon_ts(NOW, 365), NOW - 365 * DAY)
        self.assertEqual(effective_retention(365), 365)
        self.assertEqual(effective_retention(30), 180)
        self.assertEqual(effective_retention(0), 180)

    def test_a_horizon_beyond_the_default_keeps_the_backfilled_messages(self):
        ents = [raw("old%d" % i, "[PATCH] old %d" % i, t=NOW - (200 + i) * DAY, body=diff_body(i)) for i in range(3)]
        self.run_bf(FakeSource({"a": ents}), Config(days=300), names=["a"])
        self.assertEqual(self.mids("a"), {"old0", "old1", "old2"})
        self.run_bf(FakeSource({"b": ents}), Config(days=100), names=["b"])  # inside the default retention only
        self.assertEqual(self.mids("b"), set())

    def test_progress_helper(self):
        self.run_bf(FakeSource({"a": entries("a", 3)}), names=["a"])
        done, total, reqs = bf.progress(self.state.load_meta(), NOW, 10)
        self.assertEqual((done, total), (1, 1))
        self.assertGreaterEqual(reqs, 1)


class BackfilledHistoryTests(unittest.TestCase):
    """Versions of one series that arrive in different pages are still linked, and replies attach."""

    def test_versions_link_and_replies_attach_on_backfilled_history(self):
        def patch(mid, ver, idx, tot, t, parent="", name="Dev"):
            tag = "PATCH RFC" if ver == 1 else "PATCH v%d" % ver
            return raw(mid, "[%s %d/%d] ipe: add the keyring" % (tag, idx, tot), name=name, t=t, parent=parent,
                       body="Add it.\n---\n a | 1 +\n\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1,2 @@\n x\n+y\n")
        old = NOW - 20 * DAY
        v1 = [patch("v1-1", 1, 1, 2, old), patch("v1-2", 1, 2, 2, old + 1, parent="v1-1")]
        v2 = [patch("v2-1", 2, 1, 2, NOW - 8 * DAY), patch("v2-2", 2, 2, 2, NOW - 8 * DAY + 1, parent="v2-1")]
        replies = [raw("r1", "Re: [PATCH RFC 1/2] ipe: add the keyring", name="Rev One", t=old + 600, parent="v1-1",
                       body="> Add it.\nLooks wrong, please fix this properly.\n"),
                   raw("r2", "Re: [PATCH v2 1/2] ipe: add the keyring", name="Rev Two", t=NOW - 7 * DAY, parent="v2-1",
                       body="Reviewed-by: Rev Two <two@example.test>\n")]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = State(tmp.name)
        src = FakeSource({"lsm": v1 + v2 + replies}, page=3)
        with mock.patch.object(bf, "SEARCH_PAGE", 3):
            run_backfill(st, src, NOW, Config(days=60), names=["lsm"], log=quiet, clock=Clock())
        self.assertGreater(len(src.calls), 2)  # several pages: v1 and v2 came from different requests
        out = Path(tmp.name) / "public"
        build_site(st, out, NOW, names=["lsm"], log=quiet)
        meta = json.loads((out / "data/lists/lsm/meta.json").read_text())
        rows = []
        for key, pages in meta["pages"].items():
            for i in range(pages):
                rows += json.loads((out / ("data/lists/lsm/%s-%d.json" % (key, i))).read_text())["rows"]
        self.assertEqual(len(rows), 2, [(r['t'], r['k'], r['v'] if 'v' in r else None, r.get('pa')) for r in rows])
        newest = next(r for r in rows if r["v"] == 2)
        oldest = next(r for r in rows if r["v"] == 1)
        self.assertEqual(oldest["l"], "superseded")
        self.assertEqual(oldest["sb"], 2)
        self.assertEqual(newest["nv"], 2)
        self.assertEqual((newest["c"], oldest["c"]), (1, 1))
        shard = json.loads((out / ("data/threads/lsm/%d.json" % (newest["n"] // 32))).read_text())
        detail = shard[str(newest["n"])]
        self.assertEqual(len(detail["vs"]), 2)
        self.assertEqual(detail["df"], 1)
        self.assertEqual(detail["fc"], 1)
        self.assertTrue((out / ("data/diffs/lsm/%d.json.gz" % newest["n"])).is_file())


if __name__ == "__main__":
    unittest.main()
