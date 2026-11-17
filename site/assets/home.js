/* Home page: grid of lists (like a GitHub organisation) and a global activity feed. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;

  var EVENT_TEXT = {
    opened: 'opened', merged: 'merged', reviewed: 'reviewed', acked: 'acked', tested: 'tested', nacked: 'nacked'
  };

  function listCard(l, colors) {
    var status = null;
    if (l.st === 'pending') status = h('span', { class: 'chip chip-warn', title: 'Not crawled yet' }, 'pending');
    else if (l.st === 'stale') status = h('span', { class: 'chip chip-warn', title: 'The last refresh failed or is old' }, 'stale');
    return h('li', { class: 'card list-card', 'data-name': l.n },
      h('div', { class: 'list-card-head' },
        LH.icon('list', 'muted'),
        h('a', { class: 'list-name', href: LH.route(l.n) }, l.n),
        status),
      h('p', { class: 'list-desc' }, l.d || 'No description'),
      l.hs ? h('p', { class: 'history-note muted small', title: LH.historyNote(l.hs, l.hc).title }, LH.historyNote(l.hs, l.hc).text) : null,
      h('div', { class: 'list-card-foot' },
        h('span', { class: 'group', title: 'Subsystem group (guessed from the list name)' },
          h('span', { class: 'dot', style: 'background:' + LH.color(colors[l.g]) }), l.g),
        LH.sparkline(l.sp, 96, 24),
        h('span', { class: 'muted', title: 'Open patch series' }, LH.icon('pr-open'), ' ', String(l.open)),
        h('span', { class: 'muted', title: 'Last message' }, LH.icon('clock'), ' ', LH.timeAgo(l.la))));
  }

  function feedItem(ev) {
    var verb = EVENT_TEXT[ev.ty] || ev.ty;
    return h('li', { class: 'feed-item' },
      LH.avatar(ev.wh, 24, ev.w),
      h('div', null,
        h('div', null,
          ev.w ? h('a', { href: LH.authorHref(ev.wh), class: 'strong' }, ev.w) : 'Someone', ' ' + verb + ' ',
          h('a', { href: LH.threadHref(ev.hl, ev.k, ev.n) }, ev.t || '(no subject)')),
        h('div', { class: 'muted small' }, h('a', { href: LH.route(ev.hl) }, ev.hl), ' #' + ev.n + ' - ', LH.timeEl(ev.ts))));
  }

  LH.renderHome = function (app) {
    LH.setTitle('');
    return Promise.all([LH.getJSON('data/lists.json'), LH.getJSON('data/recent.json')]).then(function (res) {
      var data = res[0], recent = res[1];
      var lists = data.lists;
      var q = h('input', { type: 'search', id: 'list-search', placeholder: 'Find a list...', autocomplete: 'off',
        'aria-label': 'Filter mailing lists' });
      var sort = h('select', { id: 'list-sort', 'aria-label': 'Sort lists' },
        h('option', { value: 'activity' }, 'Last activity'),
        h('option', { value: 'name' }, 'Name'),
        h('option', { value: 'open' }, 'Open patches'));
      var grid = h('ul', { class: 'grid', 'aria-label': 'Mailing lists' });
      var count = h('p', { class: 'muted small', role: 'status' });
      var cards = {};
      var groupFilter = '';
      var legend = h('div', { class: 'legend', role: 'group', 'aria-label': 'Filter by subsystem group' });

      lists.forEach(function (l) { cards[l.n] = listCard(l, data.groups); });

      function apply() {
        var term = q.value.trim().toLowerCase();
        var mode = sort.value;
        var sorted = lists.slice().sort(function (a, b) {
          if (mode === 'name') return a.n < b.n ? -1 : 1;
          if (mode === 'open') return b.open - a.open || b.la - a.la;
          return b.la - a.la;
        });
        var shown = 0;
        LH.clear(grid);
        sorted.forEach(function (l) {
          var hit = (!term || l.n.toLowerCase().indexOf(term) >= 0 || (l.d || '').toLowerCase().indexOf(term) >= 0 ||
            l.g.toLowerCase().indexOf(term) >= 0) && (!groupFilter || l.g === groupFilter);
          if (hit) { grid.appendChild(cards[l.n]); shown++; }
        });
        count.textContent = shown + ' of ' + lists.length + ' lists';
        if (!shown) grid.appendChild(h('li', null, LH.empty('No list matches', 'Try a different search.')));
      }

      Object.keys(data.groups).forEach(function (g) {
        var btn = h('button', { type: 'button', class: 'legend-item', 'aria-pressed': 'false' },
          h('span', { class: 'dot', style: 'background:' + LH.color(data.groups[g]) }), g);
        btn.addEventListener('click', function () {
          groupFilter = groupFilter === g ? '' : g;
          Array.prototype.forEach.call(legend.children, function (b) { b.setAttribute('aria-pressed', 'false'); });
          if (groupFilter) btn.setAttribute('aria-pressed', 'true');
          apply();
        });
        legend.appendChild(btn);
      });
      q.addEventListener('input', apply);
      sort.addEventListener('change', apply);

      var feed = h('ol', { class: 'feed' });
      recent.events.slice(0, 40).forEach(function (e) { feed.appendChild(feedItem(e)); });
      var t = data.totals;
      var notes = [];
      if (t.pending) notes.push(t.pending + ' lists have not been crawled yet and fill in over the next runs.');
      var bfi = data.backfill;
      if (bfi && bfi.days && bfi.done < bfi.lists) {
        notes.push('History backfill: ' + bfi.done + ' of ' + bfi.lists + ' lists reach back ' + bfi.days + ' days (to ' + LH.shortDate(bfi.horizon) +
          '), the rest fill in over the next runs. Busy lists stop at a page cap.');
      }
      if (t.stale) notes.push(t.stale + ' lists could not be refreshed recently and may be out of date.');

      LH.clear(app).appendChild(h('div', { class: 'home' },
        h('section', { class: 'home-main', 'aria-labelledby': 'lists-h' },
          h('div', { class: 'home-head' },
            h('div', null,
              h('h1', { id: 'lists-h' }, 'Mailing lists'),
              h('p', { class: 'muted' }, 'Every lore.kernel.org list as a repository: patch series are pull requests, other threads are discussions.')),
            h('a', { class: 'btn', href: '#/about' }, 'How it works')),
          notes.length ? h('div', { class: 'banner' }, notes.join(' ')) : null,
          h('div', { class: 'toolbar' }, h('label', { class: 'sr-only', for: 'list-search' }, 'Filter mailing lists'), q,
            h('label', { class: 'sr-only', for: 'list-sort' }, 'Sort'), sort),
          legend, count, grid),
        h('aside', { class: 'home-side', 'aria-labelledby': 'feed-h' },
          h('h2', { id: 'feed-h', class: 'side-title' }, 'Recent activity'),
          recent.events.length ? h('div', { class: 'card pad' }, feed) : LH.empty('No activity yet', 'The first crawl has not produced events.'))));
      apply();
    }).catch(function (e) { LH.clear(app).appendChild(LH.errorBox(e, 'the list of lists')); });
  };
})();
