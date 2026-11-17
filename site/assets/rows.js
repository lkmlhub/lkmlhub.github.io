/* Shared pieces: thread rows (GitHub list items) and mail body rendering. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;

  var KIND_WORD = { opened: 'opened', merged: 'merged', reviewed: 'reviewed', acked: 'acked', tested: 'tested', nacked: 'nacked' };

  /* One list row. list is the page's list name, used to build cross-list links. */
  LH.threadRow = function (row, list, opts) {
    opts = opts || {};
    var home = row.hl || list;
    var href = LH.threadHref(home, row.k, row.n);
    var title = h('a', { class: 'row-title', href: href }, row.t || '(no subject)');
    var tags = [];
    if (opts.listChip) tags.push(LH.chip(home, LH.route(home), 'Open the ' + home + ' list'));
    tags.push(LH.pill(row.l));
    if (row.v && row.k === 'pr') tags.push(h('span', { class: 'chip chip-version', title: 'Series version' }, 'v' + row.v));
    if (row.nv) tags.push(h('span', { class: 'chip', title: 'Versions of this series seen' }, row.nv + ' versions'));
    if (row.pu) tags.push(h('span', { class: 'chip' }, 'pull request'));
    (row.tg || []).forEach(function (t) { tags.push(h('span', { class: 'chip chip-tag' }, t)); });
    (row.x || []).forEach(function (l) { tags.push(LH.chip(l, LH.route(l), 'Also posted to ' + l)); });
    if (row.pa) tags.push(h('span', { class: 'chip chip-partial', title: 'Some earlier messages are outside the history window' }, 'partial'));

    var parts = ['#' + row.n + (row.hl && !opts.listChip ? ' in ' + row.hl : '')];
    var opened = row.a ? ['opened ', LH.timeEl(row.o), ' by ', h('a', { href: LH.authorHref(row.ah) }, row.a)]
      : ['first seen ', LH.timeEl(row.o)];
    parts.push(opened);
    if (row.sb) parts.push('superseded by v' + row.sb);
    if (row.u && row.u - row.o > 3600) parts.push(['updated ', LH.timeEl(row.u)]);
    if (row.k === 'pr' && row.p > 1) parts.push(row.p + ' patches');
    var meta = h('div', { class: 'row-meta muted' });
    parts.forEach(function (p, i) { if (i) meta.appendChild(document.createTextNode(' - ')); meta.appendChild(h('span', null, p)); });

    var revs = h('span', { class: 'avatars', 'aria-label': 'Reviewers' });
    (row.rv || []).forEach(function (r) {
      var a = LH.avatar(r[0], 20, r[2]);
      a.appendChild(LH.svg('title', null, r[2] + ' (' + (KIND_WORD[r[1]] || r[1]) + ')'));
      revs.appendChild(h('a', { href: LH.authorHref(r[0]), class: 'avatar-link', 'aria-label': r[2] + ' ' + r[1] }, a));
    });
    var comments = h('a', { class: 'comments muted', href: href, 'aria-label': LH.plural(row.c, 'comment') },
      LH.icon('comment'), ' ', String(row.c));

    return h('li', { class: 'row' },
      h('div', { class: 'row-icon' }, LH.stateIcon(row.k, row.l)),
      h('div', { class: 'row-main' }, h('div', { class: 'row-head' }, title, tags), meta),
      h('div', { class: 'row-side' }, row.rv ? revs : null, comments));
  };

  /* Mail body: links are safe http(s) anchors, everything is text. Quoted blocks of more than QUOTE_OPEN lines are
   * collapsed behind a "Show quoted text" toggle, a message of more than CLIP_LINES lines is clipped until
   * "Show full message" is pressed (which also opens every quote). */
  var QUOTE_OPEN = 5, CLIP_LINES = 45;
  LH.renderBody = function (text) {
    var box = h('div', { class: 'mail' });
    var lines = String(text || '').split('\n');
    var i = 0, quotes = [], visible = 0;
    while (i < lines.length) {
      var quoted = /^\s*>/.test(lines[i]);
      var j = i;
      while (j < lines.length && /^\s*>/.test(lines[j]) === quoted) j++;
      var chunk = lines.slice(i, j).join('\n');
      if (quoted && j - i > QUOTE_OPEN) {
        var det = h('details', { class: 'quote' },
          h('summary', null, 'Show quoted text (' + LH.plural(j - i, 'line') + ')'),
          h('pre', { class: 'mail-pre quoted' }, chunk));
        quotes.push(det);
        box.appendChild(det);
        visible += 1;
      } else if (quoted) {
        box.appendChild(h('pre', { class: 'mail-pre quoted' }, chunk));
        visible += j - i;
      } else if (chunk.trim()) {
        box.appendChild(h('pre', { class: 'mail-pre' }, LH.linkify(chunk.replace(/^\n+|\n+$/g, ''))));
        visible += j - i;
      }
      i = j;
    }
    if (visible <= CLIP_LINES && !quotes.length) return box;
    var clipped = visible > CLIP_LINES;
    var wrap = h('div', { class: clipped ? 'mail-clipped' : null }, box);
    var full = false;
    var more = clipped ? 'Show full message' : 'Show all quoted text';
    var btn = h('button', { type: 'button', class: 'btn btn-small show-full', 'aria-expanded': 'false' }, more);
    btn.addEventListener('click', function () {
      full = !full;
      wrap.classList.toggle('mail-clipped', !full && clipped);
      quotes.forEach(function (q) { q.open = full; });
      btn.textContent = full ? 'Show less' : more;
      btn.setAttribute('aria-expanded', full ? 'true' : 'false');
    });
    return h('div', { class: 'mail-outer' }, wrap, h('div', { class: 'mail-actions' }, btn));
  };

  /* Pager over an array of rows that were already filtered. */
  LH.renderRows = function (container, rows, list, opts) {
    opts = opts || {};
    var step = opts.step || 50;
    LH.clear(container);
    if (!rows.length) {
      container.appendChild(opts.empty || LH.empty('Nothing here', 'No threads match.'));
      return;
    }
    var ul = h('ul', { class: 'rows card' });
    var shown = 0;
    var more = h('button', { class: 'btn', type: 'button' }, 'Show more');
    function next() {
      rows.slice(shown, shown + step).forEach(function (r) { ul.appendChild(LH.threadRow(r, list)); });
      shown = Math.min(rows.length, shown + step);
      more.hidden = shown >= rows.length;
      more.textContent = 'Show more (' + (rows.length - shown) + ' left)';
    }
    more.addEventListener('click', next);
    container.appendChild(ul);
    container.appendChild(h('div', { class: 'more' }, more));
    next();
  };
})();
