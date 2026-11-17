/* Search core: tokenizer, shard lookup, prefix matching and AND. Pure functions plus one function that takes a
 * loader, so it can run in a browser and in node (tests/js). The tokenizer must match lorehub/search.py. */
(function (root) {
  'use strict';
  var MAX_TERMS = 8;
  var MIN_TOKEN = 2;

  function fold(text) {
    return String(text === null || text === undefined ? '' : text).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  }

  function tokenize(text) {
    return fold(text).match(/[\p{L}\p{N}]+/gu) || [];
  }

  /* Distinct query words that can be looked up (two letters or more), at most MAX_TERMS. */
  function queryTerms(text) {
    var out = [];
    tokenize(text).forEach(function (t) {
      if (t.length >= MIN_TOKEN && out.indexOf(t) < 0 && out.length < MAX_TERMS) out.push(t);
    });
    return out;
  }

  function hex(s) {
    var bytes = typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(s) : Buffer.from(s, 'utf8');
    return Array.prototype.map.call(bytes, function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
  }

  /* File name of a token shard, same rule as search.key_file in Python. */
  function keyFile(key) {
    return /^[a-z0-9]+$/.test(key) ? key : '_' + hex(key);
  }

  /* Shard keys a query word has to read. `split` maps a two letter key to the third-letter shards it was split into. */
  function shardKeys(term, split) {
    var key = Array.from(term).slice(0, 2).join('');
    var kids = split && Object.prototype.hasOwnProperty.call(split, key) ? split[key] : null;
    if (!kids) return [key];
    if (Array.from(term).length > 2) return [Array.from(term).slice(0, 3).join('')];
    return kids.slice();
  }

  function decodeIds(deltas) {
    var out = [], acc = 0;
    for (var i = 0; i < deltas.length; i++) { acc += deltas[i]; out.push(acc); }
    return out;
  }

  /* Sorted unique document ids of every token that starts with `term`, over already loaded shards. */
  function termIds(term, shards) {
    var seen = Object.create(null), ids = [];
    shards.forEach(function (shard) {
      Object.keys(shard).forEach(function (tok) {
        if (tok.slice(0, term.length) !== term) return;
        decodeIds(shard[tok]).forEach(function (id) { if (!seen[id]) { seen[id] = 1; ids.push(id); } });
      });
    });
    return ids.sort(function (a, b) { return a - b; });
  }

  function intersect(lists) {
    if (!lists.length) return [];
    var cur = lists[0];
    for (var k = 1; k < lists.length && cur.length; k++) {
      var next = lists[k], i = 0, j = 0, out = [];
      while (i < cur.length && j < next.length) {
        if (cur[i] === next[j]) { out.push(cur[i]); i++; j++; } else if (cur[i] < next[j]) i++; else j++;
      }
      cur = out;
    }
    return cur;
  }

  /* Run a query. loadShard(name) resolves to the parsed token shard "t/<name>.json" (or null when it does not exist).
   * Resolves to {terms, ids}: ids ascending = newest activity first. */
  function search(text, split, loadShard) {
    var terms = queryTerms(text);
    if (!terms.length) return Promise.resolve({ terms: terms, ids: [] });
    return Promise.all(terms.map(function (term) {
      return Promise.all(shardKeys(term, split).map(function (k) { return loadShard(keyFile(k)); })).then(function (shards) {
        return termIds(term, shards.filter(Boolean));
      });
    })).then(function (lists) { return { terms: terms, ids: intersect(lists) }; });
  }

  /* One packed document back into a row object like those of the list pages. */
  function decodeDoc(a, index) {
    var row = { n: a[0], hl: index.lists[a[1]], k: a[2] ? 'pr' : 'issue', t: a[3], a: a[4], ah: a[5], o: a[6], u: a[7],
      l: index.labels[a[8]] || 'open', c: a[9] };
    if (a[2]) row.v = a[10];
    return row;
  }

  var api = { fold: fold, tokenize: tokenize, queryTerms: queryTerms, keyFile: keyFile, shardKeys: shardKeys,
    decodeIds: decodeIds, termIds: termIds, intersect: intersect, search: search, decodeDoc: decodeDoc };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.LHSearch = api;
})(typeof window !== 'undefined' ? window : globalThis);
