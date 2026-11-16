/* Contributor page and About page. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;

  function itemList(items) {
    var ul = h('ul', { class: 'rows card' });
    items.forEach(function (it) {
      ul.appendChild(h('li', { class: 'row' },
        h('div', { class: 'row-icon' }, LH.stateIcon(it.k, it.l)),
        h('div', { class: 'row-main' },
          h('div', { class: 'row-head' }, h('a', { class: 'row-title', href: LH.threadHref(it.hl, it.k, it.n) }, it.t || '(no subject)'), LH.pill(it.l)),
          h('div', { class: 'row-meta muted' }, h('a', { href: LH.route(it.hl) }, it.hl), ' #' + it.n + ' - ', LH.timeEl(it.o)))));
    });
    return ul;
  }

  LH.renderPerson = function (app, hash) {
    LH.setTitle('Contributor');
    if (!/^[0-9a-f]{12}$/.test(hash)) { LH.clear(app).appendChild(LH.errorBox(new Error('Bad contributor id'), 'this contributor')); return Promise.resolve(); }
    return LH.getJSON('data/authors/' + hash.slice(0, 2) + '.json').then(function (shard) {
      var p = shard[hash];
      if (!p) {
        LH.clear(app).appendChild(h('div', { class: 'card empty' },
          h('div', { class: 'center' }, LH.avatar(hash, 64)),
          h('h2', null, 'No profile for this contributor'),
          h('p', { class: 'muted' }, 'Profiles are only built for authors active in the last 30 days or in the top contributors of a list. Older activity can still be found through the list pages.'),
          h('a', { href: '#/' }, 'Back to all lists')));
        return;
      }
      LH.setTitle(p.name);
      var chips = h('div', { class: 'chips' });
      Object.keys(p.lists).forEach(function (l) { chips.appendChild(LH.chip(l + ' (' + p.lists[l] + ')', LH.route(l))); });
      LH.clear(app).appendChild(h('div', null,
        h('div', { class: 'person-head' }, LH.avatar(hash, 72, p.name),
          h('div', null, h('h1', null, p.name || '(unknown)'),
            h('p', { class: 'muted' }, LH.plural(p.msgs, 'message') + ' in the retention window across ' + LH.plural(Object.keys(p.lists).length, 'list')),
            chips)),
        h('section', null, h('h2', null, 'Patches and threads started'),
          p.items.length ? itemList(p.items) : LH.empty('Nothing started recently', 'No thread opened by this contributor is in the data.')),
        h('section', null, h('h2', null, 'Recent replies'),
          p.replies.length ? itemList(p.replies) : LH.empty('No replies', 'No replies were collected for this contributor.'))));
    }).catch(function (e) { LH.clear(app).appendChild(LH.errorBox(e, 'this contributor')); });
  };

  function section(title) {
    var kids = Array.prototype.slice.call(arguments, 1);
    return h('section', { class: 'card pad prose' }, h('h2', null, title), kids);
  }

  LH.renderAbout = function (app) {
    LH.setTitle('About');
    return LH.getJSON('data/lists.json').then(function (data) {
      var t = data.totals;
      var table = h('tbody');
      [['open', 'No reply from anyone other than the author yet.'],
        ['review', 'At least one reply from someone else.'],
        ['reviewed', 'A reply from someone else contains a Reviewed-by trailer (not quoted).'],
        ['acked', 'A reply contains an Acked-by trailer.'],
        ['tested', 'A reply contains a Tested-by trailer.'],
        ['changes', 'A reply from someone else is a NAK or a Nacked-by trailer, or has inline comments that contain requests, and that reviewer has not approved since. Only while no newer version exists.'],
        ['superseded', 'A newer version of the same series (same author, same normalised title, higher version or a RESEND) exists.'],
        ['merged', 'A reply from someone other than the author says "applied", "queued", "merged" or "pulled" in its first lines (past tense, not a question, not negated), or a [GIT PULL] gets such a reply or a pr-tracker-bot message.'],
        ['stale', 'Anything not merged or superseded with no activity for more than 60 days.']].forEach(function (r) {
        table.appendChild(h('tr', null, h('th', { scope: 'row' }, LH.pill(r[0])), h('td', null, r[1])));
      });
      LH.clear(app).appendChild(h('div', { class: 'about' },
        h('h1', null, 'About LoreHub'),
        section('What this is',
          h('p', null, 'LoreHub gives every mailing list on lore.kernel.org a GitHub-style view. A list is a repository, a patch series is a pull request, a non-patch thread is a discussion, replies with review trailers are reviews, and "Applied, thanks" replies or [GIT PULL] threads are merges.'),
          h('p', null, 'Right now it knows ' + t.lists + ' lists, ' + t.messages + ' messages and ' + t.threads + ' threads' +
            (t.pending ? ' (' + t.pending + ' lists are still waiting for their first crawl)' : '') + '.')),
        section('Data source',
          h('p', null, 'Only lore.kernel.org. The list of lists is manifest.js.gz. Activity comes from each list\'s new.atom feed, which holds just the 25 newest messages, so LoreHub accumulates history across runs and keeps up to ' + data.window_days + ' days. Busy lists can optionally be topped up with the search Atom feed (up to 200 messages per request) within a small request budget. Message text is whatever the feed carries, trimmed; follow the "View on lore" links for the full message and the diff. Public-inbox git repositories are never cloned.'),
          h('p', null, 'Requests are polite: at most 2 at a time, at least half a second apart, Retry-After honoured, a descriptive User-Agent, and lists whose archive has not changed are skipped.')),
        section('How statuses are derived (heuristics)',
          h('p', null, 'These labels are guesses from reply text. They can be wrong: people review off-list, apply patches without replying, or write in unexpected ways. Priority runs from the top of this list to the bottom in the order Merged, Superseded, Changes requested, Reviewed, Acked, Tested, In review, Open, then Stale is applied on top.'),
          h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, table)),
          h('p', null, 'Patch series are detected from subjects such as [PATCH v3 2/7], [PATCH net-next v2 0/5], [RFC PATCH], [RESEND PATCH]. The cover letter (0/N) and its patches form one pull request; successive versions are chained and only the newest is the headline. Tabs show Open, Merged and Closed (superseded or stale) counts. Cross-posted messages are de-duplicated by message-id and show the other lists as chips.')),
        section('Update cadence',
          h('p', null, 'A scheduled GitHub Actions workflow runs every 30 minutes. Each run has a time budget and continues where the previous one stopped, so a freshly started site fills in over several runs. The footer shows when the data was generated. Lists that failed to refresh are marked stale.')),
        section('Privacy',
          h('p', null, 'No cookies, no trackers, no third-party requests: fonts, icons and avatars are generated locally. E-mail addresses are never stored or shown. Contributors are identified by a salted hash of the address, which also drives the generated avatar. The only thing kept in your browser is the light or dark preference.')),
        section('Credits and related projects',
          h('p', null, 'All mail belongs to its authors and lives at ', LH.extLink('https://lore.kernel.org/', 'lore.kernel.org'),
            ', served by ', LH.extLink('https://public-inbox.org/', 'public-inbox'), '. Related tools that do other jobs: ',
            LH.extLink('https://patchwork.kernel.org/', 'patchwork'), ' (patch state tracking for some lists), ',
            LH.extLink('https://b4.docs.kernel.org/', 'b4'), ' and ', LH.extLink('https://korg.docs.kernel.org/', 'korgalore'),
            ' (command line tools), and plain archives such as lkml.org and spinics. LoreHub is not affiliated with any of them.')),
        section('Licence', h('p', null, 'MIT. Source code: ', LH.extLink('https://github.com/lkmlhub/lkmlhub.github.io', 'github.com/lkmlhub/lkmlhub.github.io'), '.'))));
    }).catch(function (e) { LH.clear(app).appendChild(LH.errorBox(e, 'the about page')); });
  };
})();
