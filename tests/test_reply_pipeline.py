"""Reply addresses: the separate address map of the state, the reply shards of the site, the completion queue for
address gaps and the opt out. Addresses must exist ONLY in state/addrs/<list>.json.gz and data/reply/<list>/<n>.json.gz."""

import contextlib
import gzip
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lorehub import state as state_mod
from lorehub.__main__ import main
from lorehub.build import build_site
from lorehub.complete import Config, run_completion
from lorehub.crawl import ingest
from lorehub.feed import list_address, parse_feed, sender_address
from lorehub.state import State

from .helpers import FIXTURES, NOW, is_address_file, jload, make_state, patch_body, raw, thread_detail
from .test_complete import Clock, ThreadSource, series

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
quiet = lambda m: None  # noqa: E731


def read_gz(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()).decode())


class FeedAddressTests(unittest.TestCase):
    def test_list_address_comes_from_the_feed_id(self):
        self.assertEqual(list_address((FIXTURES / "lkml.atom").read_bytes()), "user1@example.test")
        real = Path.home() / "git/lorehub-run/samples/lkml.atom"
        if real.exists():
            self.assertEqual(list_address(real.read_bytes()), "linux-kernel@vger.kernel.org")

    def test_thread_and_search_feeds_have_no_list_address(self):
        self.assertEqual(list_address((FIXTURES / "ipe/thread-v2.atom").read_bytes()), "")
        self.assertEqual(list_address(b"<feed><id>urn:uuid:1</id></feed>"), "")

    def test_sender_address_validates(self):
        h, a = sender_address({"email": " Dev.One@Example.TEST "})
        self.assertEqual(a, "Dev.One@Example.TEST")
        self.assertEqual(len(h), 12)
        for bad in ("", "nobody", "a@b", "a b@c.test", "a@b.test\r\nBcc: x@y.test", "<a@b.test>", "a@b@c.test", "x" * 300 + "@y.test"):
            self.assertIsNone(sender_address({"email": bad}), bad)

    def test_the_hash_is_the_one_of_the_record(self):
        from lorehub.feed import to_record
        e = raw("m1", "[PATCH] x", name="Dev One", email="dev.one@example.test")
        self.assertEqual(sender_address(e)[0], to_record(e)["h"])


class AddressMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name) / "state")

    def put(self, entries, name="l", post="", state=None):
        st = state or self.state
        _, data = ingest(st, name, entries, NOW, data=st.load_list(name) if st.segments(name) else {"v": 2, "name": name, "msgs": {}})
        if post:
            data["_post"] = post
        st.save_list(name, data)

    def test_addresses_are_stored_in_their_own_file(self):
        self.put(series("a", 2, replies=[(1, "ok\n")], name="Dev One"), post="l@lists.example.test")
        amap = self.state.load_addrs("l")
        self.assertEqual(amap["post"], "l@lists.example.test")
        self.assertEqual(sorted(amap["a"].values()), ["dev.one@example.test", "rev0@example.test"])
        self.assertTrue((self.state.root / "addrs/l.json.gz").exists())
        for h in amap["a"]:
            self.assertEqual(len(h), 12)

    def test_no_other_state_file_carries_an_address(self):
        self.put(series("a", 2, replies=[(1, "Reviewed-by: Rev Zero <rev0@example.test>\n")], name="Dev One"), post="l@lists.example.test")
        self.state.save_meta({"lists": {"l": {"n": 1}}})
        files = [p for p in self.state.root.rglob("*") if p.is_file()]
        self.assertTrue(any(is_address_file(p) for p in files))
        self.assertGreater(len([p for p in files if not is_address_file(p)]), 2)
        for p in files:
            if is_address_file(p):
                continue
            blob = p.read_bytes()
            text = (gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob).decode()
            self.assertFalse(EMAIL.search(text), p)

    def test_merge_keeps_old_and_adds_new_senders(self):
        self.put(series("a", 1, name="Dev One"), post="l@lists.example.test")
        self.put([raw("late", "Re: [PATCH 1/1] foo: add bar part 1", name="Late Comer", t=NOW - 100, parent="a-1", body="hi\n")])
        amap = self.state.load_addrs("l")
        self.assertEqual(sorted(amap["a"].values()), ["dev.one@example.test", "late.comer@example.test"])
        self.assertEqual(amap["post"], "l@lists.example.test")  # a later save without a list address keeps it

    def test_senders_of_dropped_messages_are_pruned(self):
        self.put(series("a", 1, name="Dev One"))
        data = self.state.load_list("l")
        for mid in [m for m, r in data["msgs"].items() if r["a"] == "Dev One"]:
            del data["msgs"][mid]
        data["msgs"]["x"] = {"t": NOW, "a": "Other", "h": "abcdefabcdef", "p": "", "s": "[PATCH] y"}
        self.state.save_list("l", data)
        self.assertEqual(self.state.load_addrs("l")["a"], {})

    def test_the_file_is_deterministic_and_unchanged_saves_do_not_rewrite_it(self):
        self.put(series("a", 2, name="Dev One"))
        path = self.state.root / "addrs/l.json.gz"
        before, mtime = path.read_bytes(), path.stat().st_mtime_ns
        self.state.save_list("l", self.state.load_list("l"))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_write_is_atomic(self):
        self.put(series("a", 1, name="Dev One"))
        path = self.state.root / "addrs/l.json.gz"
        before = path.read_bytes()
        with mock.patch.object(state_mod.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.state.save_addrs("l", "p@lists.example.test", {"abcdefabcdef": "x@example.test"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["l.json.gz"])  # the temporary file is gone

    def test_a_corrupt_map_is_an_error_not_silence(self):
        self.put(series("a", 1))
        (self.state.root / "addrs/l.json.gz").write_bytes(b"not gzip")
        with self.assertRaises(state_mod.StateError):
            self.state.load_addrs("l")

    def test_history_without_addresses_has_no_file_and_the_epoch_is_unchanged(self):
        self.assertEqual(state_mod.STATE_EPOCH, 3)  # the address feature must not restart the backfill
        self.state.reply_addresses = False
        self.put(series("a", 1, name="Dev One"))
        self.assertFalse((self.state.root / "addrs").exists())
        self.assertEqual(self.state.load_addrs("l"), {"post": "", "a": {}})

    def test_the_crawl_learns_the_list_address_and_senders_from_new_atom(self):
        from lorehub.crawl import CacheSource, crawl
        cache = Path(self.tmp.name) / "cache"
        cache.mkdir()
        shutil.copy(FIXTURES / "manifest.js.gz", cache / "manifest.js.gz")
        shutil.copy(FIXTURES / "lkml.atom", cache / "lkml.atom")
        crawl(self.state, CacheSource(cache), NOW, names=["lkml"], log=quiet)
        amap = self.state.load_addrs("lkml")
        self.assertEqual(amap["post"], "user1@example.test")
        entries = parse_feed((FIXTURES / "lkml.atom").read_bytes())[0]
        data = self.state.load_list("lkml")
        stored = {r["h"] for r in data["msgs"].values()}
        self.assertTrue(stored)
        self.assertTrue(set(amap["a"]) <= stored)
        self.assertGreater(len(amap["a"]), 5)
        self.assertEqual({a for a in amap["a"].values()} <= {e["email"] for e in entries}, True)


class ReplyShardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        full = series("a", 2, replies=[(1, "Looks fine.\n"), (1, "Thanks.\n", "a-r0")], name="Dev One")
        nodiff = [raw("n-0", "[PATCH 0/1] nodiff: a thing", name="No Diff", body="cover\n"),
                  raw("n-1", "[PATCH 1/1] nodiff: a thing", name="No Diff", parent="n-0", body="No diff here.\n")]
        disc = [raw("d-1", "Question about foo", name="Asker"), raw("d-2", "Re: Question about foo", name="Helper", parent="d-1")]
        cls.st, cls.tmp = make_state({"l": full + nodiff + disc})
        cls.st.save_addrs("l", "l@lists.example.test", cls.st.load_addrs("l")["a"])
        cls.st.save_manifest({"l": {"d": "l", "m": NOW, "e": [0]}})
        cls.out = Path(cls.tmp.name) / "public"
        cls.report = build_site(cls.st, cls.out, NOW, log=quiet)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self):
        meta = jload(self.out / "data/lists/l/meta.json")
        out = []
        for key, pages in meta["pages"].items():
            for i in range(pages):
                out += jload(self.out / ("data/lists/l/%s-%d.json" % (key, i)))["rows"]
        return out

    def test_a_shard_exists_for_every_displayed_thread_and_nothing_else(self):
        rows = self.rows()
        self.assertEqual(sorted(r["k"] for r in rows), ["issue", "pr"])  # the series without a diff is not shown
        shards = sorted(p.name for p in (self.out / "data/reply/l").glob("*.json.gz"))
        self.assertEqual(shards, sorted("%d.json.gz" % r["n"] for r in rows))

    def test_shard_content(self):
        pr = next(r for r in self.rows() if r["k"] == "pr")
        shard = jload(self.out / ("data/reply/l/%d.json.gz" % pr["n"]))
        d = thread_detail(self.out, "l", pr["n"])
        bodies = jload(self.out / ("data/bodies/l/%d.json.gz" % pr["n"]))["b"]
        self.assertEqual(shard["addr"], 1)
        self.assertEqual(shard["lists"], {"l": "l@lists.example.test"})
        self.assertEqual(len(shard["m"]), len(bodies))  # aligned with the bodies and the thread detail
        order = [d["cover"]] + d["patches"] + d["cm"]
        self.assertEqual([m["i"] for m in shard["m"]], [x["mid"].replace("%40", "@") for x in order])
        self.assertEqual([m["h"] for m in shard["m"]], [x["ah"] for x in order])
        by = {m["i"]: m for m in shard["m"]}
        self.assertEqual(by["a-1"]["p"], "a-0")
        self.assertEqual(by["a-r0"]["p"], "a-1")
        self.assertEqual(by["a-r1"]["p"], "a-r0")
        self.assertNotIn("p", by["a-0"])
        self.assertEqual(by["a-1"]["s"], "[PATCH 1/2] foo: add bar part 1")
        self.assertEqual(shard["who"][by["a-1"]["h"]], {"n": "Dev One", "a": "dev.one@example.test"})
        self.assertEqual(sorted(w["a"] for w in shard["who"].values()), ["dev.one@example.test", "rev0@example.test", "rev1@example.test"])

    def test_addresses_are_only_in_the_designed_files(self):
        shards = list((self.out / "data/reply").rglob("*.json.gz"))
        self.assertTrue(shards)
        self.assertTrue(any(EMAIL.search(gzip.decompress(p.read_bytes()).decode()) for p in shards))
        state_maps = list((self.st.root / "addrs").glob("*.json.gz"))
        self.assertTrue(state_maps and all(EMAIL.search(gzip.decompress(p.read_bytes()).decode()) for p in state_maps))
        allowed = {p.name for p in shards}
        for p in sorted(self.out.rglob("*")) + sorted(self.st.root.rglob("*")):
            if not p.is_file() or is_address_file(p):
                continue
            blob = p.read_bytes()
            text = (gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob).decode("utf-8", "replace")
            self.assertFalse(EMAIL.search(text), p)
        # the thread detail has the (percent encoded) message-id but never an address
        pr = next(r for r in self.rows() if r["k"] == "pr")
        self.assertNotIn("@", json.dumps(thread_detail(self.out, "l", pr["n"])))
        self.assertTrue(allowed)

    def test_the_shard_is_never_fetched_by_a_page_view(self):
        for name in ("thread.js", "files.js", "app.js", "list.js", "home.js", "people.js", "search.js", "rows.js", "util.js"):
            src = (Path(__file__).resolve().parent.parent / "site/assets" / name).read_text()
            self.assertNotIn("data/reply", src, name)

    def test_the_site_reports_the_shard_in_its_size(self):
        self.assertGreater(self.report["bytes"], sum(p.stat().st_size for p in (self.out / "data/reply").rglob("*.gz")))


class OptOutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.cache = base / "cache"
        self.cache.mkdir()
        shutil.copy(FIXTURES / "manifest.js.gz", self.cache / "manifest.js.gz")
        shutil.copy(FIXTURES / "lkml.atom", self.cache / "lkml.atom")
        shutil.copy(FIXTURES / "lkml.atom", self.cache / "lkml.win-0.atom")
        self.state, self.out = base / "state", base / "public"

    def run_cli(self, *extra, env=None):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stdout(buf):
            code = main(["all", "--state", str(self.state), "--out", str(self.out), "--lists", "lkml",
                         "--from-cache", str(self.cache), "--backfill-days", "30"] + list(extra))
        self.assertEqual(code, 0)
        return buf.getvalue()

    def reply_shards(self):
        return sorted((self.out / "data/reply").rglob("*.json.gz"))

    def test_default_collects_and_publishes(self):
        self.run_cli()
        self.assertTrue((self.state / "addrs/lkml.json.gz").exists())
        self.assertTrue(self.reply_shards())
        self.assertEqual(jload(self.reply_shards()[0])["addr"], 1)

    def collected_nothing(self):
        amap = State(self.state).load_addrs("lkml")
        self.assertEqual(amap["a"], {})
        self.assertEqual(amap["post"], "user1@example.test")  # the posting address of the list is public, not a sender
        shards = self.reply_shards()
        self.assertTrue(shards)  # the shards still carry the thread structure and the list address
        for p in shards:
            s = jload(p)
            self.assertEqual((s["addr"], s["who"]), (0, {}))
            self.assertEqual(s["lists"], {"lkml": "user1@example.test"})
            self.assertTrue(s["m"])
        sender = {e["email"] for e in parse_feed((FIXTURES / "lkml.atom").read_bytes())[0]}
        for p in list(self.out.rglob("*")) + list(Path(self.state).rglob("*")):
            if p.is_file():
                blob = p.read_bytes()
                text = (gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob).decode("utf-8", "replace")
                for addr in sender:
                    self.assertNotIn(addr, text, p)

    def test_flag_turns_collecting_and_publishing_off(self):
        self.run_cli("--no-reply-addresses")
        self.collected_nothing()

    def test_environment_variable_does_the_same(self):
        self.run_cli(env={"LOREHUB_REPLY_ADDRESSES": "off"})
        self.collected_nothing()

    def test_turning_it_off_later_removes_what_was_collected(self):
        self.run_cli()
        self.assertTrue(State(self.state).load_addrs("lkml")["a"])
        self.run_cli("--no-reply-addresses")
        self.collected_nothing()

    def test_other_values_of_the_variable_keep_it_on(self):
        self.run_cli(env={"LOREHUB_REPLY_ADDRESSES": "on"})
        self.assertTrue(State(self.state).load_addrs("lkml")["a"])


class AddressQueueTests(unittest.TestCase):
    """The thread completion queue also fetches the thread of displayed OPEN series whose participants have no stored
    address, one request each, with a cap of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name) / "state")

    def put(self, entries, name="l", addresses=False):
        self.state.reply_addresses = addresses
        _, data = ingest(self.state, name, entries, NOW, data=self.state.load_list(name) if self.state.segments(name) else {"v": 2, "name": name, "msgs": {}})
        self.state.save_list(name, data)
        self.state.reply_addresses = True

    def run_q(self, source, cfg=None, **kw):
        kw.setdefault("clock", Clock())
        return run_completion(self.state, source, NOW, cfg or Config(), log=quiet, **kw)

    def threads(self, *prefixes, **kw):
        return {p + "-0": series(p, 2, replies=[(1, "ok\n")], **kw) for p in prefixes}

    def test_open_series_without_addresses_are_scheduled_and_fill_the_map(self):
        th = self.threads("a")
        self.put(th["a-0"])
        self.assertEqual(self.state.load_addrs("l")["a"], {})
        src = ThreadSource(th)
        s = self.run_q(src)
        self.assertEqual((s["queue"], s["addr_queue"], s["requests"], s["addr_requests"]), (0, 1, 1, 1))
        self.assertEqual(src.calls, [("l", "a-0")])
        self.assertEqual(sorted(self.state.load_addrs("l")["a"].values()), ["dev@example.test", "rev0@example.test"])
        meta = self.state.load_meta()["lists"]["l"]
        self.assertEqual(meta["aq"], [])
        self.assertEqual(meta["ad"], {"a-0": NOW})
        self.assertEqual(s["addr_left"], 0)

    def test_a_series_is_fetched_once_for_addresses(self):
        th = self.threads("a")
        self.put(th["a-0"])
        self.run_q(ThreadSource(th))
        src = ThreadSource(th)
        s = self.run_q(src, touched={"l"})
        self.assertEqual((s["addr_queue"], s["requests"]), (0, 0))
        self.assertEqual(src.calls, [])

    def test_a_series_that_still_lacks_an_address_after_the_fetch_is_not_asked_again(self):
        th = self.threads("a")
        for e in th["a-0"]:
            e["email"] = "not an address"
        self.put(th["a-0"])
        self.run_q(ThreadSource(th))
        self.assertEqual(self.state.load_addrs("l")["a"], {})
        src = ThreadSource(th)
        s = self.run_q(src, touched={"l"})
        self.assertEqual((s["addr_queue"], src.calls), (0, []))

    def test_series_whose_participants_are_known_are_not_scheduled(self):
        th = self.threads("a")
        self.put(th["a-0"], addresses=True)
        s = self.run_q(ThreadSource(th))
        self.assertEqual((s["addr_queue"], s["requests"]), (0, 0))

    def test_only_open_series_are_scheduled(self):
        merged = series("m", 2, replies=[(1, "Applied, thanks.\n")])
        merged[-1]["name"] = "Maintainer"
        self.put(merged + series("o", 2, replies=[(1, "ok\n")]))
        src = ThreadSource({"m-0": merged, "o-0": series("o", 2, replies=[(1, "ok\n")])})
        s = self.run_q(src)
        self.assertEqual([c[1] for c in src.calls], ["o-0"])
        self.assertEqual(s["addr_queue"], 1)

    def test_per_run_cap_and_resume(self):
        th = self.threads(*["s%d" % i for i in range(5)])
        for i, k in enumerate(sorted(th)):
            for e in th[k]:
                e["ts"] = e["ts"] - i * 10  # newest first: s0
        self.put([e for v in th.values() for e in v])
        src = ThreadSource(th)
        s = self.run_q(src, Config(max_requests=300, max_addr_requests=2))
        self.assertEqual((s["addr_queue"], s["addr_requests"], s["addr_left"]), (5, 2, 3))
        self.assertEqual(s["stopped"], "per run cap for address requests")
        self.assertEqual(len(src.calls), 2)
        s = self.run_q(src, Config(max_requests=300, max_addr_requests=2))
        self.assertEqual((s["addr_queue"], s["addr_requests"], s["addr_left"]), (3, 2, 1))
        s = self.run_q(src, Config(max_requests=300, max_addr_requests=2))
        self.assertEqual((s["addr_queue"], s["addr_requests"], s["addr_left"]), (1, 1, 0))
        self.assertEqual(len(set(src.calls)), 5)  # each series exactly once over the three runs

    def test_the_general_request_cap_also_applies(self):
        th = self.threads("a", "b", "c")
        self.put([e for v in th.values() for e in v])
        s = self.run_q(ThreadSource(th), Config(max_requests=1, max_addr_requests=100))
        self.assertEqual((s["requests"], s["addr_left"]), (1, 2))

    def test_real_gaps_come_first_and_one_fetch_serves_both(self):
        full = series("g", 3, replies=[(1, "ok\n")])
        partial = [e for e in full if e["mid"] != "g-2"]  # patch 2/3 missing: a completion item
        th = self.threads("a")
        self.put(partial + th["a-0"])
        src = ThreadSource({"g-0": full, "a-0": th["a-0"]})
        s = self.run_q(src, Config(max_requests=300, max_addr_requests=100))
        self.assertEqual((s["queue"], s["addr_queue"]), (1, 1))  # the series with the gap is not queued twice
        self.assertEqual([c[1] for c in src.calls], ["g-0", "a-0"])
        self.assertEqual(self.state.load_meta()["lists"]["l"]["ad"].keys(), {"g-0", "a-0"})

    def test_a_cross_posted_series_is_fetched_once(self):
        th = self.threads("a")
        self.put(th["a-0"], "l")
        self.put(th["a-0"], "m")
        src = ThreadSource(th)
        s = self.run_q(src)
        self.assertEqual(s["addr_queue"], 1)
        self.assertEqual(len(src.calls), 1)

    def test_opt_out_schedules_nothing(self):
        th = self.threads("a")
        self.put(th["a-0"])
        self.state.reply_addresses = False
        src = ThreadSource(th)
        s = self.run_q(src)
        self.assertEqual((s["addr_queue"], s["requests"], src.calls), (0, 0, []))
        self.assertNotIn("aq", self.state.load_meta()["lists"]["l"])
        self.assertFalse((self.state.root / "addrs").exists())

    def test_zero_cap_turns_the_address_fetches_off(self):
        th = self.threads("a")
        self.put(th["a-0"])
        src = ThreadSource(th)
        s = self.run_q(src, Config(max_addr_requests=0))
        self.assertEqual((s["requests"], src.calls), (0, []))
        self.assertEqual(s["addr_left"], 1)

    def test_failed_fetch_is_retried_next_run(self):
        th = self.threads("a")
        self.put(th["a-0"])
        s = self.run_q(ThreadSource(th, fail={"a-0"}))
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["addr_left"], 1)
        s = self.run_q(ThreadSource(th))
        self.assertEqual((s["failed"], s["addr_left"]), (0, 0))

    def test_completion_summary_does_not_change_the_site_waiting_count(self):
        from lorehub.build import build_site as bs
        th = self.threads("a")
        self.put(th["a-0"])
        self.run_q(ThreadSource({}), Config(max_requests=0))
        self.state.save_manifest({"l": {"d": "l", "m": NOW, "e": [0]}})
        out = Path(self.tmp.name) / "public"
        bs(self.state, out, NOW, log=quiet)
        self.assertEqual(jload(out / "data/lists.json")["backfill"]["waiting"], 0)


if __name__ == "__main__":
    unittest.main()
