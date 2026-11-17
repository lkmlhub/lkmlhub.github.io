"""Unified / Split switch of the Files changed tab in headless Chrome: the split table of the real ipe series,
the layout preference (localStorage and the URL parameter), the narrow screen fallback, and a synthetic series with
a very large file and very wide lines (chunked rendering, no page overflow)."""

import functools
import http.server
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from lorehub.build import build_site

from . import ipe_fixture
from .helpers import NOW, make_state, raw

BROWSER = shutil.which("google-chrome") or shutil.which("chromium")

PAGE = r"""<!doctype html><meta charset=utf-8><body style="margin:0"><pre id=o>waiting</pre>
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
function all(d, sel) { return Array.prototype.slice.call(d.querySelectorAll(sel)); }
function seg(d, name) { return all(d, '.seg-btn').filter(function (b) { return b.textContent === name; })[0]; }
function store() { return f.contentWindow.localStorage.getItem('lkmlhub-diff-layout'); }
function overflow(d) { return [d.documentElement.scrollWidth, d.documentElement.clientWidth]; }
function tables(d) { return { split: all(d, 'table.diff-split').length, unified: all(d, 'table.diff-table').length }; }
function kinds(d) {
  var m = {};
  all(d, 'tr.d-split').forEach(function (tr) {
    var c = tr.querySelectorAll('td.code');
    var k = c[0].className.replace(/.*sp-/, '') + '/' + c[1].className.replace(/.*sp-/, '');
    m[k] = (m[k] || 0) + 1;
  });
  return m;
}
(function () {
__BODY__
})().then(function () {
  document.getElementById('o').textContent = 'RESULT ' + JSON.stringify(R);
}, function (e) { document.getElementById('o').textContent = 'RESULT ' + JSON.stringify({ error: String(e && e.stack || e) }); });
</script>
"""

IPE_WIDE = r"""
var N = __N__;
try { localStorage.clear(); } catch (e) {}
return load('#/kexec/pr/' + N + '?tab=files&layout=split').then(function (d) {
  R.tables = tables(d);
  R.cards = all(d, '.diff-file').length;
  R.rows = all(d, 'tr.d-split').length;
  R.rowShape = all(d, 'tr.d-split').every(function (tr) {
    return tr.children.length === 4 && tr.children[0].classList.contains('ln') && tr.children[1].classList.contains('code') &&
      tr.children[2].classList.contains('ln') && tr.children[3].classList.contains('code');
  });
  R.kinds = kinds(d);
  R.hunkSpan = all(d, 'table.diff-split tr.d-hunk').every(function (tr) { return tr.querySelector('td.code').getAttribute('colspan') === '3'; });
  R.hunks = all(d, 'table.diff-split tr.d-hunk').length;
  R.emptyLn = all(d, 'tr.d-split td.ln.sp-empty').every(function (td) { return !td.hasAttribute('data-n'); });
  R.leftNums = all(d, 'tr.d-split td.ln:not(.sp-right)[data-n]').length;
  R.rightNums = all(d, 'tr.d-split td.ln.sp-right[data-n]').length;
  R.addCells = all(d, 'td.code.sp-add').length;
  R.delCells = all(d, 'td.code.sp-del').length;
  R.ihSplit = all(d, 'td.code .ih').length;
  R.pageOverflow = overflow(d);
  R.scrollers = all(d, '.diff-scroll').map(function (s) { return [getComputedStyle(s).overflowX, s.scrollWidth > s.clientWidth]; });
  R.pressed = [seg(d, 'Unified').getAttribute('aria-pressed'), seg(d, 'Split').getAttribute('aria-pressed')];
  R.disabled = seg(d, 'Split').disabled;
  R.storedAfterUrlLoad = store();
  R.linkHref = d.querySelector('.diff-name-link').getAttribute('href');
  R.treeHref = d.querySelector('a.tree-file').getAttribute('href');
  seg(d, 'Unified').click();
  R.afterUnified = { tables: tables(d), stored: store(), hash: f.contentWindow.location.hash,
    pressed: [seg(d, 'Unified').getAttribute('aria-pressed'), seg(d, 'Split').getAttribute('aria-pressed')],
    addRows: all(d, 'tr.d-add').length, delRows: all(d, 'tr.d-del').length, ctxRows: all(d, 'tr.d-ctx').length,
    ih: all(d, 'td.code .ih').length, overflow: overflow(d), linkHref: d.querySelector('.diff-name-link').getAttribute('href') };
  d.querySelector('.viewed input').click();      // collapse the first card while in unified
  R.collapsed = d.querySelector('.diff-toggle').getAttribute('aria-expanded');
  seg(d, 'Split').click();
  R.afterSplit = { tables: tables(d), stored: store(), hash: f.contentWindow.location.hash,
    firstCollapsed: d.querySelector('.diff-toggle').getAttribute('aria-expanded') };
  d.querySelector('.diff-toggle').click();       // reopening the card builds it in the current layout
  var first = all(d, '.diff-file')[0];
  R.reopened = { split: first.querySelectorAll('table.diff-split').length, unified: first.querySelectorAll('table.diff-table').length };
  seg(d, 'Split').focus();
  R.focusOnSeg = d.activeElement === seg(d, 'Split');
  return load('#/kexec/pr/' + N + '?tab=files');   // stored preference, no parameter
}).then(function (d) {
  R.storedWins = { tables: tables(d), pressed: seg(d, 'Split').getAttribute('aria-pressed') };
  return load('#/kexec/pr/' + N + '?tab=files&layout=unified');   // URL beats the stored split
}).then(function (d) {
  R.urlWins = { tables: tables(d), pressed: seg(d, 'Unified').getAttribute('aria-pressed'), stored: store() };
  return load('#/kexec/pr/' + N + '?tab=files&layout=bogus');
}).then(function (d) {
  R.bogus = tables(d);   // an invalid value is ignored: the stored split applies
  f.contentWindow.localStorage.removeItem('lkmlhub-diff-layout');
  return load('#/kexec/pr/' + N + '?tab=files');
}).then(function (d) {
  R.defaultLayout = { tables: tables(d), stored: store() };
  return load('#/kexec/pr/' + N + '?tab=files&layout=split&file=' + encodeURIComponent('security/ipe/keyring.c'));
}).then(function (d) {
  var cur = d.querySelector('a.tree-file[aria-current]');
  R.deepLink = cur ? cur.getAttribute('title') : null;
  R.deepLinkSplit = all(d, 'table.diff-split').length > 0;
  R.hashKeepsLayout = f.contentWindow.location.hash;
});
"""

