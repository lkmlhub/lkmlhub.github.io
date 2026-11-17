"""Conversation tab in headless Chrome on the real ipe series: every message of the thread is on the page,
replies nested under the patch they answer, quotes collapsed, the participants list, the message count, and
no horizontal overflow or overlapping cards at desktop and phone widths."""

import functools
import http.server
import json
import re
import shutil
import subprocess
import threading
import unittest

from . import ipe_fixture

BROWSER = shutil.which("google-chrome") or shutil.which("chromium")

DRIVER = r"""<!doctype html><meta charset=utf-8><body style="margin:0"><pre id=o>waiting</pre>
<iframe id=f style="width:__W__px;height:900px;border:0"></iframe>
<script>
var f = document.getElementById('f'), R = {};
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function load(hash) {
  f.src = 'about:blank';
  return wait(50).then(function () {
    return new Promise(function (res) { f.onload = res; f.src = 'index.html' + hash; });
  }).then(function () { return wait(2000); }).then(function () { return f.contentDocument; });
}
function layout(d) {
  var cards = Array.prototype.slice.call(d.querySelectorAll('article.comment, article.patch-event'));
  var rects = cards.map(function (c) { var r = c.getBoundingClientRect(); return [r.top + f.contentWindow.scrollY, r.bottom + f.contentWindow.scrollY, r.left, r.right]; });
  var overlaps = 0;
  for (var i = 1; i < rects.length; i++) if (rects[i][0] < rects[i - 1][1] - 1 && rects[i][0] > rects[i - 1][0]) overlaps++;
  var wide = 0;
  cards.forEach(function (c, i) { if (rects[i][3] > d.documentElement.clientWidth + 1) wide++; });
  return { cards: cards.length, overlaps: overlaps, wide: wide, overflow: [d.documentElement.scrollWidth, d.documentElement.clientWidth] };
}
function page(d) {
  var out = layout(d);
  out.count = d.querySelector('.msg-count').textContent;
  out.sub = d.querySelector('.thread-sub').textContent;
  out.tabs = Array.prototype.map.call(d.querySelectorAll('nav.tabs a'), function (a) { return a.textContent; });
  out.patchCards = Array.prototype.map.call(d.querySelectorAll('article.patch-event .commit-head strong'), function (s) { return s.textContent; });
  out.comments = d.querySelectorAll('article.comment').length;
  out.quotes = d.querySelectorAll('details.quote').length;
  out.quotesOpen = d.querySelectorAll('details.quote[open]').length;
  out.replyBoxes = d.querySelectorAll('.replies').length;
  out.replyCtx = d.querySelectorAll('.reply-ctx').length;
  out.participants = d.querySelectorAll('.participants li').length;
  out.participantsTitle = Array.prototype.map.call(d.querySelectorAll('.thread-side h3'), function (h) { return h.textContent; });
  out.showFull = d.querySelectorAll('button.show-full').length;
  out.loreLinks = d.querySelectorAll('a[href^="https://lore.kernel.org/"]').length;
  out.trailers = d.querySelectorAll('.comment-head .chip-reviewed, .comment-head .chip-acked, .comment-head .chip-tested').length;
  var firstPatch = d.querySelector('article.patch-event');
  var next = firstPatch && firstPatch.nextElementSibling;
  out.repliesUnderFirstPatch = !!(next && next.classList.contains('replies') && next.querySelector('article.comment'));
  var order = Array.prototype.map.call(d.querySelectorAll('article.comment, article.patch-event'), function (c) { return c.className; });
  out.patchBeforeItsReplies = order.indexOf('patch-event') >= 0;
  out.note = !!d.querySelector('.coverage-note');
  return out;
}
(function () {
  var d;
  return load('#/kexec/pr/__N__').then(function (doc) {
    d = doc; R.v2 = page(d);
    return load('#/keyrings/pr/__K__');
  }).then(function (doc) {
    d = doc; R.v1 = page(d);
    var det = d.querySelector('.mail-outer details.quote');
    var outer = det.closest('.mail-outer');
    var btn = outer.querySelector('button.show-full');
    btn.click();
    R.opened = outer.querySelectorAll('details.quote[open]').length;
    R.btnText = btn.textContent;
    var link = d.querySelector('a.reply-ctx');
    R.ctxText = link ? link.textContent : '';
    return load('#/keyrings/pr/__K__?tab=commits');
  }).then(function (doc) {
    R.commits = doc.querySelectorAll('.commit').length;
    return load('#/keyrings/pr/__K__?tab=files');
  }).then(function (doc) {
    R.files = doc.querySelectorAll('.diff-file').length;
    R.filesNotice = !!doc.querySelector('[data-notice=partial-diffs]');
    R.emptyState = !!doc.querySelector('[data-empty=no-diff]');
    document.getElementById('o').textContent = 'RESULT ' + JSON.stringify(R);
  }).catch(function (e) { document.getElementById('o').textContent = 'RESULT ' + JSON.stringify({ error: String(e) }); });
})();
</script>
"""


