const assert = require('assert');
const fs = require('fs');
const path = require('path');
const RM = require('../../site/assets/reviewmail.js');
const load = require('./load');

const FIX = path.join(__dirname, '..', 'fixtures', 'review');
const site = JSON.parse(fs.readFileSync(path.join(FIX, 'ipe-v2-site.json'), 'utf8'));
const UPDATE = !!process.env.UPDATE_GOLDEN;
const NOW = 1794870000;                       // Mon, 16 Nov 2026 23:00:00 +0000
const JANE = { name: 'Jane Reviewer', email: 'jane@example.test' };
const P1 = 'user45%40example.test', P2 = 'user46%40example.test', P3 = 'user47%40example.test';   // stored (encoded) message-ids

function deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) { Object.freeze(o); Object.keys(o).forEach((k) => deepFreeze(o[k])); }
  return o;
}
function make(review, reviewer, siteData) {
  const input = RM.fromSite(siteData || site, review);
  input.now = NOW;
  input.reviewer = reviewer || JANE;
  return input;
}
function run(review, reviewer, siteData) { return RM.buildReview(make(review, reviewer, siteData)); }
function byKey(r) { const m = {}; r.emails.forEach((e) => { m[e.key] = e; }); return m; }
function golden(name, text) {
  const f = path.join(FIX, 'golden', name);
  if (UPDATE) { fs.mkdirSync(path.dirname(f), { recursive: true }); fs.writeFileSync(f, text); }
  assert.strictEqual(text, fs.readFileSync(f, 'utf8'), 'golden file ' + name + ' differs (UPDATE_GOLDEN=1 rewrites it)');
}
function comment(id, patch, path_, ix, text) { return { id, patch, kind: 'diff', path: path_, ix, text }; }
function bodyOf(mail) { return mail.body.split('\n'); }
function parseHead(text) {
  const [head, ...rest] = text.split('\n\n');
  const hdr = {}, order = [];
  head.replace(/\n /g, ' ').split('\n').forEach((l) => {
    const m = /^([A-Za-z-]+): ?(.*)$/.exec(l);
    assert.ok(m, 'header line expected, got: ' + JSON.stringify(l));
    hdr[m[1]] = m[2]; order.push(m[1]);
  });
  return { hdr, order, body: rest.join('\n\n') };
}
function decodeWords(s) {   // RFC 2047: white space between two encoded words is not part of the text
  const W = '=\\?UTF-8\\?B\\?[A-Za-z0-9+/=]+\\?=';
  return s.replace(new RegExp('(' + W + ')\\s+(?=' + W + ')', 'g'), '$1')
    .replace(new RegExp(W, 'g'), (w) => Buffer.from(w.split('?')[3], 'base64').toString('utf8'));
}
function qpDecode(s) {
  return Buffer.from(s.replace(/=\n/g, '').replace(/=([0-9A-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16))), 'latin1').toString('utf8');
}

// ---------------------------------------------------------------- the golden review: two comments on patch 1, one on patch 3, overall + Reviewed-by for the series
const golden_review = JSON.parse(fs.readFileSync(path.join(FIX, 'golden', 'review.json'), 'utf8'));   // the same review the browser test types in
const review = { state: 'approve', trailer: 'Reviewed-by', scope: 'series', overall: golden_review.overall, comments: golden_review.comments };
assert.deepStrictEqual(golden_review.reviewer, JANE);
{
  const input = deepFreeze(make(review));
  const r = RM.buildReview(input);
  assert.deepStrictEqual(r.problems, []);
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(r.emails.map((e) => e.key), ['patch-1', 'patch-3', 'cover']);
  assert.deepStrictEqual(r.used.sort(), ['c1', 'c2', 'c3']);
  r.emails.forEach((e) => golden(e.key + '.eml', e.text));
  assert.deepStrictEqual(RM.buildReview(make(review)).emails.map((e) => e.text), r.emails.map((e) => e.text));   // deterministic, input untouched
  const [p1, p3, cover] = r.emails;
  // exact headers of patch 1
  const h = parseHead(p1.text);
  assert.deepStrictEqual(h.order, ['From', 'To', 'Cc', 'Subject', 'In-Reply-To', 'References', 'Date', 'MIME-Version', 'Content-Type', 'Content-Transfer-Encoding', 'X-Unsent']);
  assert.strictEqual(h.hdr.From, 'Jane Reviewer <jane@example.test>');
  assert.strictEqual(h.hdr.To, 'Manuel Huber via B4 Relay <user1@example.test>');
  assert.strictEqual(h.hdr.Cc, 'kexec@lists.example.test, keyrings@lists.example.test, linux-doc@lists.example.test, ' +
    'linux-security-module@lists.example.test, lkml@lists.example.test, sashiko-bot <user11@example.test>, ' +
    'Andrew Halaney <user51@example.test>, Fan Wu <user20@example.test>, Manuel Huber <user5@example.test>');
  assert.strictEqual(h.hdr.Subject, 'Re: [PATCH v2 1/3] ipe: add the .ipe policy keyring');
  assert.strictEqual(h.hdr['In-Reply-To'], '<user45@example.test>');
  assert.strictEqual(h.hdr.References, '<user44@example.test> <user45@example.test>');   // the cover letter, then the patch
  assert.strictEqual(h.hdr.Date, 'Mon, 16 Nov 2026 23:00:00 +0000');
  assert.strictEqual(h.hdr['MIME-Version'], '1.0');
  assert.strictEqual(h.hdr['Content-Type'], 'text/plain; charset=UTF-8');
  assert.strictEqual(h.hdr['Content-Transfer-Encoding'], '8bit');
  assert.strictEqual(h.hdr['X-Unsent'], '1');
  assert.ok(p1.text.endsWith('\n') && !p1.text.includes('\r'));
  assert.strictEqual(parseHead(cover.text).hdr.Subject, 'Re: [PATCH v2 0/3] IPE: add a dedicated policy keyring');
  assert.strictEqual(parseHead(cover.text).hdr.References, '<user44@example.test>');
  assert.deepStrictEqual(p3.comments, ['c3']);
  assert.ok(!/Reviewed-by/.test(p1.body) && !/Reviewed-by/.test(p3.body), 'a series wide trailer lives in the cover reply only');
  assert.ok(/\nReviewed-by: Jane Reviewer <jane@example.test>$/.test(cover.body));
}

