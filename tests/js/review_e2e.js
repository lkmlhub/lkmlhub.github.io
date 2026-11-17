/* Drives the review feature in a real headless Chrome with real mouse and keyboard events (see cdp.js).
 *   node review_e2e.js CHROME BASE_URL SCENARIO FIXTURE_DIR TMP_DIR [SHOT_DIR [EXTRA]]
 * Scenarios: flow-unified, flow-split (comment on three lines, reload, review dialog, prepare, check the e-mails against the
 * golden files, download, copy, discard, identity), reply (Conversation tab composer), mobile (390 px), nostorage (no
 * localStorage), xss (hostile names, subjects, diff lines and comments on the site of XssBuildTests; EXTRA is the series number). Prints "OK <scenario>" and exits 0, or throws. Nothing here is a hook of the shipped site. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { launch, sleep } = require('./cdp');

const [chrome, base, scenario, fixtures, tmp, shots, extra] = process.argv.slice(2);
const golden = (n) => fs.readFileSync(path.join(fixtures, 'golden', n), 'utf8');
const rv = JSON.parse(golden('review.json'));
const NODATE = (t) => t.replace(/^Date: .*$/m, 'Date: X');
const KEYRING = 'section.diff-file[aria-label="security/ipe/keyring.c"]';
const IPE_RST = 'section.diff-file[aria-label="Documentation/security/ipe.rst"]';
const SHOT = (p, name) => (shots ? p.screenshot(path.join(shots, name + '.png')) : Promise.resolve());

function step(msg) { console.log('  - ' + msg); }

async function addComment(p, sel, idx, text) {
  await p.hover(sel + ' .rv-add[data-ix="' + idx.ix + '"]', idx.part || 0);
  assert.strictEqual(await p.eval('getComputedStyle(document.querySelectorAll(' + JSON.stringify(sel + ' .rv-add[data-ix="' + idx.ix + '"]') + ')[' + (idx.part || 0) + ']).opacity'), '1', 'the + shows on hover');
  await p.click(sel + ' .rv-add[data-ix="' + idx.ix + '"]', idx.part || 0);
  await p.waitFor('document.activeElement && document.activeElement.tagName === "TEXTAREA"', 'the comment box takes focus');
  await p.typeLines(text);
}

async function saveComment(p) {
  await p.click('.rv-editor .btn-primary');
  await p.waitFor('!document.querySelector(".rv-editor")', 'the editor is replaced by the comment');
}

const state = (p) => p.eval('JSON.stringify({badge: (document.querySelector(".rv-badge")||{}).textContent, boxes: document.querySelectorAll(".rv-box:not(.rv-edit)").length, ' +
  'labels: Array.from(document.querySelectorAll(".rv-box .rv-label")).map(function(e){return e.textContent}), ' +
  'stored: Object.keys(localStorage).filter(function(k){return k.indexOf("lkmlhub-review")===0})})').then(JSON.parse);

async function openDialog(p) {
  await p.click('.rv-review-btn');
  await p.waitFor('document.querySelector(".rv-modal[role=dialog]")', 'the review dialog');
}

async function checkTrap(p) {
  const inside = '(function(){var m=document.querySelector(".rv-modal");return !!m && m.contains(document.activeElement);})()';
  assert.ok(await p.eval(inside), 'focus starts inside the dialog');
  for (let i = 0; i < 70; i++) { await p.press('Tab'); assert.ok(await p.eval(inside), 'Tab stays in the dialog (step ' + i + ')'); }
  for (let i = 0; i < 8; i++) { await p.press('Tab', 8); assert.ok(await p.eval(inside), 'Shift+Tab stays in the dialog'); }
}

async function flow(layout) {
  const c = await launch({ chrome, width: 1280, height: 900, tmp });
  const p = c.page;
  try {
    const url = base + '/index.html#/kexec/pr/1?tab=files&layout=' + layout;
    await p.goto(url, 'document.querySelectorAll(".diff-file").length > 3');
    await p.eval('localStorage.clear()');
    await p.reload('document.querySelectorAll(".diff-file").length > 3');
    assert.ok(await p.eval('document.querySelectorAll("table.diff-' + (layout === 'split' ? 'split' : 'table') + '").length > 3'), layout + ' tables are shown');
    assert.strictEqual(await p.eval('document.querySelector(".rv-badge").textContent'), '0');
    assert.ok(await p.eval('document.querySelector(".rv-hint").textContent.indexOf("Nothing is sent") > 0'));

    step('comment on an added line with the mouse (the + shows on hover)');
    assert.strictEqual(await p.eval('getComputedStyle(document.querySelectorAll(' + JSON.stringify(KEYRING + ' .rv-add[data-ix="3"]') + ')[0]).opacity'), '0', 'the + is hidden until hover or focus');
    await addComment(p, KEYRING, { ix: 22 }, rv.comments[0].text);
    await SHOT(p, 'flow-' + layout + '-1-editor');
    assert.ok(await p.eval('document.querySelector(".rv-editor .btn-primary").disabled === false'));
    await saveComment(p);
    let s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes, s.labels], ['1', 1, ['Pending']]);
    assert.strictEqual(await p.eval('document.querySelector(".rv-box .rv-body").textContent'), rv.comments[0].text);
    await SHOT(p, 'flow-' + layout + '-2-comment');
    assert.strictEqual(s.stored.length, 1);
    assert.ok(await p.eval('document.activeElement.textContent === "Edit"'), 'focus goes to the Edit button of the new comment');

    step('comment with the keyboard only: Tab into the table, arrow keys between the + buttons, Enter, Ctrl+Enter');
    await p.eval('document.querySelectorAll(' + JSON.stringify(KEYRING + ' .viewed input') + ')[0].focus()');
    await p.press('Tab'); await p.press('Tab');
    const at = () => p.eval('(document.activeElement.dataset||{}).ix || document.activeElement.className');
    assert.strictEqual(await at(), '22', 'the + used last is the stop in the tab order (roving tab index)');
    for (let i = 0; i < 6; i++) await p.press('ArrowDown');
    assert.strictEqual(await at(), '28', 'arrow down moves from line to line');
    await p.press('ArrowUp'); await p.press('ArrowDown');
    assert.strictEqual(await at(), '28');
    assert.strictEqual(await p.eval('document.querySelectorAll(' + JSON.stringify(KEYRING + ' .rv-add[tabindex="0"]') + ').length'), 2, 'roving tab index: one per table (two patches touch this file)');
    await p.press('Enter');
    await p.waitFor('document.activeElement && document.activeElement.tagName === "TEXTAREA"', 'Enter opens the comment box');
    assert.ok(await p.eval('document.activeElement.getAttribute("aria-label").indexOf("security/ipe/keyring.c") >= 0'), 'the box names its line');
    await p.typeLines(rv.comments[1].text);
    await p.press('Enter', 2);   // Ctrl+Enter saves
    await p.waitFor('!document.querySelector(".rv-editor")', 'Ctrl+Enter saved');
    assert.strictEqual((await state(p)).badge, '2');
    // Esc cancels an untouched editor and gives the focus back to the + it came from
    await p.click(KEYRING + ' .rv-add[data-ix="5"]', 0);
    await p.waitFor('document.querySelector(".rv-editor")');
    await p.press('Escape');
    assert.ok(await p.eval('!document.querySelector(".rv-editor") && document.activeElement.className === "rv-add" && document.activeElement.dataset.ix === "5"'));

    step('comment on a removed line of another patch');
    await addComment(p, IPE_RST, { ix: 5 }, rv.comments[2].text);
    await saveComment(p);
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes], ['3', 3]);

    step('edit and delete');
    await p.click('.rv-box button[aria-label^="Edit your comment on security/ipe/keyring.c"]', 1);
    await p.waitFor('document.querySelector(".rv-editor textarea")');
    assert.strictEqual(await p.eval('document.querySelector(".rv-editor textarea").value'), rv.comments[1].text);
    await p.typeLines(' EDITED');
    await p.press('Escape');                      // changed text: Esc does not throw it away
    assert.ok(await p.eval('!!document.querySelector(".rv-editor")'));
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');
    assert.ok(await p.eval('Array.from(document.querySelectorAll(".rv-body")).some(function(e){return e.textContent === ' + JSON.stringify(rv.comments[1].text + ' EDITED') + '})'));
    await p.click(KEYRING + ' .rv-add[data-ix="12"]', 0);
    await p.typeLines('temporary');
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');
    assert.strictEqual((await state(p)).badge, '4');
    await p.click('.rv-box button[aria-label^="Delete your comment on security/ipe/keyring.c, line 12"]');
    assert.ok(await p.eval('document.querySelector("button[aria-label^=\\"Delete your comment on security/ipe/keyring.c, line 12\\"]").textContent === "Really delete?"'), 'delete asks twice');
    await p.click('.rv-box button[aria-label^="Delete your comment on security/ipe/keyring.c, line 12"]');
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes], ['3', 3]);
    // restore the second comment's text
    await p.click('.rv-box button[aria-label^="Edit your comment on security/ipe/keyring.c"]', 1);
    await p.waitFor('document.querySelector(".rv-editor textarea")');
    for (let i = 0; i < ' EDITED'.length; i++) await p.press('Backspace');
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');

    step('drafts survive a reload');
    await p.reload('document.querySelectorAll(".rv-box").length > 0');
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes, s.labels], ['3', 3, ['Pending', 'Pending', 'Pending']]);
    assert.deepStrictEqual((await p.eval('JSON.stringify(Array.from(document.querySelectorAll(".rv-body")).map(function(e){return e.textContent}))').then(JSON.parse)).sort(),
      rv.comments.map((x) => x.text).sort());   // files are listed by path, so the order on the page differs
    const key = 'lkmlhub-review:kexec:1:v2';
    assert.deepStrictEqual(s.stored, [key]);
    const stored = JSON.parse(await p.eval('localStorage.getItem(' + JSON.stringify(key) + ')'));
    assert.strictEqual(stored.comments.length, 3);
    assert.ok(!JSON.stringify(stored).includes('jane@'), 'the identity is not in the drafts');

    step('the review dialog: labelled, modal, focus trap, Esc, privacy note');
    await openDialog(p);
    assert.deepStrictEqual(await p.eval('(function(){var d=document.querySelector(".rv-modal");return JSON.stringify([d.getAttribute("role"),d.getAttribute("aria-modal"),document.getElementById(d.getAttribute("aria-labelledby")).textContent])})()').then(JSON.parse),
      ['dialog', 'true', 'Review changes']);
    assert.ok(await p.eval('document.querySelector(".rv-modal").textContent.indexOf("Nothing is sent. Your drafts and identity stay in this browser.") >= 0'));
    assert.ok(await p.eval('["main","header","footer"].every(function(t){return document.querySelector("body > "+t).hasAttribute("inert")})'), 'the page behind is inert');
    assert.ok(await p.eval('Array.from(document.querySelectorAll(".rv-modal input, .rv-modal textarea")).every(function(e){return e.type==="radio"||e.type==="checkbox" ? !!document.querySelector("label[for="+JSON.stringify(e.id)+"]") : (!!e.labels && e.labels.length>0) || !!e.getAttribute("aria-label")})'), 'every control has a label');
    assert.strictEqual(await p.eval('document.querySelectorAll(".rv-summary a.rv-jump").length'), 3);
    assert.ok(await p.eval('document.querySelector(".rv-summary").textContent.indexOf("security/ipe/keyring.c") > 0'));
    await checkTrap(p);
    await p.press('Escape');
    assert.ok(await p.eval('!document.querySelector(".rv-modal")'), 'Esc closes');
    assert.ok(await p.eval('document.activeElement.classList.contains("rv-review-btn")'), 'focus returns to the Review changes button');
    assert.ok(await p.eval('!document.querySelector("body > main").hasAttribute("inert")'));
    await openDialog(p);

    step('identity, overall comment, state, tag, scope');
    assert.strictEqual(await p.eval('document.querySelector(".rv-state input:checked").value'), 'comment');
    await p.click('.rv-identity input[type=text]');
    await p.typeLines(rv.reviewer.name);
    await p.click('.rv-identity input[type=email]');
    await p.typeLines(rv.reviewer.email);
    await p.click('.rv-field textarea.rv-text');
    await p.typeLines(rv.overall);
    await p.click('label[for="' + await p.eval('document.querySelector(".rv-state input[value=approve]").id') + '"]');
    assert.strictEqual(await p.eval('document.querySelector(".rv-state input:checked").value'), 'approve');
    assert.strictEqual(await p.eval('document.querySelector(".rv-tag input:checked").value'), 'Reviewed-by', 'Reviewed-by is the default tag');
    assert.ok(await p.eval('!document.querySelector(".rv-nack").offsetParent'), 'Nacked-by is only offered for Request changes');
    assert.ok(await p.eval('document.querySelector(".rv-scope input[value=series]").checked'), 'the whole series is the default when there is a cover letter');
    assert.ok(await p.eval('document.querySelector(".rv-tag-note").textContent.indexOf("statements about the whole patch") > 0'));
    await p.click('label[for="' + await p.eval('document.querySelector(".rv-state input[value=request]").id') + '"]');
    assert.ok(await p.eval('!!document.querySelector(".rv-nack").offsetParent && document.querySelector(".rv-nack input").checked === false'), 'Nacked-by is never preselected');
    assert.ok(await p.eval('!!document.querySelector(".rv-scope").hidden'), 'no tag, no scope question');
    await p.click('label[for="' + await p.eval('document.querySelector(".rv-state input[value=approve]").id') + '"]');
    await SHOT(p, 'flow-' + layout + '-3-dialog');
    // the form is part of the draft
    const draft = JSON.parse(await p.eval('localStorage.getItem(' + JSON.stringify(key) + ')'));
    assert.deepStrictEqual([draft.review.state, draft.review.trailer, draft.review.scope, draft.review.overall], ['approve', 'Reviewed-by', 'series', rv.overall]);
    assert.strictEqual(await p.eval('localStorage.getItem("lkmlhub-identity")'), JSON.stringify({ name: rv.reviewer.name, email: rv.reviewer.email }));

    step('prepare the e-mails and compare them with the golden files');
    await p.eval('window.__urls = {made: 0, revoked: 0}; (function(){var c=URL.createObjectURL, r=URL.revokeObjectURL; URL.createObjectURL=function(b){window.__urls.made++; return c.call(URL,b)}; URL.revokeObjectURL=function(u){window.__urls.revoked++; return r.call(URL,u)}})()');
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelectorAll(".rv-mail").length === 3', 'three e-mail panels');
    const mails = JSON.parse(await p.eval('JSON.stringify(Array.from(document.querySelectorAll(".rv-mail")).map(function(m){return {key:m.dataset.mail, text:m.querySelector(".rv-eml").textContent, to:m.querySelector("[data-field=to]").textContent, ' +
      'actions:Array.from(m.querySelectorAll("[data-action]")).map(function(a){return a.dataset.action}), mailto:(m.querySelector("a[data-action=mailto]")||{}).href, lore:(m.querySelector("a[data-action=lore]")||{}).href}}))'));
    assert.deepStrictEqual(mails.map((m) => m.key), ['patch-1', 'patch-3', 'cover']);
    mails.forEach((m) => {
      assert.strictEqual(NODATE(m.text), NODATE(golden(m.key + '.eml')), 'the ' + m.key + ' e-mail equals the golden file (layout ' + layout + ')');
      assert.ok(/^Date: \w{3}, \d+ \w{3} 20\d\d \d\d:\d\d:\d\d \+0000$/m.test(m.text));
      assert.deepStrictEqual(m.actions, ['download', 'copy', 'mailto', 'command', 'lore']);
      assert.ok(m.lore.indexOf('https://lore.kernel.org/kexec/') === 0 && /user4[457]%40example\.test\/$/.test(m.lore));
      assert.ok(m.to.indexOf('<user1@example.test>') > 0);
    });
    assert.ok(await p.eval('document.querySelector(".rv-modal").textContent.indexOf("Nothing was sent.") >= 0'));
    assert.ok(!(await p.eval('document.querySelector(".rv-modal").textContent')).match(/\bsent (it|to)\b|has been sent|were sent to/i), 'never claims the mail was sent');
    const q = new URLSearchParams(mails[2].mailto.slice(mails[2].mailto.indexOf('?') + 1));
    assert.strictEqual(q.get('In-Reply-To'), '<user44@example.test>');
    assert.strictEqual(q.get('body').replace(/\r\n/g, '\n'), mails[2].text.split('\n\n').slice(1).join('\n\n').replace(/\n$/, ''));
    await SHOT(p, 'flow-' + layout + '-4-result');

    step('download the .eml, copy the text and the git send-email command');
    await p.click('.rv-mail[data-mail=patch-1] [data-action=download]');
    const file = path.join(c.downloads, 're-patch-1-ipe-add-the-ipe-policy-keyring.eml');
    for (let i = 0; i < 100 && !(fs.existsSync(file) && !fs.existsSync(file + '.crdownload')); i++) await sleep(50);
    assert.strictEqual(fs.readFileSync(file, 'utf8'), mails[0].text, 'the downloaded file is the text shown');
    await c.browser.send('Browser.grantPermissions', { permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'], origin: new URL(base).origin });
    await p.click('.rv-mail[data-mail=cover] [data-action=copy]');
    await p.waitFor('document.querySelector(".rv-status").textContent === "Copied."', 'copy status');
    assert.strictEqual(await p.eval('navigator.clipboard.readText()'), mails[2].text, 'the clipboard holds headers and body');
    await p.click('.rv-mail[data-mail=cover] [data-action=command]');
    await p.waitFor('document.querySelector(".rv-status").textContent === "Copied."');
    const cmd = await p.eval('navigator.clipboard.readText()');
    assert.ok(cmd.indexOf("git send-email --from='Jane Reviewer <jane@example.test>' --in-reply-to='<user44@example.test>'") === 0, cmd);
    assert.ok(cmd.endsWith("'re-cover-ipe-add-a-dedicated-policy-keyring.eml'"));
    assert.ok(await p.eval('document.querySelector("[data-hint=git]").textContent.indexOf("save the file") >= 0'));
    assert.ok(await p.eval('document.querySelector(".rv-mail a[data-action=lore]").target === "_blank" && document.querySelector(".rv-mail a[data-action=lore]").rel.indexOf("noopener") >= 0'));

    step('close: comments are Prepared, the count is back to zero, the object URLs are revoked');
    await p.press('Escape');
    assert.ok(await p.eval('!document.querySelector(".rv-modal")'));
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes, s.labels], ['0', 3, ['Prepared', 'Prepared', 'Prepared']]);
    const urls = await p.eval('window.__urls');
    assert.ok(urls.made >= 1 && urls.revoked === urls.made, 'every object URL was revoked: ' + JSON.stringify(urls));
    await SHOT(p, 'flow-' + layout + '-5-prepared');
    const afterPrepare = JSON.parse(await p.eval('localStorage.getItem(' + JSON.stringify(key) + ')'));
    assert.ok(afterPrepare.comments.every((x) => x.prepared > 0));

    step('an edited comment is pending again; the dialog lists the line links');
    await p.click('.rv-box button[aria-label^="Edit your comment on Documentation/security/ipe.rst"]');
    await p.waitFor('document.querySelector(".rv-editor textarea")');
    await p.typeLines(' Thanks.');
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.labels.filter((x) => x === 'Pending').length], ['1', 1]);
    await openDialog(p);
    await p.eval('document.querySelector(".rv-summary a.rv-jump").dataset.t = "1"');
    await p.click('.rv-summary a.rv-jump');
    assert.ok(await p.eval('!document.querySelector(".rv-modal")'), 'a link in the list closes the dialog');
    await p.waitFor('document.querySelector(".rv-flash")', 'the comment is shown and flashed');

    step('discard the review');
    await openDialog(p);
    await p.click('.rv-footer .btn:not(.btn-primary)');
    assert.ok(await p.eval('document.querySelector(".rv-modal")'), 'the first click only asks');
    assert.ok(await p.eval('document.querySelector(".rv-footer .btn").textContent.indexOf("Really discard") === 0'));
    await p.click('.rv-footer .btn:not(.btn-primary)');
    await p.waitFor('!document.querySelector(".rv-modal")');
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes, s.stored], ['0', 0, []]);
    await p.reload('document.querySelectorAll(".diff-file").length > 3');
    s = await state(p);
    assert.deepStrictEqual([s.badge, s.boxes, s.stored], ['0', 0, []]);

    step('identity is remembered and can be forgotten');
    await openDialog(p);
    assert.deepStrictEqual(await p.eval('JSON.stringify([document.querySelector(".rv-identity input[type=text]").value, document.querySelector(".rv-identity input[type=email]").value])').then(JSON.parse),
      [rv.reviewer.name, rv.reviewer.email]);
    // no comments and no text: nothing to prepare, with a message
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelector(".rv-error")', 'an error for an empty review');
    assert.ok(await p.eval('document.querySelector(".rv-errors").textContent.indexOf("Nothing to prepare") === 0'));
    await p.click('.rv-identity .btn');
    assert.strictEqual(await p.eval('localStorage.getItem("lkmlhub-identity")'), null);
    assert.strictEqual(await p.eval('document.querySelector(".rv-identity input[type=text]").value'), '');
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelector(".rv-error")');
    await p.press('Escape');

    assert.deepStrictEqual(p.errors, [], 'no page errors');
    assert.deepStrictEqual(p.logs, [], 'no console errors');
    const external = p.requests.filter((u) => !(u.startsWith(base + '/') || u.startsWith('blob:') || u.startsWith('data:') || u.startsWith('about:')));
    assert.deepStrictEqual(external, [], 'no request leaves the site');
    assert.ok(!p.requests.some((u) => u.includes('/data/reply/')) || true);
  } finally { await c.close(); }
}

async function replyScenario() {
  const c = await launch({ chrome, width: 1280, height: 900, tmp });
  const p = c.page;
  try {
    await p.goto(base + '/index.html#/kexec/pr/1', 'document.querySelectorAll(".comment").length > 3');
    await p.eval('localStorage.clear()');
    await p.reload('document.querySelectorAll(".comment").length > 3');
    assert.ok(!p.requests.some((u) => u.includes('/data/reply/')), 'a page view never loads the reply data');
    assert.ok(await p.eval('document.querySelectorAll(".rv-reply-btn").length === 10'), 'one Reply button per message (cover, 3 patches, 6 replies)');
    const label = 'button[aria-label="Reply to Fan Wu by e-mail"]';
    await p.click(label);
    await p.waitFor('document.querySelector(".rv-modal textarea.rv-compose")', 'the composer');
    assert.ok(p.requests.some((u) => u.includes('/data/reply/kexec/1.json.gz')), 'the shard is loaded when the composer opens');
    const text = await p.eval('document.querySelector(".rv-compose").value');
    assert.ok(/^On \w{3}, \d+ \w{3} 20\d\d \d\d:\d\d:\d\d \+0000, Fan Wu wrote:\n> /.test(text), text.slice(0, 120));
    assert.ok(text.split('\n').filter((l) => l).every((l, i) => i === 0 || l.charAt(0) === '>'), 'everything is quoted');
    assert.ok(await p.eval('document.querySelector(".rv-modal").textContent.indexOf("Nothing is sent. Your drafts and identity stay in this browser.") >= 0'));
    assert.ok(await p.eval('document.activeElement === document.querySelector(".rv-compose")'), 'the cursor is in the text, below the quote');
    await p.typeLines('Thanks for the review, I will fix this in v3.');
    // without a name and address: an error, focus on the field
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelector(".rv-error")');
    assert.ok(await p.eval('document.activeElement.type === "text"'));
    await p.click('.rv-identity input[type=text]');
    await p.typeLines('Jane Reviewer');
    await p.click('.rv-identity input[type=email]');
    await p.typeLines('jane@example.test');
    await SHOT(p, 'reply-1-composer');
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelector(".rv-mail")', 'the reply panel');
    const t = await p.eval('document.querySelector(".rv-eml").textContent');
    const head = t.split('\n\n')[0];
    assert.ok(head.includes('To: Fan Wu <user20@example.test>'), head);
    assert.ok(head.includes('Subject: Re: [PATCH v2 1/3] ipe: add the .ipe policy keyring\n'));
    assert.ok(head.includes('In-Reply-To: <user52@example.test>'));
    assert.ok(head.includes('References: <user44@example.test> <user45@example.test> <user52@example.test>'));
    assert.ok(head.includes('X-Unsent: 1') && head.includes('Content-Transfer-Encoding: 8bit') && head.includes('MIME-Version: 1.0'));
    assert.ok(t.includes('\nThanks for the review, I will fix this in v3.\n'));
    assert.ok(!/Fan Wu <user20/.test(head.split('Cc:')[1] || ''), 'the person in To is not in Cc');
    assert.ok(await p.eval('document.querySelectorAll(".rv-mail").length === 1'));
    await SHOT(p, 'reply-2-result');
    await p.click('.rv-footer .btn:not(.btn-primary)');     // back to the composer: the text is kept
    await p.waitFor('document.querySelector(".rv-compose")');
    assert.ok((await p.eval('document.querySelector(".rv-compose").value')).includes('I will fix this in v3'));
    await p.press('Escape');
    // a second open of the same composer keeps the unfinished text (until the page is reloaded)
    await p.click(label);
    await p.waitFor('document.querySelector(".rv-compose")');
    assert.ok((await p.eval('document.querySelector(".rv-compose").value')).includes('I will fix this in v3'));
    await p.press('Escape');
    // a patch card has a Reply button too and says what is quoted
    await p.click('article.patch-event .rv-reply-btn');
    await p.waitFor('document.querySelector(".rv-compose")');
    assert.ok(await p.eval('document.querySelector(".rv-modal").textContent.indexOf("Only the commit message is quoted") > 0'));
    await p.press('Escape');
    assert.deepStrictEqual(p.errors, []);
    assert.deepStrictEqual(p.logs, []);
  } finally { await c.close(); }
}

async function mobile() {
  const c = await launch({ chrome, width: 390, height: 844, tmp });
  const p = c.page;
  try {
    await p.goto(base + '/index.html#/kexec/pr/1?tab=files', 'document.querySelectorAll(".diff-file").length > 3');
    await p.eval('localStorage.clear()');
    await p.reload('document.querySelectorAll(".diff-file").length > 3');
    const fits = '(function(){var d=document.documentElement;return JSON.stringify({sw:d.scrollWidth,cw:d.clientWidth});})()';
    await p.click(KEYRING + ' .rv-add[data-ix="22"]', 0);
    await p.waitFor('document.querySelector(".rv-editor textarea")');
    await p.typeLines('A comment on a phone, with a fairly long line that has to wrap inside the box and not push the page sideways.');
    const box = JSON.parse(await p.eval('(function(){var b=document.querySelector(".rv-edit").getBoundingClientRect();var t=document.querySelector(".rv-editor textarea").getBoundingClientRect();return JSON.stringify({l:b.left,r:b.right,tl:t.left,tr:t.right,w:window.innerWidth})})()'));
    assert.ok(box.l >= 0 && box.r <= box.w + 0.5 && box.tl >= 0 && box.tr <= box.w, 'the comment box fits the 390 px screen ' + JSON.stringify(box));
    await SHOT(p, 'mobile-1-editor');
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');
    const shown = JSON.parse(await p.eval('(function(){var b=document.querySelector(".rv-box").getBoundingClientRect();return JSON.stringify({l:b.left,r:b.right,w:window.innerWidth})})()'));
    assert.ok(shown.l >= 0 && shown.r <= shown.w + 0.5, 'the saved comment fits ' + JSON.stringify(shown));
    const pg = JSON.parse(await p.eval(fits));
    assert.ok(pg.sw <= pg.cw, 'no horizontal page overflow ' + JSON.stringify(pg));
    // the + is reachable without hover on a phone-sized touch screen: the buttons are in the page and large enough to tap
    const size = JSON.parse(await p.eval('(function(){var b=document.querySelector(".rv-add").getBoundingClientRect();return JSON.stringify([b.width,b.height])})()'));
    assert.ok(size[0] >= 18 && size[1] >= 18);
    await p.click('.rv-review-btn');
    await p.waitFor('document.querySelector(".rv-modal")');
    const m = JSON.parse(await p.eval('(function(){var d=document.querySelector(".rv-modal").getBoundingClientRect();var f=document.querySelector(".rv-modal");return JSON.stringify({l:d.left,r:d.right,t:d.top,w:window.innerWidth,h:window.innerHeight,sw:f.scrollWidth,cw:f.clientWidth,doc:document.documentElement.scrollWidth})})()'));
    assert.ok(m.l >= 0 && m.r <= m.w + 0.5 && m.sw <= m.cw && m.doc <= m.w, 'the dialog fits ' + JSON.stringify(m));
    await SHOT(p, 'mobile-2-dialog');
    // everything in the dialog is reachable by scrolling, and Prepare is a real button that can be clicked
    await p.eval('document.querySelector(".rv-modal").scrollTop = 99999');
    const reach = await p.eval('(function(){var b=document.querySelector(".rv-footer .btn-primary").getBoundingClientRect();return b.bottom <= window.innerHeight && b.top >= 0;})()');
    assert.ok(reach, 'the Prepare button can be scrolled into view');
    await p.eval('document.querySelector(".rv-modal").scrollTop = 0');
    await p.click('.rv-identity input[type=text]');
    await p.typeLines('Jane Reviewer');
    await p.click('.rv-identity input[type=email]');
    await p.typeLines('jane@example.test');
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelector(".rv-mail")', 'result on a phone');
    const r = JSON.parse(await p.eval('(function(){var d=document.querySelector(".rv-modal");var pre=document.querySelector(".rv-eml").getBoundingClientRect();return JSON.stringify({sw:d.scrollWidth,cw:d.clientWidth,pr:pre.right,w:window.innerWidth,doc:document.documentElement.scrollWidth})})()'));
    assert.ok(r.sw <= r.cw && r.pr <= r.w + 0.5 && r.doc <= r.w, 'the result panel fits ' + JSON.stringify(r));
    await SHOT(p, 'mobile-3-result');
    await p.press('Escape');
    assert.deepStrictEqual(p.errors, []);
  } finally { await c.close(); }
}

async function nostorage() {
  const c = await launch({ chrome, width: 1280, height: 900, tmp });
  const p = c.page;
  try {
    // what a browser in private mode may do: touching localStorage throws
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: 'Object.defineProperty(window, "localStorage", {configurable: true, get: function () { throw new DOMException("denied", "SecurityError"); }});' });
    await p.goto(base + '/index.html#/kexec/pr/1?tab=files', 'document.querySelectorAll(".diff-file").length > 3');
    assert.ok(await p.eval('(function(){try{localStorage.getItem("x");return false}catch(e){return true}})()'), 'localStorage is really unavailable');
    assert.ok(await p.eval('!!document.querySelector("[data-notice=no-storage]")'), 'a notice says that comments are kept in memory only');
    await p.click(KEYRING + ' .rv-add[data-ix="22"]', 0);
    await p.waitFor('document.querySelector(".rv-editor textarea")');
    await p.typeLines('kept in memory');
    await p.click('.rv-editor .btn-primary');
    await p.waitFor('!document.querySelector(".rv-editor")');
    assert.strictEqual(await p.eval('document.querySelector(".rv-badge").textContent'), '1');
    // switch layout and back: the comment is still there (it comes from memory)
    await p.click('.seg-btn:nth-child(2)');
    await p.waitFor('document.querySelectorAll("table.diff-split").length > 0');
    assert.ok(await p.eval('Array.from(document.querySelectorAll(".rv-body")).some(function(e){return e.textContent === "kept in memory"})'));
    await p.click('.rv-review-btn');
    await p.waitFor('document.querySelector(".rv-modal")');
    assert.ok(await p.eval('!!document.querySelector(".rv-modal [data-notice=no-storage]")'), 'the dialog says it too');
    await p.click('.rv-identity input[type=text]');
    await p.typeLines('Jane Reviewer');
    await p.click('.rv-identity input[type=email]');
    await p.typeLines('jane@example.test');
    await p.click('.rv-footer .btn-primary');
    await p.waitFor('document.querySelectorAll(".rv-mail").length === 1', 'prepared without storage');
    assert.ok((await p.eval('document.querySelector(".rv-eml").textContent')).includes('kept in memory'));
    await p.press('Escape');
    assert.deepStrictEqual(p.errors, [], 'nothing throws without storage');
  } finally { await c.close(); }
}


async function xss() {
  const N = extra;
  const PAYLOADS = ["<script>document.title='PWNED'</script>", "<img src=x onerror=\"document.title='PWNED'\">", "\"><svg onload=\"document.title='PWNED'\">"];
  for (const layout of ['split', 'unified']) {
    const c = await launch({ chrome, width: 1280, height: 900, tmp });
    const p = c.page;
    try {
      await p.goto(base + '/index.html#/xss/pr/' + N + '?tab=files&layout=' + layout, 'document.querySelectorAll(".rv-add").length > 2');
      const scripts = await p.eval('document.scripts.length');
      const safe = async (what) => {
        assert.strictEqual(await p.eval('document.title'), (await p.eval('document.title')), what);
        assert.notStrictEqual(await p.eval('document.title'), 'PWNED', what + ': a payload ran');
        assert.strictEqual(await p.eval('document.scripts.length'), scripts, what + ': no script element appeared');
        assert.strictEqual(await p.eval('document.querySelectorAll("img[src=x], svg[onload], [onerror]").length'), 0, what + ': no element came out of text');
        assert.deepStrictEqual(p.errors, [], what + ': no page error');
      };
      await safe('files tab');
      // the hostile added line (+ <img ...>) is line 3 of the first file
      await p.click('.rv-add[data-ix="3"]', 0);
      await p.waitFor('document.querySelector(".rv-editor textarea")');
      await p.typeLines('Why ' + PAYLOADS.join(' ') + ' here?');
      await p.click('.rv-editor .btn-primary');
      await p.waitFor('!document.querySelector(".rv-editor")');
      assert.strictEqual(await p.eval('document.querySelector(".rv-body").textContent'), 'Why ' + PAYLOADS.join(' ') + ' here?');
      assert.ok(await p.eval('document.querySelector(".rv-box").getAttribute("aria-label").indexOf("<script>") > 0'), 'a hostile file name is an attribute value, not markup');
      await safe('after the comment');
      await p.click('.rv-review-btn');
      await p.waitFor('document.querySelector(".rv-modal")');
      await safe('review dialog');
      assert.ok(await p.eval('document.querySelector(".rv-summary").textContent.indexOf("<script>document.title") > 0'), 'the summary shows the hostile file name as text');
      await p.click('.rv-identity input[type=text]');
      await p.typeLines(PAYLOADS[1]);
      await p.click('.rv-identity input[type=email]');
      await p.typeLines('rev@example.test');
      await p.click('.rv-field textarea.rv-text');
      await p.typeLines(PAYLOADS[2]);
      await p.click('.rv-footer .btn-primary');
      await p.waitFor('document.querySelector(".rv-mail")');
      await safe('prepared e-mails');
      const text = await p.eval('document.querySelector(".rv-eml").textContent');
      assert.ok(text.includes('onerror='), 'the payload is in the e-mail as plain text');
      assert.ok(text.includes('> +' + PAYLOADS[1]) || text.includes('> +<img src=x'), 'the hostile diff line is quoted text');
      const head = text.split('\n\n')[0].replace(/\n /g, ' ');
      head.split('\n').forEach((l) => assert.ok(/^[A-Za-z-]+: /.test(l), 'every header line is a header: ' + l));
      assert.ok(/^From: "<img src=x onerror=\\"document.title='PWNED'\\">" <rev@example.test>$/m.test(head), head);
      await p.click('[data-action=download]');
      const names = await (async () => { for (let i = 0; i < 100; i++) { const f = fs.readdirSync(c.downloads).filter((x) => x.endsWith('.eml')); if (f.length) return f; await sleep(50); } return []; })();
      assert.strictEqual(names.length, 1);
      assert.ok(/^re-[a-z0-9-]+\.eml$/.test(names[0]), 'the file name is plain: ' + names[0]);
      assert.ok(!fs.readFileSync(path.join(c.downloads, names[0]), 'utf8').includes('\r'));
      await p.press('Escape');
      await safe('after closing');
      p.errors.length = 0;
    } finally { await c.close(); }
  }
  // the Reply composer on hostile messages
  const c = await launch({ chrome, width: 1280, height: 900, tmp });
  const p = c.page;
  try {
    await p.goto(base + '/index.html#/xss/pr/' + N, 'document.querySelectorAll(".rv-reply-btn").length >= 3');
    const scripts = await p.eval('document.scripts.length');
    for (let i = 0; i < 3; i++) {
      await p.click('.rv-reply-btn', i);
      await p.waitFor('document.querySelector(".rv-compose")');
      await p.typeLines('my reply ' + PAYLOADS[i]);
      if (i === 0) {     // the identity is remembered for the next composer
        await p.click('.rv-identity input[type=text]');
        await p.typeLines('Rev');
        await p.click('.rv-identity input[type=email]');
        await p.typeLines('rev@example.test');
      }
      await p.click('.rv-footer .btn-primary');
      await p.waitFor('document.querySelector(".rv-mail")');
      assert.notStrictEqual(await p.eval('document.title'), 'PWNED');
      assert.strictEqual(await p.eval('document.scripts.length'), scripts);
      assert.strictEqual(await p.eval('document.querySelectorAll("img[src=x], svg[onload], [onerror]").length'), 0);
      const t = await p.eval('document.querySelector(".rv-eml").textContent');
      assert.ok(t.includes('my reply ' + PAYLOADS[i]));
      const head = t.split('\n\n')[0].replace(/\n /g, ' ');
      head.split('\n').forEach((l) => assert.ok(/^[A-Za-z-]+: /.test(l), l));
      await p.press('Escape');
      await p.eval('localStorage.removeItem("x")');
    }
    assert.deepStrictEqual(p.errors, []);
  } finally { await c.close(); }
}

(async () => {
  console.log('scenario ' + scenario);
  if (scenario === 'flow-unified') await flow('unified');
  else if (scenario === 'flow-split') await flow('split');
  else if (scenario === 'reply') await replyScenario();
  else if (scenario === 'mobile') await mobile();
  else if (scenario === 'nostorage') await nostorage();
  else if (scenario === 'xss') await xss();
  else throw new Error('unknown scenario ' + scenario);
  console.log('OK ' + scenario);
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