@unittest.skipUnless(BROWSER, "no headless browser")
class ConversationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()
        cls.n = json.loads((cls.out / "data/lists/kexec/pr-open-0.json").read_text())["rows"][0]["n"]
        rows = json.loads((cls.out / "data/lists/keyrings/pr-closed-0.json").read_text())["rows"]
        cls.k = next(r["n"] for r in rows if r["t"].startswith("IPE"))
        Quiet = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a, **k: None})
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(cls.out)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def drive(self, width):
        page = DRIVER.replace("__W__", str(width)).replace("__N__", str(self.n)).replace("__K__", str(self.k))
        (self.out / "driver2.html").write_text(page)
        res = subprocess.run([BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=90000",
                              "--dump-dom", "http://127.0.0.1:%d/driver2.html" % self.port],
                             capture_output=True, text=True, timeout=170)
        m = re.search(r"RESULT (\{.*?\})</pre>", res.stdout, re.S)
        self.assertTrue(m, res.stdout[-600:])
        return json.loads(re.sub(r"&quot;", '"', m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")))

    def check(self, r):
        self.assertNotIn("error", r)
        v1, v2 = r["v1"], r["v2"]
        # the complete conversation: v1 has 1 cover + 4 patches + 26 replies, v2 1 + 3 + 6
        self.assertIn("31 messages (26 replies)", v1["sub"])
        self.assertIn("10 messages (6 replies)", v2["sub"])
        self.assertEqual(v1["cards"], 31)  # every message of the thread is a card on the page
        self.assertEqual(v2["cards"], 10)
        self.assertEqual(v1["comments"], 27)  # cover letter + 26 replies, the 4 patches are patch cards
        self.assertEqual(v2["comments"], 7)
        self.assertEqual(len(v1["patchCards"]), 4)
        self.assertEqual(len(v2["patchCards"]), 3)
        self.assertEqual(v1["patchCards"][0], "ipe: add the .ipe policy keyring")
        self.assertEqual(v2["tabs"][0][:12], "Conversation")
        self.assertTrue(any(t.startswith("Files changed") for t in v1["tabs"]))
        self.assertGreaterEqual(v1["quotes"], 5)  # long quotes are collapsed
        self.assertEqual(v1["quotesOpen"], 0)
        self.assertGreater(v1["showFull"], 0)
        self.assertGreater(v1["replyBoxes"], 0)  # replies are nested under what they answer
        self.assertGreaterEqual(v1["replyCtx"], 20)  # "in reply to <author>"
        self.assertTrue(v1["repliesUnderFirstPatch"] or v2["repliesUnderFirstPatch"])
        self.assertGreaterEqual(v1["participants"], 3)
        self.assertTrue(any("participant" in t for t in v1["participantsTitle"]))
        self.assertGreater(v1["loreLinks"], 20)
        self.assertGreaterEqual(r["opened"], 1)  # "Show full message" opens the quotes of that message
        self.assertIn("Show less", r["btnText"])
        self.assertIn("in reply to", r["ctxText"])
        self.assertEqual(r["commits"], 4)
        self.assertGreater(r["files"], 3)
        self.assertFalse(r["filesNotice"])
        self.assertFalse(r["emptyState"])  # the defensive empty state is not reachable in a normal build
        for v in (v1, v2):
            self.assertEqual(v["overlaps"], 0)
            self.assertEqual(v["wide"], 0)
            self.assertEqual(v["overflow"][0], v["overflow"][1])
            self.assertFalse(v["note"])  # nothing is waiting: the thread is complete

    def test_desktop(self):
        self.check(self.drive(1280))

    def test_phone(self):
        self.check(self.drive(390))


if __name__ == "__main__":
    unittest.main()
