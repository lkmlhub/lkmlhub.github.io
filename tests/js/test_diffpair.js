const assert = require('assert');
const load = require('./load');
const w = load(['util.js', 'files.js', 'diffpair.js']);
const P = require('../../site/assets/diffpair.js');
assert.ok(w.LHDiffPair && typeof w.LHDiffPair.pairRows === 'function');  // also reachable as a browser global
const parse = w.LH.filesTab.parseHunks;

// compact view of a row: "<left>|<right>" with t:no:text, empty cells as "-"
function c(x) { return x.t === 'empty' ? '-' : x.t === 'hunk' ? 'H:' + x.s : x.t === 'note' ? 'N' : x.t[0] + ':' + x.no + ':' + x.s; }
function show(rows) { return rows.map((r) => (r.span ? 'S ' + c(r.left) : c(r.left) + ' | ' + c(r.right))); }
function pair(text) { return show(P.pairRows(parse(text))); }
function eq(actual, expected) { assert.deepStrictEqual(actual, expected); }

// context only: both sides, own line numbers
eq(pair('@@ -3,2 +7,2 @@\n a\n b'), ['S H:@@ -3,2 +7,2 @@', 'c:3:a | c:7:a', 'c:4:b | c:8:b']);

// equal counts: paired by position
eq(pair('@@ -1,2 +1,2 @@\n-a\n-b\n+c\n+d'), ['S H:@@ -1,2 +1,2 @@', 'd:1:a | a:1:c', 'd:2:b | a:2:d']);

// more removed than added: the right side gets placeholders
eq(pair('@@ -1,3 +1 @@\n-a\n-b\n-c\n+d'), ['S H:@@ -1,3 +1 @@', 'd:1:a | a:1:d', 'd:2:b | -', 'd:3:c | -']);

// more added than removed
eq(pair('@@ -1 +1,3 @@\n-a\n+b\n+c\n+d'), ['S H:@@ -1 +1,3 @@', 'd:1:a | a:1:b', '- | a:2:c', '- | a:3:d']);

// added only and removed only
eq(pair('@@ -4,0 +5,2 @@\n+x\n+y'), ['S H:@@ -4,0 +5,2 @@', '- | a:5:x', '- | a:6:y']);
eq(pair('@@ -4,2 +3,0 @@\n-x\n-y'), ['S H:@@ -4,2 +3,0 @@', 'd:4:x | -', 'd:5:y | -']);

// interleaved blocks keep their order, numbers follow each side
eq(pair('@@ -1,5 +1,5 @@\n a\n-b\n+B\n c\n-d\n-e\n+E\n f'), [
  'S H:@@ -1,5 +1,5 @@', 'c:1:a | c:1:a', 'd:2:b | a:2:B', 'c:3:c | c:3:c', 'd:4:d | a:4:E', 'd:5:e | -', 'c:6:f | c:5:f']);

// two hunks: header rows span both columns and restart the numbers
eq(pair('@@ -1 +1 @@\n-a\n+b\n@@ -20 +20 @@\n-c\n+d'), [
  'S H:@@ -1 +1 @@', 'd:1:a | a:1:b', 'S H:@@ -20 +20 @@', 'd:20:c | a:20:d']);
assert.strictEqual(P.pairRows(parse('@@ -1 +1 @@\n-a\n+b'))[0].span, true);

// no newline marker stays on its side
eq(pair('@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b'), ['S H:@@ -1 +1 @@', 'd:1:a | a:1:b', 'N | -']);
eq(pair('@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file'), ['S H:@@ -1 +1 @@', 'd:1:a | a:1:b', '- | N']);
eq(pair('@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file'), ['S H:@@ -1 +1 @@', 'd:1:a | a:1:b', 'N | N']);
eq(pair('@@ -1,2 +1,2 @@\n a\n b\n\\ No newline at end of file'), ['S H:@@ -1,2 +1,2 @@', 'c:1:a | c:1:a', 'c:2:b | c:2:b', 'N | N']);
// a marker after the first of several removed lines stays under that line, not under the last one
eq(pair('@@ -1,2 +1 @@\n-a\n\\ No newline at end of file\n-b\n+c'), ['S H:@@ -1,2 +1 @@', 'd:1:a | a:1:c', 'N | -', 'd:2:b | -']);
// a marker with nothing before it spans both columns
eq(pair('\\ No newline at end of file'), ['S N']);

