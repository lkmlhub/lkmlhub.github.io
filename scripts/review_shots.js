/* Screenshots of the review feature for docs/screenshots, from the ipe fixture site (python3 -m tests.ipe_fixture OUT):
 *   node scripts/review_shots.js CHROME BASE_URL OUT_DIR TMP_DIR
 * Every shot uses a fresh Chrome profile (no stale cache or storage) and is driven with real clicks and typing. */
const fs = require('fs');
const path = require('path');
const { launch } = require('../tests/js/cdp');

const [chrome, base, out, tmp] = process.argv.slice(2);
const rv = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/fixtures/review/golden/review.json'), 'utf8'));
const KEYRING = 'section.diff-file[aria-label="security/ipe/keyring.c"]';
const IPE_RST = 'section.diff-file[aria-label="Documentation/security/ipe.rst"]';

async function comment(p, sel, ix, text, part) {
  await p.click(sel + ' .rv-add[data-ix="' + ix + '"]', part || 0);
  await p.waitFor('document.activeElement && document.activeElement.tagName === "TEXTAREA"');
  await p.typeLines(text);
}
async function save(p) {
  await p.click('.rv-editor .btn-primary');
  await p.waitFor('!document.querySelector(".rv-editor")');
}
async function theme(p, dark) {
  await p.eval('document.documentElement.setAttribute("data-theme", ' + JSON.stringify(dark ? 'dark' : 'light') + ')');
}
async function threeComments(p) {
  await comment(p, KEYRING, 22, rv.comments[0].text); await save(p);
  await comment(p, KEYRING, 28, rv.comments[1].text); await save(p);
  await comment(p, IPE_RST, 5, rv.comments[2].text); await save(p);
}
async function identity(p) {
  await p.click('.rv-identity input[type=text]'); await p.typeLines(rv.reviewer.name);
  await p.click('.rv-identity input[type=email]'); await p.typeLines(rv.reviewer.email);
}

async function shot(name, w, h, dark, layout, run) {
  const c = await launch({ chrome, width: w, height: h, tmp });
  const p = c.page;
  try {
    await p.goto(base + '/index.html#/kexec/pr/1?tab=files&layout=' + layout, 'document.querySelectorAll(".diff-file").length > 3');
    await theme(p, dark);
    await run(p);
    await new Promise((r) => setTimeout(r, 200));
    await p.screenshot(path.join(out, name + '.png'));
    console.log('wrote', name);
  } finally { await c.close(); }
}

(async () => {
  for (const dark of [false, true]) {
    const t = dark ? 'dark' : 'light';
    await shot('review-comment-' + t + '-desktop', 1280, 900, dark, 'unified', async (p) => {
      await p.click('.tree-file[title="security/ipe/keyring.c"]');
      await comment(p, KEYRING, 22, rv.comments[0].text); await save(p);
      await comment(p, KEYRING, 25, 'These flags are hard to read, can they be split over lines?');
      await p.eval('document.querySelector(".rv-editor").scrollIntoView({block: "center"})');
    });
    await shot('review-comment-split-' + t + '-desktop', 1280, 900, dark, 'split', async (p) => {
      await p.click('.tree-file[title="Documentation/security/ipe.rst"]');
      await p.click('button.btn[aria-controls="files-tree-list"]');
      await comment(p, IPE_RST, 5, rv.comments[2].text); await save(p);
      await p.eval('document.querySelector(".rv-box").scrollIntoView({block: "center"})');
    });
    await shot('review-dialog-' + t + '-desktop', 1280, 900, dark, 'unified', async (p) => {
      await threeComments(p);
      await p.click('.rv-review-btn');
      await p.waitFor('document.querySelector(".rv-modal")');
      await identity(p);
      await p.click('.rv-field textarea.rv-text'); await p.typeLines(rv.overall);
      await p.click('label[for="' + await p.eval('document.querySelector(".rv-state input[value=approve]").id') + '"]');
      await p.eval('document.querySelector(".rv-modal").scrollTop = 0');
    });
    await shot('review-result-' + t + '-desktop', 1280, 900, dark, 'unified', async (p) => {
      await threeComments(p);
      await p.click('.rv-review-btn');
      await p.waitFor('document.querySelector(".rv-modal")');
      await identity(p);
      await p.click('.rv-field textarea.rv-text'); await p.typeLines(rv.overall);
      await p.click('label[for="' + await p.eval('document.querySelector(".rv-state input[value=approve]").id') + '"]');
      await p.click('.rv-footer .btn-primary');
      await p.waitFor('document.querySelectorAll(".rv-mail").length === 3');
      await p.eval('document.querySelector(".rv-eml").style.maxHeight = "none"; document.querySelector(".rv-modal").scrollTop = 0');
      await p.eval('(function(){var m=document.querySelectorAll(".rv-mail"); for (var i=1;i<m.length;i++) m[i].hidden = true;})()');
    });
  }
  for (const dark of [false, true]) {
    const t = dark ? 'dark' : 'light';
    const c = await launch({ chrome, width: 1280, height: 900, tmp });
    const p = c.page;
    try {
      await p.goto(base + '/index.html#/kexec/pr/1', 'document.querySelectorAll(".comment").length > 3');
      await theme(p, dark);
      await p.click('button[aria-label="Reply to Fan Wu by e-mail"]');
      await p.waitFor('document.querySelector(".rv-compose")');
      await identity(p);
      await p.eval('document.querySelector(".rv-compose").focus()');
      await p.press('End', 2);     // Ctrl+End: the end of the quoted text
      await p.typeLines('Thanks for the review, I will fix this in v3.');
      await p.eval('document.querySelector(".rv-modal").scrollTop = 0');
      await new Promise((r) => setTimeout(r, 200));
      await p.screenshot(path.join(out, 'review-reply-' + t + '-desktop.png'));
      console.log('wrote review-reply-' + t + '-desktop');
    } finally { await c.close(); }
    await shot('review-dialog-' + t + '-mobile', 390, 844, dark, 'unified', async (p) => {
      await threeComments(p);
      await p.click('.rv-review-btn');
      await p.waitFor('document.querySelector(".rv-modal")');
      await p.eval('document.querySelector(".rv-modal").scrollTop = 0');
    });
  }
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
