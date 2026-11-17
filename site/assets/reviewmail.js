/* Review e-mail generation: turns line comments, an overall comment and a review state into the plain text replies a
 * kernel mailing list expects (one reply per patch with the comments under the quoted hunks, one reply to the cover
 * letter), correctly threaded. Pure functions, no DOM, no storage, no clock (the caller passes `now`), so it runs in a
 * browser and in node (tests/js/test_reviewmail.js). Nothing here sends anything.
 *
 * Conventions (see docs/DESIGN.md, "Review e-mails"):
 *   quote   every quoted line is "> " + line, an empty quoted line is ">" (no trailing white space)
 *   region  a commented diff line is quoted with up to 3 lines of context BEFORE it (never above its hunk header), the
 *           comment follows unquoted, separated by blank lines; the file header and the hunk header are quoted once
 *           each; a line "[...]" separates quoted regions that are not adjacent
 *   prose   lines longer than 72 columns are wrapped; quoted lines, indented code and URLs are never reflowed
 *   mail    UTF-8, Content-Transfer-Encoding 8bit (quoted-printable only when a line is longer than 998 bytes),
 *           X-Unsent: 1, LF line endings
 */
(function (root) {
  'use strict';

  var WRAP = 72;
  var CONTEXT = 3;
  var MAX_LINE_BYTES = 998;
  var MAILTO_MAX = 1800;
  var LORE = 'https://lore.kernel.org';
  var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var TRAILERS = { approve: ['Reviewed-by', 'Acked-by', 'Tested-by'], request: ['', 'Nacked-by'], comment: [''] };

  /* ---------------------------------------------------------------- strings and safety */

  function cpLen(s) { return Array.from(s).length; }

  /* One line of header text: control characters (CR, LF, NUL ...), bidi overrides and line separators become spaces,
   * runs of white space collapse. Nothing that could end a header line survives. */
  function clean(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]/g, ' ')
      .replace(/\s+/g, ' ').trim();
  }

  function isAscii(s) { return /^[\u0020-\u007e]*$/.test(s); }

  function validAddr(a) {
    a = String(a || '');
    return a.length <= 254 && /^[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/.test(a);
  }

  /* A Message-ID without the angle brackets, or '' when it cannot be written in a header. */
  function cleanMid(mid) {
    mid = String(mid || '').replace(/^<|>$/g, '');
    return /^[\u0021-\u003b\u003d\u003f-\u007e]+$/.test(mid) && mid.indexOf('<') < 0 && mid.indexOf('>') < 0 ? mid : '';
  }

  function utf8(s) {
    if (typeof TextEncoder !== 'undefined') return Array.from(new TextEncoder().encode(s));
    return Array.from(Buffer.from(s, 'utf8'));
  }

  var B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  function base64(bytes) {
    var out = '';
    for (var i = 0; i < bytes.length; i += 3) {
      var a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
      var n = (a << 16) | ((b || 0) << 8) | (c || 0);
      out += B64.charAt((n >> 18) & 63) + B64.charAt((n >> 12) & 63) +
        (b === undefined ? '=' : B64.charAt((n >> 6) & 63)) + (c === undefined ? '=' : B64.charAt(n & 63));
    }
    return out;
  }

  /* RFC 2047 encoded words ("B" encoding, at most 75 characters each, never splitting a character). */
  function encodedWords(text) {
    var words = [], cur = [], size = 0;
    Array.from(text).forEach(function (ch) {
      var bytes = utf8(ch);
      if (size + bytes.length > 36) { words.push(cur); cur = []; size = 0; }   // 36 bytes = 48 base64 characters: a 60 character word fits on any header line
      cur = cur.concat(bytes);
      size += bytes.length;
    });
    if (cur.length) words.push(cur);
    return words.map(function (w) { return '=?UTF-8?B?' + base64(w) + '?='; });
  }

  /* A header text (Subject): plain when ASCII, else encoded words separated by a folding space. */
  function headerText(text) {
    text = clean(text);
    return isAscii(text) ? text.split(' ') : encodedWords(text);
  }

  /* "Name <addr>" for a header: a plain, quoted or RFC 2047 encoded display name. Returns an atomic list of tokens
   * (encoded words are separate tokens so that folding can happen between them). */
  function mailboxTokens(name, addr) {
    name = clean(name);
    if (!name) return [addr];
    var nameTokens;
    if (!isAscii(name)) nameTokens = encodedWords(name);
    else if (/^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~ -]+$/.test(name)) nameTokens = name.split(' ');
    else nameTokens = ['"' + name.replace(/(["\\])/g, '\\$1') + '"'];
    nameTokens.push('<' + addr + '>');
    return nameTokens;
  }

  function mailboxText(name, addr) { return mailboxTokens(name, addr).join(' '); }

  /* Fold "Name: tok tok, tok" at 78 columns. `items` are lists of tokens that belong together; `sep` is ',' for
   * address lists and '' for space separated lists. A fold is "\n " (the .eml uses LF line ends). */
  function fold(name, items, sep) {
    var toks = [], lines = [], cur = name + ':';
    items.forEach(function (tokens, k) {
      tokens.forEach(function (t, j) { toks.push(t + (j === tokens.length - 1 && k < items.length - 1 ? sep : '')); });
    });
    toks.forEach(function (t) {
      if (cur === name + ':') cur += ' ' + t;   // the first token always stays on the first line
      else if ((cur + ' ' + t).length > 78) { lines.push(cur); cur = ' ' + t; }
      else cur += ' ' + t;
    });
    lines.push(cur);
    return lines.join('\n');
  }

  /* ---------------------------------------------------------------- dates and subjects */

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  /* RFC 5322 date in UTC: "Tue, 17 Nov 2026 14:03:09 +0000". */
  function formatDate(ts) {
    var d = new Date(ts * 1000);
    return DAYS[d.getUTCDay()] + ', ' + d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear() + ' ' +
      pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + ' +0000';
  }

  /* "Re: " + subject, never "Re: Re: " (also removes Re:, RE:, Aw:, Sv: and Fwd: prefixes that are already there). */
  function replySubject(subject) {
    var s = clean(subject).replace(/^(?:(?:re|aw|sv|fwd?)\s*:\s*)+/i, '');
    return 'Re: ' + (s || '(no subject)');
  }

  /* ---------------------------------------------------------------- prose */

  /* Wrap one line of prose at `width` columns. Quoted lines, code (4+ spaces or a tab of indent) and short lines stay as
   * they are; a bullet or number keeps its hanging indent; a word longer than the width (an URL) is never cut. */
  function wrapLine(line, width) {
    width = width || WRAP;
    line = line.replace(/\s+$/, '');
    if (cpLen(line) <= width || /^\s*>/.test(line) || /^(?: {4,}|\t)/.test(line)) return [line];
    var m = /^(\s*)((?:[-*+]|\d+[.)])\s+)?/.exec(line);
    var indent = m[1], hang = indent + (m[2] ? new Array(cpLen(m[2]) + 1).join(' ') : '');
    var words = line.slice(m[0].length).split(/ +/).filter(Boolean);
    var out = [], cur = m[0].replace(/\s+$/, '') ? m[0] : indent, curLen = cpLen(cur), empty = true;
    words.forEach(function (w) {
      var wl = cpLen(w);
      if (!empty && curLen + 1 + wl > width) {
        out.push(cur.replace(/\s+$/, ''));
        cur = hang; curLen = cpLen(cur); empty = true;
      }
      if (!empty) { cur += ' '; curLen++; }
      cur += w; curLen += wl; empty = false;
    });
    out.push(cur.replace(/\s+$/, ''));
    return out;
  }

  /* Text typed by the reviewer: line ends normalised, white space at the line ends removed, long lines wrapped, at most
   * one blank line in a row, no blank lines at the start and the end. Returns a list of lines. */
  function proseLines(text, width) {
    var out = [];
    String(text === null || text === undefined ? '' : text).replace(/\r\n?/g, '\n').split('\n').forEach(function (l) {
      l = l.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u2028\u2029]/g, '');
      wrapLine(l, width).forEach(function (w) {
        if (w === '' && (out.length === 0 || out[out.length - 1] === '')) return;
        out.push(w);
      });
    });
    while (out.length && out[out.length - 1] === '') out.pop();
    return out;
  }

  /* Quoted line: "> " + line, ">" for an empty (or white space only) line. */
  function quoteLine(l) {
    l = String(l).replace(/[\u0000-\u0008\u000b-\u001f\u007f\u2028\u2029]/g, '');
    return /^\s*$/.test(l) ? '>' : '> ' + l;
  }

  function attribution(msg) {
    var name = clean(msg && msg.name) || 'someone';
    return proseLines('On ' + formatDate(msg ? msg.ts : 0) + ', ' + name + ' wrote:');
  }

  /* The text of a message as the quoted start of a reply: attribution, every line quoted, a blank line to type below. */
  function quoteMessage(msg, text) {
    var lines = attribution(msg);
    String(text || '').replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n').forEach(function (l) { lines.push(quoteLine(l.replace(/\s+$/, ''))); });
    lines.push('', '');
    return lines.join('\n');
  }

  /* ---------------------------------------------------------------- reconstructing a patch message */

  /* The three header lines of a file that are quoted above its first commented hunk. */
  function fileHeader(f) {
    var oldp = f.o || f.p, newp = f.p;
    return ['diff --git a/' + oldp + ' b/' + newp,
      '--- ' + (f.k === 'a' ? '/dev/null' : 'a/' + oldp),
      '+++ ' + (f.k === 'd' ? '/dev/null' : 'b/' + newp)];
  }

  function rowsOf(f) { return f && f.x ? String(f.x).split('\n') : []; }

  function isHunk(row) { return /^@@ /.test(row); }

  /* Quote the regions of one segment (commit message or one file). `rows` are the lines, `heads` the lines that are
   * quoted once before the first region of the segment, `wants` a sorted list of {ix, texts}.
   * Appends blocks (arrays of lines) to `blocks`; `state.any` says that something was quoted before (so "[...]" is
   * needed). */
  function segmentBlocks(blocks, state, rows, heads, wants, hunks) {
    var upTo = -1, lastHunk = -2, started = false;
    wants.forEach(function (w) {
      var ix = w.ix, h = -1;
      if (hunks) { for (var i = Math.min(ix, rows.length - 1); i >= 0; i--) { if (isHunk(rows[i])) { h = i; break; } } }
      var start = isHunk(rows[ix]) ? ix : Math.max(h + 1, ix - CONTEXT, 0);
      var lines = [];
      if (!started) {
        if (state.any) blocks.push(['[...]']);
        if (heads.length) lines = lines.concat(heads.map(quoteLine));
        if (h >= 0 && h !== ix) lines.push(quoteLine(rows[h]));
        for (var a = start; a <= ix; a++) lines.push(quoteLine(rows[a]));
        started = true;
      } else if (h === lastHunk && start <= upTo + 1) {
        for (var b = upTo + 1; b <= ix; b++) lines.push(quoteLine(rows[b]));
      } else {
        blocks.push(['[...]']);
        if (h >= 0 && h !== lastHunk && h !== ix) lines.push(quoteLine(rows[h]));
        for (var c = start; c <= ix; c++) lines.push(quoteLine(rows[c]));
      }
      if (lines.length) blocks.push(lines);
      w.texts.forEach(function (t) { blocks.push(proseLines(t)); });
      upTo = Math.max(upTo, ix);
      lastHunk = h;
      state.any = true;
    });
  }

  /* ---------------------------------------------------------------- recipients and threading */

  function indexMessages(thread) {
    var by = Object.create(null);
    (thread.messages || []).forEach(function (m) { if (m && m.mid) by[m.mid] = m; });
    return by;
  }

  /* Message-IDs of the ancestors of message `mid` (oldest first) followed by `mid` itself: the References header of a
   * reply to it. Cycles and missing parents end the walk. */
  function referencesFor(by, mid) {
    var chain = [mid], seen = Object.create(null), cur = by[mid] ? by[mid].parent : '';
    seen[mid] = true;
    while (cur && !seen[cur]) {
      seen[cur] = true;
      chain.unshift(cur);
      cur = by[cur] ? by[cur].parent : '';
    }
    return chain.map(cleanMid).filter(Boolean);
  }

  /* Who a reply goes to. To is the author of the message replied to (when its address is known); Cc is the posting
   * addresses of the lists and the other participants of the thread, each address once, without the reviewer. */
  function recipients(thread, target, reviewer) {
    var who = thread.who || {};
    var me = String(reviewer.email || '').toLowerCase();
    var seen = Object.create(null);
    seen[me] = true;
    var person = who[target.alt || target.hash] || who[target.hash];
    var to = [];
    if (person && validAddr(person.addr) && !seen[person.addr.toLowerCase()]) {
      seen[person.addr.toLowerCase()] = true;
      to.push({ name: clean(person.name || target.name), addr: person.addr });
    }
    var cc = [];
    (thread.lists || []).forEach(function (a) {
      if (validAddr(a) && !seen[a.toLowerCase()]) { seen[a.toLowerCase()] = true; cc.push({ name: '', addr: a, list: true }); }
    });
    (thread.messages || []).forEach(function (m) {
      var p = who[m.hash];
      if (p && validAddr(p.addr) && !seen[p.addr.toLowerCase()]) {
        seen[p.addr.toLowerCase()] = true;
        cc.push({ name: clean(p.name || m.name), addr: p.addr });
      }
    });
    return { to: to, cc: cc, known: to.length > 0 };
  }

  function loreUrl(list, mid) {
    return LORE + '/' + encodeURIComponent(list || '') + '/' + encodeURIComponent(mid) + '/';
  }

  function slug(s) {
    return clean(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '') || 'message';
  }

  /* ---------------------------------------------------------------- output formats */

  function qpEncode(text) {
    return text.split('\n').map(function (line) {
      var bytes = utf8(line), out = [], cur = '', end = lastNonBlank(bytes);
      bytes.forEach(function (b, i) {
        var enc;
        var trailing = i >= end;
        if ((b >= 33 && b <= 126 && b !== 61) || ((b === 32 || b === 9) && !trailing)) enc = String.fromCharCode(b);
        else enc = '=' + (b < 16 ? '0' : '') + b.toString(16).toUpperCase();
        if (cur.length + enc.length > 75) { out.push(cur + '='); cur = ''; }
        cur += enc;
      });
      out.push(cur);
      return out.join('\n');
    }).join('\n');
  }
  function lastNonBlank(bytes) {
    var i = bytes.length;
    while (i > 0 && (bytes[i - 1] === 32 || bytes[i - 1] === 9)) i--;
    return i;
  }

  /* The complete message text (headers, blank line, body) of a prepared email. */
  function renderEml(mail) {
    var h = mail.headers, head = [];
    head.push(fold('From', [mailboxTokens(h.from.name, h.from.addr)], ','));
    if (h.to.length) head.push(fold('To', h.to.map(function (p) { return mailboxTokens(p.name, p.addr); }), ','));
    if (h.cc.length) head.push(fold('Cc', h.cc.map(function (p) { return mailboxTokens(p.name, p.addr); }), ','));
    head.push(fold('Subject', headerText(h.subject).map(function (t) { return [t]; }), ''));
    head.push('In-Reply-To: <' + h.inReplyTo + '>');
    if (h.references.length) head.push(fold('References', h.references.map(function (r) { return ['<' + r + '>']; }), ''));
    head.push('Date: ' + h.date);
    head.push('MIME-Version: 1.0');
    head.push('Content-Type: text/plain; charset=UTF-8');
    head.push('Content-Transfer-Encoding: ' + mail.encoding);
    head.push('X-Unsent: 1');
    var body = mail.encoding === 'quoted-printable' ? qpEncode(mail.body) : mail.body;
    return head.join('\n') + '\n\n' + body + '\n';
  }

  function needsQp(body) {
    return body.split('\n').some(function (l) { return utf8(l).length > MAX_LINE_BYTES; });
  }

  /* RFC 6068 mailto: URL (to, cc, subject, In-Reply-To, body with CRLF line breaks) or null when it would be longer
   * than `max` characters: mail apps do not take much more through a link. */
  function mailtoUrl(mail, max) {
    var h = mail.headers;
    function enc(s) { return encodeURIComponent(s).replace(/[!'()*]/g, function (c) { return '%' + c.charCodeAt(0).toString(16).toUpperCase(); }); }
    function addrs(list) { return list.map(function (p) { return enc(p.addr).replace(/%40/g, '@'); }).join(','); }
    var q = [];
    if (h.cc.length) q.push('cc=' + addrs(h.cc));
    q.push('subject=' + enc(h.subject));
    q.push('In-Reply-To=' + enc('<' + h.inReplyTo + '>'));
    q.push('body=' + enc(mail.body).replace(/%0A/g, '%0D%0A'));
    var url = 'mailto:' + addrs(h.to) + '?' + q.join('&');
    return url.length <= (max || MAILTO_MAX) ? url : null;
  }

  function shq(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }

  /* A git send-email command for one saved .eml file. git send-email takes From and Subject (and the body) from the
   * file, ignores most other headers and takes the recipients and the message it answers from its options, so those
   * are given on the command line; --suppress-cc=all keeps it from adding anyone else. */
  function gitSendEmail(mail, filename) {
    var h = mail.headers;
    var to = h.to.map(function (p) { return p.addr; }), cc = h.cc.map(function (p) { return p.addr; });
    if (!to.length && cc.length) to = [cc.shift()];
    var parts = ['git send-email', '--from=' + shq(mailboxText(h.from.name, h.from.addr)), '--in-reply-to=' + shq('<' + h.inReplyTo + '>'),
      '--no-thread', '--suppress-cc=all', '--8bit-encoding=UTF-8'];
    to.forEach(function (a) { parts.push('--to=' + shq(a)); });
    cc.forEach(function (a) { parts.push('--cc=' + shq(a)); });
    parts.push(shq(filename));
    return parts.join(' ');
  }

  /* ---------------------------------------------------------------- one email */

  function assemble(ctx, spec) {
    var target = spec.target, reviewer = ctx.reviewer;
    var rec = recipients(ctx.thread, target, reviewer);
    var refs = referencesFor(ctx.by, target.mid);
    var warnings = [];
    if (!rec.known) {
      warnings.push({ code: 'no-to', text: 'The address of ' + (clean(target.name) || 'the author') + ' is not known to LkmlHub, so To is empty. ' +
        'Add it yourself (open the message on lore for its address).' });
    }
    if (!ctx.thread.addr) {
      warnings.push({ code: 'no-cc', text: 'Sender addresses are not published by this site, so only the list address is in Cc. ' +
        'Add the other participants to Cc yourself (reply-all on lore shows them).' });
    } else if (!(ctx.thread.lists || []).some(validAddr)) {
      warnings.push({ code: 'no-list', text: 'The posting address of the list is not known yet, add the list to Cc yourself.' });
    }
    if (!cleanMid(target.mid)) warnings.push({ code: 'bad-mid', text: 'The Message-ID of the message cannot be written in a header.' });
    var body = spec.lines.join('\n');
    var encoding = needsQp(body) ? 'quoted-printable' : '8bit';
    if (encoding === 'quoted-printable') warnings.push({ code: 'qp', text: 'A quoted line is longer than 998 bytes, so this email uses quoted-printable.' });
    var mail = {
      key: spec.key, kind: spec.kind, index: spec.index || 0, label: spec.label, mid: target.mid,
      headers: {
        from: { name: clean(reviewer.name), addr: reviewer.email }, to: rec.to, cc: rec.cc, subject: replySubject(target.subject),
        inReplyTo: cleanMid(target.mid), references: refs, date: formatDate(ctx.now)
      },
      body: body, encoding: encoding, warnings: warnings, comments: spec.comments || [], lore: loreUrl(ctx.thread.list, target.mid),
      filename: 're-' + spec.key + '-' + slug(replySubject(target.subject).replace(/^Re: /, '').replace(/^\[[^\]]*\]\s*/, '')) + '.eml',
      toKnown: rec.known
    };
    mail.text = renderEml(mail);
    mail.mailto = mailtoUrl(mail);
    mail.command = gitSendEmail(mail, mail.filename);
    return mail;
  }

  function checkReviewer(r, problems, needed) {
    var ok = true;
    if (!clean(r && r.name)) { problems.push({ level: 'error', code: 'name', text: 'Enter your name: it goes into the From header and the trailer.' }); ok = false; }
    if (!validAddr(r && r.email)) { problems.push({ level: 'error', code: 'email', text: 'Enter a valid e-mail address: it goes into the From header' + (needed ? ' and the trailer.' : '.') }); ok = false; }
    return ok;
  }

  function trailerLine(name, reviewer) {
    return name + ': ' + clean(reviewer.name).replace(/[<>]/g, '') + ' <' + reviewer.email + '>';
  }

  /* ---------------------------------------------------------------- the review */

  /* input = {
   *   now,                                   unix seconds of the Date header
   *   reviewer: {name, email},
   *   thread: {list, lists: [posting addresses], addr: 1|0, who: {hash: {name, addr}},
   *            messages: [{mid, parent, name, hash, ts, subject}]},      mids without angle brackets
   *   cover: mid or null,
   *   patches: [{mid, index, total, message: text or null, files: [{p, o, k, x}]}],     in series order
   *   review: {state: 'approve'|'comment'|'request', overall, trailer, scope: 'series'|'patches', picks: [mid],
   *            comments: [{id, patch: mid, kind: 'diff'|'msg', path, ix, line, text}]}
   * }
   * Returns {emails, problems: [{level, code, text}], ok, used: [comment ids], skipped: [comment ids]}. */
  function buildReview(input) {
    var problems = [], review = input.review || {};
    var state = review.state === 'approve' || review.state === 'request' ? review.state : 'comment';
    var trailer = TRAILERS[state].indexOf(review.trailer || '') >= 0 ? (review.trailer || '') : TRAILERS[state][0];
    var overall = proseLines(review.overall);
    var thread = input.thread || {};
    var ctx = { now: input.now, reviewer: input.reviewer || {}, thread: thread, by: indexMessages(thread) };
    var patches = input.patches || [];
    var patchBy = Object.create(null);
    patches.forEach(function (p) { patchBy[p.mid] = p; });
    var cover = input.cover && ctx.by[input.cover] ? input.cover : null;

    var scope = review.scope === 'series' ? 'series' : 'patches';
    if (scope === 'series' && !cover) {
      problems.push({ level: 'warn', code: 'no-cover', text: 'This series has no cover letter, so the trailer goes to the picked patches instead.' });
      scope = 'patches';
    }
    var picks = (review.picks || []).filter(function (m) { return patchBy[m]; });
    var used = [], skipped = [];

    /* comments per patch, checked against the stored diff */
    var perPatch = Object.create(null);
    (review.comments || []).forEach(function (c) {
      var p = patchBy[c.patch], text = String(c.text || '');
      if (!/\S/.test(text)) { skipped.push(c.id); return; }
      var rows = null;
      if (p) {
        if (c.kind === 'msg') rows = p.message === null || p.message === undefined ? null : String(p.message).split('\n');
        else { var f = p.files.filter(function (x) { return x.p === c.path; })[0]; rows = f ? rowsOf(f) : null; }
      }
      if (!rows || !(c.ix >= 0 && c.ix < rows.length) || (c.line !== undefined && c.line !== null && rows[c.ix] !== c.line)) {
        skipped.push(c.id);
        problems.push({ level: 'warn', code: 'stale-comment', id: c.id, text: 'A comment on ' + (c.kind === 'msg' ? 'the commit message' : c.path) +
          ' no longer matches the stored text and is left out.' });
        return;
      }
      (perPatch[c.patch] = perPatch[c.patch] || []).push(c);
    });

    var trailerNeeded = !!trailer;
    var emailsWanted = [];   // [{patch or cover, ...}]
    var patchTrailer = trailerNeeded && scope === 'patches';
    var coverTrailer = trailerNeeded && scope === 'series';
    var firstPatch = patches[0];
    patches.forEach(function (p) {
      var has = !!perPatch[p.mid], pick = patchTrailer && picks.indexOf(p.mid) >= 0;
      var overallHere = !cover && overall.length && p === firstPatch;
      if (has || pick || overallHere) emailsWanted.push({ patch: p, pick: pick, overall: overallHere });
    });
    var coverWanted = cover && (overall.length || coverTrailer);

    if (!emailsWanted.length && !coverWanted) {
      problems.push({ level: 'error', code: 'empty', text: state === 'approve' ?
        'Nothing to prepare: choose a trailer scope (the series or at least one patch), write a comment, or add line comments.' :
        'Nothing to prepare: add a line comment or write an overall comment.' });
    }
    if (state === 'request' && !overall.length && !Object.keys(perPatch).length) {
      /* handled by "empty" above; kept explicit so the message above names the problem */
    }
    var reviewerOk = checkReviewer(ctx.reviewer, problems, trailerNeeded);
    if (!reviewerOk || problems.some(function (p) { return p.level === 'error'; })) {
      return { emails: [], problems: problems, ok: false, used: [], skipped: skipped };
    }

    var emails = [];
    emailsWanted.forEach(function (w) {
      var p = w.patch, target = ctx.by[p.mid];
      if (!target) { problems.push({ level: 'error', code: 'unknown-message', text: 'Patch ' + p.index + ' is not in the thread data.' }); return; }
      var blocks = [], st = { any: false }, comments = perPatch[p.mid] || [];
      var msgWants = group(comments.filter(function (c) { return c.kind === 'msg'; }));
      if (msgWants.length) segmentBlocks(blocks, st, String(p.message).split('\n'), [], msgWants, false);
      p.files.forEach(function (f) {
        var cs = comments.filter(function (c) { return c.kind !== 'msg' && c.path === f.p; });
        if (cs.length) segmentBlocks(blocks, st, rowsOf(f), fileHeader(f), group(cs), true);
      });
      if (w.overall) blocks.push(overall);
      if (w.pick) blocks.push([trailerLine(trailer, ctx.reviewer)]);
      var lines = attribution(target);
      blocks.forEach(function (b, i) {
        var quote = /^>/.test(b[0]);
        if (i > 0 || !quote) lines.push('');
        Array.prototype.push.apply(lines, b);
      });
      comments.forEach(function (c) { used.push(c.id); });
      emails.push(assemble(ctx, { key: 'patch-' + p.index, kind: 'patch', index: p.index, target: target, lines: lines,
        label: 'Patch ' + p.index + (p.total ? '/' + p.total : ''), comments: comments.map(function (c) { return c.id; }) }));
    });
    if (coverWanted) {
      var ctarget = ctx.by[cover], clines = attribution(ctarget), cblocks = [];
      if (overall.length) cblocks.push(overall);
      if (coverTrailer) cblocks.push([trailerLine(trailer, ctx.reviewer)]);
      cblocks.forEach(function (b) { clines.push(''); Array.prototype.push.apply(clines, b); });
      emails.push(assemble(ctx, { key: 'cover', kind: 'cover', target: ctarget, lines: clines, label: 'Cover letter' }));
    }
    return { emails: emails, problems: problems, ok: !problems.some(function (p) { return p.level === 'error'; }), used: used, skipped: skipped };
  }

  /* comments of one segment sorted by line, several on the same line in the order given */
  function group(comments) {
    var by = Object.create(null), order = [];
    comments.forEach(function (c) {
      if (!by[c.ix]) { by[c.ix] = { ix: c.ix, texts: [] }; order.push(by[c.ix]); }
      by[c.ix].texts.push(c.text);
    });
    return order.sort(function (a, b) { return a.ix - b.ix; });
  }

  /* A plain reply to one message (Conversation tab): `body` is the text the reviewer edited, which starts with the
   * quoted message. input = {now, reviewer, thread, target: mid, body}. Same result shape as buildReview. */
  function buildReply(input) {
    var problems = [], thread = input.thread || {};
    var ctx = { now: input.now, reviewer: input.reviewer || {}, thread: thread, by: indexMessages(thread) };
    var target = ctx.by[input.target];
    var lines = proseLines(input.body);
    if (!target) problems.push({ level: 'error', code: 'unknown-message', text: 'This message is not in the thread data.' });
    var hasText = lines.some(function (l) { return l !== '' && !/^>/.test(l) && !/^On .{0,200} wrote:$/.test(l); });
    if (!hasText) problems.push({ level: 'error', code: 'empty', text: 'Write your reply below the quoted text.' });
    checkReviewer(ctx.reviewer, problems, false);
    if (problems.some(function (p) { return p.level === 'error'; })) return { emails: [], problems: problems, ok: false, used: [], skipped: [] };
    var mail = assemble(ctx, { key: 'reply', kind: 'reply', target: target, lines: lines, label: 'Reply' });
    return { emails: [mail], problems: problems, ok: true, used: [], skipped: [] };
  }

  /* ---------------------------------------------------------------- site data to input */

  function safeDecode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }

  /* Message-ID as stored in the site data (percent encoded) -> the real Message-ID. */
  function midOf(stored) { return safeDecode(String(stored || '')); }

  /* Build the input of buildReview / buildReply from the files the site loads for a thread:
   *   detail  the thread detail (data/threads/...), `reply` the reply shard (may be null: the review is still possible,
   *   without addresses), `bodies` the texts (data/bodies/..., aligned with the thread order), `diffs` the diff shard's
   *   "p" map (stored message-id -> {f: files}).
   * `review` is passed through with the patch of every comment turned into the real Message-ID. */
  function fromSite(site, review, extra) {
    var d = site.detail, shard = site.reply || null, bodies = site.bodies || [], diffs = site.diffs || {};
    var order = (d.cover ? [d.cover] : []).concat(d.patches || [], d.cm || []);
    var base = d.cover ? 1 : 0, total = d.total || (d.patches || []).length;
    var m = shard && shard.m && shard.m.length === order.length ? shard.m : null;
    var tag = '[PATCH' + (d.v > 1 ? ' v' + d.v : '');
    var messages = order.map(function (x, i) {
      if (m) return { mid: m[i].i, parent: m[i].p || '', name: m[i].n, hash: m[i].h, ts: m[i].t, subject: m[i].s };
      var parent = x.pi !== undefined && order[x.pi] ? midOf(order[x.pi].mid) : '';
      var subject = d.k === 'pr' ? (i < base ? tag + ' 0/' + total + '] ' + d.t : i < base + (d.patches || []).length ?
        tag + ' ' + x.i + '/' + total + '] ' + x.t : 'Re: ' + d.t) : (i === 0 ? d.t : 'Re: ' + d.t);
      return { mid: midOf(x.mid), parent: parent, name: x.a, hash: x.ah, ts: x.o, subject: subject };
    });
    var who = {};
    if (shard && shard.who) Object.keys(shard.who).forEach(function (h) { who[h] = { name: shard.who[h].n, addr: shard.who[h].a }; });
    var lists = shard && shard.lists ? Object.keys(shard.lists).sort().map(function (k) { return shard.lists[k]; }) : [];
    var patches = (d.patches || []).map(function (p, k) {
      var pd = diffs[p.mid];
      var text = bodies[base + k];
      return { mid: midOf(p.mid), index: p.i, total: total, message: typeof text === 'string' ? text : null, files: pd && pd.f ? pd.f : [] };
    });
    var rv = review || {};
    var out = {
      thread: { list: d.home, lists: lists, addr: shard && shard.addr ? 1 : 0, who: who, messages: messages },
      cover: d.cover ? messages[0].mid : null,
      patches: patches,
      review: {
        state: rv.state, overall: rv.overall, trailer: rv.trailer, scope: rv.scope,
        picks: (rv.picks || []).map(midOf),
        comments: (rv.comments || []).map(function (c) { return Object.assign({}, c, { patch: midOf(c.patch) }); })
      },
      bodies: bodies.map(function (b) { return typeof b === 'string' ? b : null; }),
      order: order.map(function (x) { return midOf(x.mid); })
    };
    Object.keys(extra || {}).forEach(function (k) { out[k] = extra[k]; });
    return out;
  }

  var api = {
    fromSite: fromSite, midOf: midOf,
    clean: clean, validAddr: validAddr, cleanMid: cleanMid, mailboxText: mailboxText, headerText: headerText, encodedWords: encodedWords,
    formatDate: formatDate, replySubject: replySubject, wrapLine: wrapLine, proseLines: proseLines, quoteLine: quoteLine,
    quoteMessage: quoteMessage, attribution: attribution, fileHeader: fileHeader, referencesFor: referencesFor,
    recipients: recipients, loreUrl: loreUrl, qpEncode: qpEncode, renderEml: renderEml, mailtoUrl: mailtoUrl,
    gitSendEmail: gitSendEmail, buildReview: buildReview, buildReply: buildReply, trailerLine: trailerLine,
    MAILTO_MAX: MAILTO_MAX, WRAP: WRAP, CONTEXT: CONTEXT
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.LHReviewMail = api;
})(typeof window !== 'undefined' ? window : globalThis);