// empty hunk list and empty text
eq(P.pairRows([]), []);
eq(show(P.pairRows(parse(''))), ['c:0: | c:0:']);   // the one empty line that parseHunks makes of '' is context, not a crash

// a hostile content line is data: kept verbatim, nothing is interpreted, no prototype pollution
const evil = '<img src=x onerror=alert(1)>\u0000\u202e__proto__ constructor toString';
let rows = P.pairRows(parse('@@ -1 +1 @@\n-' + evil + '\n+<script>alert(1)</script>'));
assert.strictEqual(rows[1].left.s, evil);
assert.strictEqual(rows[1].right.s, '<script>alert(1)</script>');
assert.strictEqual(typeof rows[1].left.hl === 'undefined' || Array.isArray(rows[1].left.hl), true);
assert.strictEqual(Object.prototype.polluted, undefined);
rows = P.pairRows([{ t: '__proto__', s: 'x' }, { t: 'constructor', s: 'y' }, { t: 'add', n: 1, s: 'z' }]);
assert.ok(Array.isArray(rows));
assert.strictEqual(rows[rows.length - 1].right.s, 'z');

// the empty placeholder carries no line number or text
rows = P.pairRows(parse('@@ -1 +1,2 @@\n-a\n+b\n+c'));
assert.deepStrictEqual(rows[2].left, { t: 'empty' });

// intra-line highlight: common prefix and suffix are trimmed
assert.deepStrictEqual(P.intraline('foo(a, b)', 'foo(a, c)'), { a: [7, 8], b: [7, 8] });
assert.deepStrictEqual(P.intraline('int x = 1;', 'int xyz = 1;'), { a: [5, 5], b: [5, 7] });   // pure insertion: empty middle on the left
assert.deepStrictEqual(P.intraline('hello world', 'hello'), { a: [5, 11], b: [5, 5] });
assert.strictEqual(P.intraline('same', 'same'), null);
assert.strictEqual(P.intraline('abc', 'xyz'), null);                  // nothing in common
assert.strictEqual(P.intraline('', 'x'), null);
const long = 'a'.repeat(501);
assert.strictEqual(P.intraline(long + 'x', long + 'y'), null);        // too long
assert.notStrictEqual(P.intraline('a'.repeat(499) + 'x', 'a'.repeat(499) + 'y'), null);
// never splits a surrogate pair
const r = P.intraline('a\ud83d\ude00b', 'a\ud83d\ude01b');
assert.deepStrictEqual(r, { a: [1, 3], b: [1, 3] });
assert.strictEqual('a\ud83d\ude00b'.slice(r.a[0], r.a[1]), '\ud83d\ude00');
const r2 = P.intraline('\ud83d\ude00x', '\ud83d\ude01x');
assert.deepStrictEqual(r2, { a: [0, 2], b: [0, 2] });
// combining characters and RTL text are plain code units
assert.deepStrictEqual(P.intraline('e\u0301a', 'e\u0301b'), { a: [2, 3], b: [2, 3] });

// intra-line marks follow the pairing: first removed with first added, extra lines stay unmarked
let ls = parse('@@ -1,3 +1,2 @@\n-one 1\n-two 2\n-three\n+one 9\n+two 8');
P.markIntraline(ls);
assert.deepStrictEqual(ls[1].hl, [4, 5]); assert.deepStrictEqual(ls[4].hl, [4, 5]);
assert.deepStrictEqual(ls[2].hl, [4, 5]); assert.deepStrictEqual(ls[5].hl, [4, 5]);
assert.strictEqual(ls[3].hl, undefined);
rows = P.pairRows(parse('@@ -1 +1 @@\n-foo 1 bar\n+foo 2 bar'));
assert.deepStrictEqual(rows[1].left.hl, [4, 5]); assert.deepStrictEqual(rows[1].right.hl, [4, 5]);
// lines of different blocks are not paired
rows = P.pairRows(parse('@@ -1,3 +1,3 @@\n-same a\n ctx\n+same b'));
assert.strictEqual(rows[1].left.hl, undefined); assert.strictEqual(rows[3].right.hl, undefined);

console.log('diffpair ok');
