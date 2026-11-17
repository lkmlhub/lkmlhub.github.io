/* A tiny Chrome DevTools Protocol client for the browser tests (node 22+, built in WebSocket and fetch).
 * It starts a headless Chrome with a fresh profile and drives it with REAL input events (mouse, keyboard), so the tests
 * use the page like a person does and nothing in the shipped site is there for the tests. */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const KEYS = {
  Enter: { code: 'Enter', vk: 13, text: '\r' }, Escape: { code: 'Escape', vk: 27 }, Tab: { code: 'Tab', vk: 9 },
  ArrowDown: { code: 'ArrowDown', vk: 40 }, ArrowUp: { code: 'ArrowUp', vk: 38 }, ArrowLeft: { code: 'ArrowLeft', vk: 37 },
  ArrowRight: { code: 'ArrowRight', vk: 39 }, End: { code: 'End', vk: 35 }, Space: { code: 'Space', vk: 32, text: ' ', key: ' ' }, Backspace: { code: 'Backspace', vk: 8 }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Page {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = []; this.requests = []; this.errors = []; this.logs = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
      } else if (msg.method) {
        this.handlers.forEach((h) => h(msg));
      }
    });
    this.on((m) => {
      if (m.method === 'Network.requestWillBeSent') this.requests.push(m.params.request.url);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(m.params.exceptionDetails.exception ? m.params.exceptionDetails.exception.description : m.params.exceptionDetails.text);
      if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'assert')) this.logs.push(m.params.args.map((a) => a.value || a.description).join(' '));
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') this.logs.push(m.params.entry.text + ' ' + (m.params.entry.url || ''));
    });
  }
  on(fn) { this.handlers.push(fn); }
  send(method, params) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('page error: ' + (r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text) + '\nin: ' + expr.slice(0, 300));
    return r.result.value;
  }
  async waitFor(expr, what, ms) {
    const end = Date.now() + (ms || 10000);
    let last;
    while (Date.now() < end) {
      try { last = await this.eval(expr); if (last) return last; } catch (e) { last = e.message; }
      await sleep(40);
    }
    throw new Error('timeout waiting for ' + (what || expr) + ' (last: ' + JSON.stringify(last) + ')');
  }
  async goto(url, ready) {
    await this.send('Page.navigate', { url });
    await this.waitFor('document.readyState === "complete"', 'load', 15000);
    if (ready) await this.waitFor(ready, 'page ready', 15000);
  }
  async reload(ready) {
    await this.send('Page.reload', {});
    await sleep(150);
    await this.waitFor('document.readyState === "complete"', 'reload', 15000);
    if (ready) await this.waitFor(ready, 'page ready after reload', 15000);
  }
  /* centre of the first element matching the selector, after scrolling it into view */
  async center(sel, idx) {
    const r = await this.eval('(function(){var e=document.querySelectorAll(' + JSON.stringify(sel) + ')[' + (idx || 0) + '];if(!e)return null;e.scrollIntoView({block:"center",inline:"nearest"});' +
      'var b=e.getBoundingClientRect();return {x:b.left+b.width/2,y:b.top+b.height/2,w:b.width,h:b.height};})()');
    if (!r) throw new Error('no element for ' + sel);
    return r;
  }
  async hover(sel, idx) {
    const c = await this.center(sel, idx);
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y });
    await sleep(60);
    return c;
  }
  async click(sel, idx) {
    const c = await this.center(sel, idx);
    if (c.w < 1 || c.h < 1) throw new Error('element has no size: ' + sel);
    // a real click: the element under the pointer must be the target or inside it
    const hit = await this.eval('(function(){var t=document.querySelectorAll(' + JSON.stringify(sel) + ')[' + (idx || 0) + '];var e=document.elementFromPoint(' + c.x + ',' + c.y + ');return !!e && (t===e||t.contains(e)||e.contains(t));})()');
    if (!hit) throw new Error('the pointer would not hit ' + sel + ' (covered by another element)');
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y });
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1 });
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1 });
    await sleep(80);
  }
  /* type text; "\n" is the Enter key */
  async typeLines(text) {
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      if (lines[i]) await this.send('Input.insertText', { text: lines[i] });
      if (i < lines.length - 1) await this.press('Enter');
    }
  }
  async press(key, mods) {
    const k = KEYS[key] || { code: 'Key' + key.toUpperCase(), vk: key.toUpperCase().charCodeAt(0), text: key, key };
    const base = { key: k.key || key, code: k.code, windowsVirtualKeyCode: k.vk, nativeVirtualKeyCode: k.vk, modifiers: mods || 0 };
    await this.send('Input.dispatchKeyEvent', Object.assign({ type: k.text && !(mods & 2) ? 'keyDown' : 'rawKeyDown' }, base, k.text && !(mods & 2) ? { text: k.text } : {}));
    await this.send('Input.dispatchKeyEvent', Object.assign({ type: 'keyUp' }, base));
    await sleep(40);
  }
  async viewport(w, h) {
    await this.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  }
  async screenshot(file) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
  }
  close() { try { this.ws.close(); } catch (e) { /* closed already */ } }
}

/* Start Chrome with a fresh profile. Returns {page, close, profile, downloads}. */
async function launch(opts) {
  const chrome = opts.chrome;
  const profile = fs.mkdtempSync(path.join(opts.tmp || os.tmpdir(), 'lkmlhub-cdp-'));
  const downloads = path.join(profile, 'downloads');
  fs.mkdirSync(downloads);
  const args = ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run',
    '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--hide-scrollbars',
    '--window-size=' + (opts.width || 1280) + ',' + (opts.height || 900), '--force-device-scale-factor=1', 'about:blank'];
  const proc = spawn(chrome, args, { stdio: ['ignore', 'ignore', 'ignore'], detached: true });   // own process group: only our own Chrome is ever killed
  const portFile = path.join(profile, 'DevToolsActivePort');
  const end = Date.now() + 20000;
  while (!fs.existsSync(portFile) && Date.now() < end) await sleep(50);
  if (!fs.existsSync(portFile)) { proc.kill('SIGKILL'); throw new Error('chrome did not start'); }
  const [port, browserPath] = fs.readFileSync(portFile, 'utf8').trim().split('\n');
  let targets;
  for (let i = 0; i < 100; i++) {
    targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
    if (targets.some((t) => t.type === 'page')) break;
    await sleep(50);
  }
  const target = targets.filter((t) => t.type === 'page')[0];
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve); ws.addEventListener('error', reject); });
  const page = new Page(ws);
  const bws = new WebSocket('ws://127.0.0.1:' + port + browserPath);
  await new Promise((resolve, reject) => { bws.addEventListener('open', resolve); bws.addEventListener('error', reject); });
  const browser = new Page(bws);
  await page.send('Page.enable'); await page.send('Runtime.enable'); await page.send('Network.enable'); await page.send('Log.enable');
  await browser.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
  await page.viewport(opts.width || 1280, opts.height || 900);
  return {
    page, browser, profile, downloads, pid: proc.pid,
    async close() {
      try { await Promise.race([browser.send('Browser.close'), sleep(3000)]); } catch (e) { /* the connection closes with the browser */ }
      page.close(); browser.close();
      await sleep(200);
      try { process.kill(-proc.pid, 'SIGKILL'); } catch (e) { /* already gone */ }
      fs.rmSync(profile, { recursive: true, force: true });
    }
  };
}

module.exports = { launch, sleep, Page };