// ---------------------------------------------------------------- line numbers map to the right diff lines, in both layouts
{
  const w = load(['util.js', 'files.js', 'diffpair.js']);
  const file = site.diffs[P3].f.filter((f) => f.p === 'Documentation/security/ipe.rst')[0];
  const rows = file.x.split('\n');
  const unified = w.LH.filesTab.parseHunks(file.x).filter((r) => r.t !== 'hunk' && r.t !== 'note');
  const pairs = w.LHDiffPair.pairRows(w.LH.filesTab.parseHunks(file.x));
  const cells = [];
  pairs.forEach((r) => { if (!r.span) { [r.left, r.right].forEach((c) => { if (c.t === 'ctx' || c.t === 'add' || c.t === 'del') cells.push(c); }); } });
  // a context line shows on both sides of the split layout and is one anchor
  const splitAnchors = Array.from(new Set(cells.map((c) => c.ix))).sort((a, b) => a - b);
  assert.deepStrictEqual(splitAnchors, Array.from(unified.map((r) => r.ix)));   // Array.from: the page scripts run in another realm
  unified.forEach((r) => assert.strictEqual(rows[r.ix], (r.t === 'add' ? '+' : r.t === 'del' ? '-' : ' ') + r.s));
  cells.forEach((c) => assert.strictEqual(rows[c.ix].slice(1), c.s));
  // the email of a comment is the same whichever layout named the line
  const kinds = { ctx: 3, del: 5, add: 14 };
  ['ctx', 'del', 'add'].forEach((kind) => {
    const u = unified.filter((r) => r.t === kind && r.s.trim())[0];
    const c = cells.filter((x) => x.t === kind && x.s.trim())[0];
    assert.strictEqual(u.ix, c.ix, kind);
    const a = run({ state: 'comment', comments: [comment('x', P3, file.p, u.ix, 'note')] }).emails[0].body;
    const b = run({ state: 'comment', comments: [comment('x', P3, file.p, c.ix, 'note')] }).emails[0].body;
    assert.strictEqual(a, b);
    const quoted = a.split('\n').filter((l) => l.charAt(0) === '>');
    assert.strictEqual(quoted[quoted.length - 1], '> ' + rows[u.ix], kind + ' line is the last quoted line, with its +/-/space');
    assert.strictEqual(a.split('\n').slice(-1)[0], 'note');
    assert.ok(Object.keys(kinds).includes(kind));
  });
  // removed line 5 ("-this is  the ..."): context above it, comment right below
  const b = bodyOf(run({ state: 'comment', comments: [comment('x', P3, file.p, 5, 'Why drop this?')] }).emails[0]);
  assert.deepStrictEqual(b.slice(-7), [
    '> @@ -248,11 +248,20 @@ policy updates have to be done rebootlessly.',
    '>  To allow an update from an external source, it could be potentially malicious,',
    '>  so this policy needs to have a way to be identified as trusted. This is',
    '> -done via a signature chained to a trust source in the kernel. Arbitrarily,',
    '> -this is  the ``SYSTEM_TRUSTED_KEYRING``, a keyring that is initially',
    '',
    'Why drop this?']);
}

// the first line after the hunk header: no context above it, the header is quoted right before it
{
  const b = bodyOf(run({ state: 'comment', comments: [comment('x', P3, 'Documentation/security/ipe.rst', 1, 'blank context line')] }).emails[0]);
  assert.deepStrictEqual(b.slice(1), [
    '> diff --git a/Documentation/security/ipe.rst b/Documentation/security/ipe.rst',
    '> --- a/Documentation/security/ipe.rst',
    '> +++ b/Documentation/security/ipe.rst',
    '> @@ -248,11 +248,20 @@ policy updates have to be done rebootlessly.',
    '>',     // the empty context line of the diff is a quoted empty line, without trailing white space
    '',
    'blank context line']);
}

// a comment on a hunk header line quotes just that line (after the file header)
{
  const b = bodyOf(run({ state: 'comment', comments: [comment('x', P3, 'Documentation/security/ipe.rst', 0, 'which hunk is this?')] }).emails[0]);
  assert.strictEqual(b[4], '> @@ -248,11 +248,20 @@ policy updates have to be done rebootlessly.');
  assert.strictEqual(b[5], '');
  assert.strictEqual(b[6], 'which hunk is this?');
  assert.strictEqual(b.length, 7);
}

// adjacent comments in one hunk continue the quote without "[...]", distant ones get "[...]" and no second hunk header
{
  const f = 'security/ipe/keyring.c';
  const adj = bodyOf(run({ state: 'comment', comments: [comment('a', P1, f, 22, 'first'), comment('b', P1, f, 24, 'second'), comment('c', P1, f, 25, 'third')] }).emails[0]);
  assert.ok(!adj.includes('[...]'));
  assert.strictEqual(adj.filter((l) => l.indexOf('> @@') === 0).length, 1);
  const lines = adj.join('\n');
  assert.ok(lines.indexOf('> +\t\t\t      current_cred(), KEY_POS_SEARCH |\n> +\t\t\t\tKEY_USR_VIEW | KEY_USR_READ | KEY_USR_WRITE |\n\nsecond\n\n') > 0, lines);
  // no duplicate quoted lines: every quoted diff line appears once
  const q = adj.filter((l) => l.charAt(0) === '>');
  assert.strictEqual(new Set(q).size, q.length - q.filter((l) => l === '>').length + (q.some((l) => l === '>') ? 1 : 0));
  const far = bodyOf(run({ state: 'comment', comments: [comment('a', P1, f, 5, 'first'), comment('b', P1, f, 28, 'second')] }).emails[0]);
  assert.strictEqual(far.filter((l) => l === '[...]').length, 1);
  assert.strictEqual(far.filter((l) => l.indexOf('> @@') === 0).length, 1);
  assert.strictEqual(far[far.indexOf('[...]') - 1], '');
  assert.strictEqual(far[far.indexOf('[...]') + 1], '');
}