IPE_NARROW = r"""
var N = __N__;
try { localStorage.clear(); localStorage.setItem('lkmlhub-diff-layout', 'split'); } catch (e) {}
return load('#/kexec/pr/' + N + '?tab=files&layout=split').then(function (d) {
  R.tables = tables(d);
  R.cards = all(d, '.diff-file').length;
  var b = seg(d, 'Split');
  R.switchState = { disabled: b.disabled, unifiedDisabled: seg(d, 'Unified').disabled, title: d.querySelector('.seg').title,
    pressed: [seg(d, 'Unified').getAttribute('aria-pressed'), b.getAttribute('aria-pressed')] };
  R.stored = store();
  R.pageOverflow = overflow(d);
  b.click();    // disabled: nothing happens
  R.afterClick = tables(d);
  return load('#/kexec/pr/' + N + '?tab=files');
}).then(function (d) {
  R.storedKept = { stored: store(), tables: tables(d) };
});
"""

BREAKPOINT = r"""
var N = __N__;
try { localStorage.clear(); } catch (e) {}
return load('#/kexec/pr/' + N + '?tab=files&layout=split').then(function (d) {
  R.tables = tables(d); R.disabled = seg(d, 'Split').disabled;
});
"""

BIG = r"""
var N = __N__;
try { localStorage.clear(); } catch (e) {}
return load('#/big/pr/' + N + '?tab=files&layout=split').then(function (d) {
  R.rows = all(d, '.diff-file')[0].querySelectorAll('tr').length;
  R.more = all(d, '.diff-more button').map(function (b) { return b.textContent; });
  R.overflow = overflow(d);
  R.cards = all(d, '.diff-file').length;
  all(d, '.diff-toggle[aria-expanded=false]').forEach(function (b) { b.click(); });   // the files after the big one are collapsed
  R.notes = all(d, 'p.diff-note').map(function (p) { return p.textContent; });
  d.querySelector('.diff-more button').click();
  R.rows2 = all(d, '.diff-file')[0].querySelectorAll('tr').length;
  R.more2 = all(d, '.diff-more button').map(function (x) { return x.textContent; });
  R.scrollW = all(d, '.diff-scroll').map(function (s) { return [s.scrollWidth, s.clientWidth]; });
  R.overflow2 = overflow(d);
  return load('#/big/pr/' + N + '?tab=files&layout=unified');
}).then(function (d) {
  R.uRows = all(d, '.diff-file')[0].querySelectorAll('tr').length;
  R.uMore = all(d, '.diff-more button').map(function (b) { return b.textContent; });
  R.uOverflow = overflow(d);
});
"""


