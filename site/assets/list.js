/* List ("repository") page: Patches, Discussions, Activity, Contributors, About. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var AUTO_PAGES = 8;

  function base(name) { return 'data/lists/' + encodeURIComponent(name) + '/'; }

  function tabLink(name, tab, text, current, count) {
    var a = h('a', { href: LH.route(name, tab), class: 'tab', 'aria-current': tab === current ? 'page' : null },
      text, count !== undefined ? h('span', { class: 'counter' }, String(count)) : null);
    return a;
  }

  function header(meta) {
    var name = meta.name;
    var banner = null;
    if (meta.crawl === 'pending') {
      banner = h('div', { class: 'banner' }, 'This list has not been crawled yet. It will fill in over the next scheduled runs.');
    } else if (meta.crawl === 'stale') {
      banner = h('div', { class: 'banner banner-warn' },
        'Stale: the last refresh ' + (meta.err ? 'failed (' + meta.err + ')' : 'is more than 6 hours old') +
        '. Showing the last good data, checked ', LH.timeEl(meta.checked), '.');
    }
    var trimmed = meta.threads_total > meta.counts.pr.open + meta.counts.pr.merged + meta.counts.pr.closed +
      meta.counts.issue.open + meta.counts.issue.closed;
    return h('div', { class: 'repo-head' },
      h('div', { class: 'repo-title' },
        LH.icon('list', 'muted'), h('span', { class: 'muted' }, 'lore / '),
        h('h1', null, name), h('span', { class: 'chip' }, 'public archive')),
      h('p', { class: 'repo-desc' }, meta.desc || 'No description'),
      h('div', { class: 'repo-actions' },
        LH.extLink('https://lore.kernel.org/' + encodeURIComponent(name) + '/', ['Archive on lore ', LH.icon('external')], 'btn'),
        LH.extLink('https://lore.kernel.org/' + encodeURIComponent(name) + '/new.atom', 'Atom feed', 'btn'),
        LH.extLink('https://subspace.kernel.org/subscribing.html', 'Subscribe', 'btn'),
        h('span', { class: 'group' }, h('span', { class: 'dot', style: 'background:' + LH.color(meta.gc) }), meta.g),
        h('span', { class: 'muted small' }, 'Last message ', LH.timeEl(meta.last))),
      banner,
      trimmed ? h('div', { class: 'banner' }, 'Showing the newest ' + (meta.counts.pr.open + meta.counts.pr.merged + meta.counts.pr.closed +
        meta.counts.issue.open + meta.counts.issue.closed) + ' of ' + meta.threads_total + ' threads to keep the site small.') : null);
  }

  /* ---------------------------------------------------------------- thread tabs */
  function threadTab(name, meta, kind, query) {
    var states = kind === 'pr' ? ['open', 'merged', 'closed'] : ['open', 'closed'];
    var state = states.indexOf(query.get('state')) >= 0 ? query.get('state') : 'open';
    var counts = meta.counts[kind];
    var stateNames = { open: 'Open', merged: 'Merged', closed: kind === 'pr' ? 'Closed (superseded, stale)' : 'Closed (stale)' };
    var tab = kind === 'pr' ? 'patches' : 'discussions';

    var nav = h('div', { class: 'state-nav', role: 'group', 'aria-label': 'Filter by state' });
    states.forEach(function (s) {
      var icon = kind === 'issue' ? (s === 'open' ? 'issue-open' : 'issue-closed') : (s === 'open' ? 'pr-open' : s === 'merged' ? 'pr-merged' : 'pr-closed');
      nav.appendChild(h('a', { href: LH.route(name, tab) + '?state=' + s, class: 'state-link', 'aria-current': s === state ? 'true' : null },
        LH.icon(icon, 'state-' + s), ' ', h('strong', null, String(counts[s])), ' ' + stateNames[s]));
    });

    var q = h('input', { type: 'search', id: 'f-q', placeholder: 'Search titles', value: query.get('q') || '', autocomplete: 'off' });
    var author = h('input', { type: 'search', id: 'f-author', placeholder: 'Author name', value: query.get('author') || '', autocomplete: 'off' });
    var labelSel = h('select', { id: 'f-label' }, h('option', { value: '' }, 'All statuses'));
    Object.keys(meta.labels[kind]).forEach(function (l) {
      if (LH.stateOf(l) !== state) return;
      var opt = h('option', { value: l }, (LH.LABELS[l] ? LH.LABELS[l].text : l) + ' (' + meta.labels[kind][l] + ')');
      if (query.get('label') === l) opt.setAttribute('selected', '');
      labelSel.appendChild(opt);
    });
    var form = h('form', { class: 'filters', role: 'search', 'aria-label': 'Filter threads', onsubmit: function (e) { e.preventDefault(); } },
      h('label', { class: 'sr-only', for: 'f-q' }, 'Search titles'), q,
      h('label', { class: 'sr-only', for: 'f-author' }, 'Author'), author,
      h('label', { class: 'sr-only', for: 'f-label' }, 'Status'), labelSel);

    var results = h('div', { class: 'results', 'aria-live': 'polite' });
    var status = h('p', { class: 'muted small', role: 'status' });
    var loadBtn = h('button', { type: 'button', class: 'btn', hidden: true }, 'Load older threads');
    var pages = (meta.pages[kind + '-' + state] || 0);
    var st = { rows: [], next: 0, loading: null };

    function loadNext() {
      if (st.next >= pages) return Promise.resolve();
      var i = st.next++;
      return LH.getJSON(base(name) + kind + '-' + state + '-' + i + '.json').then(function (d) {
        st.rows = st.rows.concat(d.rows);
      });
    }
    function filtered() {
      var term = q.value.trim().toLowerCase(), au = author.value.trim().toLowerCase(), lab = labelSel.value;
      return st.rows.filter(function (r) {
        return (!term || (r.t || '').toLowerCase().indexOf(term) >= 0) &&
          (!au || (r.a || '').toLowerCase().indexOf(au) >= 0) && (!lab || r.l === lab);
      });
    }
    function active() { return q.value.trim() || author.value.trim() || labelSel.value; }
    function sync() {
      var p = new URLSearchParams();
      if (state !== 'open') p.set('state', state);
      if (q.value.trim()) p.set('q', q.value.trim());
      if (author.value.trim()) p.set('author', author.value.trim());
      if (labelSel.value) p.set('label', labelSel.value);
      var qs = p.toString();
      history.replaceState(null, '', LH.route(name, tab) + (qs ? '?' + qs : ''));
    }
    function paint() {
      var rows = filtered();
      var empty = counts[state] === 0 ? LH.empty('No ' + (kind === 'pr' ? 'patch series' : 'discussions') + ' in this view',
        kind === 'pr' ? 'Nothing matching "' + stateNames[state] + '" was seen on this list in the retention window.' :
          'No non-patch threads were seen on this list in the retention window.') : null;
      LH.renderRows(results, rows, name, { empty: empty || LH.empty('No matches', 'No loaded thread matches the filters.') });
      var more = st.next < pages;
      status.textContent = st.rows.length ? 'Showing ' + rows.length + ' of ' + st.rows.length + ' loaded' +
        (more ? ' (' + (pages - st.next) + ' more pages not loaded)' : '') + '.' : '';
      loadBtn.hidden = !more;
    }
    function refresh() {
      sync();
      var chain = Promise.resolve();
      if (active()) {
        var budget = AUTO_PAGES;
        var step = function () { if (st.next < pages && budget-- > 0) return loadNext().then(step); };
        chain = Promise.resolve().then(step);
      }
      return chain.then(paint);
    }
    [q, author].forEach(function (el) { el.addEventListener('input', refresh); });
    labelSel.addEventListener('change', refresh);
    loadBtn.addEventListener('click', function () { loadNext().then(paint); });

    var node = h('div', null, h('div', { class: 'toolbar-row' }, nav, form), results, status, h('div', { class: 'more' }, loadBtn));
    return loadNext().then(refresh).then(function () { return node; }).catch(function (e) { return LH.errorBox(e, 'threads'); });
  }

  /* ---------------------------------------------------------------- activity */
  function eventLine(ev, list) {
    var verb = { opened: 'opened', merged: 'merged', reviewed: 'reviewed', acked: 'acked', tested: 'tested', nacked: 'nacked' }[ev.ty] || ev.ty;
    return h('li', { class: 'feed-item' },
      LH.avatar(ev.wh, 24, ev.w),
      h('div', null,
        h('div', null, ev.w ? h('a', { href: LH.authorHref(ev.wh), class: 'strong' }, ev.w) : 'Someone', ' ' + verb + ' ',
          h('a', { href: LH.threadHref(ev.hl, ev.k, ev.n) }, ev.t || '(no subject)')),
        h('div', { class: 'muted small' }, '#' + ev.n + ' - ', LH.timeEl(ev.ts))));
  }

  function chart(daily) {
    var n = daily.msgs.length, W = 720, H = 150, padL = 30, padB = 20, bw = (W - padL) / n;
    var max = Math.max.apply(null, daily.msgs.concat([1]));
    var amax = Math.max.apply(null, daily.authors.concat([1]));
    var svg = LH.svg('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'chart', role: 'img',
      'aria-label': 'Messages and distinct authors per day over the last ' + n + ' days' });
    svg.appendChild(LH.svg('text', { x: 0, y: 10, class: 'chart-text' }, String(max)));
    svg.appendChild(LH.svg('text', { x: 0, y: H - padB, class: 'chart-text' }, '0'));
    var pts = [];
    daily.msgs.forEach(function (v, i) {
      var day = new Date((daily.start + i * 86400) * 1000);
      var ch = Math.round((v / max) * (H - padB - 14));
      var r = LH.svg('rect', { x: (padL + i * bw).toFixed(1), y: H - padB - ch, width: Math.max(1, bw - 1).toFixed(1), height: ch, class: 'bar' },
        LH.svg('title', null, day.toISOString().slice(0, 10) + ': ' + LH.plural(v, 'message') + ', ' + LH.plural(daily.authors[i], 'author')));
      svg.appendChild(r);
      pts.push((padL + i * bw + bw / 2).toFixed(1) + ',' + (H - padB - (daily.authors[i] / amax) * (H - padB - 14)).toFixed(1));
      if (day.getUTCDate() === 1) {
        svg.appendChild(LH.svg('text', { x: (padL + i * bw).toFixed(1), y: H - 5, class: 'chart-text' },
          day.toLocaleString('en', { month: 'short', timeZone: 'UTC' })));
      }
    });
    svg.appendChild(LH.svg('polyline', { points: pts.join(' '), class: 'chart-line', fill: 'none' }));
    return svg;
  }

  function activityTab(name, meta) {
    var d = meta.daily;
    var total30 = d.msgs.slice(-30).reduce(function (a, b) { return a + b; }, 0);
    var peak = Math.max.apply(null, d.authors);
    var c = meta.counts;
    var stats = h('div', { class: 'stats' },
      stat(total30, 'messages, last 30 days'), stat(peak, 'most authors in one day'),
      stat(c.pr.open, 'open patch series'), stat(c.pr.merged, 'merged'), stat(c.issue.open, 'open discussions'));
    var tl = h('ol', { class: 'feed' });
    meta.events.forEach(function (e) { tl.appendChild(eventLine(e, name)); });
    return h('div', null, stats,
      h('section', { class: 'card pad' }, h('h2', { class: 'side-title' }, 'Messages per day'),
        h('p', { class: 'muted small' }, 'Bars are messages, the line is distinct authors. Up to ' + meta.window_days + ' days.'), chart(d)),
      h('section', { class: 'card pad' }, h('h2', { class: 'side-title' }, 'Timeline'),
        meta.events.length ? tl : h('p', { class: 'muted' }, 'No review or merge events were seen yet.')));
  }
  function stat(n, label) { return h('div', { class: 'stat card pad' }, h('div', { class: 'stat-n' }, String(n)), h('div', { class: 'muted small' }, label)); }

  /* ---------------------------------------------------------------- contributors */
  function contributorsTab(name, meta) {
    if (!meta.contributors.length) return LH.empty('No contributors yet', 'No messages from this list have been collected.');
    var max = meta.contributors[0].m;
    var body = h('tbody');
    meta.contributors.forEach(function (c, i) {
      body.appendChild(h('tr', null,
        h('td', { class: 'muted' }, String(i + 1)),
        h('td', null, h('a', { href: LH.authorHref(c.h), class: 'person' }, LH.avatar(c.h, 20, c.name), ' ', c.name || '(unknown)')),
        h('td', { class: 'num' }, String(c.m),
          h('span', { class: 'bar-bg', 'aria-hidden': 'true' }, h('span', { class: 'bar-fg', style: 'width:' + Math.round(c.m / max * 100) + '%' }))),
        h('td', { class: 'num' }, String(c.p)), h('td', { class: 'num' }, String(c.r)), h('td', { class: 'num' }, String(c.v))));
    });
    return h('div', { class: 'card table-wrap' }, h('table', { class: 'table' },
      h('caption', { class: 'sr-only' }, 'Top contributors by messages'),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, '#'), h('th', { scope: 'col' }, 'Author'),
        h('th', { scope: 'col', class: 'num' }, 'Messages'), h('th', { scope: 'col', class: 'num' }, 'Patches'),
        h('th', { scope: 'col', class: 'num' }, 'Replies'), h('th', { scope: 'col', class: 'num' }, 'Reviews'))), body));
  }

  /* ---------------------------------------------------------------- about */
  function aboutTab(name, meta) {
    function row(k, v) { return h('tr', null, h('th', { scope: 'row' }, k), h('td', null, v)); }
    return h('div', { class: 'card pad' },
      h('h2', null, 'About ' + name),
      h('table', { class: 'table facts' }, h('tbody', null,
        row('Description', meta.desc || 'None'),
        row('Archive', LH.extLink('https://lore.kernel.org/' + encodeURIComponent(name) + '/', 'lore.kernel.org/' + name + '/')),
        row('Subsystem group', meta.g + ' (guessed from the name)'),
        row('Archive epochs', meta.epochs.length ? String(meta.epochs.length) : 'unknown'),
        row('Messages collected', String(meta.msgs)),
        row('Threads known', String(meta.threads_total)),
        row('History kept', 'up to ' + meta.window_days + ' days'),
        row('Last checked', meta.checked ? LH.timeEl(meta.checked) : 'never'),
        row('Crawl status', meta.crawl + (meta.err ? ' - ' + meta.err : '')))),
      h('p', null, 'Statuses are heuristics. ', h('a', { href: '#/about' }, 'Read how they are derived.')));
  }

  /* ---------------------------------------------------------------- entry */
  LH.renderList = function (app, name, tab, query) {
    LH.setTitle(name);
    return LH.getJSON(base(name) + 'meta.json').then(function (meta) {
      var c = meta.counts;
      var nav = h('nav', { class: 'tabs', 'aria-label': 'List sections' },
        tabLink(name, 'patches', 'Patches', tab, c.pr.open),
        tabLink(name, 'discussions', 'Discussions', tab, c.issue.open),
        tabLink(name, 'activity', 'Activity', tab),
        tabLink(name, 'contributors', 'Contributors', tab),
        tabLink(name, 'about', 'About', tab));
      var body = h('div', { class: 'tab-body' });
      LH.clear(app).appendChild(h('div', null, header(meta), nav, body));
      var make = {
        patches: function () { return threadTab(name, meta, 'pr', query); },
        discussions: function () { return threadTab(name, meta, 'issue', query); },
        activity: function () { return activityTab(name, meta); },
        contributors: function () { return contributorsTab(name, meta); },
        about: function () { return aboutTab(name, meta); }
      }[tab] || function () { return threadTab(name, meta, 'pr', query); };
      return Promise.resolve(make()).then(function (node) { body.appendChild(node); });
    }).catch(function (e) {
      LH.clear(app).appendChild(h('div', { class: 'card empty' },
        h('h2', null, 'List not found'),
        h('p', { class: 'muted' }, 'There is no list called "' + name + '" in this build (' + (e && e.message) + ').'),
        h('a', { href: '#/' }, 'Back to all lists')));
    });
  };
})();