// two comments on the same line share one quote
{
  const b = bodyOf(run({ state: 'comment', comments: [comment('a', P1, 'security/ipe/keyring.c', 12, 'one'), comment('b', P1, 'security/ipe/keyring.c', 12, 'two')] }).emails[0]);
  const tail = b.slice(b.indexOf('one') - 1);
  assert.deepStrictEqual(tail, ['', 'one', '', 'two']);
  assert.strictEqual(b.filter((l) => l === '> +struct key *ipe_keyring __ro_after_init;').length, 1);
}

// several files and the "[...]" between files; header lines of a renamed, new and deleted file
{
  const b = bodyOf(run({ state: 'comment', comments: [
    comment('a', P1, 'security/ipe/ipe.c', 8, 'in ipe.c'), comment('b', P1, 'security/ipe/keyring.c', 3, 'in keyring.c')] }).emails[0]);
  assert.ok(b.includes('> diff --git a/security/ipe/ipe.c b/security/ipe/ipe.c'));
  assert.ok(b.includes('> diff --git a/security/ipe/keyring.c b/security/ipe/keyring.c'));
  assert.ok(b.includes('> --- /dev/null'), 'a new file is shown as in the patch');
  assert.strictEqual(b.filter((l) => l === '[...]').length, 1);
  assert.ok(b.indexOf('> diff --git a/security/ipe/ipe.c b/security/ipe/ipe.c') < b.indexOf('> diff --git a/security/ipe/keyring.c b/security/ipe/keyring.c'), 'files in diff order');
}
assert.deepStrictEqual(RM.fileHeader({ p: 'new.c', o: 'old.c', k: 'r' }), ['diff --git a/old.c b/new.c', '--- a/old.c', '+++ b/new.c']);
assert.deepStrictEqual(RM.fileHeader({ p: 'x.c', k: 'd' }), ['diff --git a/x.c b/x.c', '--- a/x.c', '+++ /dev/null']);
assert.deepStrictEqual(RM.fileHeader({ p: 'x.c', k: 'a' }), ['diff --git a/x.c b/x.c', '--- /dev/null', '+++ b/x.c']);

// the commit message is quoted only when it is commented on
{
  const msg = site.bodies[2];
  const lines = msg.split('\n');
  const plain = run({ state: 'comment', comments: [comment('a', P2, 'include/linux/key.h', 3, 'x')] }).emails[0];
  assert.ok(!plain.body.includes(lines[0]), 'no commit message in a diff only review');
  const withMsg = run({ state: 'comment', comments: [{ id: 'm', patch: P2, kind: 'msg', ix: 4, text: 'Say why in the commit message.' },
    comment('a', P2, 'include/linux/key.h', 3, 'x')] }).emails[0];
  const b = bodyOf(withMsg);
  assert.deepStrictEqual(b.slice(1, 5), lines.slice(1, 5).map((l) => (l === '' ? '>' : '> ' + l)));
  assert.ok(b.indexOf('Say why in the commit message.') > 0);
  assert.strictEqual(b[b.indexOf('Say why in the commit message.') - 2], '> ' + lines[4]);   // comment right below the quoted line
  assert.ok(b.indexOf('[...]') > b.indexOf('Say why in the commit message.'), 'then the diff part after a [...]');
  assert.ok(b.findIndex((l) => l.indexOf('> diff --git a/include/linux/key.h') === 0) > b.indexOf('[...]'));
}

// stale and empty comments are left out, with a problem for the stale ones
{
  const r = run({ state: 'comment', comments: [
    { id: 'old', patch: P1, kind: 'diff', path: 'security/ipe/keyring.c', ix: 22, line: '+not the line that is stored', text: 'stale' },
    comment('far', P1, 'security/ipe/keyring.c', 9999, 'no such line'), comment('nofile', P1, 'no/such/file.c', 1, 'x'),
    comment('empty', P1, 'security/ipe/keyring.c', 3, '   \n  '), comment('good', P3, 'Documentation/security/ipe.rst', 5, 'fine')] });
  assert.deepStrictEqual(r.emails.map((e) => e.key), ['patch-3']);
  assert.deepStrictEqual(r.skipped.sort(), ['empty', 'far', 'nofile', 'old']);
  assert.strictEqual(r.problems.filter((p) => p.code === 'stale-comment').length, 3);
  const ok = run({ state: 'comment', comments: [{ id: 'ok', patch: P1, kind: 'diff', path: 'security/ipe/keyring.c', ix: 22, line: site.diffs[P1].f[4].x.split('\n')[22], text: 'matches' }] });
  assert.strictEqual(ok.emails.length, 1);
}

// ---------------------------------------------------------------- wrapping
{
  const long = 'This is a long sentence that should be wrapped at seventy two columns because mail readers expect that for prose.';
  const w = RM.proseLines(long);
  assert.deepStrictEqual(w, ['This is a long sentence that should be wrapped at seventy two columns',
    'because mail readers expect that for prose.']);
  assert.ok(w.every((l) => Array.from(l).length <= 72));
  assert.deepStrictEqual(RM.proseLines('short\nlines\nstay'), ['short', 'lines', 'stay']);   // short lines are never joined
  const bullet = RM.proseLines('- ' + long);
  assert.strictEqual(bullet[0].slice(0, 2), '- ');
  assert.strictEqual(bullet[1].slice(0, 2), '  ');
  assert.ok(bullet.every((l) => l.length <= 72));
  assert.deepStrictEqual(RM.proseLines('  1. ' + long)[1].slice(0, 5), '     ');
  const url = 'see https://lore.kernel.org/linux-security-module/20261029-b4-ipe-keyring-rfc-v2-1-77727b205844@example.test/ for details';
  const u = RM.proseLines(url);
  assert.ok(u.some((l) => l.indexOf('https://lore.kernel.org/linux-security-module/20261029-b4-ipe-keyring-rfc-v2-1-77727b205844@example.test/') >= 0), 'an URL is never cut');
  const code = '        ' + long;
  assert.deepStrictEqual(RM.proseLines(code), [code], 'indented code is not reflowed');
  assert.deepStrictEqual(RM.proseLines('\tint x = ' + 'y + '.repeat(40) + '1;'), ['\tint x = ' + 'y + '.repeat(40) + '1;']);
  const quoted = '> ' + long + ' ' + long;
  assert.deepStrictEqual(RM.proseLines(quoted), [quoted], 'quoted text is never reflowed');
  assert.deepStrictEqual(RM.proseLines('a\n\n\n\nb\n\n'), ['a', '', 'b']);
  assert.deepStrictEqual(RM.proseLines('\n\n  \na  \r\nb\r\n'), ['a', 'b']);
  // 72 columns are counted in characters, not bytes: 24 three-byte characters per word keep this under the limit
  const wide = Array.from({ length: 12 }, () => '\u00e4\u00f6\u00fc\u00e4\u00f6\u00fc\u00e4\u00f6\u00fc\u00e4').join(' ');
  assert.ok(RM.proseLines(wide).every((l) => Array.from(l).length <= 72));
  assert.strictEqual(RM.proseLines(wide).join(' '), wide);
  // exactly 72 columns stays on one line, 73 wraps
  const l72 = 'x'.repeat(35) + ' ' + 'y'.repeat(36);
  assert.strictEqual(l72.length, 72);
  assert.deepStrictEqual(RM.proseLines(l72), [l72]);
  assert.strictEqual(RM.proseLines(l72 + 'z').length, 2);
  // wrapping inside a review comment, quoted diff lines of the same email stay as they are
  const longDiff = '+' + 'a'.repeat(200);
  const site2 = JSON.parse(JSON.stringify(site));
  const fl = site2.diffs[P1].f.filter((f) => f.p === 'security/ipe/ipe.c')[0];
  fl.x = fl.x + '\n' + longDiff;
  const ix = fl.x.split('\n').length - 1;
  const b = bodyOf(run({ state: 'comment', comments: [comment('x', P1, 'security/ipe/ipe.c', ix, long)] }, JANE, site2).emails[0]);
  assert.ok(b.includes('> ' + longDiff), 'a long quoted diff line is not wrapped');
  assert.ok(b.includes('because mail readers expect that for prose.'));
}

