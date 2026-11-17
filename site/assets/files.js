/* "Files changed" tab of a patch series: diffstat, file tree, one card per file with a unified diff.
 * Diff text arrives as plain unified hunks (first character = line type) and is turned into table rows
 * with createElement/textContent only. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/;
  var EAGER_LINES = 2500;       // files are expanded until this many lines are on the page
  var VIEWED_PREFIX = 'lh-viewed:';

  /* ---------------------------------------------------------------- pure helpers (no DOM) */

  /* Rows of one file part: [{t: 'hunk'|'add'|'del'|'ctx'|'note', o, n, s}] from raw hunk text. */
  function parseHunks(text) {
    var rows = [], o = 0, n = 0;
    String(text || '').split('\n').forEach(function (line) {
      var m = HUNK.exec(line);
      if (m) {
        o = parseInt(m[1], 10); n = parseInt(m[3], 10);
        rows.push({ t: 'hunk', s: line });
        return;
      }
      var c = line.charAt(0), rest = line.slice(1);
      if (c === '+') rows.push({ t: 'add', n: n++, s: rest });
      else if (c === '-') rows.push({ t: 'del', o: o++, s: rest });
      else if (c === '\\') rows.push({ t: 'note', s: line });
      else rows.push({ t: 'ctx', o: o++, n: n++, s: c === ' ' ? rest : line });
    });
    return rows;
  }

  /* Merge the files of the selected patches into one list of entries (sorted by path when all patches are shown). */
  function collect(patches, diffs, only) {
    var order = [], byPath = new Map();
    patches.forEach(function (p) {
      if (only !== null && p.i !== only) return;
      var pd = diffs ? diffs[p.mid] : null;
      if (!pd) return;
      pd.f.forEach(function (f) {
        var e = byPath.get(f.p);
        if (!e) { e = { path: f.p, parts: [], a: 0, d: 0 }; byPath.set(f.p, e); order.push(e); }
        e.parts.push({ patch: p, f: f });
        e.a += f.a; e.d += f.d;
      });
    });
    if (only === null) order.sort(function (x, y) { return x.path < y.path ? -1 : x.path > y.path ? 1 : 0; });
    order.forEach(function (e) {
      var first = e.parts[0].f, last = e.parts[e.parts.length - 1].f;
      e.kind = e.parts.length === 1 ? (first.k || '') : (first.k === 'a' && !last.k ? 'a' : last.k === 'd' ? 'd' : '');
      e.old = first.o || '';
      e.sim = first.s;
      e.binary = e.parts.some(function (x) { return x.f.b; });
      e.combined = e.parts.some(function (x) { return x.f.cb; });
      e.cut = e.parts.some(function (x) { return x.f.t; });
      e.mode = (first.m && e.parts.length === 1) ? first.m : null;
      e.lines = e.parts.reduce(function (s, x) { return s + (x.f.x ? x.f.x.split('\n').length : 0); }, 0);
    });
    return order;
  }

  /* Directory tree with single-child chains merged. Uses Map so hostile names like __proto__ are harmless. */
  function buildTree(entries) {
    var root = { name: '', dirs: new Map(), files: [] };
    entries.forEach(function (e) {
      var parts = e.path.split('/'), name = parts.pop(), node = root;
      parts.forEach(function (p) {
        if (!node.dirs.has(p)) node.dirs.set(p, { name: p, dirs: new Map(), files: [] });
        node = node.dirs.get(p);
      });
      node.files.push({ name: name, entry: e });
    });
    function squash(node) {
      node.dirs.forEach(function (d, key) {
        while (d.dirs.size === 1 && d.files.length === 0) {
          var only = d.dirs.values().next().value;
          only.name = d.name + '/' + only.name;
          d = only;
        }
        squash(d);
        node.dirs.set(key, d);
      });
    }
    squash(root);
    return root;
  }

  function badge(text, cls, title) { return h('span', { class: 'diff-badge ' + (cls || ''), title: title || null }, text); }

  function counts(a, d) {
    return h('span', { class: 'diff-counts' }, h('span', { class: 'add' }, '+' + a), ' ', h('span', { class: 'del' }, '-' + d));
  }

  var bars = LH.diffBars;

  function kb(n) { return n >= 1024 ? Math.round(n / 1024) + ' KB' : n + ' bytes'; }

  /* ---------------------------------------------------------------- viewed (localStorage) */
  function viewedStore(key) {
    var data = {};
    try { data = JSON.parse(localStorage.getItem(VIEWED_PREFIX + key) || '{}') || {}; } catch (e) { data = {}; }
    if (typeof data !== 'object' || Array.isArray(data)) data = {};
    return {
      has: function (k) { return Object.prototype.hasOwnProperty.call(data, k); },
      set: function (k, on) {
        if (on) data[k] = 1; else delete data[k];
        try { localStorage.setItem(VIEWED_PREFIX + key, JSON.stringify(data)); } catch (e) { /* storage may be blocked */ }
      }
    };
  }

  /* ---------------------------------------------------------------- diff table */
  function diffTable(part) {
    var f = part.f;
    if (f.cb) return h('p', { class: 'diff-note muted' }, 'Combined (merge) diffs are not shown.');
    if (f.b && !f.x) return h('p', { class: 'diff-note muted' }, 'Binary file not shown.');
    if (!f.x) {
      var why = f.t ? 'The diff text of this file is not kept because the patch is larger than LoreHub stores.' :
        f.k === 'r' ? 'File renamed without changes.' : f.k === 'c' ? 'File copied without changes.' :
        f.m ? 'File mode changed from ' + f.m[0] + ' to ' + f.m[1] + '.' : 'No textual changes.';
      return h('p', { class: 'diff-note muted' }, why);
    }
    var tbody = h('tbody');
    parseHunks(f.x).forEach(function (r) {
      var tr;
      if (r.t === 'hunk') {
        tr = h('tr', { class: 'd-hunk' }, h('td', { class: 'ln' }), h('td', { class: 'ln' }), h('td', { class: 'mk' }), h('td', { class: 'code' }, r.s));
      } else if (r.t === 'note') {
        tr = h('tr', { class: 'd-note' }, h('td', { class: 'ln' }), h('td', { class: 'ln' }), h('td', { class: 'mk' }), h('td', { class: 'code' }, r.s));
      } else {
        tr = h('tr', { class: 'd-' + r.t },
          h('td', { class: 'ln', 'data-n': r.o === undefined ? null : String(r.o) }),
          h('td', { class: 'ln', 'data-n': r.n === undefined ? null : String(r.n) }),
          h('td', { class: 'mk' }, r.t === 'add' ? '+' : r.t === 'del' ? '-' : ' '),
          h('td', { class: 'code' }, r.s));
      }
      tbody.appendChild(tr);
    });
    var table = h('table', { class: 'diff-table', 'aria-label': 'Unified diff of ' + part.f.p }, tbody);
    var out = h('div', { class: 'diff-scroll', tabindex: '0', role: 'region', 'aria-label': 'Diff of ' + part.f.p }, table);
    if (f.t) out.appendChild(h('p', { class: 'diff-note muted' }, 'The rest of this file is not shown, the patch was cut at LoreHub\'s size limit.'));
    return out;
  }

  /* ---------------------------------------------------------------- main */
  LH.filesTab = { parseHunks: parseHunks, collect: collect, buildTree: buildTree };

  LH.renderFiles = function (ctx) {
    var d = ctx.d, list = ctx.list, n = ctx.n, query = ctx.query;
    var patches = d.patches || [];
    var wrap = h('div', { class: 'files-tab' });
    if (!patches.length) {
      wrap.appendChild(LH.empty('No patches collected', 'Only replies to this series are known. The patch messages are older than the window. ' +
        'Open the thread on lore to see the diff.'));
      return Promise.resolve(wrap);
    }
    var statNote = d.fc ? LH.plural(d.fc, 'file') + ' changed, +' + d.fa + ' -' + d.fd + ' according to the patches. ' : '';
    var pending = d.df ? LH.getGz('data/diffs/' + encodeURIComponent(list) + '/' + n + '.json.gz').then(function (x) { return x.p; }) :
      Promise.resolve(null);
    return pending.then(function (diffs) {
      if (!diffs) {
        /* defensive only: the build shows a series only when its diff is stored, so this is not reachable in a normal build */
        wrap.appendChild(h('div', { class: 'card empty', 'data-empty': 'no-diff' }, h('h3', null, 'The diff of this series is not available'),
          h('p', { class: 'muted' }, statNote + 'The diff file could not be loaded.'),
          h('p', null, ctx.loreLink(patches[0], 'View the patches on lore'))));
        return wrap;
      }
      build(wrap, ctx, patches, diffs, statNote);
      return wrap;
    }, function (err) {
      wrap.appendChild(LH.errorBox(err, 'the diff of this series'));
      return wrap;
    });
  };

  /* "diffs for patches 4-7 are not stored": ranges of the patch numbers that have no diff in the series. */
  function ranges(nums) {
    var out = [], i = 0;
    nums.sort(function (a, b) { return a - b; });
    while (i < nums.length) {
      var j = i;
      while (j + 1 < nums.length && nums[j + 1] === nums[j] + 1) j++;
      out.push(j > i ? nums[i] + '-' + nums[j] : String(nums[i]));
      i = j + 1;
    }
    return out.join(', ');
  }
  LH.filesTab.ranges = ranges;

  function build(wrap, ctx, patches, diffs, statNote) {
    var d = ctx.d, list = ctx.list, n = ctx.n, query = ctx.query;
    var total = d.total || patches.length;
    var only = null;
    var want = parseInt(query.get('patch'), 10);
    if (want > 0 && patches.some(function (p) { return p.i === want; })) only = want;
    var entries, cards;
    var storeKey = list + '/' + n;
    var store = viewedStore(storeKey);

    var summary = h('div', { class: 'files-summary' });
    var progress = h('span', { class: 'muted small', role: 'status' });
    var banners = h('div', { class: 'files-banners' });
    var treeBox = h('nav', { class: 'files-tree', 'aria-label': 'Changed files' });
    var treeToggle = h('button', { type: 'button', class: 'btn', 'aria-expanded': 'true', 'aria-controls': 'files-tree-list' }, 'Hide file tree');
    var listBox = h('div', { class: 'files-list' });
    var patchBox = h('div', { class: 'files-patch' });
    var layout = h('div', { class: 'files-layout' }, treeBox, h('div', { class: 'files-main' }, patchBox, listBox));
    var select = h('select', { id: 'patch-select', 'aria-label': 'Show all patches or one patch' });

    select.appendChild(h('option', { value: 'all' }, 'All patches combined'));
    patches.forEach(function (p) {
      var pd = diffs[p.mid];
      var o = h('option', { value: String(p.i) }, p.i + '/' + total + ' ' + p.t + (pd ? ' (' + LH.plural(pd.f.length, 'file') + ')' : ' (no diff stored)'));
      select.appendChild(o);
    });
    select.value = only === null ? 'all' : String(only);

    function sync(file) {
      var p = new URLSearchParams();
      p.set('tab', 'files');
      if (only !== null) p.set('patch', String(only));
      if (file) p.set('file', file);
      history.replaceState(null, '', LH.threadHref(list, 'pr', n) + '?' + p.toString());
    }
    function key(e) { return (only === null ? 'all' : only) + '|' + e.path; }

    function updateProgress() {
      var done = entries.filter(function (e) { return store.has(key(e)); }).length;
      progress.textContent = done + ' / ' + entries.length + ' files viewed';
    }

    function setOpen(c, open) {
      c.toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      c.body.hidden = !open;
      c.section.classList.toggle('collapsed', !open);
      if (open && !c.built) {
        c.built = true;
        c.entry.parts.forEach(function (part) {
          if (c.entry.parts.length > 1 || (only === null && patches.length > 1)) {
            c.body.appendChild(h('div', { class: 'diff-part' }, 'Patch ' + part.patch.i + '/' + total + ': ' + part.patch.t));
          }
          c.body.appendChild(diffTable(part));
        });
      }
    }

    function focusFile(c, push) {
      setOpen(c, true);
      Array.prototype.forEach.call(treeBox.querySelectorAll('a.tree-file'), function (a) { a.removeAttribute('aria-current'); });
      if (c.treeLink) c.treeLink.setAttribute('aria-current', 'true');
      c.section.scrollIntoView({ block: 'start' });
      var head = c.section.querySelector('.diff-name-link');
      if (head) head.focus({ preventScroll: true });
      if (push !== false) sync(c.entry.path);
    }

    function card(e, i) {
      var title = [];
      if (e.kind === 'r' || e.kind === 'c') title.push(h('span', { class: 'muted' }, e.old), ' ', h('span', { class: 'muted' }, '\u2192'), ' ');
      title.push(e.path);
      var badges = [];
      if (e.kind === 'a') badges.push(badge('new', 'b-new', 'New file'));
      if (e.kind === 'd') badges.push(badge('deleted', 'b-deleted', 'Deleted file'));
      if (e.kind === 'r') badges.push(badge('renamed' + (e.sim !== undefined ? ' ' + e.sim + '%' : ''), 'b-renamed', 'Renamed'));
      if (e.kind === 'c') badges.push(badge('copied' + (e.sim !== undefined ? ' ' + e.sim + '%' : ''), 'b-renamed', 'Copied'));
      if (e.binary) badges.push(badge('binary', 'b-binary', 'Binary file'));
      if (e.mode) badges.push(badge('mode ' + e.mode[0] + ' \u2192 ' + e.mode[1], 'b-mode', 'File mode changed'));
      if (e.combined) badges.push(badge('combined', 'b-binary', 'Combined diff, not shown'));
      if (e.cut) badges.push(badge('truncated', 'b-cut', 'Part of this diff was cut at the size limit'));

      var bodyId = 'diff-body-' + i;
      var toggle = h('button', { type: 'button', class: 'diff-toggle', 'aria-expanded': 'true', 'aria-controls': bodyId,
        'aria-label': 'Toggle diff of ' + e.path }, LH.icon('chevron'));
      var box = h('input', { type: 'checkbox', 'aria-label': 'Mark ' + e.path + ' as viewed' });
      var link = h('a', { class: 'diff-name-link', href: LH.threadHref(list, 'pr', n) + '?tab=files&file=' + encodeURIComponent(e.path) +
        (only !== null ? '&patch=' + only : ''), title: 'Link to this file' }, title);
      var section = h('section', { class: 'diff-file', id: 'diff-file-' + i, 'aria-label': e.path },
        h('header', { class: 'diff-file-head' }, toggle, h('span', { class: 'diff-name' }, link), badges,
          h('span', { class: 'spacer' }), counts(e.a, e.d), bars(e.a, e.d),
          h('label', { class: 'viewed' }, box, ' Viewed')));
      var body = h('div', { class: 'diff-body', id: bodyId });
      section.appendChild(body);
      var c = { entry: e, section: section, body: body, toggle: toggle, box: box, built: false, treeLink: null };
      toggle.addEventListener('click', function () { setOpen(c, c.body.hidden); });
      link.addEventListener('click', function (ev) { ev.preventDefault(); focusFile(c); });
      box.addEventListener('change', function () {
        store.set(key(e), box.checked);
        if (c.treeLink) c.treeLink.classList.toggle('is-viewed', box.checked);
        setOpen(c, !box.checked);
        updateProgress();
      });
      return c;
    }

    function treeNode(node, depth) {
      var ul = h('ul', { class: 'tree-list' });
      var dirs = Array.from(node.dirs.values()).sort(function (a, b) { return a.name < b.name ? -1 : 1; });
      dirs.forEach(function (dn) {
        ul.appendChild(h('li', { class: 'tree-dir' },
          h('details', { open: true }, h('summary', null, LH.icon('folder', 'muted'), ' ', h('span', { class: 'tree-name' }, dn.name)), treeNode(dn, depth + 1))));
      });
      node.files.slice().sort(function (a, b) { return a.name < b.name ? -1 : 1; }).forEach(function (f) {
        var c = f.entry.card;
        var a = h('a', { class: 'tree-file', href: LH.threadHref(list, 'pr', n) + '?tab=files&file=' + encodeURIComponent(f.entry.path) +
          (only !== null ? '&patch=' + only : ''), title: f.entry.path },
          h('span', { class: 'tree-name' }, f.name), ' ', h('span', { class: 'tree-counts' }, h('span', { class: 'add' }, '+' + f.entry.a), ' ', h('span', { class: 'del' }, '-' + f.entry.d)));
        a.addEventListener('click', function (ev) { ev.preventDefault(); focusFile(c); });
        c.treeLink = a;
        ul.appendChild(h('li', { class: 'tree-item' }, a));
      });
      return ul;
    }

    function patchHeader() {
      LH.clear(patchBox);
      if (only === null) return;
      var p = patches.filter(function (x) { return x.i === only; })[0];
      var box = h('div', { class: 'card pad-sm patch-head' },
        h('div', { class: 'commit-head' }, LH.icon('commit', 'muted'), h('span', { class: 'chip' }, p.i + '/' + total), h('strong', null, p.t)),
        h('div', { class: 'muted small' }, p.a ? h('a', { href: LH.authorHref(p.ah) }, p.a) : 'unknown', ' - ', LH.timeEl(p.o), ' - ', ctx.loreLink(p, 'patch on lore')),
        h('details', { class: 'commit-body', open: true }, h('summary', null, 'Commit message'), ctx.bodyNode(p)));
      var nav = h('div', { class: 'patch-nav' });
      var idx = patches.indexOf(p);
      if (idx > 0) nav.appendChild(h('button', { type: 'button', class: 'btn', onclick: function () { choose(String(patches[idx - 1].i)); } }, '\u2190 Previous patch'));
      if (idx + 1 < patches.length) nav.appendChild(h('button', { type: 'button', class: 'btn', onclick: function () { choose(String(patches[idx + 1].i)); } }, 'Next patch \u2192'));
      box.appendChild(nav);
      patchBox.appendChild(box);
    }

    function warnings() {
      LH.clear(banners);
      var missing = patches.filter(function (p) { return !diffs[p.mid]; }).map(function (p) { return p.i; });
      if (missing.length) {
        banners.appendChild(h('div', { class: 'banner', 'data-notice': 'partial-diffs' }, 'Diffs for ' + (missing.length === 1 ? 'patch ' : 'patches ') +
          ranges(missing) + ' ' + (missing.length === 1 ? 'is' : 'are') + ' not stored, ', ctx.loreLink(patches[0], 'see lore'), '.'));
      }
      var shown = only === null ? patches : patches.filter(function (p) { return p.i === only; });
      shown.forEach(function (p) {
        var pd = diffs[p.mid];
        if (!pd) {
          if (only !== null) banners.appendChild(h('div', { class: 'banner' }, 'Patch ' + p.i + '/' + total + ' has no stored diff (not kept, or its message was not seen). ', ctx.loreLink(p, 'View full patch on lore'), '.'));
        } else if (pd.tr) {
          banners.appendChild(h('div', { class: 'banner banner-warn' }, 'The diff of patch ' + p.i + '/' + total + ' was truncated: it is ' + kb(pd.sz) +
            ' and LoreHub keeps at most 120 KB per patch and 600 KB per series. ', ctx.loreLink(p, 'View full patch on lore'), '.'));
        }
      });
    }

    function render(focus) {
      entries = collect(patches, diffs, only);
      LH.clear(listBox); LH.clear(treeBox);
      cards = entries.map(function (e, i) { var c = card(e, i); e.card = c; return c; });
      var totalA = entries.reduce(function (s, e) { return s + e.a; }, 0), totalD = entries.reduce(function (s, e) { return s + e.d; }, 0);
      LH.clear(summary);
      summary.appendChild(h('strong', null, LH.plural(entries.length, 'file') + ' changed'));
      summary.appendChild(document.createTextNode(', '));
      summary.appendChild(h('span', { class: 'add' }, '+' + totalA));
      summary.appendChild(document.createTextNode(' '));
      summary.appendChild(h('span', { class: 'del' }, '-' + totalD));
      patchHeader();
      warnings();
      if (!entries.length) {
        listBox.appendChild(LH.empty('No file changes in this selection', statNote || 'The selected patch carries no diff.'));
        return;
      }
      var lines = 0;
      cards.forEach(function (c) {
        listBox.appendChild(c.section);
        var done = store.has(key(c.entry));
        c.box.checked = done;
        if (c.treeLink) c.treeLink.classList.toggle('is-viewed', done);
        var open = !done && lines < EAGER_LINES;
        if (open) lines += c.entry.lines;
        setOpen(c, open);
      });
      treeBox.appendChild(h('h3', { class: 'sr-only' }, 'Changed files'));
      var rootList = treeNode(buildTree(entries), 0);
      rootList.id = 'files-tree-list';
      treeBox.appendChild(rootList);
      cards.forEach(function (c) { if (c.treeLink && store.has(key(c.entry))) c.treeLink.classList.add('is-viewed'); });
      updateProgress();
      if (focus) {
        var hit = cards.filter(function (c) { return c.entry.path === focus; })[0];
        if (hit) focusFile(hit, false);
      }
    }

    function choose(val) {
      only = val === 'all' ? null : parseInt(val, 10);
      select.value = val;
      sync(null);
      render(null);
      var top = document.getElementById('patch-select');
      if (top) top.focus({ preventScroll: true });
    }
    select.addEventListener('change', function () { choose(select.value); });

    function setTree(open) {
      treeBox.hidden = !open;
      layout.classList.toggle('no-tree', !open);
      treeToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      treeToggle.textContent = open ? 'Hide file tree' : 'Show file tree';
    }
    treeToggle.addEventListener('click', function () { setTree(treeBox.hidden); });
    function all(open) { cards.forEach(function (c) { setOpen(c, open); }); }

    var toolbar = h('div', { class: 'files-toolbar card' },
      summary, h('span', { class: 'spacer' }), progress,
      h('label', { class: 'version-picker' }, h('span', { class: 'sr-only' }, 'Patch'), select),
      h('button', { type: 'button', class: 'btn', onclick: function () { all(true); } }, 'Expand all'),
      h('button', { type: 'button', class: 'btn', onclick: function () { all(false); } }, 'Collapse all'),
      treeToggle);
    wrap.appendChild(toolbar);
    wrap.appendChild(banners);
    wrap.appendChild(layout);
    if (window.matchMedia && window.matchMedia('(max-width: 860px)').matches) setTree(false);
    render(query.get('file'));
  }
})();
