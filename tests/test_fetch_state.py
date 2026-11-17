import gzip
import json
import tempfile
import threading
import unittest
from pathlib import Path

from lorehub.crawl import CacheSource, crawl
from lorehub.feed import to_record
from lorehub.fetch import FetchError, Fetcher, Response, parse_retry_after
from lorehub.retention import Limits, enforce
from lorehub.state import State, StateError, atomic_write, gzip_bytes, merge_records

from .helpers import FIXTURES, NOW, raw


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make_fetcher(responses, clock=None, **kw):
    clock = clock or FakeClock()
    calls = []
    it = iter(responses)

    def transport(url, headers):
        calls.append((clock.t, url, headers))
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    f = Fetcher(transport=transport, sleep=clock.sleep, clock=clock.now, jitter=False, **kw)
    return f, calls, clock


class FetcherTests(unittest.TestCase):
    def test_sends_user_agent_and_validators(self):
        f, calls, _ = make_fetcher([Response(304)])
        r = f.get("https://lore.kernel.org/lkml/new.atom", etag='"abc"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
        self.assertEqual(r.status, 304)
        h = calls[0][2]
        self.assertIn("lkmlhub-bot", h["User-Agent"])
        self.assertEqual(h["If-None-Match"], '"abc"')
        self.assertIn("If-Modified-Since", h)

    def test_min_gap_between_requests(self):
        f, calls, _ = make_fetcher([Response(200, b"x")] * 4)
        for _ in range(4):
            f.get("https://lore.kernel.org/a")
        starts = [c[0] for c in calls]
        for a, b in zip(starts, starts[1:]):
            self.assertGreaterEqual(b - a, 0.5 - 1e-9)

    def test_retry_after_is_honoured(self):
        f, calls, clock = make_fetcher(
            [Response(429, headers={"retry-after": "7"}), Response(200, b"ok")])
        r = f.get("https://lore.kernel.org/a")
        self.assertEqual(r.body, b"ok")
        self.assertGreaterEqual(calls[1][0] - calls[0][0], 7)

    def test_503_without_header_backs_off_exponentially(self):
        f, calls, _ = make_fetcher([Response(503), Response(503), Response(200, b"ok")])
        f.get("https://lore.kernel.org/a")
        gap1 = calls[1][0] - calls[0][0]
        gap2 = calls[2][0] - calls[1][0]
        self.assertGreater(gap2, gap1)

    def test_giant_retry_after_stops_the_crawl(self):
        f, _, _ = make_fetcher([Response(429, headers={"retry-after": "99999"})])
        with self.assertRaises(FetchError) as cm:
            f.get("https://lore.kernel.org/a")
        self.assertTrue(cm.exception.backoff)

    def test_403_fails_fast_with_hint(self):
        f, calls, _ = make_fetcher([Response(403)])
        with self.assertRaises(FetchError) as cm:
            f.get("https://lore.kernel.org/a")
        self.assertIn("User-Agent", str(cm.exception))
        self.assertEqual(len(calls), 1)

    def test_network_errors_retry_then_give_up(self):
        f, calls, _ = make_fetcher([OSError("boom")] * 3, max_retries=2)
        with self.assertRaises(FetchError):
            f.get("https://lore.kernel.org/a")
        self.assertEqual(len(calls), 3)

    def test_concurrency_never_exceeds_two(self):
        lock = threading.Lock()
        state = {"now": 0, "max": 0}

        def transport(url, headers):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            threading.Event().wait(0.02)
            with lock:
                state["now"] -= 1
            return Response(200, b"x")

        f = Fetcher(transport=transport, min_gap=0.0, max_concurrent=2)
        ts = [threading.Thread(target=f.get, args=("https://lore.kernel.org/%d" % i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertLessEqual(state["max"], 2)
        self.assertEqual(f.requests, 8)

    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after("12"), 12.0)
        self.assertIsNone(parse_retry_after("soon"))
        self.assertIsNone(parse_retry_after(None))


class StateTests(unittest.TestCase):
    def test_atomic_write_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a" / "b.bin"
            atomic_write(p, b"one")
            atomic_write(p, b"two")
            self.assertEqual(p.read_bytes(), b"two")
            self.assertEqual([x.name for x in p.parent.iterdir()], ["b.bin"])

    def test_gzip_is_deterministic(self):
        self.assertEqual(gzip_bytes(b"abc" * 100), gzip_bytes(b"abc" * 100))

    def test_merge_adds_new_records_and_applies_the_age_limit(self):
        data = {"msgs": {}}
        old = to_record(raw("old", "x", t=NOW - 200 * 86400))
        mid_age = to_record(raw("mid", "y", t=NOW - 70 * 86400, body="body text\n"))
        new = to_record(raw("new", "z", t=NOW - 10, body="fresh\n"))
        added = merge_records(data, {"old": old, "mid": mid_age, "new": new}, NOW)
        self.assertEqual(added, 2)
        self.assertNotIn("old", data["msgs"])  # older than the 180 day retention
        self.assertEqual(merge_records(data, {"new": new}, NOW), 0)
        self.assertEqual(merge_records(data, {"old": old}, NOW, retention_days=None), 1)  # thread completion takes any age

    def test_a_record_fetched_again_replaces_the_old_trimmed_one(self):
        data = {"msgs": {"a": {"t": NOW - 5, "a": "A", "h": "x", "p": "", "s": "s", "b": "short\n> [...]", "tr": [["acked", "n", "h"]]}}}
        full = to_record(raw("a", "s", t=NOW - 5, body="complete text\n> quoted 1\n> quoted 2\n"))
        merge_records(data, {"a": full}, NOW)
        rec = data["msgs"]["a"]
        self.assertIn("quoted 2", rec["b"])
        self.assertEqual(rec["bv"], 3)
        self.assertNotIn("tr", rec)  # derived data is recomputed from the complete message
        nobody = to_record(raw("a", "s", t=NOW - 5, body="x\n"), keep_body=False)
        merge_records(data, {"a": nobody}, NOW)
        self.assertIn("quoted 2", data["msgs"]["a"]["b"])  # a fetch without bodies keeps the stored body

    def test_day_segments_only_rewrite_what_changed(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            data = {"msgs": {}}
            day = 86400
            recs = {"a": to_record(raw("a", "x", t=NOW - 3 * day)), "b": to_record(raw("b", "y", t=NOW - 2 * day)),
                    "c": to_record(raw("c", "z", t=NOW - 100))}
            merge_records(data, recs, NOW)
            st.save_list("lkml", data)
            files = sorted(st.snapshot("lkml"))
            self.assertEqual(len(files), 3)
            before = {p.name: p.stat().st_mtime_ns for p in st.segments("lkml")}
            loaded = st.load_list("lkml")
            self.assertEqual(set(loaded["msgs"]), {"a", "b", "c"})
            merge_records(loaded, {"d": to_record(raw("d", "w", t=NOW - 50))}, NOW)
            st.save_list("lkml", loaded)
            after = {p.name: p.stat().st_mtime_ns for p in st.segments("lkml")}
            changed = [n for n in after if before.get(n) != after[n]]
            self.assertEqual(changed, [files[-1]])
            self.assertEqual(st.list_names(), ["lkml"])

    def test_discussion_caps_count_age_and_bodies(self):
        data = {"msgs": {}}
        recs = {"m%d" % i: to_record(raw("m%d" % i, "s %d" % i, t=NOW - i * 60, body="b\n")) for i in range(50)}
        recs["ancient"] = to_record(raw("ancient", "old", t=NOW - 90 * 86400, body="body\n"))
        merge_records(data, recs, NOW)
        enforce(data, "l", NOW, Limits(max_msgs=20, max_body_msgs=5))
        self.assertEqual(len(data["msgs"]), 20)
        self.assertEqual(sum(1 for r in data["msgs"].values() if "b" in r), 5)
        self.assertIn("m0", data["msgs"])
        data = {"msgs": {}}
        merge_records(data, {"ancient": recs["ancient"], "m0": recs["m0"]}, NOW)
        enforce(data, "l", NOW)
        self.assertNotIn("b", data["msgs"]["ancient"])  # discussion bodies are kept for 60 days
        self.assertIn("b", data["msgs"]["m0"])

    def test_corrupt_state_is_an_error_not_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            (st.list_dir("x")).mkdir(parents=True)
            (st.list_dir("x") / "2026-01-01.json.gz").write_bytes(b"garbage")
            with self.assertRaises(StateError):
                st.load_list("x")


class CrawlTests(unittest.TestCase):
    def make_cache(self, d, names=("lkml",)):
        d = Path(d)
        (d / "manifest.js.gz").write_bytes((FIXTURES / "manifest.js.gz").read_bytes())
        for n in names:
            (d / (n + ".atom")).write_bytes((FIXTURES / "lkml.atom").read_bytes())

    def test_first_run_with_empty_state(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            self.make_cache(c)
            st = State(s)
            summary = crawl(st, CacheSource(c), NOW, names=["lkml"], log=lambda m: None)
            self.assertEqual(summary["fetched"], 1)
            self.assertEqual(len(st.load_list("lkml")["msgs"]), 25)
            meta = st.load_meta()["lists"]["lkml"]
            self.assertEqual(meta["err"], "")
            self.assertTrue(meta["fetched"])
            self.assertIn("lkml", st.load_manifest())

    def test_unchanged_manifest_modified_skips_the_list(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            self.make_cache(c)
            st = State(s)
            crawl(st, CacheSource(c), NOW, names=["lkml"], log=lambda m: None)
            summary = crawl(st, CacheSource(c), NOW + 1800, names=["lkml"], log=lambda m: None)
            self.assertEqual((summary["fetched"], summary["skipped"]), (0, 1))

    def test_failed_fetch_keeps_state_and_flags_error(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            self.make_cache(c)
            st = State(s)
            crawl(st, CacheSource(c), NOW, names=["lkml"], log=lambda m: None)
            before = st.snapshot("lkml")
            (Path(c) / "lkml.atom").unlink()
            m = st.load_meta()
            m["lists"]["lkml"]["mod"] = 0  # force a refetch
            st.save_meta(m)
            summary = crawl(st, CacheSource(c), NOW + 100, names=["lkml"], log=lambda m: None)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(st.snapshot("lkml"), before)
            self.assertTrue(st.load_meta()["lists"]["lkml"]["err"])

    def test_garbage_feed_never_shrinks_state(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            self.make_cache(c)
            st = State(s)
            crawl(st, CacheSource(c), NOW, names=["lkml"], log=lambda m: None)
            before = st.snapshot("lkml")
            (Path(c) / "lkml.atom").write_text("<html>oops</html>")
            m = st.load_meta()
            m["lists"]["lkml"]["mod"] = 0
            st.save_meta(m)
            summary = crawl(st, CacheSource(c), NOW + 100, names=["lkml"], log=lambda m: None)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(st.snapshot("lkml"), before)

    def test_budget_defers_remaining_lists(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            names = ["lkml", "netdev", "bpf", "stable"]
            self.make_cache(c, names)
            ticks = iter(range(0, 1000, 1))
            summary = crawl(State(s), CacheSource(c), NOW, names=names, budget_s=1,
                            log=lambda m: None, clock=lambda: next(ticks))
            self.assertGreater(summary["deferred"], 0)
            self.assertLess(summary["fetched"], 4)

    def test_least_recently_fetched_first(self):
        with tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as s:
            names = ["bpf", "lkml", "netdev"]
            self.make_cache(c, names)
            st = State(s)
            st.save_meta({"lists": {"bpf": {"fetched": 50, "mod": 0}, "lkml": {"fetched": 10, "mod": 0}}})
            order = []
            crawl(st, CacheSource(c), NOW, names=names, log=order.append)
            fetched = [l.split()[0] for l in order if "new (" in l]
            self.assertEqual(set(fetched[:2]), {"netdev", "lkml"})
            self.assertEqual(fetched[2], "bpf")


if __name__ == "__main__":
    unittest.main()
