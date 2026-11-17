/* Review by e-mail: line comments in the Files changed tab, the "Review changes" dialog, the Reply composer of the
 * Conversation tab and the panels with the prepared e-mails. The site is static and cannot send anything: it only prepares
 * plain text replies (see reviewmail.js) that the reviewer sends from their own mail client. Drafts and the reviewer's
 * identity stay in this browser (reviewstore.js). Every node is built with createElement/textContent. */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var RM = window.LHReviewMail, RS = window.LHReviewStore;
  var PRIVACY = 'Nothing is sent. Your drafts and identity stay in this browser.';
  var TAG_NOTE = 'Kernel tags are statements about the whole patch: a Reviewed-by says you reviewed all of it, not only the lines you commented on.';
  var modalSeq = 0;
  var replyDrafts = {};            // text of a reply composer that was closed without preparing, per message, until the page is reloaded
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function storage() { try { return window.localStorage; } catch (e) { return null; } }

  /* ---------------------------------------------------------------- modal dialog */

  /* A dialog with a focus trap, aria-modal, Esc to close, focus back to the opener. The page behind is inert. */
  function modal(title, opener, extraClass) {
    var id = 'rv-modal-' + (++modalSeq);
    var titleEl = h('h2', { id: id + '-title', class: 'rv-title' }, title);
    var body = h('div', { class: 'rv-modal-body' });
    var box = h('div', { class: 'rv-modal ' + (extraClass || ''), role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id + '-title', tabindex: '-1' }, titleEl, body);
    var backdrop = h('div', { class: 'rv-backdrop' }, box);
    var behind = Array.prototype.slice.call(document.querySelectorAll('body > header, body > main, body > footer, body > .skip-link'));
    var urls = [], closers = [], closed = false;
    var api = { id: id, box: box, body: body, urls: urls };

    function focusables() {
      return Array.prototype.filter.call(box.querySelectorAll(FOCUSABLE), function (el) { return !el.hidden && el.offsetParent !== null; });
    }
    function onKey(ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); api.close(); return; }
      if (ev.key !== 'Tab') return;
      var f = focusables();
      if (!f.length) { ev.preventDefault(); box.focus(); return; }
      var first = f[0], last = f[f.length - 1], cur = document.activeElement;
      if (ev.shiftKey && (cur === first || cur === box || !box.contains(cur))) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && (cur === last || !box.contains(cur))) { ev.preventDefault(); first.focus(); }
    }
    function onHash() { api.close(); }
    api.setTitle = function (t) { titleEl.textContent = t; };
    api.onclose = function (fn) { closers.push(fn); };
    api.focusFirst = function () {
      var f = focusables().filter(function (el) { return !el.classList.contains('rv-close'); });
      (f[0] || box).focus();
    };
    api.close = function () {
      if (closed) return;
      closed = true;
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('hashchange', onHash);
      urls.forEach(function (u) { URL.revokeObjectURL(u); });
      closers.forEach(function (fn) { fn(); });
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      behind.forEach(function (el) { el.removeAttribute('inert'); });
      document.body.classList.remove('rv-modal-open');
      if (opener && opener.isConnected) opener.focus();
    };
    behind.forEach(function (el) { el.setAttribute('inert', ''); });
    document.body.classList.add('rv-modal-open');
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('hashchange', onHash);
    return api;
  }

  /* ---------------------------------------------------------------- small helpers */

  var fieldSeq = 0;
  function uid(p) { return p + (++fieldSeq); }

  function field(label, control, hint) {
    var id = uid('rv-f-');
    control.id = id;
    return h('div', { class: 'rv-field' }, h('label', { for: id }, label), control, hint ? h('div', { class: 'muted small' }, hint) : null);
  }

  function radio(name, value, label, checked, desc) {
    var id = uid('rv-r-');
    var input = h('input', { type: 'radio', name: name, value: value, id: id });
    input.checked = !!checked;
    return { input: input, node: h('div', { class: 'rv-radio' }, input, h('label', { for: id }, label, desc ? h('span', { class: 'muted small rv-desc' }, desc) : null)) };
  }

  function copyText(text, status) {
    function fallback() {
      var ta = h('textarea', { class: 'sr-only', 'aria-hidden': 'true', tabindex: '-1', readonly: 'readonly' });
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      return ok;
    }
    var done = function (ok) { status.textContent = ok ? 'Copied.' : 'Could not copy automatically: select the text and copy it by hand.'; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallback()); });
    } else done(fallback());
  }

  function download(mail, api) {
    var blob = new Blob([mail.text], { type: 'message/rfc822' });
    var url = URL.createObjectURL(blob);
    api.urls.push(url);
    var a = h('a', { href: url, download: mail.filename, class: 'sr-only' });
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () {
      URL.revokeObjectURL(url);
      var i = api.urls.indexOf(url);
      if (i >= 0) api.urls.splice(i, 1);
    }, 10000);
  }

  function identityBox(session, onEnter) {
    var cur = session.ident.get();
    var name = h('input', { type: 'text', autocomplete: 'name', spellcheck: 'false', maxlength: '200' });
    var email = h('input', { type: 'email', autocomplete: 'email', spellcheck: 'false', maxlength: '254' });
    name.value = cur.name; email.value = cur.email;
    var forgot = h('span', { class: 'muted small', role: 'status' });
    var forget = h('button', { type: 'button', class: 'btn btn-small', onclick: function () {
      session.ident.forget();
      name.value = ''; email.value = '';
      forgot.textContent = 'Forgotten: your name and address were removed from this browser.';
      name.focus();
    } }, 'Forget me');
    function save() { session.ident.set({ name: name.value, email: email.value }); }
    name.addEventListener('change', save); email.addEventListener('change', save);
    [name, email].forEach(function (el) { el.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && onEnter) { ev.preventDefault(); onEnter(); } }); });
    var node = h('fieldset', { class: 'rv-fieldset rv-identity' },
      h('legend', null, 'Who you are'),
      h('p', { class: 'muted small' }, 'Your name and address go into the From header and the tag of the e-mail. They are stored only in this browser (localStorage key ' + RS.IDENTITY_KEY + ').'),
      h('div', { class: 'rv-two' }, field('Name', name), field('E-mail address', email)),
      h('div', { class: 'rv-inline' }, forget, forgot));
    return { node: node, read: function () { save(); return session.ident.get(); }, name: name, email: email };
  }

  function storageNotice(session) {
    if (session.drafts.persistent && session.ident.persistent) return null;
    return h('p', { class: 'banner banner-warn rv-notice', 'data-notice': 'no-storage' },
      'This browser does not allow local storage here (private mode?), so your comments and your name are kept in memory only: they are gone when you reload or close the page.');
  }

  /* ---------------------------------------------------------------- the session of one thread page */

  /* opts = {d: thread detail, bodies: texts or null, diffs: the diff shard's "p" map or null}. */
  function makeSession(opts) {
    var d = opts.d;
    var s = {
      d: d, bodies: opts.bodies || [], diffs: opts.diffs || null,
      drafts: RS.openDrafts(storage(), RS.draftKey(d.home, d.n, d.v || 1)),
      ident: RS.identity(storage()),
      shardP: null, boxes: new Map(), revealer: null, afterDiscard: null
    };
    s.order = (d.cover ? [d.cover] : []).concat(d.patches || [], d.cm || []);
    s.shard = function () {
      if (!s.shardP) {
        s.shardP = LH.getGz('data/reply/' + encodeURIComponent(d.home) + '/' + d.n + '.json.gz').then(function (x) { return x; }, function (err) {
          s.shardError = err;
          return null;
        });
      }
      return s.shardP;
    };
    s.drafts.onchange(function () {
      s.boxes.forEach(function (b, id) {
        var c = s.drafts.find(id);
        if (!c || !b.label.isConnected) return;
        b.label.textContent = c.prepared ? 'Prepared' : 'Pending';
        b.label.className = 'rv-label ' + (c.prepared ? 'prepared' : 'pending');
        b.label.title = c.prepared ? 'Included in prepared e-mails. Nothing was sent.' : 'Not yet included in prepared e-mails';
      });
    });
    s.patch = function (mid) { return (d.patches || []).filter(function (p) { return p.mid === mid; })[0] || null; };
    return s;
  }

  /* ---------------------------------------------------------------- line comments (used by files.js) */

  /* One place that can be commented on: the diff of one file in one patch (kind 'diff') or the commit message of a
   * patch (kind 'msg'). `lines` are the raw lines (diff rows with their +/-/space, or message lines), `cols` the
   * column count of the table the comment rows go into. */
  function makePart(session, patch, path, kind, lines, cols) {
    var drafts = session.drafts;
    var mine = null;
    var part = { patch: patch, path: path, kind: kind, first: true };

    function comments() {
      return drafts.comments.filter(function (c) { return c.patch === patch.mid && c.kind === kind && (kind === 'msg' || c.path === path); });
    }
    function where(ix, label) { return (kind === 'msg' ? 'the commit message of patch ' + patch.i : path) + ', ' + label; }

    function row(content, cid) {
      return h('tr', { class: 'rv-row', 'data-cid': cid || null }, h('td', { class: 'rv-cell', colspan: String(cols) }, content));
    }

    function display(c, label) {
      var who = session.ident.get().name || 'You';
      var pending = h('span', { class: 'rv-label ' + (c.prepared ? 'prepared' : 'pending'), title: c.prepared ? 'Included in prepared e-mails. Nothing was sent.' : 'Not yet included in prepared e-mails' },
        c.prepared ? 'Prepared' : 'Pending');
      var tr;
      var del = h('button', { type: 'button', class: 'btn btn-small', 'aria-label': 'Delete your comment on ' + where(c.ix, label), onclick: function () {
        if (!del.dataset.sure) {
          del.dataset.sure = '1'; del.textContent = 'Really delete?';
          setTimeout(function () { if (del.isConnected) { delete del.dataset.sure; del.textContent = 'Delete'; } }, 4000);
          return;
        }
        session.boxes.delete(c.id);
        drafts.remove(c.id);
        var next = tr.nextSibling, prevRow = tr.previousSibling;
        tr.parentNode.removeChild(tr);
        var focusTo = prevRow && prevRow.querySelector ? prevRow.querySelector('.rv-add') : null;
        if (focusTo) focusTo.focus(); else if (next && next.querySelector) { var b = next.querySelector('.rv-add'); if (b) b.focus(); }
      } }, 'Delete');
      var edit = h('button', { type: 'button', class: 'btn btn-small', 'aria-label': 'Edit your comment on ' + where(c.ix, label), onclick: function () {
        var ed = editor(c.ix, c.line, label, c);
        tr.parentNode.replaceChild(ed.tr, tr);
        session.boxes.delete(c.id);
        ed.ta.focus();
      } }, 'Edit');
      var box = h('div', { class: 'rv-box', role: 'group', 'aria-label': 'Your review comment on ' + where(c.ix, label) },
        h('div', { class: 'rv-head' }, h('strong', null, who), pending, h('span', { class: 'spacer' }), edit, del),
        h('div', { class: 'rv-body' }, c.text));
      tr = row(box, c.id);
      session.boxes.set(c.id, { label: pending });
      return tr;
    }

    function editor(ix, line, label, existing) {
      var ta = h('textarea', { class: 'rv-text', rows: '4', maxlength: String(RS.MAX_TEXT), 'aria-label': 'Review comment on ' + where(ix, label), placeholder: 'Leave a comment' });
      ta.value = existing ? existing.text : '';
      var tr;
      var add = h('button', { type: 'button', class: 'btn btn-primary btn-small' }, existing ? 'Save comment' : 'Add review comment');
      function sync() { add.disabled = !/\S/.test(ta.value); }
      function cancel() {
        if (existing) { var back = display(existing, label); tr.parentNode.replaceChild(back, tr); var e = back.querySelector('button'); if (e) e.focus(); }
        else { var prev = tr.previousSibling; tr.parentNode.removeChild(tr); var b = prev && prev.querySelector ? prev.querySelector('.rv-add') : null; if (b) b.focus(); }
      }
      add.addEventListener('click', function () {
        if (!/\S/.test(ta.value)) return;
        var c;
        try {
          c = existing ? drafts.update(existing.id, ta.value) : drafts.add({ patch: patch.mid, kind: kind, path: path || '', ix: ix, line: line, text: ta.value });
        } catch (err) { status.textContent = err.message; return; }
        var shown = display(c, label);
        tr.parentNode.replaceChild(shown, tr);
        var edit = shown.querySelector('button');
        if (edit) edit.focus();
      });
      ta.addEventListener('input', sync);
      ta.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && (!existing ? ta.value === '' : ta.value === existing.text)) { ev.preventDefault(); cancel(); }
        else if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); add.click(); }
      });
      var status = h('span', { class: 'muted small', role: 'status' });
      tr = row(h('div', { class: 'rv-box rv-edit' }, ta,
        h('div', { class: 'rv-actions' }, h('span', { class: 'muted small' }, 'Pending until you prepare the e-mails. Nothing is sent.'), h('span', { class: 'spacer' }),
          status, h('button', { type: 'button', class: 'btn btn-small', onclick: cancel }, 'Cancel'), add)));
      sync();
      tr.classList.add('rv-editor');
      return { tr: tr, ta: ta };
    }

    /* The "+" of one line: `ix` is the index of the line in `lines`, `label` names it for screen readers ("line 14"). */
    part.button = function (ix, label) {
      var b = h('button', { type: 'button', class: 'rv-add', tabindex: part.first ? '0' : '-1', 'data-ix': String(ix), title: 'Add a review comment',
        'aria-label': 'Add a review comment on ' + where(ix, label) }, '+');
      part.first = false;
      b.addEventListener('click', function () {
        var tr = b.closest('tr');
        var at = tr.nextSibling;
        while (at && at.classList && at.classList.contains('rv-row')) {
          if (at.classList.contains('rv-editor') && at.dataset.ix === String(ix)) { at.querySelector('textarea').focus(); return; }
          at = at.nextSibling;
        }
        var ed = editor(ix, lines[ix] === undefined ? null : lines[ix], label, null);
        ed.tr.dataset.ix = String(ix);
        tr.parentNode.insertBefore(ed.tr, at);
        ed.ta.focus();
        ed.tr.scrollIntoView({ block: 'nearest' });
      });
      return b;
    };

    /* Rows of the saved comments of these lines, to add right after the row of the line. */
    part.existing = function (pairs) {
      if (!mine) mine = comments();
      var out = [];
      pairs.forEach(function (pr) {
        mine.filter(function (c) { return c.ix === pr[0]; }).forEach(function (c) { out.push(display(c, pr[1])); });
      });
      return out;
    };
    part.reset = function () { mine = null; };
    return part;
  }

  /* Arrow keys move between the "+" buttons of a table, only one of them is in the tab order. */
  function roving(table) {
    table.addEventListener('focusin', function (ev) {
      var t = ev.target;
      if (!t.classList || !t.classList.contains('rv-add')) return;
      Array.prototype.forEach.call(table.querySelectorAll('.rv-add[tabindex="0"]'), function (b) { if (b !== t) b.setAttribute('tabindex', '-1'); });
      t.setAttribute('tabindex', '0');
    });
    table.addEventListener('keydown', function (ev) {
      var t = ev.target;
      if (!t.classList || !t.classList.contains('rv-add')) return;
      var key = ev.key;
      if (key !== 'ArrowDown' && key !== 'ArrowUp' && key !== 'ArrowLeft' && key !== 'ArrowRight') return;
      var all = Array.prototype.slice.call(table.querySelectorAll('.rv-add'));
      var i = all.indexOf(t), tr = t.closest('tr'), next = null;
      if (key === 'ArrowLeft' || key === 'ArrowRight') {
        var sib = Array.prototype.slice.call(tr.querySelectorAll('.rv-add'));
        next = sib[sib.indexOf(t) + (key === 'ArrowRight' ? 1 : -1)] || null;
      } else {
        var step = key === 'ArrowDown' ? 1 : -1, side = Array.prototype.indexOf.call(tr.querySelectorAll('.rv-add'), t);
        for (var k = i + step; k >= 0 && k < all.length; k += step) {
          if (all[k].closest('tr') === tr) continue;
          next = all[k];
          var sibs = all[k].closest('tr').querySelectorAll('.rv-add');
          if (sibs.length > 1 && sibs[side]) next = sibs[side];
          break;
        }
      }
      if (next) { ev.preventDefault(); next.focus(); }
    });
  }

  /* ---------------------------------------------------------------- the review dialog */

  function shortLine(line) {
    var t = String(line || '').replace(/\s+/g, ' ').trim();
    return t.length > 90 ? t.slice(0, 90) + '...' : t;
  }

  /* "line 14" or "removed line 12" for the list of comments. */
  function lineLabel(session, c) {
    if (c.kind === 'msg') return 'line ' + (c.ix + 1);
    var p = session.patch(c.patch);
    var f = p && session.diffs && session.diffs[p.mid] ? session.diffs[p.mid].f.filter(function (x) { return x.p === c.path; })[0] : null;
    var r = f && f.x && LH.filesTab ? LH.filesTab.parseHunks(f.x)[c.ix] : null;
    if (!r) return 'a line';
    return r.t === 'del' ? 'removed line ' + r.o : 'line ' + r.n;
  }

  function openReview(session, opener) {
    var d = session.d, drafts = session.drafts, rv = drafts.review;
    session.shard();          // the reply data is fetched now that the dialog is used, never on a normal page view
    var m = modal('Review changes', opener, 'rv-review');
    var hasCover = !!d.cover;
    var form = {
      state: rv.state, nack: rv.trailer === 'Nacked-by' && rv.state === 'request',
      approveTag: rv.state === 'approve' && rv.trailer ? rv.trailer : 'Reviewed-by',
      scope: rv.scope === 'series' && hasCover ? 'series' : rv.scope === 'patches' ? 'patches' : hasCover ? 'series' : 'patches',
      picks: rv.picks.slice()
    };

    var identity = identityBox(session, null);
    var overall = h('textarea', { class: 'rv-text', rows: '5', maxlength: String(RS.MAX_TEXT) });
    overall.value = rv.overall;
    var errors = h('div', { class: 'rv-errors', role: 'alert' });
    var stateName = uid('rv-state-');
    var rApprove = radio(stateName, 'approve', 'Approve', form.state === 'approve', 'Looks good. Adds a Reviewed-by, Acked-by or Tested-by tag.');
    var rComment = radio(stateName, 'comment', 'Comment', form.state === 'comment', 'General feedback, no tag.');
    var rRequest = radio(stateName, 'request', 'Request changes', form.state === 'request', 'Changes are needed. Adds no tag unless you choose Nacked-by below.');
    var tagName = uid('rv-tag-');
    var tagRadios = ['Reviewed-by', 'Acked-by', 'Tested-by'].map(function (t) { return radio(tagName, t, t, form.approveTag === t); });
    var nackBox = h('input', { type: 'checkbox', id: uid('rv-nack-') });
    nackBox.checked = form.nack;
    var tagBox = h('fieldset', { class: 'rv-fieldset rv-tag' }, h('legend', null, 'Tag'),
      h('div', { class: 'rv-approve-tags' }, tagRadios.map(function (r) { return r.node; })),
      h('div', { class: 'rv-nack' }, nackBox, h('label', { for: nackBox.id }, 'Add a Nacked-by tag (explicit, never selected for you)')),
      h('p', { class: 'muted small rv-tag-note' }, TAG_NOTE));
    var scopeName = uid('rv-scope-');
    var rSeries = radio(scopeName, 'series', 'The whole series: the tag goes into the reply to the cover letter (b4 applies it to every patch)', form.scope === 'series');
    var rPatches = radio(scopeName, 'patches', 'Only these patches: the tag goes into the reply to each patch you pick', form.scope === 'patches');
    var picks = (d.patches || []).map(function (p) {
      var box = h('input', { type: 'checkbox', value: p.mid, id: uid('rv-pick-') });
      box.checked = form.picks.indexOf(p.mid) >= 0;
      return { box: box, node: h('div', { class: 'rv-pick' }, box, h('label', { for: box.id }, 'Patch ' + p.i + '/' + (d.total || d.patches.length) + ': ' + p.t)) };
    });
    var pickBox = h('div', { class: 'rv-picks', role: 'group', 'aria-label': 'Patches that get the tag' }, picks.map(function (p) { return p.node; }));
    var scopeBox = h('fieldset', { class: 'rv-fieldset rv-scope' }, h('legend', null, 'Where the tag goes'),
      hasCover ? rSeries.node : null, rPatches.node, pickBox);
    if (!hasCover) scopeBox.appendChild(h('p', { class: 'muted small' }, 'This series has no cover letter, so a tag can only go to individual patches.'));

    var summary = h('div', { class: 'rv-summary' });
    var prepareBtn = h('button', { type: 'button', class: 'btn btn-primary' }, 'Prepare e-mails');
    var discardBtn = h('button', { type: 'button', class: 'btn' }, 'Discard review');

    function currentState() { return rApprove.input.checked ? 'approve' : rRequest.input.checked ? 'request' : 'comment'; }
    function effectiveTrailer() {
      var st = currentState();
      if (st === 'approve') { var t = tagRadios.filter(function (r) { return r.input.checked; })[0]; return t ? t.input.value : 'Reviewed-by'; }
      return st === 'request' && nackBox.checked ? 'Nacked-by' : '';
    }
    function scopeNow() { return hasCover && rSeries.input.checked ? 'series' : 'patches'; }
    function picked() { return picks.filter(function (p) { return p.box.checked; }).map(function (p) { return p.box.value; }); }
    function persist() {
      drafts.setReview({ state: currentState(), overall: overall.value, trailer: effectiveTrailer(), scope: scopeNow(), picks: picked() });
    }
    function show() {
      var st = currentState();
      tagBox.hidden = st === 'comment';
      tagBox.querySelector('.rv-approve-tags').hidden = st !== 'approve';
      tagBox.querySelector('.rv-nack').hidden = st !== 'request';
      tagBox.querySelector('.rv-tag-note').hidden = st === 'comment';
      scopeBox.hidden = !effectiveTrailer();
      pickBox.hidden = scopeNow() !== 'patches';
    }
    function onChange() { persist(); show(); }
    [overall, nackBox].concat(picks.map(function (p) { return p.box; }), tagRadios.map(function (r) { return r.input; }),
      [rApprove.input, rComment.input, rRequest.input, rSeries.input, rPatches.input]).forEach(function (el) {
      el.addEventListener(el === overall ? 'input' : 'change', onChange);
    });

    function renderSummary() {
      LH.clear(summary);
      var all = drafts.comments;
      summary.appendChild(h('h3', null, 'Line comments (' + all.length + ')'));
      if (!all.length) {
        summary.appendChild(h('p', { class: 'muted small' }, 'None yet. In Files changed, hover a line and press + to comment on it. A review with only an overall comment is fine too.'));
        return;
      }
      (d.patches || []).forEach(function (p) {
        var mine = all.filter(function (c) { return c.patch === p.mid; });
        if (!mine.length) return;
        var ul = h('ul', { class: 'rv-list' });
        var groups = [];
        mine.forEach(function (c) {
          var key = c.kind === 'msg' ? '' : c.path;
          var g = groups.filter(function (x) { return x.key === key; })[0];
          if (!g) { g = { key: key, items: [] }; groups.push(g); }
          g.items.push(c);
        });
        groups.forEach(function (g) {
          var items = h('ul', { class: 'rv-items' });
          g.items.sort(function (a, b) { return a.ix - b.ix; }).forEach(function (c) {
            var href = LH.threadHref(d.home, 'pr', d.n) + '?tab=files&patch=' + p.i + (c.kind === 'msg' ? '' : '&file=' + encodeURIComponent(c.path));
            var link = h('a', { href: href, class: 'rv-jump' }, lineLabel(session, c));
            link.addEventListener('click', function (ev) {
              if (!session.revealer) return;           // not on the Files changed tab: the link navigates there
              ev.preventDefault();
              m.close();
              session.revealer(c);
            });
            items.appendChild(h('li', null, link, ' ',
              h('span', { class: 'rv-label ' + (c.prepared ? 'prepared' : 'pending') }, c.prepared ? 'Prepared' : 'Pending'), ' ',
              h('span', { class: 'muted small rv-quoted' }, shortLine(c.line ? c.line.slice(1) : '')),
              h('div', { class: 'rv-excerpt' }, shortLine(c.text))));
          });
          ul.appendChild(h('li', null, h('div', { class: 'rv-file' }, g.key === '' ? 'Commit message' : g.key), items));
        });
        summary.appendChild(h('div', { class: 'rv-patch' }, h('h4', null, 'Patch ' + p.i + '/' + (d.total || d.patches.length) + ': ' + p.t), ul));
      });
    }

    discardBtn.addEventListener('click', function () {
      if (!discardBtn.dataset.sure) {
        discardBtn.dataset.sure = '1';
        discardBtn.textContent = 'Really discard all comments and the review text?';
        return;
      }
      drafts.clear();
      session.boxes.clear();
      m.close();
      if (session.afterDiscard) session.afterDiscard();
    });
    discardBtn.addEventListener('blur', function () { delete discardBtn.dataset.sure; discardBtn.textContent = 'Discard review'; });

    function prepare() {
      persist();
      LH.clear(errors);
      prepareBtn.disabled = true;
      var ident = identity.read();
      session.shard().then(function (shard) {
        prepareBtn.disabled = false;
        var data = Object.assign({}, drafts.review, { comments: drafts.comments });
        var input = RM.fromSite({ detail: d, reply: shard, bodies: session.bodies, diffs: session.diffs || {} }, data);
        input.now = LH.now();
        input.reviewer = ident;
        var res = RM.buildReview(input);
        var errs = res.problems.filter(function (p) { return p.level === 'error'; });
        if (errs.length) {
          errs.forEach(function (p) { errors.appendChild(h('p', { class: 'rv-error' }, p.text)); });
          var bad = errs.some(function (p) { return p.code === 'name'; }) ? identity.name : errs.some(function (p) { return p.code === 'email'; }) ? identity.email : null;
          if (bad) bad.focus();
          return;
        }
        drafts.markPrepared(res.used);
        showResult(session, m, res, shard, function () { openReviewBody(); });
      });
    }
    prepareBtn.addEventListener('click', prepare);

    function openReviewBody() {
      LH.clear(m.body);
      m.setTitle('Review changes');
      renderSummary();
      m.body.appendChild(h('p', { class: 'rv-privacy', 'data-note': 'privacy' }, h('strong', null, PRIVACY),
        ' The site only prepares e-mails for your own mail client, it never sends anything.'));
      var n = storageNotice(session);
      if (n) m.body.appendChild(n);
      m.body.appendChild(summary);
      m.body.appendChild(field('Overall comment', overall, 'Becomes the reply to the cover letter. Wrapped at 72 columns in the e-mail.'));
      m.body.appendChild(h('fieldset', { class: 'rv-fieldset rv-state' }, h('legend', null, 'Your review'), rApprove.node, rComment.node, rRequest.node));
      m.body.appendChild(tagBox);
      m.body.appendChild(scopeBox);
      m.body.appendChild(identity.node);
      m.body.appendChild(errors);
      m.body.appendChild(h('div', { class: 'rv-actions rv-footer' }, discardBtn, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn', onclick: function () { m.close(); } }, 'Cancel'), prepareBtn));
      show();
    }
    openReviewBody();
    m.body.appendChild(h('button', { type: 'button', class: 'btn rv-close sr-only', onclick: function () { m.close(); } }, 'Close dialog'));
    m.focusFirst();
    return m;
  }

  /* ---------------------------------------------------------------- the prepared e-mails */

  function recipientsText(list) {
    return list.map(function (p) { return p.name ? p.name + ' <' + p.addr + '>' : p.addr; }).join(', ');
  }

  function emailPanel(mail, m, status) {
    var title = uid('rv-mail-');
    var pre = h('pre', { class: 'rv-eml', tabindex: '0', 'aria-label': 'Complete text of the e-mail: ' + mail.label });
    pre.textContent = mail.text;
    var meta = h('dl', { class: 'rv-meta' },
      h('dt', null, 'To'), h('dd', { 'data-field': 'to' }, mail.headers.to.length ? recipientsText(mail.headers.to) : 'not known: add the recipient yourself'),
      h('dt', null, 'Cc'), h('dd', { 'data-field': 'cc' }, mail.headers.cc.length ? recipientsText(mail.headers.cc) : 'nobody known'),
      h('dt', null, 'Subject'), h('dd', { 'data-field': 'subject' }, mail.headers.subject),
      h('dt', null, 'Replies to'), h('dd', null, '<' + mail.headers.inReplyTo + '>'));
    var warnings = mail.warnings.map(function (w) { return h('p', { class: 'banner banner-warn rv-warning', 'data-warning': w.code }, w.text); });
    var buttons = [
      h('button', { type: 'button', class: 'btn btn-primary btn-small', 'data-action': 'download', onclick: function () { download(mail, m); status.textContent = 'Downloaded ' + mail.filename + '.'; } }, 'Download .eml'),
      h('button', { type: 'button', class: 'btn btn-small', 'data-action': 'copy', onclick: function () { copyText(mail.text, status); } }, 'Copy whole text'),
      mail.mailto ? h('a', { class: 'btn btn-small', href: mail.mailto, 'data-action': 'mailto' }, 'Open in mail app') : null,
      h('button', { type: 'button', class: 'btn btn-small', 'data-action': 'command', onclick: function () { copyText(mail.command, status); } }, 'Copy git send-email command'),
      LH.extLink(mail.lore, 'Open on lore', 'btn btn-small')
    ];
    buttons[buttons.length - 1].setAttribute('data-action', 'lore');
    var hint = [];
    if (!mail.mailto) hint.push(h('p', { class: 'muted small', 'data-hint': 'mailto' }, 'No mail app link: this e-mail is too long for one (limit about 1800 characters). Use Download or Copy whole text.'));
    else hint.push(h('p', { class: 'muted small', 'data-hint': 'mailto' }, 'The mail app link fills in To, Cc, Subject, the reply header and the text, but your app picks the sender.'));
    hint.push(h('p', { class: 'muted small', 'data-hint': 'git' }, 'git send-email: save the file with Download .eml first and run the command in that folder. ' +
      'git send-email takes From, Subject and the text from the file, and the recipients and the message it answers from the command line; add --dry-run to try it. ' +
      'The list may want a plain reply, so check the To and Cc above first.'));
    return h('section', { class: 'rv-mail card', 'aria-labelledby': title, 'data-mail': mail.key },
      h('h3', { id: title }, mail.label + ': ', h('span', { class: 'muted' }, mail.headers.subject)),
      meta, warnings,
      h('div', { class: 'rv-buttons' }, buttons),
      hint, pre);
  }

  function showResult(session, m, res, shard, back) {
    LH.clear(m.body);
    m.setTitle('Prepared e-mails');
    var status = h('p', { class: 'rv-status', role: 'status', 'aria-live': 'polite' });
    m.body.appendChild(h('p', { class: 'rv-privacy', 'data-note': 'prepared' }, h('strong', null, 'Nothing was sent.'),
      ' These are drafts: download them or copy them into your own mail client and send them from there. Your drafts and identity stay in this browser.'));
    if (!shard) {
      m.body.appendChild(h('p', { class: 'banner banner-warn', 'data-warning': 'no-shard' },
        'The address data of this thread could not be loaded' + (session.shardError ? ' (' + session.shardError.message + ')' : '') +
        ', so the recipients are empty. Add To and Cc yourself.'));
    }
    res.problems.filter(function (p) { return p.level !== 'error'; }).forEach(function (p) {
      m.body.appendChild(h('p', { class: 'banner', 'data-warning': p.code }, p.text));
    });
    res.emails.forEach(function (mail) { m.body.appendChild(emailPanel(mail, m, status)); });
    m.body.appendChild(status);
    m.body.appendChild(h('div', { class: 'rv-actions rv-footer' }, back ? h('button', { type: 'button', class: 'btn', onclick: back }, 'Back to the review') : null,
      h('span', { class: 'spacer' }), h('button', { type: 'button', class: 'btn btn-primary rv-done', onclick: function () { m.close(); } }, 'Close')));
    m.box.scrollTop = 0;
    var first = m.body.querySelector('[data-action="download"]');
    (first || m.box).focus();
  }

  /* ---------------------------------------------------------------- the Reply composer (Conversation tab) */

  function openReply(session, i, opener) {
    var d = session.d, msg = session.order[i];
    var mid = RM.midOf(msg.mid);
    session.shard();
    var m = modal('Reply to ' + (msg.a || 'this message'), opener, 'rv-reply');
    var identity = identityBox(session, null);
    var text = session.bodies[i];
    var notes = [];
    if (text === null || text === undefined) notes.push('The text of this message is not kept by LkmlHub, so nothing is quoted. Open it on lore to quote it by hand.');
    if (msg.lb) notes.push('The stored text is a shortened copy of the message (an older format), so the quote is incomplete.');
    if (msg.bt) notes.push('The message is longer than the 128 KB LkmlHub keeps, so the quote is cut.');
    var isPatch = (d.patches || []).some(function (p) { return p.mid === msg.mid; });
    if (isPatch) notes.push('Only the commit message is quoted. To comment on lines of the diff use Files changed and Review changes.');
    var ta = h('textarea', { class: 'rv-text rv-compose', rows: '16', maxlength: String(RS.MAX_TEXT * 3), spellcheck: 'true' });
    ta.value = replyDrafts[mid] !== undefined ? replyDrafts[mid] :
      RM.quoteMessage({ name: msg.a, ts: msg.o }, typeof text === 'string' ? text : '');
    ta.addEventListener('input', function () { replyDrafts[mid] = ta.value; });
    var errors = h('div', { class: 'rv-errors', role: 'alert' });
    var prepareBtn = h('button', { type: 'button', class: 'btn btn-primary' }, 'Prepare reply');
    function compose() {
      LH.clear(m.body);
      m.setTitle('Reply to ' + (msg.a || 'this message'));
      m.body.appendChild(h('p', { class: 'rv-privacy', 'data-note': 'privacy' }, h('strong', null, PRIVACY),
        ' The site only prepares an e-mail for your own mail client, it never sends anything.'));
      var n = storageNotice(session);
      if (n) m.body.appendChild(n);
      notes.forEach(function (t) { m.body.appendChild(h('p', { class: 'banner', 'data-notice': 'quote' }, t)); });
      m.body.appendChild(identity.node);
      m.body.appendChild(field('Your reply (write below the quoted text)', ta, 'Lines of your own are wrapped at 72 columns, quoted lines are kept as they are.'));
      m.body.appendChild(errors);
      m.body.appendChild(h('div', { class: 'rv-actions rv-footer' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn', onclick: function () { m.close(); } }, 'Cancel'), prepareBtn));
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
    prepareBtn.addEventListener('click', function () {
      LH.clear(errors);
      prepareBtn.disabled = true;
      var ident = identity.read();
      session.shard().then(function (shard) {
        prepareBtn.disabled = false;
        var input = RM.fromSite({ detail: d, reply: shard, bodies: session.bodies, diffs: session.diffs || {} }, {});
        input.now = LH.now();
        input.reviewer = ident;
        input.target = mid;
        input.body = ta.value;
        var res = RM.buildReply(input);
        var errs = res.problems.filter(function (p) { return p.level === 'error'; });
        if (errs.length) {
          errs.forEach(function (p) { errors.appendChild(h('p', { class: 'rv-error' }, p.text)); });
          var bad = errs.some(function (p) { return p.code === 'name'; }) ? identity.name : errs.some(function (p) { return p.code === 'email'; }) ? identity.email : ta;
          bad.focus();
          return;
        }
        res.emails[0].label = 'Reply';
        showResult(session, m, res, shard, function () { compose(); });
      });
    });
    compose();
    return m;
  }

  /* ---------------------------------------------------------------- toolbar button */

  function reviewButton(session) {
    var badge = h('span', { class: 'counter rv-badge' }, '0');
    var hidden = h('span', { class: 'sr-only rv-badge-text' }, ' pending comments');
    var btn = h('button', { type: 'button', class: 'btn btn-primary rv-review-btn', 'aria-haspopup': 'dialog', title: 'Prepare e-mail replies from your comments. Nothing is sent.' },
      'Review changes', badge, hidden);
    function paint() {
      var n = session.drafts.pending().length;
      badge.textContent = String(n);
      hidden.textContent = n === 1 ? ' pending comment' : ' pending comments';
      badge.hidden = false;
    }
    session.drafts.onchange(paint);
    btn.addEventListener('click', function () { openReview(session, btn); });
    paint();
    return btn;
  }

  function replyButton(session, i) {
    var m = session.order[i];
    return h('button', { type: 'button', class: 'btn btn-small rv-reply-btn', 'aria-haspopup': 'dialog', title: 'Prepare an e-mail reply to this message. Nothing is sent.',
      'aria-label': 'Reply to ' + (m.a || 'this message') + ' by e-mail', onclick: function (ev) { openReply(session, i, ev.currentTarget); } }, 'Reply');
  }

  LH.review = { session: makeSession, part: makePart, roving: roving, reviewButton: reviewButton, replyButton: replyButton,
    openReview: openReview, openReply: openReply, modal: modal, PRIVACY: PRIVACY };
})();