// ---------------------------------------------------------------- trailers and scopes
{
  const lines = { comments: [comment('c1', P1, 'security/ipe/keyring.c', 22, 'x')] };
  ['Reviewed-by', 'Acked-by', 'Tested-by'].forEach((t) => {
    const r = run(Object.assign({ state: 'approve', trailer: t, scope: 'series', overall: 'Looks good.' }, lines));
    const cover = byKey(r).cover;
    assert.ok(cover.body.endsWith('Looks good.\n\n' + t + ': Jane Reviewer <jane@example.test>'), t);
    assert.ok(!byKey(r)['patch-1'].body.includes(t + ':'));
  });
  // approve with a trailer for the picked patches only: the trailer is in each picked patch reply, there is no cover reply
  const picked = run({ state: 'approve', trailer: 'Tested-by', scope: 'patches', picks: [P1, P3], overall: '', comments: [comment('c3', P3, 'Documentation/security/ipe.rst', 5, 'doc nit')] });
  assert.deepStrictEqual(picked.emails.map((e) => e.key), ['patch-1', 'patch-3']);
  assert.ok(picked.emails.every((e) => e.body.endsWith('Tested-by: Jane Reviewer <jane@example.test>')));
  assert.strictEqual(byKey(picked)['patch-1'].body, [RM.attribution({ name: 'Manuel Huber via B4 Relay', ts: site.reply.m[1].t })[0], '', 'Tested-by: Jane Reviewer <jane@example.test>'].join('\n'));
  assert.ok(/doc nit\n\nTested-by: /.test(byKey(picked)['patch-3'].body), 'the trailer follows the comments after a blank line');
  // a patch that is not picked gets no trailer and, without comments, no email
  const one = run({ state: 'approve', trailer: 'Acked-by', scope: 'patches', picks: [P2] });
  assert.deepStrictEqual(one.emails.map((e) => e.key), ['patch-2']);
  // request changes: no trailer unless Nacked-by is chosen explicitly
  const rq = run({ state: 'request', overall: 'This needs another approach.', scope: 'series', trailer: '' });
  assert.deepStrictEqual(rq.emails.map((e) => e.key), ['cover']);
  assert.ok(!/-by:/.test(rq.emails[0].body));
  const nack = run({ state: 'request', trailer: 'Nacked-by', scope: 'series', overall: 'No.' });
  assert.ok(nack.emails[0].body.endsWith('No.\n\nNacked-by: Jane Reviewer <jane@example.test>'));
  // request changes without text still works with line comments only
  const only = run({ state: 'request', overall: '', comments: lines.comments });
  assert.deepStrictEqual(only.emails.map((e) => e.key), ['patch-1']);
  assert.ok(!/-by:/.test(only.emails[0].body));
  // a trailer is never produced for the wrong state
  assert.ok(!/Reviewed-by/.test(run({ state: 'comment', trailer: 'Reviewed-by', overall: 'hello' }).emails[0].body));
  assert.ok(!/Nacked-by/.test(run({ state: 'approve', trailer: 'Nacked-by', scope: 'series', overall: 'hi' }).emails[0].body));
  assert.ok(/^Reviewed-by:/m.test(run({ state: 'approve', trailer: 'Nacked-by', scope: 'series', overall: 'hi' }).emails[0].body), 'invalid trailer falls back to Reviewed-by');
  // a series wide trailer needs a cover letter: without one it falls back to the picked patches and says so
  const nocover = JSON.parse(JSON.stringify(site));
  delete nocover.detail.cover;
  nocover.bodies = nocover.bodies.slice(1);
  nocover.reply.m = nocover.reply.m.slice(1);
  const nc = run({ state: 'approve', trailer: 'Reviewed-by', scope: 'series', picks: [P1], overall: 'General remark.' }, JANE, nocover);
  assert.ok(nc.problems.some((p) => p.code === 'no-cover'));
  assert.deepStrictEqual(nc.emails.map((e) => e.key), ['patch-1']);
  assert.ok(nc.emails[0].body.endsWith('General remark.\n\nReviewed-by: Jane Reviewer <jane@example.test>'), 'the overall comment goes into the first patch reply');
  // nothing to say: an error and no email
  const none = run({ state: 'comment', overall: '' });
  assert.strictEqual(none.ok, false);
  assert.deepStrictEqual(none.emails, []);
  assert.strictEqual(run({ state: 'approve', trailer: 'Reviewed-by', scope: 'patches', picks: [] }).ok, false);
  // trailer line: the name cannot break out of the angle brackets, the address is checked
  assert.strictEqual(RM.trailerLine('Reviewed-by', { name: 'Jo <evil@x.test> Doe', email: 'jo@example.test' }), 'Reviewed-by: Jo evil@x.test Doe <jo@example.test>');
  const nonAscii = run({ state: 'approve', trailer: 'Reviewed-by', scope: 'series' }, { name: 'J\u00f6rg M\u00fcller', email: 'joerg@example.test' });
  assert.ok(nonAscii.emails[0].body.endsWith('Reviewed-by: J\u00f6rg M\u00fcller <joerg@example.test>'));
  assert.strictEqual(parseHead(nonAscii.emails[0].text).hdr.From, '=?UTF-8?B?' + Buffer.from('J\u00f6rg M\u00fcller').toString('base64') + '?= <joerg@example.test>');
}

