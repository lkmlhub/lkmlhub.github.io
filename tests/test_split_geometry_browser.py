"""Geometry of the Split diff in headless Chrome, measured from the DOM: four logical columns, equal code columns, equal row
heights, a +/- marker outside the text flow, wrapped lines that start at the same edge as the first line, tab size, one very long
token, unselectable numbers and markers, one-sided selection, wide container, a tree that does not starve the code, sticky headers.
Uses the ipe series (real data) and a synthetic file with the awkward lines."""

import json
import tempfile
import unittest
from pathlib import Path

from lorehub.build import build_site

from . import ipe_fixture
from .helpers import NOW, make_state, raw
from .test_diff_layout_browser import BROWSER, Served

WORDS = " ".join("word%03d" % i for i in range(90))            # 719 characters, breaks only at the spaces
TAB_WORDS = "\tif (" + " ".join("tab%03d" % i for i in range(60)) + ")"
TOKEN_PAIR = ("Q" * 150, "T" * 400)
LONE_TOKEN = "Z" * 300

COMMON = r"""
function textOf(td) { return td.firstChild; }
function lineBoxes(tn) {
  var lines = [], last = null, r = tn.ownerDocument.createRange();
  for (var i = 0; i < tn.length; i++) {
    r.setStart(tn, i); r.setEnd(tn, i + 1);
    var q = r.getClientRects()[0];
    if (!q) continue;
    var top = Math.round(q.top);
    if (!last || top !== last.top) { last = { top: top, left: q.left, text: '', w: q.width }; lines.push(last); }
    last.text += tn.data.charAt(i);
  }
  return lines;
}
function cellWith(d, start) {
  return all(d, 'tr.d-split td.code').filter(function (td) { return td.textContent.indexOf(start) === 0; })[0];
}
function box(el) { var r = el.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; }
function rowsGeometry(d) {
  var rows = all(d, 'tr.d-split'), out = { rows: rows.length, codeDiff: 0, heightDiff: 0, lnMin: 999, lnMax: 0, bad: 0 };
  rows.forEach(function (tr) {
    var c = tr.children;
    if (c.length !== 4) { out.bad++; return; }
    var w = [1, 3].map(function (i) { return c[i].getBoundingClientRect().width; });
    out.codeDiff = Math.max(out.codeDiff, Math.abs(w[0] - w[1]));
    [0, 2].forEach(function (i) { var x = c[i].getBoundingClientRect().width; out.lnMin = Math.min(out.lnMin, x); out.lnMax = Math.max(out.lnMax, x); });
    var hs = [0, 1, 2, 3].map(function (i) { return c[i].getBoundingClientRect().height; });
    out.heightDiff = Math.max(out.heightDiff, Math.max.apply(null, hs) - Math.min.apply(null, hs));
  });
  return out;
}
function layoutInfo(d) {
  var app = d.getElementById('app'), tree = d.querySelector('.files-tree'), t = d.querySelector('table.diff-split');
  var first = t.querySelector('tr.d-split');
  return { app: box(app), treeHidden: tree.hidden, tree: box(tree), table: box(t), cardW: box(d.querySelector('.diff-file')).width,
    half: [box(first.children[1]).width, box(first.children[3]).width], docOverflow: [d.documentElement.scrollWidth, d.documentElement.clientWidth],
    scrollers: all(d, '.diff-scroll').map(function (s) { return s.scrollWidth - s.clientWidth; }) };
}
"""

IPE_GEOM = COMMON + r"""
var N = __N__;
try { localStorage.clear(); } catch (e) {}
return load('#/kexec/pr/' + N + '?tab=files&layout=split').then(function (d) {
  R.g = rowsGeometry(d);
  R.l = layoutInfo(d);
  R.treeLabel = d.querySelector('.files-toolbar .btn[aria-controls]').textContent;
  var td = all(d, 'tr.d-split td.code.sp-add')[0];
  var cs = f.contentWindow.getComputedStyle(td);
  R.font = { size: cs.fontSize, lh: cs.lineHeight, tab: cs.tabSize, ws: cs.whiteSpace, ff: cs.fontFamily };
});
"""