class Served:
    """Serve a built site directory on a free local port and drive pages in headless Chrome."""

    def __init__(self, out):
        self.out = Path(out)
        Quiet = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a, **k: None})
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(self.out)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def drive(self, width, body, **subst):
        for k, v in subst.items():
            body = body.replace("__%s__" % k, str(v))
        (self.out / "driver-layout.html").write_text(PAGE.replace("__W__", str(width)).replace("__BODY__", body))
        profile = tempfile.mkdtemp()
        try:
            res = subprocess.run([BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=60000",
                                  "--user-data-dir=" + profile, "--dump-dom",
                                  "http://127.0.0.1:%d/driver-layout.html" % self.port],
                                 capture_output=True, text=True, timeout=170)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        m = re.search(r"RESULT (\{.*?\})</pre>", res.stdout, re.S)
        assert m, res.stdout[-600:]
        return json.loads(re.sub(r"&quot;", '"', m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")))


@unittest.skipUnless(BROWSER, "no headless browser")
class SplitLayoutIpeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()
        cls.n = json.loads((cls.out / "data/lists/kexec/pr-open-0.json").read_text())["rows"][0]["n"]
        cls.site = Served(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()
        cls.tmp.cleanup()

    def test_desktop_split_and_preference(self):
        r = self.site.drive(1280, IPE_WIDE, N=self.n)
        self.assertNotIn("error", r)
        # the split table: paired rows, two gutters, placeholders, spanning hunk headers
        self.assertGreaterEqual(r["tables"]["split"], r["cards"])   # a file touched by two patches has two tables
        self.assertEqual(r["tables"]["unified"], 0)
        self.assertGreater(r["rows"], 100)
        self.assertTrue(r["rowShape"])
        self.assertTrue(r["hunkSpan"])
        self.assertGreaterEqual(r["hunks"], 13)
        k = r["kinds"]
        ctx = k.get("ctx/ctx", 0)
        self.assertGreater(ctx, 10)
        self.assertGreater(k.get("empty/add", 0), 50)      # added only blocks leave the left side empty
        self.assertGreater(k.get("del/add", 0), 0)         # changed lines are paired
        self.assertTrue(r["emptyLn"])
        self.assertGreater(r["leftNums"], 0)
        self.assertGreater(r["rightNums"], r["leftNums"])
        self.assertEqual(r["leftNums"], ctx + r["delCells"])
        self.assertEqual(r["rightNums"], ctx + r["addCells"])
        self.assertGreater(r["ihSplit"], 0)
        # width: the page never overflows and, like GitHub, the split halves wrap long lines so no card needs a scrollbar
        self.assertEqual(r["pageOverflow"][0], r["pageOverflow"][1])
        self.assertTrue(all(s[0] == "auto" for s in r["scrollers"]))
        self.assertFalse(any(s[1] for s in r["scrollers"]), r["scrollers"])
        # the control
        self.assertEqual(r["pressed"], ["false", "true"])
        self.assertFalse(r["disabled"])
        self.assertIsNone(r["storedAfterUrlLoad"], "a layout in the URL must not be written to the stored preference")
        self.assertIn("layout=split", r["linkHref"])
        self.assertIn("layout=split", r["treeHref"])
        # toggle to unified: same lines, preference stored, URL updated, links follow
        u = r["afterUnified"]
        self.assertEqual(u["tables"]["split"], 0)
        self.assertGreater(u["tables"]["unified"], 0)
        self.assertEqual(u["stored"], "unified")
        self.assertIn("layout=unified", u["hash"])
        self.assertIn("tab=files", u["hash"])
        self.assertEqual(u["pressed"], ["true", "false"])
        self.assertEqual(u["addRows"], r["addCells"])
        self.assertEqual(u["delRows"], r["delCells"])
        self.assertEqual(u["ctxRows"], ctx)
        self.assertEqual(u["ih"], r["ihSplit"])
        self.assertEqual(u["overflow"][0], u["overflow"][1])
        self.assertIn("layout=unified", u["linkHref"])
        # back to split: a collapsed card stays collapsed and is built in the new layout when reopened
        self.assertEqual(r["collapsed"], "false")
        s = r["afterSplit"]
        self.assertEqual(s["stored"], "split")
        self.assertIn("layout=split", s["hash"])
        self.assertEqual(s["firstCollapsed"], "false")
        self.assertGreater(s["tables"]["split"], 0)
        self.assertEqual(s["tables"]["unified"], 0)
        self.assertEqual(r["reopened"], {"split": 1, "unified": 0})
        self.assertTrue(r["focusOnSeg"])
        # preference: stored split applies without a parameter, the URL parameter wins, invalid values are ignored
        self.assertGreater(r["storedWins"]["tables"]["split"], 0)
        self.assertEqual(r["storedWins"]["tables"]["unified"], 0)
        self.assertEqual(r["storedWins"]["pressed"], "true")
        self.assertEqual(r["urlWins"]["tables"]["split"], 0)
        self.assertGreater(r["urlWins"]["tables"]["unified"], 0)
        self.assertEqual(r["urlWins"]["pressed"], "true")
        self.assertEqual(r["urlWins"]["stored"], "split")
        self.assertGreater(r["bogus"]["split"], 0)
        self.assertEqual(r["defaultLayout"]["tables"]["split"], 0)
        self.assertGreater(r["defaultLayout"]["tables"]["unified"], 0)
        self.assertIsNone(r["defaultLayout"]["stored"])
        # deep link keeps working in split
        self.assertEqual(r["deepLink"], "security/ipe/keyring.c")
        self.assertTrue(r["deepLinkSplit"])
        self.assertIn("layout=split", r["hashKeepsLayout"])

    def test_phone_shows_unified_and_disables_the_switch(self):
        r = self.site.drive(390, IPE_NARROW, N=self.n)
        self.assertNotIn("error", r)
        self.assertEqual(r["tables"]["split"], 0)
        self.assertGreater(r["tables"]["unified"], 0)
        self.assertEqual(r["cards"], 13)
        self.assertTrue(r["switchState"]["disabled"])
        self.assertTrue(r["switchState"]["unifiedDisabled"])
        self.assertIn("900", r["switchState"]["title"])
        self.assertEqual(r["switchState"]["pressed"], ["true", "false"])
        self.assertEqual(r["stored"], "split")                        # the preference is kept
        self.assertEqual(r["pageOverflow"][0], r["pageOverflow"][1])
        self.assertEqual(r["afterClick"]["split"], 0)
        self.assertEqual(r["storedKept"]["stored"], "split")
        self.assertEqual(r["storedKept"]["tables"]["split"], 0)

    def test_breakpoint_is_900px(self):
        wide = self.site.drive(900, BREAKPOINT, N=self.n)
        narrow = self.site.drive(899, BREAKPOINT, N=self.n)
        self.assertGreater(wide["tables"]["split"], 0)
        self.assertFalse(wide["disabled"])
        self.assertEqual(narrow["tables"]["split"], 0)
        self.assertTrue(narrow["disabled"])


@unittest.skipUnless(BROWSER, "no headless browser")
class SplitLayoutLargeFileTests(unittest.TestCase):
    ROWS = 5000

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        lines = "".join("+line %d\n" % i for i in range(cls.ROWS))
        wide = "x" * 3000
        body = (
            "Big.\n\nSigned-off-by: Alice Author <alice@example.test>\n---\n\n"
            "diff --git a/big.c b/big.c\n--- a/big.c\n+++ b/big.c\n@@ -1,4 +1,%d @@\n a\n-old %s\n+new %s\n%s b\n c\n"
            "\\ No newline at end of file\n"
            "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
            "diff --git a/old.c b/new.c\nsimilarity index 100%%\nrename from old.c\nrename to new.c\n"
            % (cls.ROWS + 4, wide, wide + "y", lines)
        )
        st, tmp = make_state({"big": [raw("big-1", "[PATCH] big file", body=body)]})
        cls.tmp2 = tmp
        st.save_manifest({"big": {"d": "big", "m": NOW, "e": [0]}})
        cls.out = Path(cls.tmp.name) / "public"
        build_site(st, cls.out, NOW, log=lambda m: None)
        cls.n = json.loads((cls.out / "data/lists/big/pr-open-0.json").read_text())["rows"][0]["n"]
        cls.site = Served(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()
        cls.tmp.cleanup()
        cls.tmp2.cleanup()

    def test_large_file_is_rendered_in_chunks_and_wide_lines_wrap_in_split(self):
        r = self.site.drive(1280, BIG, N=self.n)
        self.assertNotIn("error", r)
        self.assertEqual(r["cards"], 3)
        self.assertEqual(r["rows"], 2000)
        self.assertEqual(len(r["more"]), 1)
        self.assertIn("Show 2000 more lines", r["more"][0])
        self.assertEqual(r["rows2"], 4000)
        self.assertEqual(len(r["more2"]), 1)
        self.assertEqual(r["overflow"][0], r["overflow"][1])
        self.assertEqual(r["overflow2"][0], r["overflow2"][1])
        # binary and rename-only files are plain notes above both columns
        self.assertTrue(any("Binary file not shown" in t for t in r["notes"]), r["notes"])
        self.assertTrue(any("renamed without changes" in t for t in r["notes"]), r["notes"])
        # split lines wrap, so no card gets wider than its box (allow 1px rounding)
        self.assertTrue(all(w[0] <= w[1] + 1 for w in r["scrollW"]), r["scrollW"])
        # the unified layout is chunked as well
        self.assertEqual(r["uRows"], 2000)
        self.assertEqual(len(r["uMore"]), 1)
        self.assertEqual(r["uOverflow"][0], r["uOverflow"][1])


if __name__ == "__main__":
    unittest.main()