// ---------------------------------------------------------------- threading: References, In-Reply-To, no stacked Re:
{
  const d = site.detail;
  const thread = RM.fromSite({ detail: d, reply: site.reply, bodies: site.bodies, diffs: site.diffs }, {}).thread;
  const by = {};
  thread.messages.forEach((m) => { by[m.mid] = m; });
  // Manuel's reply to Fan Wu's reply to patch 1: cover, patch 1, Fan Wu, then itself is not part of its own references
  assert.deepStrictEqual(RM.referencesFor(by, 'user52@example.test'), ['user44@example.test', 'user45@example.test', 'user52@example.test']);
  assert.deepStrictEqual(RM.referencesFor(by, 'user53@example.test'), ['user44@example.test', 'user45@example.test', 'user52@example.test', 'user53@example.test']);
  assert.deepStrictEqual(RM.referencesFor(by, 'user44@example.test'), ['user44@example.test']);
  // a parent that is not in the thread ends the chain but stays in it (the oldest reference)
  assert.deepStrictEqual(RM.referencesFor({ b: { mid: 'b', parent: 'a-unknown' } }, 'b'), ['a-unknown', 'b']);
  // cycles cannot loop
  assert.deepStrictEqual(RM.referencesFor({ a: { mid: 'a', parent: 'b' }, b: { mid: 'b', parent: 'a' } }, 'a'), ['b', 'a']);
  assert.deepStrictEqual(RM.referencesFor({ a: { mid: 'a', parent: 'a' } }, 'a'), ['a']);
  // through the reply composer
  const reply = RM.buildReply(Object.assign(RM.fromSite(site, {}), { now: NOW, reviewer: JANE, target: 'user52@example.test', body: 'Thanks, applied to my tree.' }));
  const h = parseHead(reply.emails[0].text);
  assert.strictEqual(h.hdr['In-Reply-To'], '<user52@example.test>');
  assert.strictEqual(h.hdr.References, '<user44@example.test> <user45@example.test> <user52@example.test>');
  assert.strictEqual(h.hdr.Subject, 'Re: [PATCH v2 1/3] ipe: add the .ipe policy keyring', 'one Re:, the subject of the message replied to had one already');
  assert.strictEqual(h.hdr.To, 'Fan Wu <user20@example.test>');
  assert.ok(!/Fan Wu/.test(h.hdr.Cc), 'the person in To is not repeated in Cc');
}
['Re: [PATCH] x', 'RE: re: Re: [PATCH] x', 'Aw: Re: [PATCH] x', 'Fwd: [PATCH] x', 're:[PATCH] x', '[PATCH] x', '  Re:   [PATCH]   x  '].forEach((s) => {
  assert.strictEqual(RM.replySubject(s), 'Re: [PATCH] x', s);
});
assert.strictEqual(RM.replySubject(''), 'Re: (no subject)');
assert.strictEqual(RM.replySubject('Re: '), 'Re: (no subject)');
assert.strictEqual(RM.replySubject('Return of the patch'), 'Re: Return of the patch');
assert.strictEqual(RM.replySubject('Reply: x'), 'Re: Reply: x');

// ---------------------------------------------------------------- recipients: Cc dedupe, the reviewer is never a recipient
{
  const th = {
    list: 'l', addr: 1, lists: ['l@lists.example.test', 'L@lists.example.test', 'bad list@x', 'other@lists.example.test'],
    who: { a: { name: 'Alice', addr: 'alice@example.test' }, b: { name: 'Bob', addr: 'Alice@Example.test' }, c: { name: 'Jane', addr: 'JANE@example.test' },
      d: { name: 'Dan', addr: 'dan@example.test' }, e: { name: 'Eve', addr: 'not an address' } },
    messages: [{ mid: 'm1', parent: '', name: 'Alice', hash: 'a', ts: 1, subject: '[PATCH] x' },
      { mid: 'm2', parent: 'm1', name: 'Bob', hash: 'b', ts: 2, subject: 'Re: [PATCH] x' },
      { mid: 'm3', parent: 'm2', name: 'Jane', hash: 'c', ts: 3, subject: 'Re: [PATCH] x' },
      { mid: 'm4', parent: 'm1', name: 'Dan', hash: 'd', ts: 4, subject: 'Re: [PATCH] x' },
      { mid: 'm5', parent: 'm1', name: 'Eve', hash: 'e', ts: 5, subject: 'Re: [PATCH] x' },
      { mid: 'm6', parent: 'm1', name: 'Dan again', hash: 'd', ts: 6, subject: 'Re: [PATCH] x' }]
  };
  const r = RM.buildReply({ now: NOW, reviewer: JANE, thread: th, target: 'm1', body: 'hi' });
  const h = parseHead(r.emails[0].text).hdr;
  assert.strictEqual(h.To, 'Alice <alice@example.test>');
  assert.strictEqual(h.Cc, 'l@lists.example.test, other@lists.example.test, Dan <dan@example.test>');   // lists once, Alice (To), Jane (the reviewer) and the broken address are out
  assert.deepStrictEqual(r.emails[0].headers.cc.map((p) => p.addr), ['l@lists.example.test', 'other@lists.example.test', 'dan@example.test']);
  // replying to the reviewer's own message does not put the reviewer in To
  const self = RM.buildReply({ now: NOW, reviewer: JANE, thread: th, target: 'm3', body: 'hi' });
  assert.strictEqual(parseHead(self.emails[0].text).hdr.To, undefined);
  assert.ok(!/jane@/i.test(parseHead(self.emails[0].text).hdr.Cc));
}

