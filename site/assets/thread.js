/* Thread page: a patch series (pull request) or a discussion (issue). */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var SHARD = 32;
  var TRAILER_TEXT = { reviewed: 'Reviewed-by', acked: 'Acked-by', tested: 'Tested-by', nacked: 'Nacked-by',
    reported: 'Reported-by', suggested: 'Suggested-by', signed: 'Signed-off-by', codev: 'Co-developed-by' };
  var REVIEW_KINDS = ['reviewed', 'acked', 'tested', 'nacked'];

  function loreLink(d, m, text) {
    return LH.extLink(LH.loreUrl(m.ml || d.home, m.mid), text || 'View on lore', 'small');
  }

  function bodyNode(d, m) {
    if (m.b) return LH.renderBody(m.b);
    return h('p', { class: 'muted pad-sm' }, 'The text of this message is no longer kept by LoreHub (older than 60 days or over the size budget). ',
      LH.extLink(LH.loreUrl(m.ml || d.home, m.mid), 'Read it on lore'), '.');
  }

  function flagChips(m) {
    var out = [];
    (m.tr || []).forEach(function (t) {
      if (REVIEW_KINDS.indexOf(t[0]) >= 0) out.push(h('span', { class: 'chip chip-' + t[0] }, TRAILER_TEXT[t[0]] + ': ' + (t[1] || 'someone')));
    });
    if (m.f && m.f.indexOf('A') >= 0) out.push(h('span', { class: 'chip chip-merged', title: 'Phrasing like "applied" or "pulled" near the top of the reply' }, 'applied'));
    if (m.f && m.f.indexOf('N') >= 0) out.push(h('span', { class: 'chip chip-nacked' }, 'NAK'));
    return out;
  }

  function card(d, m, verb, extra) {
    return h('article', { class: 'comment' },
      h('div', { class: 'comment-avatar' }, LH.avatar(m.ah, 40, m.a)),
      h('div', { class: 'comment-box' },
        h('header', { class: 'comment-head' },
          m.a ? h('a', { href: LH.authorHref(m.ah), class: 'strong' }, m.a) : h('span', { class: 'strong' }, 'Unknown'),
          h('span', { class: 'muted' }, ' ' + verb + ' '), LH.timeEl(m.o),
          m.re ? h('span', { class: 'muted small' }, ' (replying to ' + m.re + ')') : null,
          h('span', { class: 'spacer' }), flagChips(m), loreLink(d, m, 'lore')),
        extra || null,
        bodyNode(d, m)));
  }

  function sideSection(title, node) {
    return h('section', { class: 'side-section' }, h('h3', null, title), node);
  }

  function person(r) {
    return h('li', { class: 'person-row' }, LH.avatar(r.h, 20, r.name),
      h('a', { href: LH.authorHref(r.h) }, r.name || 'someone'),
      r.carried ? h('span', { class: 'muted small', title: 'Present in the patch itself, probably carried from an earlier version' }, 'carried') : null);
  }

  function sidebar(d) {
    var parts = [];
    var rv = d.rv || [];
    var reviewers = h('ul', { class: 'people' });
    var n = 0;
    REVIEW_KINDS.forEach(function (k) {
      rv.filter(function (r) { return r.kind === k; }).forEach(function (r) {
        n++;
        var li = person(r);
        li.appendChild(h('span', { class: 'review-kind review-' + k }, LH.icon(k === 'nacked' ? 'x' : 'check'), ' ' + TRAILER_TEXT[k].replace('-by', '')));
        reviewers.appendChild(li);
      });
    });
    parts.push(sideSection('Reviewers', n ? reviewers : h('p', { class: 'muted small' }, 'No Reviewed-by, Acked-by, Tested-by or Nacked-by trailers yet.')));

    var others = h('div');
    ['reported', 'suggested', 'signed'].forEach(function (k) {
      var items = rv.filter(function (r) { return r.kind === k; });
      if (!items.length) return;
      var ul = h('ul', { class: 'people' });
      items.forEach(function (r) { ul.appendChild(person(r)); });
      others.appendChild(h('h4', null, TRAILER_TEXT[k]));
      others.appendChild(ul);
    });
    if (others.firstChild) parts.push(sideSection('Trailers', others));

    var labels = h('div', { class: 'chips' }, LH.pill(d.l));
    (d.tg || []).forEach(function (t) { labels.appendChild(h('span', { class: 'chip chip-tag' }, t)); });
    parts.push(sideSection('Status and labels', labels));

    var lists = h('div', { class: 'chips' });
    (d.lists || []).forEach(function (l) { lists.appendChild(LH.chip(l, LH.route(l))); });
    parts.push(sideSection('Posted to', lists));

    var seen = {}, ppl = h('div', { class: 'avatars' });
    [d.cover].concat(d.cm || []).forEach(function (m) {
      if (m && m.ah && !seen[m.ah]) {
        seen[m.ah] = 1;
        ppl.appendChild(h('a', { href: LH.authorHref(m.ah), 'aria-label': m.a, class: 'avatar-link' }, LH.avatar(m.ah, 28, m.a)));
      }
    });
    parts.push(sideSection(LH.plural(Object.keys(seen).length, 'participant'), ppl));

    parts.push(sideSection('Source', h('a', { class: 'btn btn-primary block', href: d.lore, rel: 'noopener noreferrer', target: '_blank' },
      'View on lore ', LH.icon('external'))));
    return h('aside', { class: 'thread-side' }, parts);
  }

  function versionSelect(d, tab) {
    if (!d.vs || d.vs.length < 2) return null;
    var sel = h('select', { id: 'version-select', 'aria-label': 'Version history' });
    d.vs.forEach(function (v) {
      var o = h('option', { value: LH.threadHref(v.home, 'pr', v.n) + (tab === 'files' ? '?tab=files' : tab === 'commits' ? '?tab=commits' : '') },
        'v' + v.v + (v.rs ? ' (resend)' : '') + ' - #' + v.n + ' - ' + (LH.LABELS[v.l] ? LH.LABELS[v.l].text : v.l) + ' - ' + LH.timeAgo(v.o));
      if (v.n === d.n && v.home === d.home) o.setAttribute('selected', '');
      sel.appendChild(o);
    });
    sel.addEventListener('change', function () { location.hash = sel.value; });
    return h('label', { class: 'version-picker' }, h('span', { class: 'muted small' }, 'Version history '), sel);
  }

  function conversation(d) {
    var box = h('div', { class: 'timeline' });
    if (d.cover) {
      var verb = d.k === 'pr' ? (d.cover.single ? 'sent this patch' : 'sent this series') : 'started this discussion';
      box.appendChild(card(d, d.cover, verb));
    } else {
      box.appendChild(h('div', { class: 'banner' }, 'The first message of this thread is older than the 120 day window, so only replies are shown. ',
        LH.extLink(d.lore, 'Open the thread on lore'), '.'));
    }
    (d.cm || []).forEach(function (m) { box.appendChild(card(d, m, 'replied')); });
    if (!(d.cm || []).length) box.appendChild(h('p', { class: 'muted pad-sm' }, 'No replies were seen yet.'));
    return box;
  }

  function commits(d) {
    var patches = d.patches || [];
    if (!patches.length) return LH.empty('No patches collected', 'Only replies to this series are known. The patch messages are older than the window.');
    var ol = h('ol', { class: 'commits card' });
    patches.forEach(function (p) {
      var stat = p.ds ? h('span', { class: 'diffstat muted small' }, LH.plural(p.ds[0], 'file') + ', ',
        h('span', { class: 'add' }, '+' + p.ds[1]), ' ', h('span', { class: 'del' }, '-' + p.ds[2]), ' ', LH.diffBars(p.ds[1], p.ds[2])) : null;
      ol.appendChild(h('li', { class: 'commit' },
        h('div', { class: 'commit-head' },
          LH.icon('commit', 'muted'),
          h('span', { class: 'chip' }, p.i + '/' + (d.total || patches.length)),
          h('strong', null, p.t), h('span', { class: 'spacer' }), stat),
        h('div', { class: 'muted small' }, p.a ? h('a', { href: LH.authorHref(p.ah) }, p.a) : 'unknown', ' - ', LH.timeEl(p.o), ' - ',
          d.pu ? null : [h('a', { href: LH.threadHref(d.home, 'pr', d.n) + '?tab=files&patch=' + p.i }, 'View changes'), ' - '],
          loreLink(d, p, 'patch on lore')),
        h('details', { class: 'commit-body' }, h('summary', null, 'Commit message'), bodyNode(d, p))));
    });
    return ol;
  }

  LH.renderThread = function (app, list, kind, num, query) {
    var n = parseInt(num, 10);
    LH.setTitle(list + ' #' + num);
    if (!(n > 0)) { LH.clear(app).appendChild(LH.errorBox(new Error('Bad thread number'), 'this thread')); return Promise.resolve(); }
    return LH.getJSON('data/threads/' + encodeURIComponent(list) + '/' + Math.floor(n / SHARD) + '.json').then(function (shard) {
      var d = shard[String(n)];
      if (!d) throw new Error('Thread #' + n + ' is not in the current build (it may have aged out of the window).');
      LH.setTitle(d.t + ' (' + list + ' #' + n + ')');
      var qt = query.get('tab');
      var tab = qt === 'commits' && d.k === 'pr' ? 'commits' : qt === 'files' && d.k === 'pr' && !d.pu ? 'files' : 'conversation';
      var verb = d.k === 'pr' ? (d.pu ? 'sent this pull request' : 'opened this patch series') : 'started this discussion';
      var banners = [];
      if (d.sb) {
        banners.push(h('div', { class: 'banner' }, 'Superseded by ', h('a', { href: LH.threadHref(d.sb.home, 'pr', d.sb.n) }, 'v' + d.sb.v), ', a newer version of this series.'));
      }
      if (d.pa && d.cover) {
        banners.push(h('div', { class: 'banner' }, d.k === 'pr' ? 'Some messages of this series are outside the retention window, so it may be incomplete.' :
          'Earlier messages of this discussion are outside the retention window.'));
      }
      var head = h('div', { class: 'thread-head' },
        h('h1', null, d.t, ' ', h('span', { class: 'muted num-title' }, '#' + d.n)),
        h('div', { class: 'thread-sub' },
          LH.pill(d.l),
          d.a ? [h('a', { href: LH.authorHref(d.ah), class: 'strong' }, d.a), ' ' + verb + ' '] : 'First seen ',
          LH.timeEl(d.o), ' - ', LH.plural(d.c, 'comment'),
          h('span', { class: 'spacer' }), versionSelect(d, tab)));
      var tabs = h('nav', { class: 'tabs', 'aria-label': 'Thread sections' },
        h('a', { class: 'tab', href: LH.threadHref(list, d.k, n), 'aria-current': tab === 'conversation' ? 'page' : null },
          'Conversation', h('span', { class: 'counter' }, String(d.c))),
        d.k === 'pr' ? h('a', { class: 'tab', href: LH.threadHref(list, d.k, n) + '?tab=commits', 'aria-current': tab === 'commits' ? 'page' : null },
          'Commits', h('span', { class: 'counter' }, String((d.patches || []).length))) : null,
        d.k === 'pr' && !d.pu ? h('a', { class: 'tab', href: LH.threadHref(list, d.k, n) + '?tab=files', 'aria-current': tab === 'files' ? 'page' : null },
          'Files changed', h('span', { class: 'counter' }, String(d.fc || 0))) : null);
      var crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
        h('a', { href: LH.route(list) }, list), ' / ', h('a', { href: LH.route(list, d.k === 'pr' ? 'patches' : 'discussions') }, d.k === 'pr' ? 'Patches' : 'Discussions'), ' / #' + d.n);
      var mainNode;
      if (tab === 'files') {
        mainNode = LH.renderFiles({ d: d, list: list, n: n, query: query, bodyNode: function (m) { return bodyNode(d, m); },
          loreLink: function (m, text) { return loreLink(d, m, text); } });
      } else {
        mainNode = Promise.resolve(tab === 'commits' ? commits(d) : conversation(d));
      }
      return mainNode.then(function (node) {
        LH.clear(app).appendChild(h('div', null, crumbs, head, banners, tabs,
          tab === 'files' ? h('div', { class: 'thread-wide' }, node) :
            h('div', { class: 'thread-layout' }, h('div', { class: 'thread-main' }, node), sidebar(d))));
        if (tab === 'files' && !query.get('file')) window.scrollTo(0, 0);
      });
    }).catch(function (e) { LH.clear(app).appendChild(LH.errorBox(e, 'this thread')); });
  };
})();
