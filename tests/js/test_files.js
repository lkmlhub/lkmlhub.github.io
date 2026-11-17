const assert = require('assert');
const load = require('./load');
const w = load(['util.js', 'files.js']);
const T = w.LH.filesTab;

// line numbers follow the hunk header
let rows = T.parseHunks('@@ -10,3 +20,4 @@ fn\n a\n-b\n+c\n+d\n e\n\\ No newline at end of file');
assert.strictEqual(JSON.stringify(rows.map((r) => r.t)), JSON.stringify(['hunk', 'ctx', 'del', 'add', 'add', 'ctx', 'note']));
assert.strictEqual(rows[1].o, 10); assert.strictEqual(rows[1].n, 20);
assert.strictEqual(rows[2].o, 11); assert.strictEqual(rows[2].n, undefined);
assert.strictEqual(rows[3].n, 21); assert.strictEqual(rows[4].n, 22);
assert.strictEqual(rows[5].o, 12); assert.strictEqual(rows[5].n, 23);
assert.strictEqual(rows[2].s, 'b');

// zero context and missing counts
rows = T.parseHunks('@@ -5 +5 @@\n-x\n+y\n@@ -9,0 +10,1 @@\n+z');
assert.strictEqual(rows[1].o, 5); assert.strictEqual(rows[2].n, 5); assert.strictEqual(rows[4].n, 10);

// the text of a line is data, never interpreted
rows = T.parseHunks('@@ -1 +1 @@\n+<script>alert(1)</script>');
assert.strictEqual(rows[1].s, '<script>alert(1)</script>');

// collect merges patches by path, sorts, and tracks kinds
const p1 = { i: 1, mid: 'a', t: 'one' }, p2 = { i: 2, mid: 'b', t: 'two' };
const diffs = {
  a: { f: [{ p: 'z.c', a: 1, d: 0, k: 'a', x: '@@ -0,0 +1 @@\n+x' }, { p: 'b.c', a: 2, d: 1, x: '@@ -1 +1,2 @@\n-q\n+r\n+s' }] },
  b: { f: [{ p: 'z.c', a: 0, d: 3, k: 'd', x: '@@ -1,3 +0,0 @@\n-x\n-y\n-z' }, { p: 'img.png', a: 0, d: 0, b: 1 },
           { p: 'new.c', o: 'old.c', k: 'r', s: 98, a: 0, d: 0 }] },
};
let all = T.collect([p1, p2], diffs, null);
assert.strictEqual(JSON.stringify(all.map((e) => e.path)), JSON.stringify(['b.c', 'img.png', 'new.c', 'z.c']));
const z = all[3];
assert.strictEqual(z.parts.length, 2); assert.strictEqual(z.a, 1); assert.strictEqual(z.d, 3);
assert.strictEqual(z.kind, 'd');
assert.strictEqual(all[1].binary, true);
assert.strictEqual(all[2].kind, 'r'); assert.strictEqual(all[2].old, 'old.c'); assert.strictEqual(all[2].sim, 98);
const one = T.collect([p1, p2], diffs, 1);
assert.strictEqual(JSON.stringify(one.map((e) => e.path)), JSON.stringify(['z.c', 'b.c']));  // patch order is kept for one patch
assert.strictEqual(one[0].kind, 'a');
assert.strictEqual(T.collect([p1, p2], null, null).length, 0);
assert.strictEqual(T.collect([p1, { i: 3, mid: 'nope', t: '' }], diffs, 3).length, 0);

// tree: directories merged, hostile names are harmless
const hostile = ['__proto__/constructor/x.c', 'a/b/c/d.c', 'a/b/c/e.c', 'a/f.c', 'toString', '<script>/y'];
const tree = T.buildTree(hostile.map((p) => ({ path: p, a: 1, d: 0 })));
assert.strictEqual(Object.prototype.toString.call(tree.dirs), '[object Map]');
assert.ok(tree.dirs.has('__proto__'));
const a = tree.dirs.get('a');
assert.strictEqual(JSON.stringify(Array.from(a.dirs.keys())), JSON.stringify(['b']));
assert.strictEqual(a.dirs.get('b').name, 'b/c');           // single-child chain squashed
assert.strictEqual(a.dirs.get('b').files.length, 2);
assert.strictEqual(JSON.stringify(tree.files.map((f) => f.name)), JSON.stringify(['toString']));
assert.strictEqual(Object.prototype.polluted, undefined);
console.log('files.js ok');
