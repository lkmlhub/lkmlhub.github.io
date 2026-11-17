/* Search page: #/search?q=<text>. Static, sharded index: a query loads a few token shards, then only the
 * document shards of the results on screen. Everything is rendered with createElement/textContent. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h, S = window.LHSearch;
  var STEP = 40;

  function loadShard(name) {
    return LH.getJSON('data/search/t/' + name + '.json').catch(function () { return null; });
  }

  function loreSearchLink(q) {
    return h('p', { class: 'search-lore' }, LH.extLink('https://lore.kernel.org/all/?q=' + encodeURIComponent(q), ['Search lore.kernel.org for this ', LH.icon('external')], 'btn'),
      h('span', { class: 'muted small' }, ' The full archive on lore may hold messages that are outside the window LkmlHub keeps.'));
  }

  function listMatches(lists, terms) {
    return lists.filter(function (l) {
      var toks = S.tokenize(l.n + ' ' + (l.d || ''));
      return terms.every(function (t) { return toks.some(function (k) { return k.slice(0, t.length) === t; }); });
    }).slice(0, 8);
  }

  LH.renderSearch = function (app, query) {
    var q = (query.get('q') || '').trim().slice(0, 200);
    LH.setTitle(q ? 'Search: ' + q : 'Search');
    var input = h('input', { type: 'search', name: 'q', id: 'search-page-q', value: q, autocomplete: 'off',
      placeholder: 'Titles, authors, lists...', 'aria-label': 'Search all lists' });
    var form = h('form', { class: 'search-form', role: 'search', onsubmit: function (e) {
      e.preventDefault();
      var v = input.value.trim();
      location.hash = '#/search' + (v ? '?q=' + encodeURIComponent(v) : '');
    } }, input, h('button', { type: 'submit', class: 'btn btn-primary' }, 'Search'));
    var status = h('p', { class: 'muted', role: 'status' });
    var results = h('div', { class: 'results', 'aria-live': 'polite' });
    var listBox = h('div', { class: 'search-lists' });
    var footer = h('div', { class: 'search-footer' });
    LH.clear(app).appendChild(h('div', { class: 'search-page' }, h('h1', null, 'Search'),
      h('p', { class: 'muted' }, 'Patch series and discussions by title, author name and list, across all lists. Words are matched as prefixes, all words must match.'),
      form, status, listBox, results, footer));
    var box = document.getElementById('site-q');
    if (box) box.value = q;
    if (!q) {
      status.textContent = 'Type a few words, for example "ipe keyring" or an author name.';
      footer.appendChild(h('p', null, LH.extLink('https://lore.kernel.org/all/', 'Browse the full archive on lore.kernel.org')));
      return Promise.resolve();
    }
    footer.appendChild(loreSearchLink(q));
    var terms = S.queryTerms(q);
    if (!terms.length) {
      status.textContent = 'Use words of at least two letters or digits.';
      return Promise.resolve();
    }
    return Promise.all([LH.getJSON('data/search/index.json'), LH.getJSON('data/lists.json')]).then(function (res) {
      var index = res[0], lists = res[1].lists;
      return S.search(q, index.split, loadShard).then(function (found) {
        var ids = found.ids;
        var lm = listMatches(lists, found.terms);
        if (lm.length) {
          listBox.appendChild(h('h2', { class: 'side-title' }, 'Lists'));
          var ul = h('ul', { class: 'rows card' });
          lm.forEach(function (l) {
            ul.appendChild(h('li', { class: 'row' }, h('div', { class: 'row-icon' }, LH.icon('list', 'muted')),
              h('div', { class: 'row-main' }, h('a', { class: 'row-title', href: LH.route(l.n) }, l.n),
                h('div', { class: 'row-meta muted' }, l.d || 'No description'))));
          });
          listBox.appendChild(ul);
        }
        status.textContent = ids.length ? LH.plural(ids.length, 'result') + (lm.length ? ' (' + LH.plural(lm.length, 'list') + ' above)' : '') + '.'
          : 'No patch series or discussion matches "' + q + '".';
        if (!ids.length) {
          results.appendChild(LH.empty('Nothing found', 'Try fewer or shorter words. LkmlHub keeps recent history only; the button below searches the whole archive.'));
          return;
        }
        results.appendChild(h('h2', { class: 'side-title' }, 'Patch series and discussions'));
        var ulr = h('ul', { class: 'rows card' });
        var more = h('button', { type: 'button', class: 'btn' }, 'Show more');
        var shown = 0;
        function next() {
          var slice = ids.slice(shown, shown + STEP);
          var need = {};
          slice.forEach(function (id) { need[Math.floor(id / index.doc_shard)] = 1; });
          more.disabled = true;
          return Promise.all(Object.keys(need).map(function (k) { return LH.getJSON('data/search/d/' + k + '.json').then(function (d) { return [k, d]; }); })).then(function (pairs) {
            var byShard = {};
            pairs.forEach(function (p) { byShard[p[0]] = p[1]; });
            slice.forEach(function (id) {
              var doc = byShard[Math.floor(id / index.doc_shard)][id % index.doc_shard];
              if (doc) ulr.appendChild(LH.threadRow(S.decodeDoc(doc, index), doc && index.lists[doc[1]], { listChip: true }));
            });
            shown += slice.length;
            more.disabled = false;
            more.hidden = shown >= ids.length;
            more.textContent = 'Show more (' + (ids.length - shown) + ' left)';
          });
        }
        more.addEventListener('click', next);
        results.appendChild(ulr);
        results.appendChild(h('div', { class: 'more' }, more));
        return next();
      });
    }).catch(function (e) {
      status.textContent = '';
      results.appendChild(LH.errorBox(e, 'the search index'));
    });
  };
})();
