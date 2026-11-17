/* Thread page: a patch series (pull request) or a discussion (issue).
 *
 * data/threads/<list>/<shard>.json.gz holds the metadata of every message (author, time, trailers, the index of
 * the message it replies to) and data/bodies/<list>/<n>.json.gz the texts, in the same order:
 * the opening message (cover letter), the patches, the replies. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var SHARD = 32;
  var MAX_INDENT = 4;
  var TRAILER_TEXT = { reviewed: 'Reviewed-by', acked: 'Acked-by', tested: 'Tested-by', nacked: 'Nacked-by',
    reported: 'Reported-by', suggested: 'Suggested-by', signed: 'Signed-off-by', codev: 'Co-developed-by' };
  var REVIEW_KINDS = ['reviewed', 'acked', 'tested', 'nacked'];

  function loreLink(d, m, text) {
    return LH.extLink(LH.loreUrl(m.ml || d.home, m.mid), text || 'View on lore', 'small');
  }

  /* The messages in the order of the bodies file: opening message, patches, replies. */
  function orderOf(d) {
    return (d.cover ? [d.cover] : []).concat(d.patches || [], d.cm || []);
  }

  function bodyNode(ctx, i) {
    var m = ctx.order[i], text = ctx.bodies ? ctx.bodies[i] : undefined;
    var link = LH.extLink(LH.loreUrl(m.ml || ctx.d.home, m.mid), 'View on lore');
    if (text === undefined) return h('p', { class: 'muted pad-sm' }, 'The text of this message could not be loaded. ', link, '.');
    if (text === null) return h('p', { class: 'muted pad-sm' }, 'The text of this message is not kept by LoreHub (an older version of the series, or outside the history window). ', link, '.');
    var out = h('div', { class: 'mail-wrap' }, LH.renderBody(text));
    if (m.lb) {
      out.appendChild(h('p', { class: 'muted small pad-sm note-lb' }, 'This is a shortened copy, the complete text is being fetched again. ', link, '.'));
    }
    if (m.bt) {
      out.appendChild(h('p', { class: 'muted small pad-sm note-bt' }, 'The message is longer than the 128 KB LoreHub keeps, the rest is cut. ', link, '.'));
    }
    return out;
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

  function jumpTo(i) {
    var el = document.getElementById('msg-' + i);
    if (!el) return;
    el.scrollIntoView({ block: 'center' });
    el.classList.add('flash');
    setTimeout(function () { el.classList.remove('flash'); }, 1500);
  }

  function replyContext(m) {
    if (!m.re) return null;
    var label = 'in reply to ' + m.re;
    if (m.pi === undefined) return h('span', { class: 'muted small reply-ctx' }, ' (' + label + ')');
    return h('a', { class: 'muted small reply-ctx', href: '#msg-' + m.pi, title: 'Jump to the message this one replies to',
      onclick: function (ev) { ev.preventDefault(); jumpTo(m.pi); } }, ' (' + label + ')');
  }

  function card(ctx, i, verb) {
    var m = ctx.order[i];
    return h('article', { class: 'comment', id: 'msg-' + i },
      h('div', { class: 'comment-avatar' }, LH.avatar(m.ah, 40, m.a)),
      h('div', { class: 'comment-box' },
        h('header', { class: 'comment-head' },
          m.a ? h('a', { href: LH.authorHref(m.ah), class: 'strong' }, m.a) : h('span', { class: 'strong' }, 'Unknown'),
          h('span', { class: 'muted' }, ' ' + verb + ' '), LH.timeEl(m.o),
          replyContext(m),
          h('span', { class: 'spacer' }), flagChips(m), loreLink(ctx.d, m, 'lore')),
        bodyNode(ctx, i)));
  }

  function patchCard(ctx, i) {
    var d = ctx.d, p = ctx.order[i];
    var stat = p.ds ? h('span', { class: 'diffstat muted small' }, LH.plural(p.ds[0], 'file') + ', ',
      h('span', { class: 'add' }, '+' + p.ds[1]), ' ', h('span', { class: 'del' }, '-' + p.ds[2])) : null;
    var msg = h('details', { class: 'commit-body' }, h('summary', null, 'Commit message'));
    var filled = false;
    msg.addEventListener('toggle', function () {
      if (msg.open && !filled) { filled = true; msg.appendChild(bodyNode(ctx, i)); }
    });
    return h('article', { class: 'patch-event', id: 'msg-' + i },
      h('div', { class: 'commit-head' },
        LH.icon('commit', 'muted'),
        h('span', { class: 'chip' }, p.i + '/' + (d.total || (d.patches || []).length)),
        h('strong', null, p.t), h('span', { class: 'spacer' }), stat, flagChips(p)),
      h('div', { class: 'muted small' }, p.a ? h('a', { href: LH.authorHref(p.ah) }, p.a) : 'unknown', ' - ', LH.timeEl(p.o), ' - ',
        h('a', { href: LH.threadHref(d.home, 'pr', d.n) + '?tab=files&patch=' + p.i }, p.nd ? 'Patch (diff not stored)' : 'View changes'), ' - ',
        loreLink(d, p, 'patch on lore')),
      msg);
  }

  function sideSection(title, node) {
    return h('section', { class: 'side-section' }, h('h3', null, title), node);
  }

  function person(r) {
    return h('li', { class: 'person-row' }, LH.avatar(r.h, 20, r.name),
      h('a', { href: LH.authorHref(r.h) }, r.name || 'someone'),
      r.carried ? h('span', { class: 'muted small', title: 'Present in the patch itself, probably carried from an earlier version' }, 'carried') : null);
  }

  function participants(order) {
    var by = {}, list = [];
    order.forEach(function (m) {
      if (!m.ah) return;
      if (!by[m.ah]) { by[m.ah] = { h: m.ah, name: m.a, n: 0 }; list.push(by[m.ah]); }
      by[m.ah].n++;
    });
    return list.sort(function (a, b) { return b.n - a.n; });
  }

  function sidebar(d, order) {
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

    var people = participants(order);
    var ul = h('ul', { class: 'people participants' });
    people.forEach(function (p) {
      ul.appendChild(h('li', { class: 'person-row' }, LH.avatar(p.h, 20, p.name), h('a', { href: LH.authorHref(p.h) }, p.name || 'someone'),
        h('span', { class: 'muted small spacer-left', title: LH.plural(p.n, 'message') }, String(p.n))));
    });
    parts.push(sideSection(LH.plural(people.length, 'participant'), ul));

    if (d.vs && d.vs.length > 1) {
      var vl = h('ul', { class: 'people versions' });
      d.vs.forEach(function (v) {
        var here = v.n === d.n && v.home === d.home;
        vl.appendChild(h('li', { class: 'person-row' }, here ? h('strong', null, 'v' + v.v + (v.rs ? ' (resend)' : '')) :
          h('a', { href: LH.threadHref(v.home, 'pr', v.n) }, 'v' + v.v + (v.rs ? ' (resend)' : '')),
          h('span', { class: 'muted small spacer-left' }, LH.plural(v.c || 0, 'reply'))));
      });
      parts.push(sideSection('Versions and their replies', vl));
    }

    parts.push(sideSection('Source', h('a', { class: 'btn btn-primary block', href: d.lore, rel: 'noopener noreferrer', target: '_blank' },
      'View thread on lore ', LH.icon('external'))));
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

  /* Reply tree: children[i] are the indexes of the replies to message i, oldest first. */
  function replyTree(d, order) {
    var base = d.cover ? 1 : 0, np = (d.patches || []).length, first = base + np;
    var children = {}, orphans = [], placed = {};
    function anchorOf(i) {
      var cur = i, guard = 0;
      while (cur !== undefined && guard++ < order.length + 1) {
        if (cur < first) return cur;
        cur = order[cur].pi;
      }
      return -1;
    }
    for (var c = first; c < order.length; c++) {
      var pi = order[c].pi;
      if (pi !== undefined && pi !== c && anchorOf(c) >= 0) {
        (children[pi] = children[pi] || []).push(c);
        placed[c] = true;
      }
    }
    for (var o = first; o < order.length; o++) {
      if (!placed[o]) orphans.push(o);
    }
    return { children: children, orphans: orphans, first: first };
  }

  function repliesBox(ctx, tree, parent, depth) {
    var kids = tree.children[parent];
    if (!kids || !kids.length) return null;
    var box = h('div', { class: 'replies' + (depth >= MAX_INDENT ? ' flat' : '') });
    kids.forEach(function (i) {
      box.appendChild(card(ctx, i, 'replied'));
      var sub = repliesBox(ctx, tree, i, depth + 1);
      if (sub) box.appendChild(sub);
    });
    return box;
  }

  function completeNote(d) {
    if (d.k !== 'pr' || !d.cp) return null;
    var link = LH.extLink(d.lore, 'Open the full thread on lore');
    if (d.cp === 1) {
      return h('p', { class: 'muted small coverage-note', 'data-coverage': 'waiting' },
        'Conversation still being completed: LoreHub fetches the whole thread from lore in the background, so some replies may be missing for now. ', link, '.');
    }
    return h('p', { class: 'muted small coverage-note', 'data-coverage': 'gaps' },
      'Some messages of this thread could not be found on lore, the conversation may have gaps. ', link, '.');
  }

  function conversation(ctx) {
    var d = ctx.d, order = ctx.order;
    var box = h('div', { class: 'timeline' });
    var tree = replyTree(d, order);
    var note = completeNote(d);
    if (note) box.appendChild(note);
    if (d.cover) {
      var verb = d.k === 'pr' ? 'sent this series' : 'started this discussion';
      if (d.cover.single) verb = 'sent this patch';
      box.appendChild(card(ctx, 0, verb));
      var sub = repliesBox(ctx, tree, 0, 0);
      if (sub) box.appendChild(sub);
    } else if (d.pa) {
      box.appendChild(h('div', { class: 'banner' }, 'The first message of this thread is not in LoreHub, so only the messages that were found are shown. ',
        LH.extLink(d.lore, 'Open the thread on lore'), '.'));
    }
    var base = d.cover ? 1 : 0;
    (d.patches || []).forEach(function (p, k) {
      var i = base + k;
      box.appendChild(patchCard(ctx, i));
      var sub = repliesBox(ctx, tree, i, 0);
      if (sub) box.appendChild(sub);
    });
    if (tree.orphans.length) {
      box.appendChild(h('div', { class: 'banner small' }, 'Replies whose parent message is not in LoreHub (yet). ', LH.extLink(d.lore, 'Open the thread on lore'), '.'));
      var flat = h('div', { class: 'replies flat' });
      tree.orphans.forEach(function (i) { flat.appendChild(card(ctx, i, 'replied')); });
      box.appendChild(flat);
    }
    if (!(d.cm || []).length) box.appendChild(h('p', { class: 'muted pad-sm' }, 'No replies were seen yet.'));
    return box;
  }

  function commits(ctx) {
    var d = ctx.d, patches = d.patches || [];
    if (!patches.length) return LH.empty('No patches collected', 'Only replies to this series are known. The patch messages are older than the window.');
    var base = d.cover ? 1 : 0;
    var ol = h('ol', { class: 'commits card' });
    patches.forEach(function (p, k) {
      var stat = p.ds ? h('span', { class: 'diffstat muted small' }, LH.plural(p.ds[0], 'file') + ', ',
        h('span', { class: 'add' }, '+' + p.ds[1]), ' ', h('span', { class: 'del' }, '-' + p.ds[2]), ' ', LH.diffBars(p.ds[1], p.ds[2])) : null;
      var msg = h('details', { class: 'commit-body' }, h('summary', null, 'Commit message'));
      var filled = false;
      msg.addEventListener('toggle', function () {
        if (msg.open && !filled) { filled = true; msg.appendChild(bodyNode(ctx, base + k)); }
      });
      ol.appendChild(h('li', { class: 'commit' },
        h('div', { class: 'commit-head' },
          LH.icon('commit', 'muted'),
          h('span', { class: 'chip' }, p.i + '/' + (d.total || patches.length)),
          h('strong', null, p.t), h('span', { class: 'spacer' }), stat),
        h('div', { class: 'muted small' }, p.a ? h('a', { href: LH.authorHref(p.ah) }, p.a) : 'unknown', ' - ', LH.timeEl(p.o), ' - ',
          [h('a', { href: LH.threadHref(d.home, 'pr', d.n) + '?tab=files&patch=' + p.i }, 'View changes'), ' - '],
          loreLink(d, p, 'patch on lore')),
        msg));
    });
    return ol;
  }

  LH.renderThread = function (app, list, kind, num, query) {
    var n = parseInt(num, 10);
    LH.setTitle(list + ' #' + num);
    if (!(n > 0)) { LH.clear(app).appendChild(LH.errorBox(new Error('Bad thread number'), 'this thread')); return Promise.resolve(); }
    return LH.getGz('data/threads/' + encodeURIComponent(list) + '/' + Math.floor(n / SHARD) + '.json.gz').then(function (shard) {
      var d = shard[String(n)];
      if (!d) throw new Error('Thread #' + n + ' is not in the current build (it may have aged out of the window).');
      LH.setTitle(d.t + ' (' + list + ' #' + n + ')');
      var qt = query.get('tab');
      var tab = qt === 'commits' && d.k === 'pr' ? 'commits' : qt === 'files' && d.k === 'pr' ? 'files' : 'conversation';
      var order = orderOf(d);
      var verb = d.k === 'pr' ? 'opened this patch series' : d.pu ? 'sent this pull request' : 'started this discussion';
      var banners = [];
      if (d.sb) {
        banners.push(h('div', { class: 'banner' }, 'Superseded by ', h('a', { href: LH.threadHref(d.sb.home, 'pr', d.sb.n) }, 'v' + d.sb.v), ', a newer version of this series.'));
      } else if (d.sbh) {
        banners.push(h('div', { class: 'banner' }, 'Superseded by v' + d.sbh + ', a newer version of this series that LoreHub does not show (no diff stored). ',
          LH.extLink(d.lore, 'Open the thread on lore'), '.'));
      }
      if (d.pa && d.cover) {
        banners.push(h('div', { class: 'banner' }, d.k === 'pr' ? 'Some messages of this series are outside the history window, so it may be incomplete.' :
          'Earlier messages of this discussion are outside the history window.'));
      }
      var head = h('div', { class: 'thread-head' },
        h('h1', null, d.t, ' ', h('span', { class: 'muted num-title' }, '#' + d.n)),
        h('div', { class: 'thread-sub' },
          LH.pill(d.l),
          d.a ? [h('a', { href: LH.authorHref(d.ah), class: 'strong' }, d.a), ' ' + verb + ' '] : 'First seen ',
          LH.timeEl(d.o), ' - ', h('span', { class: 'msg-count' }, LH.plural(d.mc || order.length, 'message')),
          ' (' + d.c + (d.c === 1 ? ' reply' : ' replies') + ')',
          h('span', { class: 'spacer' }), versionSelect(d, tab)));
      var tabs = h('nav', { class: 'tabs', 'aria-label': 'Thread sections' },
        h('a', { class: 'tab', href: LH.threadHref(list, d.k, n), 'aria-current': tab === 'conversation' ? 'page' : null },
          'Conversation', h('span', { class: 'counter' }, String(d.c))),
        d.k === 'pr' ? h('a', { class: 'tab', href: LH.threadHref(list, d.k, n) + '?tab=commits', 'aria-current': tab === 'commits' ? 'page' : null },
          'Commits', h('span', { class: 'counter' }, String((d.patches || []).length))) : null,
        d.k === 'pr' ? h('a', { class: 'tab', href: LH.threadHref(list, d.k, n) + '?tab=files', 'aria-current': tab === 'files' ? 'page' : null },
          'Files changed', h('span', { class: 'counter' }, String(d.fc || 0))) : null);
      var crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
        h('a', { href: LH.route(list) }, list), ' / ', h('a', { href: LH.route(list, d.k === 'pr' ? 'patches' : 'discussions') }, d.k === 'pr' ? 'Patches' : 'Discussions'), ' / #' + d.n);
      var bodiesP = LH.getGz('data/bodies/' + encodeURIComponent(d.home) + '/' + d.n + '.json.gz').then(function (x) { return x.b; }, function () { return null; });
      return bodiesP.then(function (bodies) {
        var ctx = { d: d, order: order, bodies: bodies };
        var mainNode;
        if (tab === 'files') {
          mainNode = LH.renderFiles({ d: d, list: d.home, n: n, query: query,
            bodyNode: function (m) { return bodyNode(ctx, order.indexOf(m)); },
            loreLink: function (m, text) { return loreLink(d, m, text); } });
        } else {
          mainNode = Promise.resolve(tab === 'commits' ? commits(ctx) : conversation(ctx));
        }
        return mainNode.then(function (node) {
          LH.clear(app).appendChild(h('div', null, crumbs, head, banners, tabs,
            tab === 'files' ? h('div', { class: 'thread-wide' }, node) :
              h('div', { class: 'thread-layout' }, h('div', { class: 'thread-main' }, node), sidebar(d, order))));
          if (tab === 'files' && !query.get('file')) window.scrollTo(0, 0);
        });
      });
    }).catch(function (e) { LH.clear(app).appendChild(LH.errorBox(e, 'this thread')); });
  };
})();
