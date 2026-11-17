const assert = require('assert');
const S = require('../../site/assets/reviewstore.js');

function fakeStorage(opts) {
  const data = {};
  return {
    data,
    getItem(k) { if (opts && opts.blocked) { const e = new Error('denied'); e.name = 'SecurityError'; throw e; } return k in data ? data[k] : null; },
    setItem(k, v) { if (opts && (opts.blocked || opts.full)) { const e = new Error('quota'); e.name = 'QuotaExceededError'; throw e; } data[k] = String(v); },
    removeItem(k) { delete data[k]; }
  };
}
const key = S.draftKey('kexec', 1, 2);
assert.strictEqual(key, 'lkmlhub-review:kexec:1:v2');
assert.notStrictEqual(S.draftKey('kexec', 1, 1), key);     // per version
assert.notStrictEqual(S.draftKey('kexec', 2, 2), key);     // per series number
assert.notStrictEqual(S.draftKey('keyrings', 1, 2), key);  // per list
assert.strictEqual(S.IDENTITY_KEY, 'lkmlhub-identity');

// add, edit, delete, persist, reload
{
  const st = fakeStorage();
  const d = S.openDrafts(st, key);
  assert.strictEqual(d.persistent, true);
  let changes = 0;
  d.onchange(() => { changes++; });
  const c = d.add({ patch: 'p1', kind: 'diff', path: 'a/b.c', ix: 5, line: '+x', text: 'hello' });
  assert.ok(c.id && c.prepared === 0);
  assert.strictEqual(d.pending().length, 1);
  assert.strictEqual(Object.keys(st.data).length, 1);
  const again = S.openDrafts(st, key);                 // a reload
  assert.deepStrictEqual(again.comments.map((x) => [x.patch, x.path, x.ix, x.text, x.line]), [['p1', 'a/b.c', 5, 'hello', '+x']]);
  again.update(c.id, 'edited');
  assert.strictEqual(S.openDrafts(st, key).comments[0].text, 'edited');
  again.markPrepared([c.id]);
  assert.strictEqual(S.openDrafts(st, key).pending().length, 0);
  assert.ok(S.openDrafts(st, key).comments[0].prepared > 0);
  again.update(c.id, 'edited again');
  assert.strictEqual(again.pending().length, 1, 'an edited comment is pending again');
  assert.strictEqual(again.remove(c.id), true);
  assert.strictEqual(again.remove(c.id), false);
  assert.strictEqual(Object.keys(st.data).length, 0, 'an empty draft leaves no key');
  assert.ok(changes >= 1);
  // the review form is kept with the comments
  d.setReview({ state: 'approve', trailer: 'Acked-by', overall: 'text', scope: 'patches', picks: ['p1'] });
  const rv = S.openDrafts(st, key).review;
  assert.deepStrictEqual([rv.state, rv.trailer, rv.overall, rv.scope, rv.picks], ['approve', 'Acked-by', 'text', 'patches', ['p1']]);
  d.setReview({ state: 'bogus', trailer: 'Evil-by', scope: 'x' });
  assert.deepStrictEqual([d.review.state, d.review.trailer, d.review.scope], ['comment', '', '']);
  // clear = "Discard review"
  d.add({ patch: 'p1', path: 'f', ix: 1, text: 'x' });
  d.clear();
  assert.deepStrictEqual([d.comments.length, d.review.overall, Object.keys(st.data).length], [0, '', 0]);
  // other versions are untouched
  const other = S.openDrafts(st, S.draftKey('kexec', 1, 1));
  other.add({ patch: 'q', path: 'f', ix: 1, text: 'v1 comment' });
  d.clear();
  assert.strictEqual(S.openDrafts(st, S.draftKey('kexec', 1, 1)).comments.length, 1);
}

