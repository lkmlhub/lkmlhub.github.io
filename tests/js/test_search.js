const assert = require('assert');
const S = require('../../site/assets/searchcore.js');
const eq = (a, b, m) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), m);

// tokenizer: case, punctuation, accents, digits
eq(S.tokenize('IPE: add the .ipe policy-keyring v2'), ['ipe', 'add', 'the', 'ipe', 'policy', 'keyring', 'v2']);
eq(S.tokenize('Müller, José'), ['muller', 'jose']);
eq(S.tokenize('net_next/6.12'), ['net', 'next', '6', '12']);
eq(S.tokenize('李明 wrote'), ['李明', 'wrote']);
eq(S.tokenize(''), []);
eq(S.tokenize(null), []);

// query terms: two letters minimum, distinct, bounded
eq(S.queryTerms('ipe keyring'), ['ipe', 'keyring']);
eq(S.queryTerms('.ipe'), ['ipe']);
eq(S.queryTerms('a b ipe ipe IPE'), ['ipe']);
eq(S.queryTerms('one two three four five six seven eight nine ten').length, 8);

// shard keys and file names
eq(S.shardKeys('keyring', {}), ['ke']);
eq(S.shardKeys('keyring', { ke: ['key', 'kex'] }), ['key']);
eq(S.shardKeys('ke', { ke: ['key', 'kex', 'ke'] }), ['key', 'kex', 'ke']);
eq(S.shardKeys('ke', undefined), ['ke']);
eq(S.shardKeys('toString', { toString: ['x'] }), ['to']);  // no prototype keys
assert.strictEqual(S.keyFile('ip'), 'ip');
assert.strictEqual(S.keyFile('李明'), '_e69d8ee6988e');
assert.strictEqual(S.keyFile('a/'), '_612f');

// postings and set operations
eq(S.decodeIds([3, 2, 5]), [3, 5, 10]);
eq(S.intersect([[1, 3, 5, 9], [3, 4, 5, 6], [5, 9]]), [5]);
eq(S.intersect([[1, 2]]), [1, 2]);
eq(S.intersect([]), []);
eq(S.intersect([[1, 2], []]), []);
const shard = { ipe: [2, 3], ipv4: [1, 1], ipv6: [5], ip: [0] };
eq(S.termIds('ip', [shard]), [0, 1, 2, 5]);          // prefix: ip, ipe, ipv4, ipv6
eq(S.termIds('ipe', [shard]), [2, 5]);
eq(S.termIds('ipv', [shard]), [1, 2, 5]);
eq(S.termIds('zz', [shard]), []);

// search with a loader: loads only the shards of the terms, ANDs, prefix matches
const shards = {
  ip: { ipe: [3, 4], ipv6: [1] },
  ke: { keyring: [3, 1, 7], keys: [9] },
  ne: { net: [1] },
};
const loaded = [];
const loader = (name) => { loaded.push(name); return Promise.resolve(shards[name] || null); };
(async () => {
  let r = await S.search('ipe keyring', {}, loader);
  eq(r.ids, [3]);
  eq(loaded.sort(), ['ip', 'ke']);
  loaded.length = 0;
  r = await S.search('IPE key', {}, loader);               // prefix: keyring and keys
  eq(r.ids, [3]);
  r = await S.search('.ipe', {}, loader);
  eq(r.ids, [3, 7]);
  r = await S.search('ipe nothing', {}, loader);
  eq(r.ids, []);
  r = await S.search('a', {}, loader);
  eq(r, { terms: [], ids: [] });
  r = await S.search('zzzz', {}, loader);
  eq(r.ids, []);
  // split shards: a long word goes to its three letter shard
  loaded.length = 0;
  await S.search('keyring', { ke: ['key'] }, (n) => { loaded.push(n); return Promise.resolve({ keyring: [1] }); });
  eq(loaded, ['key']);

  const index = { lists: ['bpf', 'netdev'], labels: ['open', 'merged'] };
  eq(S.decodeDoc([12, 1, 1, 'Title', 'Ann', 'abc', 100, 200, 1, 4, 3], index),
    { n: 12, hl: 'netdev', k: 'pr', t: 'Title', a: 'Ann', ah: 'abc', o: 100, u: 200, l: 'merged', c: 4, v: 3 });
  eq(S.decodeDoc([1, 0, 0, 'Bug', '', '', 1, 2, 0, 0, 0], index).k, 'issue');
  console.log('searchcore ok');
})().catch((e) => { console.error(e); process.exit(1); });
