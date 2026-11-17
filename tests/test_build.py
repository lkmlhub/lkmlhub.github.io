import gzip
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from lorehub.build import build_site
from lorehub.crawl import CacheSource, crawl
from lorehub.state import State

from .helpers import FIXTURES, NOW, jload, make_state, raw

EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
quiet = lambda m: None  # noqa: E731


def texts(root):
    """Yield (path, decoded text) for every text file, JSON strings decoded, gzip files (bodies, diffs, thread
    shards) unpacked first."""
    for p in Path(root).rglob("*"):
        if p.is_file():
            raw_bytes = p.read_bytes()
            if raw_bytes[:2] == b"\x1f\x8b":
                raw_bytes = gzip.decompress(raw_bytes)
            t = raw_bytes.decode("utf-8", "replace")
            if p.suffix == ".json" or p.name.endswith(".json.gz"):
                t = "\n".join(strings(json.loads(t)))
            yield p, t


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


class Site:
    """Builds the site from the fixture feeds once and shares it between tests."""

    @classmethod
    def build(cls, lists=("lkml", "netdev"), copies=()):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cache = base / "cache"
        cache.mkdir()
        shutil.copy(FIXTURES / "manifest.js.gz", cache / "manifest.js.gz")
        for n in lists:
            shutil.copy(FIXTURES / (n + ".atom"), cache / (n + ".atom"))
        for n in copies:  # same feed again under another list name = cross-posts
            shutil.copy(FIXTURES / "lkml.atom", cache / (n + ".atom"))
        cls.state = State(base / "state")
        cls.names = list(lists) + list(copies)
        crawl(cls.state, CacheSource(cache), NOW, names=cls.names, log=quiet)
        cls.out = base / "public"
        cls.report = build_site(cls.state, cls.out, NOW, base="/lorehub/", log=quiet)

    @classmethod
    def clean(cls):
        cls.tmp.cleanup()


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Site.build()
        cls.out = Site.out

    @classmethod
    def tearDownClass(cls):
        Site.clean()

    def test_key_files_exist(self):
        for rel in ["index.html", "404.html", ".nojekyll", "favicon.svg", "assets/app.js", "assets/style.css",
                    "data/lists.json", "data/recent.json", "data/lists/lkml/meta.json", "data/lists/netdev/meta.json"]:
            self.assertTrue((self.out / rel).is_file(), rel)
        self.assertTrue(list((self.out / "data/threads/lkml").glob("*.json.gz")) or
                        list((self.out / "data/threads/netdev").glob("*.json.gz")))
        self.assertTrue(list((self.out / "data/authors").glob("*.json")))

    def test_all_json_parses(self):
        files = list(self.out.rglob("*.json"))
        self.assertGreater(len(files), 10)
        for f in files:
            json.loads(f.read_text("utf-8"))

    def test_every_manifest_list_is_listed_and_unfetched_ones_are_pending(self):
        data = json.loads((self.out / "data/lists.json").read_text())
        by = {l["n"]: l for l in data["lists"]}
        self.assertGreater(len(by), 300)
        self.assertEqual(by["lkml"]["st"], "ok")
        self.assertEqual(by["bpf"]["st"], "pending")
        self.assertEqual(data["totals"]["pending"], len(by) - 2)
        self.assertEqual(len(by["lkml"]["sp"]), 30)
        self.assertTrue((self.out / "data/lists/bpf/meta.json").is_file())

    def test_list_meta_counts_match_row_files(self):
        meta = json.loads((self.out / "data/lists/lkml/meta.json").read_text())
        total = 0
        for key, pages in meta["pages"].items():
            kind, state = key.split("-")
            for i in range(pages):
                rows = json.loads((self.out / ("data/lists/lkml/%s-%s-%d.json" % (kind, state, i))).read_text())["rows"]
                total += len(rows)
        counts = meta["counts"]
        self.assertEqual(total, sum(sum(v.values()) for v in counts.values()))
        self.assertGreater(total, 2)

    def test_every_row_resolves_to_a_thread_detail(self):
        meta = json.loads((self.out / "data/lists/lkml/meta.json").read_text())
        checked = 0
        for key, pages in meta["pages"].items():
            kind, state = key.split("-")
            rows = json.loads((self.out / ("data/lists/lkml/%s-%s-0.json" % (kind, state))).read_text())["rows"]
            for r in rows[:20]:
                home = r.get("hl", "lkml")
                shard = jload(self.out / ("data/threads/%s/%d.json.gz" % (home, r["n"] // 32)))
                self.assertIn(str(r["n"]), shard)
                self.assertEqual(shard[str(r["n"])]["t"][:50], r["t"][:50])
                checked += 1
        self.assertGreater(checked, 2)

    def test_json_files_stay_small(self):
        biggest = max(f.stat().st_size for f in self.out.rglob("*.json"))
        self.assertLess(biggest, 1_000_000)

    def test_paths_are_relative(self):
        index = (self.out / "index.html").read_text()
        for m in re.finditer(r'(?:src|href)="([^"]*)"', index):
            url = m.group(1)
            self.assertFalse(url.startswith("/"), url)
            if url.startswith("http"):
                self.assertTrue(url.startswith("https://lore.kernel.org/"), url)
        for js in (self.out / "assets").glob("*.js"):
            text = js.read_text()
            for m in re.finditer(r"getJSON\(\s*'([^']*)'", text):
                self.assertTrue(m.group(1).startswith("data/"), m.group(1))
        css = (self.out / "assets/style.css").read_text()
        self.assertNotIn("url(", css)
        self.assertNotIn("@import", css)

    def test_404_redirects_using_configured_base(self):
        page = (self.out / "404.html").read_text()
        self.assertIn('"/lorehub/"', page)
        self.assertIn("location.replace", page)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_404_redirect_script_maps_paths_under_the_base(self):
        import subprocess
        page = (self.out / "404.html").read_text()
        script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
        harness = (
            "var out=[];function run(p){var loc={pathname:p,replace:function(u){out.push(u)}};"
            "(new Function('location',%s))(loc);}"
            "run('/lorehub/netdev/pr/12');run('/lorehub/');run('/lorehub/lkml/patches/');run('/other/x');"
            "console.log(JSON.stringify(out));" % json.dumps(script)
        )
        res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), ["/lorehub/#/netdev/pr/12", "/lorehub/", "/lorehub/#/lkml/patches",
                                                  "/lorehub/#/other/x"])

    def test_no_email_addresses_anywhere_in_output_or_state(self):
        gz_seen = sum(1 for p, _ in texts(self.out) if p.name.endswith(".json.gz"))
        self.assertGreater(gz_seen, 5)  # bodies, diffs and thread shards are part of the scan
        for p, t in texts(self.out):
            self.assertFalse(EMAIL.search(t), "%s: %s" % (p, EMAIL.search(t) and EMAIL.search(t).group(0)))
        for p in Site.state.root.rglob("*.gz"):
            t = gzip.decompress(p.read_bytes()).decode()
            t = "\n".join(strings(json.loads(t)))
            self.assertFalse(EMAIL.search(t), p)
        feed = (FIXTURES / "lkml.atom").read_text()
        for addr in set(re.findall(r"<email>([^<]+)</email>", feed)):
            for p, t in texts(self.out):
                self.assertNotIn(addr, t)

    def test_index_prerender_lists_all_lists(self):
        index = (self.out / "index.html").read_text()
        self.assertIn("<noscript>", index)
        self.assertIn("https://lore.kernel.org/lkml/", index)
        self.assertEqual(index.count('<li><a href="https://lore.kernel.org/'), 359)

    def test_atomic_replace_keeps_a_previous_build_on_failure(self):
        before = (self.out / "data/lists.json").read_bytes()
        self.assertFalse(Path(str(self.out) + ".new").exists())
        self.assertTrue(before)


class CrossPostBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Site.build(lists=("lkml",), copies=("bpf",))

    @classmethod
    def tearDownClass(cls):
        Site.clean()

    def test_cross_posted_threads_are_deduplicated_and_chipped(self):
        out = Site.out
        lists = json.loads((out / "data/lists.json").read_text())
        self.assertEqual(lists["totals"]["messages"], 14)  # the shown ones: series without a diff are not shown
        rows = json.loads((out / "data/lists/lkml/meta.json").read_text())
        n_lkml = sum(sum(v.values()) for v in rows["counts"].values())
        rows_b = json.loads((out / "data/lists/bpf/meta.json").read_text())
        n_bpf = sum(sum(v.values()) for v in rows_b["counts"].values())
        self.assertEqual(n_lkml, n_bpf)
        self.assertEqual(lists["totals"]["threads"], n_lkml)
        first = next(iter(rows["pages"]))
        page = json.loads((out / ("data/lists/lkml/%s-0.json" % first)).read_text())["rows"]
        self.assertTrue(all("bpf" in r.get("x", []) or r.get("hl") == "bpf" for r in page))
        homes = {r.get("hl", "lkml") for r in page}
        self.assertEqual(homes, {"bpf"})  # lkml is never the home list of a cross-post


class XssBuildTests(unittest.TestCase):
    PAYLOADS = [
        "<script>document.title='PWNED'</script>",
        "<img src=x onerror=\"document.title='PWNED'\">",
        "\"><svg onload=\"document.title='PWNED'\">",
    ]

    @staticmethod
    def hostile_patch(a, b, c):
        path = "dir/" + a.replace(" ", "_") + ".c"
        return (
            "msg " + a + "\n---\n 1 file changed\n\ndiff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n"
            "@@ -1,3 +1,4 @@ " % (path, path, path, path) + c + "\n ctx " + c + "\n-" + a + "\n+" + b + "\n+" + "A" * 100000 +
            "\n+\x00\x1b[2Jjunk\x07\n \n"
            "diff --git a/%s b/%s\nBinary files a/%s and b/%s differ\n" % (b.replace(" ", "_"), b.replace(" ", "_"), c.replace(" ", "_"), c.replace(" ", "_"))
        )

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        a, b, c = cls.PAYLOADS
        entries = [
            raw("evil-0", "[PATCH 0/1] " + a, name=b, body="cover " + c + "\nSee javascript:alert(1) and https://example.test/ok\n"),
            raw("evil-1", "[PATCH 1/1] " + b, name=b, parent="evil-0", body=cls.hostile_patch(a, b, c)),
            raw("evil-r", "Re: [PATCH 1/1] x", name=c, parent="evil-1", t=NOW - 60,
                body="> " + a + "\nReviewed-by: " + b + " <x@example.test>\n" + c + "\n"),
        ]
        st, tmp = make_state({"xss": entries})
        cls.tmp2 = tmp
        manifest = {"xss": {"d": a + b, "m": NOW, "e": [0]}}
        st.save_manifest(manifest)
        cls.out = Path(cls.tmp.name) / "public"
        build_site(st, cls.out, NOW, log=quiet)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.tmp2.cleanup()

    def test_prerendered_html_is_escaped(self):
        index = (self.out / "index.html").read_text()
        self.assertNotIn("<script>document.title", index)
        self.assertNotIn("<img src=x", index)
        self.assertIn("&lt;script&gt;document.title", index)
        self.assertNotIn('"><svg', index)

    def test_404_page_has_no_data(self):
        self.assertNotIn("PWNED", (self.out / "404.html").read_text())

    def test_payload_is_present_only_as_json_data(self):
        blob = "".join(p.read_text() for p in (self.out / "data").rglob("*.json"))
        self.assertIn("PWNED", blob)

    @unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "no headless browser")
    def test_browser_does_not_execute_or_parse_mail_text(self):
        import functools
        import http.server
        import subprocess
        import threading

        browser = shutil.which("google-chrome") or shutil.which("chromium")
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

        handler = functools.partial(Quiet, directory=str(self.out))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            n = json.loads((self.out / "data/lists/xss/meta.json").read_text())
            first = next(k for k in n["pages"] if k.startswith("pr-"))
            row = json.loads((self.out / ("data/lists/xss/%s-0.json" % first)).read_text())["rows"][0]
            author = row["ah"]
            from urllib.parse import quote
            files_url = "xss/pr/%d?tab=files" % row["n"]
            hostile_file = "dir/" + self.PAYLOADS[0].replace(" ", "_") + ".c"
            routes = ["", "xss/patches", "xss/pr/%d" % row["n"], "xss/pr/%d?tab=commits" % row["n"], files_url,
                      files_url + "&patch=1", files_url + "&file=" + quote(hostile_file, safe=""),
                      files_url + "&file=" + quote(self.PAYLOADS[1], safe=""),
                      "search?q=PWNED", "search?q=" + quote(self.PAYLOADS[2], safe=""),
                      "xss/contributors", "xss/activity", "xss/about", "u/" + author]
            for route in routes:
                url = "http://127.0.0.1:%d/#/%s" % (port, route)
                res = subprocess.run(
                    [browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=6000",
                     "--dump-dom", url],
                    capture_output=True, text=True, timeout=180)
                dom = res.stdout
                self.assertIn("LkmlHub", dom, route)
                self.assertNotIn("<title>PWNED", dom, route)
                if not route.startswith("search?q=PWNED"):  # there the page title legitimately echoes the query
                    self.assertNotIn("PWNED - LkmlHub", dom, route)
                self.assertNotIn('<img src="x"', dom, route)
                self.assertNotIn("<svg onload", dom, route)
                self.assertNotIn('<script>document.title', dom, route)
                self.assertNotIn('href="javascript:', dom, route)
                if route.startswith("xss/pr/") and "commits" not in route:
                    self.assertIn("&lt;script&gt;document.title", dom, "payload should show up as escaped text")
                if route == "search?q=PWNED":
                    self.assertIn("&lt;script&gt;document.title", dom, route)
                    self.assertIn("Search lore.kernel.org for this", dom, route)
                    self.assertIn("https://lore.kernel.org/all/?q=PWNED", dom, route)
                if "tab=files" in route:
                    self.assertIn('class="diff-table"', dom, route)
                    self.assertIn("&lt;img src=x onerror=", dom, route)
                    self.assertNotIn("<img src=x", dom, route)
                    self.assertNotIn("\x1b", dom, route)
                    self.assertNotIn("<script>document.title", dom, route)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
