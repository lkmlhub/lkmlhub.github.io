"""Files changed tab in headless Chrome, on the real ipe series: rendering, Viewed memory, deep links,
patch selector, version switch, Commits tab diffstat, and no horizontal page overflow on a phone."""

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
  }).then(function () { return wait(1500); }).then(function () { return f.contentDocument; });
}
var N = __N__, K = __K__;
(function () {
  try { localStorage.clear(); } catch (e) {}
  var d;
  return load('#/kexec/pr/' + N + '?tab=files').then(function (doc) {
    d = doc;
    R.cards = d.querySelectorAll('.diff-file').length;
    R.summary = d.querySelector('.files-summary').textContent;
    R.tab = d.querySelector('a.tab[aria-current]').textContent;
    R.rows = d.querySelectorAll('.diff-table tr').length;
    R.addRows = d.querySelectorAll('tr.d-add').length;
    R.delRows = d.querySelectorAll('tr.d-del').length;
    R.hunks = d.querySelectorAll('tr.d-hunk').length;
    R.treeFiles = d.querySelectorAll('a.tree-file').length;
    R.firstPath = d.querySelector('.diff-name-link').textContent;
    R.pageOverflow = [d.documentElement.scrollWidth, d.documentElement.clientWidth];
    d.querySelector('.viewed input').click();
    R.stored = f.contentWindow.localStorage.getItem('lh-viewed:kexec/' + N);
    R.collapsedAfterViewed = d.querySelector('.diff-toggle').getAttribute('aria-expanded');
    R.progress = d.querySelector('.files-toolbar [role=status]').textContent;
    return load('#/kexec/pr/' + N + '?tab=files');
  }).then(function (doc) {
    d = doc;
    R.persisted = d.querySelector('.viewed input').checked;
    R.persistedCollapsed = d.querySelector('.diff-toggle').getAttribute('aria-expanded');
    return load('#/kexec/pr/' + N + '?tab=files&file=' + encodeURIComponent('security/ipe/keyring.c'));
  }).then(function (doc) {
    d = doc;
    var cur = d.querySelector('a.tree-file[aria-current]');
    R.deepLink = cur ? cur.getAttribute('title') : null;
    var open = d.querySelectorAll('.diff-toggle[aria-expanded=true]').length;
    R.deepLinkOpen = open > 0;
    return load('#/kexec/pr/' + N + '?tab=files&patch=3');
  }).then(function (doc) {
    d = doc;
    R.patch3 = d.querySelector('.files-summary').textContent;
    R.patchHead = !!d.querySelector('.patch-head');
    R.patchSelect = d.querySelector('#patch-select').value;
    R.commitMsg = d.querySelector('.patch-head .mail') ? d.querySelector('.patch-head .mail').textContent.length : 0;
    return load('#/kexec/pr/' + N + '?tab=commits');
  }).then(function (doc) {
    d = doc;
    R.commitStats = d.querySelectorAll('.commit .diffstat').length;
    R.changesLinks = d.querySelectorAll('.commit a[href*="tab=files&patch="]').length;
    return load('#/keyrings/pr/' + K + '?tab=files');
  }).then(function (doc) {
    d = doc;
    R.v1Summary = d.querySelector('.files-summary').textContent;
    R.v1Option = d.querySelector('#version-select').value;
    var sel = d.querySelector('#version-select');
    R.versionKeepsTab = Array.prototype.map.call(sel.options, function (o) { return o.value; });
    document.getElementById('o').textContent = 'RESULT ' + JSON.stringify(R);
  }).catch(function (e) { document.getElementById('o').textContent = 'RESULT ' + JSON.stringify({ error: String(e) }); });
})();
</script>
"""


@unittest.skipUnless(BROWSER, "no headless browser")
class FilesTabBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()
        meta = json.loads((cls.out / "data/lists/kexec/meta.json").read_text())
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
        (self.out / "driver.html").write_text(page)
        res = subprocess.run([BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=60000",
                              "--dump-dom", "http://127.0.0.1:%d/driver.html" % self.port],
                             capture_output=True, text=True, timeout=170)
        m = re.search(r"RESULT (\{.*?\})</pre>", res.stdout, re.S)
        self.assertTrue(m, res.stdout[-600:])
        return json.loads(re.sub(r"&quot;", '"', m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")))

    def test_desktop(self):
        r = self.drive(1280)
        self.assertNotIn("error", r)
        self.assertEqual(r["cards"], 13)
        self.assertTrue(r["summary"].startswith("13 files changed"))
        self.assertIn("+217", r["summary"])
        self.assertTrue(r["tab"].startswith("Files changed"))
        self.assertGreater(r["addRows"], 100)
        self.assertGreater(r["delRows"], 0)
        self.assertGreaterEqual(r["hunks"], 13)
        self.assertEqual(r["treeFiles"], 13)
        self.assertEqual(r["pageOverflow"][0], r["pageOverflow"][1])
        # Viewed: remembered in localStorage, collapses the card, survives a reload
        self.assertIn(r["firstPath"], r["stored"])
        self.assertEqual(r["collapsedAfterViewed"], "false")
        self.assertTrue(r["progress"].startswith("1 / 13"))
        self.assertTrue(r["persisted"])
        self.assertEqual(r["persistedCollapsed"], "false")
        # deep link and patch selector
        self.assertEqual(r["deepLink"], "security/ipe/keyring.c")
        self.assertTrue(r["deepLinkOpen"])
        self.assertTrue(r["patch3"].startswith("2 files changed"))
        self.assertTrue(r["patchHead"])
        self.assertEqual(r["patchSelect"], "3")
        self.assertGreater(r["commitMsg"], 100)
        # commits tab and version switch
        self.assertEqual(r["commitStats"], 3)
        self.assertEqual(r["changesLinks"], 3)
        self.assertRegex(r["v1Summary"], r"^[1-9]\d* files changed")  # the v1 page shows v1's own diff
        self.assertTrue(all("?tab=files" in v for v in r["versionKeepsTab"]))

    def test_phone_has_no_page_overflow(self):
        r = self.drive(390)
        self.assertNotIn("error", r)
        self.assertEqual(r["pageOverflow"][0], r["pageOverflow"][1])
        self.assertEqual(r["cards"], 13)


if __name__ == "__main__":
    unittest.main()