WRAP_GEOM = COMMON + r"""
var N = __N__, WORDS = __WORDS__, TABW = __TABW__, TP = __TP__, LONE = __LONE__;
try { localStorage.clear(); } catch (e) {}
return load('#/wrap/pr/' + N + '?tab=files&layout=split').then(function (d) {
  var win = f.contentWindow, gs = function (el, pseudo) { return win.getComputedStyle(el, pseudo || null); };
  R.g = rowsGeometry(d);
  R.l = layoutInfo(d);
  // the long line of words: lines start at the same edge, break only at spaces
  var wc = cellWith(d, 'word000'), wcs = gs(wc), cl = box(wc).left + parseFloat(wcs.paddingLeft);
  var wl = lineBoxes(textOf(wc));
  R.words = { n: wl.length, lefts: wl.map(function (x) { return Math.round((x.left - cl) * 10) / 10; }),
    brokenWords: wl.slice(0, -1).filter(function (x) { return !/ $/.test(x.text); }).length, padL: parseFloat(wcs.paddingLeft),
    before: { pos: gs(wc, '::before').position, sel: gs(wc, '::before').userSelect, content: gs(wc, '::before').content },
    scrollOver: wc.scrollWidth - wc.clientWidth };
  var row = wc.parentNode;
  R.wordsRow = { h: [0, 1, 2, 3].map(function (i) { return row.children[i].getBoundingClientRect().height; }),
    leftBg: gs(row.children[1]).backgroundColor, leftText: row.children[1].textContent, leftLines: lineBoxes(textOf(row.children[1])).length,
    leftLnBg: gs(row.children[0]).backgroundColor, rightLnBg: gs(row.children[2]).backgroundColor, rightBg: gs(wc).backgroundColor };
  // tab indented long line: the tab is 4 columns, continuation lines start at the text edge like GitHub
  var tc = cellWith(d, '\tif (tab000'), tl = lineBoxes(textOf(tc)), tcl = box(tc).left + parseFloat(gs(tc).paddingLeft);
  R.tab = { n: tl.length, first: tl[0].left - tcl, textStart: tl[0].left - tcl, cw: wl[0].w, rest: tl.slice(1).map(function (x) { return Math.round((x.left - tcl) * 10) / 10; }) };
  var tn = textOf(tc), rg = d.createRange(); rg.setStart(tn, 1); rg.setEnd(tn, 2);
  R.tab.afterTab = rg.getClientRects()[0].left - tcl;
  // one long token: wraps inside the cell, nothing sticks out
  var kc = cellWith(d, 'ZZZZ'), kl = lineBoxes(textOf(kc)), kcl = box(kc).left + parseFloat(gs(kc).paddingLeft);
  R.token = { n: kl.length, lefts: kl.map(function (x) { return Math.round((x.left - kcl) * 10) / 10; }), over: kc.scrollWidth - kc.clientWidth,
    right: box(kc).left + box(kc).width, textRight: Math.max.apply(null, kl.map(function (x) { return x.left + x.w * x.text.length; })),
    rowEmptyLeft: kc.parentNode.children[1].className, emptyBg: gs(kc.parentNode.children[1]).backgroundColor,
    emptyImg: gs(kc.parentNode.children[1]).backgroundImage, emptyH: [box(kc.parentNode.children[1]).height, box(kc).height] };
  // a pair with different wrap counts: both cells are as tall as the taller one and keep their colours
  var qc = cellWith(d, 'QQQQ'), qr = qc.parentNode, tcell = qr.children[3];
  R.pair = { left: lineBoxes(textOf(qc)).length, right: lineBoxes(textOf(tcell)).length, hs: [0, 1, 2, 3].map(function (i) { return qr.children[i].getBoundingClientRect().height; }),
    leftBg: gs(qc).backgroundColor, rightBg: gs(tcell).backgroundColor };
  // numbers and markers cannot be selected, the font is the GitHub one
  var ln = all(d, 'tr.d-split td.ln[data-n]')[0];
  R.sel = { ln: gs(ln).userSelect, lnAlign: gs(ln).textAlign, addMarker: gs(wc.parentNode.children[3], '::before').userSelect };
  var addCell = cellWith(d, 'word000'), delCell = cellWith(d, 'short old line');
  R.marker = { pos: gs(addCell, '::before').position, content: gs(addCell, '::before').content, delContent: gs(delCell, '::before').content,
    left: gs(addCell, '::before').left, padL: gs(addCell).paddingLeft };
  // one sided selection: a press in the left half makes the right code unselectable and the other way round
  var table = wc.closest('table'), lcell = cellWith(d, 'short old line'), rcell = wc;
  function press(el) { el.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, cancelable: true })); }
  press(lcell);
  R.selLeft = { cls: table.className, left: gs(lcell).userSelect, right: gs(rcell).userSelect, rightLn: gs(rcell.previousSibling).userSelect };
  press(rcell);
  R.selRight = { cls: table.className, left: gs(lcell).userSelect, right: gs(rcell).userSelect };
  press(table.querySelector('tr.d-hunk .code'));
  R.selHunk = table.className;
  // hunk header: bluish full width row with an icon cell
  var hk = table.querySelector('tr.d-hunk');
  R.hunk = { cells: hk.children.length, span: hk.children[1].getAttribute('colspan'), icon: hk.children[0].querySelectorAll('svg').length,
    bg: gs(hk.children[1]).backgroundColor, ctxBg: gs(cellWith(d, 'ctx 00')).backgroundColor, w0: box(hk.children[0]).width,
    textLeft: gs(hk.children[1]).textAlign, textW: box(hk.children[1]).width, tableW: box(table).width };
  // sticky file header: stays at the top of the window while the file scrolls under it
  var head = d.querySelector('.diff-file-head'), cardTop = d.querySelector('.diff-file').getBoundingClientRect().top + win.pageYOffset;
  win.scrollTo(0, cardTop + 300);
  R.sticky = { pos: gs(head).position, top: head.getBoundingClientRect().top, bgAlpha: gs(head).backgroundColor };
  win.scrollTo(0, 0);
});
"""


