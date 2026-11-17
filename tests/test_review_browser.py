"""The review feature in a real headless Chrome, driven with real mouse and keyboard events through the DevTools protocol
(tests/js/cdp.js, tests/js/review_e2e.js): line comments in both layouts, the review dialog, the prepared e-mails compared
with golden files, downloads, reload, discard, identity, the reply composer, a phone, no localStorage, hostile input."""

import functools
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lorehub.build import build_site

from . import ipe_fixture
from .helpers import NOW, make_state, raw
from .test_build import XssBuildTests

BROWSER = shutil.which("google-chrome") or shutil.which("chromium")
NODE = shutil.which("node")


def node_major():
    try:
        return int(subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=20).stdout.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


if NODE and node_major() < 22:   # the DevTools client uses the built in WebSocket of node 22 and later
    NODE = None
E2E = Path(__file__).parent / "js" / "review_e2e.js"
FIXTURES = Path(__file__).parent / "fixtures" / "review"


def serve(directory):
    Quiet = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a, **k: None})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_scenario(base, name, tmp, extra=""):
    res = subprocess.run([NODE, str(E2E), BROWSER, base, name, str(FIXTURES), tmp, "", extra], capture_output=True, text=True, timeout=170)
    return name, res.returncode, res.stdout + res.stderr


@unittest.skipUnless(BROWSER and NODE, "no headless browser or node")
class ReviewFlowTests(unittest.TestCase):
    SCENARIOS = ["flow-unified", "flow-split", "reply", "request", "message", "mobile", "nostorage"]

    @classmethod
    def setUpClass(cls):
        cls.ipe, _, cls.out, _, _ = ipe_fixture.build()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = serve(cls.out)
        base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(run_scenario, base, n, cls.tmp.name) for n in cls.SCENARIOS]
            cls.results = {n: (rc, out) for n, rc, out in (f.result() for f in futs)}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.ipe.cleanup()
        cls.tmp.cleanup()

    def check(self, name):
        rc, out = self.results[name]
        self.assertEqual(rc, 0, out[-3000:])
        self.assertIn("OK " + name, out)

    def test_unified_layout_comment_dialog_prepare_download_reload_discard(self):
        self.check("flow-unified")

    def test_split_layout_comment_dialog_prepare_download_reload_discard(self):
        self.check("flow-split")

    def test_reply_composer_of_the_conversation_tab(self):
        self.check("reply")

    def test_request_changes_with_an_explicit_nacked_by_for_one_patch(self):
        self.check("request")

    def test_comment_on_the_commit_message_quotes_it_in_the_reply(self):
        self.check("message")

    def test_phone_layout_of_the_comment_box_and_the_dialogs(self):
        self.check("mobile")

    def test_without_local_storage_comments_live_in_memory_with_a_notice(self):
        self.check("nostorage")


@unittest.skipUnless(BROWSER and NODE, "no headless browser or node")
class ReviewHostileInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        a, b, c = XssBuildTests.PAYLOADS
        entries = [
            raw("evil-0", "[PATCH 0/1] " + a, name=b, email="mal@example.test", body="cover " + c + "\n"),
            raw("evil-1", "[PATCH 1/1] " + b, name=b, email="mal@example.test", parent="evil-0", body=XssBuildTests.hostile_patch(a, b, c)),
            raw("evil-r", "Re: [PATCH 1/1] x", name=c, email="mal2@example.test", parent="evil-1", t=NOW - 60,
                body="> " + a + "\nReviewed-by: " + b + " <x@example.test>\n" + c + "\n"),
        ]
        st, cls.state_tmp = make_state({"xss": entries})
        st.save_addrs("xss", "xss@lists.example.test", st.load_addrs("xss")["a"])
        st.save_manifest({"xss": {"d": a + b, "m": NOW, "e": [0]}})
        cls.out = Path(cls.tmp.name) / "public"
        build_site(st, cls.out, NOW, log=lambda m: None)
        meta = json.loads((cls.out / "data/lists/xss/meta.json").read_text())
        key = next(k for k in meta["pages"] if k.startswith("pr-"))
        cls.n = json.loads((cls.out / ("data/lists/xss/%s-0.json" % key)).read_text())["rows"][0]["n"]
        cls.srv = serve(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()
        cls.state_tmp.cleanup()

    def test_hostile_names_subjects_diff_lines_and_comments_stay_text(self):
        base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        name, rc, out = run_scenario(base, "xss", self.tmp.name, str(self.n))
        self.assertEqual(rc, 0, out[-3000:])
        self.assertIn("OK xss", out)


if __name__ == "__main__":
    unittest.main()
