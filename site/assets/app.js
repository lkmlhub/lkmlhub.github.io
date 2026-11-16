/* Router and theme toggle. Routes are hash based so they work under any sub-path with no server support:
 *   #/                      home
 *   #/about                 about
 *   #/u/<hash>              contributor
 *   #/<list>[/<tab>]        list, tab is patches|discussions|activity|contributors|about
 *   #/<list>/pr/<n>         patch series, #/<list>/issue/<n> discussion
 */
(function () {
  'use strict';
  var LH = window.LH, h = LH.h;
  var app = document.getElementById('app');

  function parse() {
    var raw = location.hash.replace(/^#\/?/, '');
    var qi = raw.indexOf('?');
    var path = qi >= 0 ? raw.slice(0, qi) : raw;
    var query = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
    var parts = path.split('/').filter(Boolean).map(function (p) {
      try { return decodeURIComponent(p); } catch (e) { return p; }
    });
    return { parts: parts, query: query };
  }

  function dispatch() {
    var r = parse(), p = r.parts;
    var done;
    if (!p.length) done = LH.renderHome(app);
    else if (p[0] === 'about' && p.length === 1) done = LH.renderAbout(app);
    else if (p[0] === 'u' && p.length === 2) done = LH.renderPerson(app, p[1]);
    else if (p.length === 3 && (p[1] === 'pr' || p[1] === 'issue')) done = LH.renderThread(app, p[0], p[1], p[2], r.query);
    else if (p.length <= 2) done = LH.renderList(app, p[0], p[1] || 'patches', r.query);
    else {
      LH.clear(app).appendChild(LH.errorBox(new Error('Unknown page: ' + p.join('/')), 'this page'));
      done = Promise.resolve();
    }
    return done;
  }

  var first = true;
  function onRoute() {
    Promise.resolve().then(dispatch).catch(function (e) {
      LH.clear(app).appendChild(LH.errorBox(e, 'this page'));
    }).then(function () {
      if (!first) {
        window.scrollTo(0, 0);
        var main = document.getElementById('main');
        if (main) main.focus({ preventScroll: true });
      }
      first = false;
    });
  }

  /* ---------------------------------------------------------------- theme */
  var toggle = document.getElementById('theme-toggle');
  function effectiveTheme() {
    var t = document.documentElement.getAttribute('data-theme');
    if (t) return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function paintToggle() {
    var dark = effectiveTheme() === 'dark';
    LH.clear(toggle).appendChild(LH.icon(dark ? 'sun' : 'moon'));
    toggle.setAttribute('aria-pressed', dark ? 'true' : 'false');
    toggle.setAttribute('title', dark ? 'Switch to light mode' : 'Switch to dark mode');
  }
  toggle.addEventListener('click', function () {
    var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('lh-theme', next); } catch (e) { /* ignore */ }
    paintToggle();
  });
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () { paintToggle(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
  }
  paintToggle();

  window.addEventListener('hashchange', onRoute);
  onRoute();
})();