// ---------------------------------------------------------------- unknown addresses
{
  const nobody = JSON.parse(JSON.stringify(site));
  nobody.reply.who = {};
  const r = run({ state: 'comment', overall: 'hello' }, JANE, nobody);
  const h = parseHead(r.emails[0].text);
  assert.strictEqual(h.hdr.To, undefined, 'To is left out, not guessed');
  assert.ok(h.hdr.Cc.indexOf('kexec@lists.example.test') === 0);
  assert.ok(r.emails[0].warnings.some((w) => w.code === 'no-to'));
  assert.strictEqual(r.emails[0].toKnown, false);
  assert.ok(/^git send-email .*--to='kexec@lists.example.test'/.test(r.emails[0].command), 'the first list address is the To of the command');
  const opted = JSON.parse(JSON.stringify(site));
  opted.reply.addr = 0; opted.reply.who = {};
  const o = run({ state: 'comment', overall: 'hello' }, JANE, opted).emails[0];
  assert.ok(o.warnings.some((w) => w.code === 'no-cc' && /add the other participants to Cc yourself/i.test(w.text)));
  assert.ok(o.warnings.some((w) => w.code === 'no-to'));
  assert.strictEqual(parseHead(o.text).hdr.Cc, 'kexec@lists.example.test, keyrings@lists.example.test, linux-doc@lists.example.test, linux-security-module@lists.example.test, lkml@lists.example.test');
  // no shard at all (it could not be loaded): the email still has everything else
  const bare = JSON.parse(JSON.stringify(site));
  bare.reply = null;
  const b = run({ state: 'approve', trailer: 'Reviewed-by', scope: 'series', overall: 'fine', comments: [comment('c', P1, 'security/ipe/keyring.c', 22, 'x')] }, JANE, bare);
  assert.deepStrictEqual(b.emails.map((e) => e.key), ['patch-1', 'cover']);
  assert.strictEqual(parseHead(b.emails[0].text).hdr['In-Reply-To'], '<user45@example.test>');
  assert.strictEqual(parseHead(b.emails[0].text).hdr.References, '<user44@example.test> <user45@example.test>');
  assert.strictEqual(parseHead(b.emails[0].text).hdr.Subject, 'Re: [PATCH v2 1/3] ipe: add the .ipe policy keyring');
  assert.ok(b.emails[0].warnings.some((w) => w.code === 'no-to'));
}