// data from the storage is not trusted
{
  const st = fakeStorage();
  st.data[key] = '{not json';
  assert.strictEqual(S.openDrafts(st, key).comments.length, 0);
  st.data[key] = JSON.stringify({ comments: [
    { id: 'ok', patch: 'p', ix: 3, text: 't', path: 'f' },
    { id: 'neg', patch: 'p', ix: -1, text: 't' }, { id: 'frac', patch: 'p', ix: 1.5, text: 't' }, { id: 'nopatch', ix: 1, text: 't' },
    { patch: 'p', ix: 1, text: 'noid' }, { id: 'obj', patch: {}, ix: 1, text: 't' }, 'string', null, 7,
    { id: '__proto__', patch: 'p', ix: 2, text: 'x'.repeat(30000), kind: 'evil' }],
  review: { state: 'request', trailer: 'Nacked-by', overall: 5, picks: ['a', 7, {}], scope: 'series', extra: 1 }, __proto__: { polluted: 1 } });
  const d = S.openDrafts(st, key);
  assert.deepStrictEqual(d.comments.map((c) => c.id), ['ok', '__proto__']);
  assert.strictEqual(d.comments[1].text.length, S.MAX_TEXT);
  assert.strictEqual(d.comments[1].kind, 'diff');
  assert.deepStrictEqual([d.review.state, d.review.trailer, d.review.overall, d.review.picks, d.review.scope], ['request', 'Nacked-by', '', ['a'], 'series']);
  assert.strictEqual({}.polluted, undefined);
  st.data[key] = JSON.stringify([1, 2, 3]);
  assert.strictEqual(S.openDrafts(st, key).comments.length, 0);
  st.data[key] = JSON.stringify({ comments: Array.from({ length: 900 }, (_, i) => ({ id: 'i' + i, patch: 'p', ix: i, text: 't' })) });
  assert.strictEqual(S.openDrafts(st, key).comments.length, S.MAX_COMMENTS);
  const full = S.openDrafts(st, key);
  assert.throws(() => full.add({ patch: 'p', path: 'f', ix: 1, text: 'one more' }), /At most/);
}

// no usable storage: comments live in memory, the flag says so, nothing throws
[fakeStorage({ blocked: true }), null, undefined].forEach((st, i) => {
  const k = key + ':mem' + i;
  const d = S.openDrafts(st, k);
  assert.strictEqual(d.persistent, false);
  d.add({ patch: 'p', path: 'f', ix: 1, text: 'kept in memory' });
  assert.strictEqual(d.comments.length, 1);
  assert.strictEqual(S.openDrafts(st, k).comments.length, 1, 'still there when the page opens the same series again');
  d.clear();
  assert.strictEqual(S.openDrafts(st, k).comments.length, 0);
  const id = S.identity(st);
  assert.strictEqual(id.persistent, false);
  id.set({ name: 'A', email: 'a@example.test' });
  assert.strictEqual(S.identity(st).get().name, 'A');
  id.forget();
  assert.deepStrictEqual(S.identity(st).get(), { name: '', email: '' });
});
// a storage that fills up later falls back to memory
{
  const st = fakeStorage();
  const d = S.openDrafts(st, key + ':full');
  d.add({ patch: 'p', path: 'f', ix: 1, text: 'one' });
  st.setItem = () => { throw new Error('quota'); };
  d.add({ patch: 'p', path: 'f', ix: 2, text: 'two' });
  assert.strictEqual(d.persistent, false);
  assert.strictEqual(d.comments.length, 2);
}

// identity: stored only under lkmlhub-identity, forgotten on request
{
  const st = fakeStorage();
  const id = S.identity(st);
  assert.deepStrictEqual(id.get(), { name: '', email: '' });
  id.set({ name: 'Jane Reviewer', email: 'jane@example.test' });
  assert.deepStrictEqual(Object.keys(st.data), ['lkmlhub-identity']);
  assert.deepStrictEqual(JSON.parse(st.data['lkmlhub-identity']), { name: 'Jane Reviewer', email: 'jane@example.test' });
  assert.deepStrictEqual(S.identity(st).get(), { name: 'Jane Reviewer', email: 'jane@example.test' });
  S.identity(st).forget();
  assert.deepStrictEqual(Object.keys(st.data), []);
  assert.deepStrictEqual(S.identity(st).get(), { name: '', email: '' });
  st.data['lkmlhub-identity'] = '{"name": 5, "email": ["x"]}';
  assert.deepStrictEqual(S.identity(st).get(), { name: '', email: '' });
  st.data['lkmlhub-identity'] = 'garbage';
  assert.deepStrictEqual(S.identity(st).get(), { name: '', email: '' });
  // drafts never contain the identity
  const d = S.openDrafts(st, key + ':ident');
  id.set({ name: 'Secret Name', email: 'secret@example.test' });
  d.add({ patch: 'p', path: 'f', ix: 1, text: 'x' });
  assert.ok(!st.data[key + ':ident'].includes('secret'));
}
console.log('reviewstore ok');
