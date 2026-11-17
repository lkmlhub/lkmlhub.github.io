/* Split diff pairing: turns the parsed lines of one hunk list (files.js parseHunks) into side by side rows, and finds
 * the changed middle of a removed/added pair. Pure functions, no DOM, so they run in a browser and in node (tests/js).
 *
 * input line:  {t: 'hunk'|'ctx'|'del'|'add'|'note', o, n, s, ix}   (ix: index of the line in the file's hunk text, kept on the cell)
 * output row:  {left, right} with cells {t: 'ctx'|'del'|'add'|'empty'|'note', no, s, hl}
 *              or {span: true, left, right} for hunk headers and notes that belong to no line (left === right). */
(function (root) {
  'use strict';
  var MAX_INTRALINE = 500;

  /* Differing middle of two lines: the common prefix and suffix are trimmed. Returns {a: [start, end], b: [start, end]}
   * (ranges of the middle in each line) or null when the lines are identical, share nothing, or are too long. */
  function intraline(a, b) {
    a = String(a); b = String(b);
    if (a === b || a.length > MAX_INTRALINE || b.length > MAX_INTRALINE) return null;
    var max = Math.min(a.length, b.length), p = 0, q = 0;
    while (p < max && a.charCodeAt(p) === b.charCodeAt(p)) p++;
    while (q < max - p && a.charCodeAt(a.length - 1 - q) === b.charCodeAt(b.length - 1 - q)) q++;
    /* never cut inside a surrogate pair */
    if (p > 0 && isHigh(a.charCodeAt(p - 1))) p--;
    if (q > 0 && isLow(a.charCodeAt(a.length - q))) q--;
    if (p + q === 0) return null;
    return { a: [p, a.length - q], b: [p, b.length - q] };
  }
  function isHigh(c) { return c >= 0xd800 && c <= 0xdbff; }
  function isLow(c) { return c >= 0xdc00 && c <= 0xdfff; }

  /* Splits lines into blocks: each is {ctx: line} or {dels: [line], adds: [line]} (a run of changes between context). */
  function eachBlock(lines, onChange) {
    var dels = [], adds = [];
    function flush() {
      if (dels.length || adds.length) onChange(dels, adds);
      dels = []; adds = [];
    }
    lines.forEach(function (l) {
      if (l.t === 'del') dels.push(l);
      else if (l.t === 'add') adds.push(l);
      else if (l.t === 'note') return;
      else flush();
    });
    flush();
  }

  /* Sets line.hl = [start, end] on the paired removed/added lines of every change block (first with first, ...). */
  function markIntraline(lines) {
    eachBlock(lines, function (dels, adds) {
      var k = Math.min(dels.length, adds.length);
      for (var i = 0; i < k; i++) {
        var r = intraline(dels[i].s, adds[i].s);
        if (r) { dels[i].hl = r.a; adds[i].hl = r.b; }
      }
    });
  }

  function cell(l, t, no) { return { t: t, no: no, s: l.s, hl: l.hl, ix: l.ix }; }
  var EMPTY = { t: 'empty' };

  function pairRows(lines) {
    var rows = [], dels = [], adds = [], delNotes = {}, addNotes = {}, last = null;

    function flush() {
      var k = Math.max(dels.length, adds.length);
      for (var i = 0; i < k; i++) {
        rows.push({
          left: i < dels.length ? cell(dels[i], 'del', dels[i].o) : EMPTY,
          right: i < adds.length ? cell(adds[i], 'add', adds[i].n) : EMPTY
        });
        var ln = delNotes[i], rn = addNotes[i];
        if (ln || rn) {
          var m = Math.max(ln ? ln.length : 0, rn ? rn.length : 0);
          for (var j = 0; j < m; j++) {
            rows.push({
              left: ln && ln[j] ? { t: 'note', s: ln[j] } : EMPTY,
              right: rn && rn[j] ? { t: 'note', s: rn[j] } : EMPTY
            });
          }
        }
      }
      dels = []; adds = []; delNotes = {}; addNotes = {};
    }
    function push(map, i, s) { (map[i] = map[i] || []).push(s); }

    markIntraline(lines);
    lines.forEach(function (l) {
      if (l.t === 'del') { dels.push(l); last = 'del'; }
      else if (l.t === 'add') { adds.push(l); last = 'add'; }
      else if (l.t === 'note') {
        /* the marker belongs to the line before it: a removed line (left), an added line (right) or context (both) */
        if (last === 'del') push(delNotes, dels.length - 1, l.s);
        else if (last === 'add') push(addNotes, adds.length - 1, l.s);
        else if (last === 'ctx') rows.push({ left: { t: 'note', s: l.s }, right: { t: 'note', s: l.s } });
        else { var c = { t: 'note', s: l.s }; rows.push({ span: true, left: c, right: c }); }
      } else {
        flush();
        if (l.t === 'hunk') { var hc = { t: 'hunk', s: l.s }; rows.push({ span: true, left: hc, right: hc }); last = 'hunk'; }
        else { rows.push({ left: cell(l, 'ctx', l.o), right: cell(l, 'ctx', l.n) }); last = 'ctx'; }
      }
    });
    flush();
    return rows;
  }

  var api = { pairRows: pairRows, intraline: intraline, markIntraline: markIntraline, MAX_INTRALINE: MAX_INTRALINE };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.LHDiffPair = api;
})(typeof window !== 'undefined' ? window : globalThis);