// ---------------------------------------------------------------- hostile input
{
  const hostileName = 'Eve\r\nBcc: boss@example.test\r\nX-Evil: 1';
  const hostileSubject = 'Re: [PATCH] hi\r\nBcc: victim@example.test\nContent-Type: text/html\u2028X: y';
  const th = {
    list: 'l', addr: 1, lists: ['l@lists.example.test'],
    who: { a: { name: hostileName, addr: 'eve@example.test' }, b: { name: 'Mal "Quote" \\ Back, Comma <x@y.test>', addr: 'mal@example.test' },
      c: { name: '\u202eevil\u202c', addr: 'rlo@example.test' } },
    messages: [{ mid: 'm1', parent: '', name: hostileName, hash: 'a', ts: 1, subject: hostileSubject },
      { mid: 'm2', parent: 'm1', name: 'Mal', hash: 'b', ts: 2, subject: 'Re: ok' },
      { mid: 'm3', parent: 'm1', name: 'Rlo', hash: 'c', ts: 3, subject: 'Re: ok' }]
  };
  const reviewer = { name: 'Rev\r\nBcc: spy@example.test', email: 'rev@example.test' };
  const r = RM.buildReply({ now: NOW, reviewer, thread: th, target: 'm1', body: 'text\r\nBcc: not-a-header@example.test' });
  const mail = r.emails[0];
  const p = parseHead(mail.text);       // parseHead fails on any header line that is not "Name: value"
  assert.deepStrictEqual(p.order, ['From', 'To', 'Cc', 'Subject', 'In-Reply-To', 'References', 'Date', 'MIME-Version', 'Content-Type', 'Content-Transfer-Encoding', 'X-Unsent']);
  assert.ok(!/(^|\n)(Bcc|X-Evil):/.test(mail.text.split('\n\n')[0]), 'no injected header');
  assert.strictEqual(p.hdr.From, '"Rev Bcc: spy@example.test" <rev@example.test>');
  assert.strictEqual(p.hdr.To, '"Eve Bcc: boss@example.test X-Evil: 1" <eve@example.test>');
  assert.strictEqual(p.hdr.Subject, 'Re: [PATCH] hi Bcc: victim@example.test Content-Type: text/html X: y');
  assert.ok(p.hdr.Cc.indexOf('"Mal \\"Quote\\" \\\\ Back, Comma <x@y.test>" <mal@example.test>') >= 0, p.hdr.Cc);
  assert.ok(p.hdr.Cc.indexOf('\u202e') < 0 && p.hdr.Cc.indexOf('<rlo@example.test>') >= 0);
  assert.ok(!mail.text.includes('\r'));
  assert.ok(mail.body.split('\n').includes('Bcc: not-a-header@example.test'), 'in the body it is plain text');
  // the address is checked: nothing that is not an addr-spec gets into a header
  ['a@b.test\r\nBcc: x@y.test', 'a b@c.test', '<a@b.test>', 'a@b', 'a@b.test, c@d.test', '"x"@b.test', '', 'a@b.test>\nBcc: x@y.test'].forEach((a) => {
    const bad = RM.buildReply({ now: NOW, reviewer: { name: 'N', email: a }, thread: th, target: 'm2', body: 'x' });
    assert.strictEqual(bad.ok, false, JSON.stringify(a));
    assert.deepStrictEqual(bad.emails, []);
  });
  const th2 = JSON.parse(JSON.stringify(th));
  th2.who.a.addr = 'x@y.test\r\nBcc: z@y.test';
  th2.who.b.addr = 'a@b.test, c@d.test';
  const m2 = RM.buildReply({ now: NOW, reviewer: JANE, thread: th2, target: 'm1', body: 'x' }).emails[0];
  assert.ok(!/(^|\n)Bcc:/.test(m2.text.split('\n\n')[0]));
  assert.strictEqual(parseHead(m2.text).hdr.To, undefined);
  assert.ok(m2.warnings.some((w) => w.code === 'no-to'));
  // a hostile Message-ID cannot end the header either
  const th3 = JSON.parse(JSON.stringify(th));
  th3.messages[0].mid = 'm1>\r\nBcc: z@y.test';
  const m3 = RM.buildReply({ now: NOW, reviewer: JANE, thread: th3, target: th3.messages[0].mid, body: 'x' }).emails[0];
  assert.ok(!/(^|\n)Bcc:/.test(m3.text.split('\n\n')[0]));
  assert.ok(m3.warnings.some((w) => w.code === 'bad-mid'));
  // hostile diff lines are only ever quoted text
  const evil = JSON.parse(JSON.stringify(site));
  const f = evil.diffs[P1].f.filter((x) => x.p === 'security/ipe/ipe.c')[0];
  const rows = f.x.split('\n');
  rows[3] = '+\rBcc: boss@example.test\u0000\u001b[31m</script><img src=x onerror=alert(1)>';
  f.x = rows.join('\n');
  const e = run({ state: 'comment', comments: [comment('x', P1, 'security/ipe/ipe.c', 3, 'what is <b>this</b>?\r\n\u0000')] }, JANE, evil).emails[0];
  assert.ok(!e.text.includes('\r') && !e.text.includes('\u0000') && !e.text.includes('\u001b'));
  assert.ok(e.body.split('\n').every((l) => l === '' || l.charAt(0) !== 'B'), 'no line starts a header');
  assert.ok(e.body.includes('> +Bcc: boss@example.test[31m</script><img src=x onerror=alert(1)>'), 'a hostile diff line stays quoted, control characters dropped');
  assert.ok(e.body.includes('what is <b>this</b>?'));
  // header lines stay within 78 columns, encoded words within 75
  const manyCc = { list: 'l', addr: 1, lists: ['l@lists.example.test'], who: {}, messages: [{ mid: 'm1', parent: '', name: 'A', hash: 'h0', ts: 1, subject: 'x'.repeat(30) + ' ' + 'word '.repeat(30) }] };
  for (let i = 0; i < 40; i++) {
    manyCc.who['h' + i] = { name: 'Person Number ' + i + ' \u00e4\u00f6\u00fc', addr: 'person' + i + '@example.test' };
    manyCc.messages.push({ mid: 'r' + i, parent: 'm1', name: 'P', hash: 'h' + i, ts: 2 + i, subject: 'Re: x' });
  }
  const long = RM.buildReply({ now: NOW, reviewer: JANE, thread: manyCc, target: 'm1', body: 'x' }).emails[0];
  long.text.split('\n\n')[0].split('\n').forEach((l) => assert.ok(l.length <= 78, 'header line too long: ' + l));
  long.text.match(/=\?UTF-8\?B\?[A-Za-z0-9+/=]*\?=/g).forEach((w) => assert.ok(w.length <= 75, w));
  const hh = parseHead(long.text).hdr;
  assert.strictEqual(decodeWords(hh.Cc).split(', ').length, 40);   // the list and 39 people, the 40th is in To
  assert.ok(decodeWords(hh.Cc).indexOf('Person Number 7 \u00e4\u00f6\u00fc <person7@example.test>') > 0);
  // a very long non-ASCII subject and name are split into several encoded words that decode to the original
  const subject = '[PATCH] ' + '\u00fcber\u2603 '.repeat(30);
  const t4 = { list: 'l', addr: 1, lists: [], who: { a: { name: 'N\u00e4me '.repeat(20).trim(), addr: 'n@example.test' } }, messages: [{ mid: 'm1', parent: '', name: 'x', hash: 'a', ts: 1, subject }] };
  const m4 = RM.buildReply({ now: NOW, reviewer: JANE, thread: t4, target: 'm1', body: 'x' }).emails[0];
  m4.text.split('\n\n')[0].split('\n').forEach((l) => assert.ok(l.length <= 78, l));
  const h4 = parseHead(m4.text).hdr;
  assert.strictEqual(decodeWords(h4.Subject), 'Re: ' + subject.trim().replace(/\s+/g, ' '));
  assert.strictEqual(decodeWords(h4.To), 'N\u00e4me '.repeat(20).trim() + ' <n@example.test>');
  // quoted-printable only when a line is longer than 998 bytes, then consistently for the whole email
  const big = JSON.parse(JSON.stringify(site));
  const bf = big.diffs[P1].f.filter((x) => x.p === 'security/ipe/ipe.c')[0];
  bf.x = bf.x + '\n+' + '\u00e4'.repeat(600);
  const q = run({ state: 'comment', comments: [comment('x', P1, 'security/ipe/ipe.c', bf.x.split('\n').length - 1, 'long line = \u00fc \n')] }, JANE, big).emails[0];
  assert.strictEqual(parseHead(q.text).hdr['Content-Transfer-Encoding'], 'quoted-printable');
  assert.ok(q.warnings.some((w) => w.code === 'qp'));
  const qbody = q.text.split('\n\n').slice(1).join('\n\n');
  qbody.split('\n').forEach((l) => assert.ok(l.length <= 76, 'qp line too long'));
  assert.ok(!/[^\x20-\x7e\n\t]/.test(qbody));
  assert.ok(qpDecode(qbody).includes('> +' + '\u00e4'.repeat(600)));
  assert.ok(qpDecode(qbody).includes('long line = \u00fc'));
  assert.strictEqual(parseHead(mail.text).hdr['Content-Transfer-Encoding'], '8bit');
}