def wrap_body():
    ctx = lambda a, b: [" ctx %02d" % i for i in range(a, b)]

    # block builder: (old lines, new lines) with proper counts
    left, right = [], []
    out = []

    def c(i):
        out.append((" ", "ctx %02d" % i)); left.append(1); right.append(1)

    for i in range(8):
        c(i)
    out.append(("-", "short old line")); out.append(("+", WORDS))
    for i in range(8, 11):
        c(i)
    out.append(("+", TAB_WORDS))
    for i in range(11, 14):
        c(i)
    out.append(("-", TOKEN_PAIR[0])); out.append(("+", TOKEN_PAIR[1])); out.append(("+", LONE_TOKEN))
    for i in range(14, 14 + 80):
        c(i)
    n_old = sum(1 for k, _ in out if k in " -")
    n_new = sum(1 for k, _ in out if k in " +")
    text = "".join(k + s + "\n" for k, s in out)
    return ("Wrapping.\n\nSigned-off-by: Alice Author <alice@example.test>\n---\n\n"
            "diff --git a/wrap.c b/wrap.c\n--- a/wrap.c\n+++ b/wrap.c\n@@ -1,%d +1,%d @@ void wrap(void)\n%s" % (n_old, n_new, text))


@unittest.skipUnless(BROWSER, "no headless browser")
class SplitGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.state, cls.out, cls.source, cls.report = ipe_fixture.build()
        cls.n = json.loads((cls.out / "data/lists/kexec/pr-open-0.json").read_text())["rows"][0]["n"]
        cls.site = Served(cls.out)
        cls.tmp2 = tempfile.TemporaryDirectory()
        st, cls.tmp3 = make_state({"wrap": [raw("wrap-1", "[PATCH] wrapping lines", body=wrap_body())]})
        st.save_manifest({"wrap": {"d": "wrap", "m": NOW, "e": [0]}})
        cls.out2 = Path(cls.tmp2.name) / "public"
        build_site(st, cls.out2, NOW, log=lambda m: None)
        cls.n2 = json.loads((cls.out2 / "data/lists/wrap/pr-open-0.json").read_text())["rows"][0]["n"]
        cls.site2 = Served(cls.out2)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()
        cls.site2.close()
        cls.tmp.cleanup()
        cls.tmp2.cleanup()
        cls.tmp3.cleanup()

    def wrap(self, width):
        body = (WRAP_GEOM.replace("__WORDS__", json.dumps(WORDS)).replace("__TABW__", json.dumps(TAB_WORDS))
                .replace("__TP__", json.dumps(TOKEN_PAIR)).replace("__LONE__", json.dumps(LONE_TOKEN)))
        r = self.site2.drive(width, body, N=self.n2)
        self.assertNotIn("error", r)
        return r

    def check_columns(self, r, width):
        g, l = r["g"], r["l"]
        self.assertGreater(g["rows"], 50)
        self.assertEqual(g["bad"], 0)
        self.assertLessEqual(g["codeDiff"], 2, "the two code columns are equally wide")
        self.assertLessEqual(g["heightDiff"], 1, "both halves of a row have the same height")
        self.assertTrue(48 <= g["lnMin"] and g["lnMax"] <= 54, (g["lnMin"], g["lnMax"]))      # about 50 px number columns
        self.assertLessEqual(abs(l["half"][0] - l["half"][1]), 2)
        self.assertGreaterEqual(min(l["half"]), 450, "each half has at least 450 px at %d" % width)
        self.assertEqual(l["docOverflow"][0], l["docOverflow"][1])
        self.assertTrue(all(x <= 1 for x in l["scrollers"]), "no horizontal scrollbar in the cards")
        # the table fills the card
        self.assertGreaterEqual(l["table"]["width"], l["cardW"] - 4)

    def test_wrapping_cells_marker_and_columns_at_1400(self):
        r = self.wrap(1400)
        self.check_columns(r, 1400)
        l = r["l"]
        self.assertGreaterEqual(l["app"]["width"], 1250, "the Files page uses a wide container")
        self.assertLessEqual(l["tree"]["width"], 260)
        # the words line wraps at spaces only and every line starts at the same edge, the marker is not in the flow
        w = r["words"]
        self.assertGreaterEqual(w["n"], 8)
        self.assertEqual(w["brokenWords"], 0, "words are not split")
        self.assertTrue(all(abs(x) <= 0.6 for x in w["lefts"]), w["lefts"])    # hanging indent equals the first line's indent
        self.assertGreaterEqual(w["padL"], 20)
        self.assertEqual(w["before"]["pos"], "absolute")
        self.assertEqual(w["before"]["sel"], "none")
        self.assertEqual(w["scrollOver"], 0)
        m = r["marker"]
        self.assertEqual(m["pos"], "absolute")
        self.assertIn("+", m["content"])
        self.assertIn("-", m["delContent"])
        self.assertLess(float(m["left"][:-2]), float(m["padL"][:-2]))        # the marker sits in the padding, left of the text
        # short left side next to the long right side: same height, colour kept for the full height
        wr = r["wordsRow"]
        self.assertLessEqual(max(wr["h"]) - min(wr["h"]), 1)
        self.assertGreaterEqual(wr["h"][1], 8 * 20)
        self.assertEqual(wr["leftLines"], 1)
        self.assertNotIn(wr["leftBg"], ("rgba(0, 0, 0, 0)", "transparent"))
        self.assertNotEqual(wr["leftBg"], wr["rightBg"])
        self.assertNotEqual(wr["leftLnBg"], wr["rightLnBg"])
        # tab
        t = r["tab"]
        self.assertGreaterEqual(t["n"], 5)
        self.assertAlmostEqual(t["first"], 0, delta=0.6)            # the tab itself starts at the text edge
        self.assertAlmostEqual(t["afterTab"], 4 * t["cw"], delta=1.5)    # tab-size 4
        self.assertTrue(all(abs(x) <= 0.6 for x in t["rest"]), t["rest"])    # continuation lines start at the text edge (GitHub)
        # one very long token breaks inside the cell, left side is an empty hatched cell of full height
        k = r["token"]
        self.assertGreaterEqual(k["n"], 4)
        self.assertTrue(all(abs(x) <= 0.6 for x in k["lefts"]), k["lefts"])
        self.assertEqual(k["over"], 0)
        self.assertLessEqual(k["textRight"], k["right"] + 1)
        self.assertIn("sp-empty", k["rowEmptyLeft"])
        self.assertNotEqual(k["emptyImg"], "none")
        self.assertLessEqual(abs(k["emptyH"][0] - k["emptyH"][1]), 1)
        # pairs with different wrap counts
        p = r["pair"]
        self.assertGreater(p["right"], p["left"] + 2)
        self.assertLessEqual(max(p["hs"]) - min(p["hs"]), 1)
        self.assertGreaterEqual(p["hs"][1], p["right"] * 20)
        self.assertNotEqual(p["leftBg"], p["rightBg"])
        self.assertNotIn(p["leftBg"], ("rgba(0, 0, 0, 0)", "transparent"))

    def test_selection_and_hunk_header_and_sticky(self):
        r = self.wrap(1400)
        s = r["sel"]
        self.assertEqual(s["ln"], "none")
        self.assertEqual(s["lnAlign"], "right")
        self.assertEqual(s["addMarker"], "none")
        self.assertIn("sel-l", r["selLeft"]["cls"])
        self.assertEqual(r["selLeft"]["right"], "none")
        self.assertNotEqual(r["selLeft"]["left"], "none")
        self.assertEqual(r["selLeft"]["rightLn"], "none")
        self.assertIn("sel-r", r["selRight"]["cls"])
        self.assertEqual(r["selRight"]["left"], "none")
        self.assertNotEqual(r["selRight"]["right"], "none")
        self.assertNotIn("sel-", r["selHunk"])
        h = r["hunk"]
        self.assertEqual((h["cells"], h["span"], h["icon"]), (2, "3", 1))
        self.assertNotEqual(h["bg"], h["ctxBg"])
        self.assertAlmostEqual(h["w0"], 50, delta=2)
        self.assertEqual(h["textLeft"], "start")
        self.assertAlmostEqual(h["textW"] + h["w0"], h["tableW"], delta=2)
        self.assertEqual(r["sticky"]["pos"], "sticky")
        self.assertAlmostEqual(r["sticky"]["top"], 0, delta=1)

    def test_other_widths_keep_the_halves_equal_and_wide_enough(self):
        for width in (1100, 1700):
            r = self.wrap(width)
            self.check_columns(r, width)
            if width == 1100:
                self.assertTrue(r["l"]["treeHidden"], "the tree starts collapsed when it would squeeze the halves")
            else:
                self.assertFalse(r["l"]["treeHidden"])
        # on a window of 1400 the tree stays open and still leaves 450 px per half (checked by check_columns above)

    def test_ipe_series_rows_and_font(self):
        r = self.site.drive(1400, IPE_GEOM, N=self.n)
        self.assertNotIn("error", r)
        self.check_columns(r, 1400)
        self.assertFalse(r["l"]["treeHidden"])
        self.assertEqual(r["treeLabel"], "Hide file tree")
        self.assertGreaterEqual(r["l"]["app"]["width"], 1250)
        f = r["font"]
        self.assertEqual((f["size"], f["lh"], f["tab"], f["ws"]), ("12px", "20px", "4", "pre-wrap"))
        self.assertIn("monospace", f["ff"])

    def test_ipe_series_at_1100_and_1700(self):
        for width in (1100, 1700):
            r = self.site.drive(width, IPE_GEOM, N=self.n)
            self.assertNotIn("error", r)
            self.check_columns(r, width)
            self.assertEqual(r["l"]["treeHidden"], width == 1100)


if __name__ == "__main__":
    unittest.main()