// ---------------------------------------------------------------- date, quoting, composer text
assert.strictEqual(RM.formatDate(0), 'Thu, 1 Jan 1970 00:00:00 +0000');
assert.strictEqual(RM.formatDate(1794870000), 'Mon, 16 Nov 2026 23:00:00 +0000');
assert.strictEqual(RM.formatDate(1793233882), 'Thu, 29 Oct 2026 00:31:22 +0000');
assert.strictEqual(RM.quoteLine(''), '>');
assert.strictEqual(RM.quoteLine('  '), '>');
assert.strictEqual(RM.quoteLine('> already'), '> > already');
assert.strictEqual(RM.quoteLine(' context'), '>  context');
assert.strictEqual(RM.quoteMessage({ name: 'Ann', ts: 0 }, 'a\n\nb\n> c\n'), 'On Thu, 1 Jan 1970 00:00:00 +0000, Ann wrote:\n> a\n>\n> b\n> > c\n\n');
assert.deepStrictEqual(RM.attribution({ name: 'Firstname Lastname '.repeat(5).trim(), ts: 0 }).map((l) => l.length <= 72), [true, true]);   // a long attribution wraps
assert.strictEqual(RM.attribution({ name: '', ts: 0 })[0], 'On Thu, 1 Jan 1970 00:00:00 +0000, someone wrote:');
{
  const text = 'On x, A wrote:\n> quoted\n\nmy reply that is long enough to need wrapping because it goes past the seventy two column limit\n';
  const r = RM.buildReply({ now: NOW, reviewer: JANE, thread: RM.fromSite(site, {}).thread, target: 'user45@example.test', body: text });
  assert.deepStrictEqual(r.emails[0].body.split('\n'), ['On x, A wrote:', '> quoted', '', 'my reply that is long enough to need wrapping because it goes past the',
    'seventy two column limit']);
  assert.strictEqual(RM.buildReply({ now: NOW, reviewer: JANE, thread: RM.fromSite(site, {}).thread, target: 'user45@example.test', body: 'On x, A wrote:\n> quoted\n\n' }).ok, false, 'only quoted text is not a reply');
  assert.strictEqual(RM.buildReply({ now: NOW, reviewer: JANE, thread: RM.fromSite(site, {}).thread, target: 'nobody', body: 'x' }).ok, false);
}

// ---------------------------------------------------------------- output formats: mailto, git send-email, file name
{
  const r = run({ state: 'comment', overall: 'Short.' });
  const m = r.emails[0];
  assert.ok(m.mailto && m.mailto.indexOf('mailto:user1@example.test?cc=') === 0, m.mailto);
  const q = new URLSearchParams(m.mailto.slice(m.mailto.indexOf('?') + 1));
  assert.strictEqual(q.get('subject'), 'Re: [PATCH v2 0/3] IPE: add a dedicated policy keyring');
  assert.strictEqual(q.get('In-Reply-To'), '<user44@example.test>');
  assert.strictEqual(q.get('body'), m.body.replace(/\n/g, '\r\n'));
  assert.strictEqual(q.get('cc').split(',').length, 9);
  assert.ok(m.mailto.indexOf('\n') < 0 && m.mailto.indexOf(' ') < 0);
  assert.ok(m.mailto.length <= 1800);
  // a long body: no link
  const longReview = run({ state: 'comment', overall: 'word '.repeat(600) });
  assert.strictEqual(longReview.emails[0].mailto, null);
  assert.strictEqual(RM.mailtoUrl(longReview.emails[0], 100000).indexOf('mailto:'), 0);
  // the limit is on the whole link, in characters of the encoded URL
  const edge = (n) => run({ state: 'comment', overall: 'x'.repeat(n).replace(/(.{60})/g, '$1 ') }).emails[0].mailto;
  let k = 25; while (edge(k) && k < 3000) k += 25;
  assert.ok(k > 400 && k < 3000);
  assert.ok(edge(k - 25).length <= 1800);
  // no To: "mailto:?cc=..."
  const nobody = JSON.parse(JSON.stringify(site)); nobody.reply.who = {};
  assert.strictEqual(run({ state: 'comment', overall: 'x' }, JANE, nobody).emails[0].mailto.indexOf('mailto:?cc='), 0);
  // reserved characters are percent encoded
  const odd = run({ state: 'comment', overall: 'a&b=c?d#e%f+g /h' }).emails[0];
  const b = new URLSearchParams(odd.mailto.slice(odd.mailto.indexOf('?') + 1)).get('body');
  assert.ok(b.indexOf('a&b=c?d#e%f+g /h') > 0);
  assert.ok(odd.mailto.indexOf('a&b') < 0 && odd.mailto.indexOf('#') < 0);
  // git send-email: the command names the file, the message it answers and every recipient, and quotes for the shell
  assert.strictEqual(m.filename, 're-cover-ipe-add-a-dedicated-policy-keyring.eml');
  assert.strictEqual(m.command, "git send-email --from='Jane Reviewer <jane@example.test>' --in-reply-to='<user44@example.test>' --no-thread --suppress-cc=all " +
    "--8bit-encoding=UTF-8 --to='user1@example.test' --cc='kexec@lists.example.test' --cc='keyrings@lists.example.test' --cc='linux-doc@lists.example.test' " +
    "--cc='linux-security-module@lists.example.test' --cc='lkml@lists.example.test' --cc='user11@example.test' --cc='user51@example.test' " +
    "--cc='user20@example.test' --cc='user5@example.test' 're-cover-ipe-add-a-dedicated-policy-keyring.eml'");
  const quote = RM.gitSendEmail({ headers: { from: { name: "O'Neil $(x)", addr: 'o@example.test' }, to: [], cc: [], inReplyTo: "a'b" } }, "it's.eml");
  assert.strictEqual(quote, "git send-email --from='\"O'\\''Neil $(x)\" <o@example.test>' --in-reply-to='<a'\\''b>' --no-thread --suppress-cc=all --8bit-encoding=UTF-8 'it'\\''s.eml'");
  // file names are plain
  run({ state: 'approve', trailer: 'Reviewed-by', scope: 'patches', picks: [P1, P2, P3] }).emails.forEach((e) => assert.ok(/^re-[a-z0-9-]+\.eml$/.test(e.filename), e.filename));
  assert.strictEqual(run({ state: 'comment', overall: 'x' }).emails[0].lore, 'https://lore.kernel.org/kexec/user44%40example.test/');
}

// ---------------------------------------------------------------- identity is required
{
  [{ name: '', email: 'a@example.test' }, { name: 'A', email: '' }, { name: '  ', email: 'bad' }, null, {}].forEach((rv) => {
    const r = RM.buildReview(Object.assign(make({ state: 'comment', overall: 'x' }), { reviewer: rv }));
    assert.strictEqual(r.ok, false);
    assert.deepStrictEqual(r.emails, []);
    assert.ok(r.problems.some((p) => p.level === 'error'));
  });
}

console.log('reviewmail ok');
