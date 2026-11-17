/* Drafts and identity of the review feature. Everything lives in localStorage of THIS browser and nowhere else:
 *   lkmlhub-review:<list>:<number>:v<version>   the pending line comments and the review form of one series version
 *   lkmlhub-identity                            name and e-mail of the reviewer (From header and trailers)
 * Without a usable localStorage (private mode, blocked) the drafts live in memory until the page is reloaded, and
 * `persistent` says so, so that the page can show a notice. The storage object is passed in, so node can test this. */
(function (root) {
  'use strict';
  var IDENTITY_KEY = 'lkmlhub-identity';
  var MAX_COMMENTS = 500;
  var MAX_TEXT = 20000;
  var memory = Object.create(null);     // per page load, used when localStorage is not usable
  var STATES = ['approve', 'comment', 'request'];
  var TRAILERS = ['', 'Reviewed-by', 'Acked-by', 'Tested-by', 'Nacked-by'];

  function draftKey(list, n, version) { return 'lkmlhub-review:' + list + ':' + n + ':v' + version; }

  function str(v, max) { return typeof v === 'string' ? v.slice(0, max) : ''; }

  /* A draft read back from storage is data from an untrusted place: only known fields of the right type survive. */
  function sanitize(raw) {
    var out = { v: 1, comments: [], review: { state: 'comment', overall: '', trailer: '', scope: '', picks: [] } };
    if (!raw || typeof raw !== 'object') return out;
    if (Array.isArray(raw.comments)) {
      raw.comments.slice(0, MAX_COMMENTS).forEach(function (c) {
        if (!c || typeof c !== 'object' || typeof c.patch !== 'string' || !(c.ix >= 0) || Math.floor(c.ix) !== c.ix) return;
        if (typeof c.text !== 'string' || !c.id || typeof c.id !== 'string') return;
        out.comments.push({
          id: c.id.slice(0, 40), patch: c.patch.slice(0, 400), kind: c.kind === 'msg' ? 'msg' : 'diff', path: str(c.path, 400), ix: c.ix,
          line: typeof c.line === 'string' ? c.line.slice(0, 2100) : null, text: c.text.slice(0, MAX_TEXT),
          t: typeof c.t === 'number' ? c.t : 0, prepared: typeof c.prepared === 'number' ? c.prepared : 0
        });
      });
    }
    var r = raw.review;
    if (r && typeof r === 'object') {
      out.review.state = STATES.indexOf(r.state) >= 0 ? r.state : 'comment';
      out.review.overall = str(r.overall, MAX_TEXT);
      out.review.trailer = TRAILERS.indexOf(r.trailer) >= 0 ? r.trailer : '';
      out.review.scope = r.scope === 'series' || r.scope === 'patches' ? r.scope : '';
      out.review.picks = Array.isArray(r.picks) ? r.picks.filter(function (p) { return typeof p === 'string'; }).slice(0, 200) : [];
    }
    return out;
  }

  /* The parsed value of a key, or null when it is missing or not valid JSON. */
  function readStorage(storage, key) {
    try {
      var text = storage.getItem(key);
      return text === null ? null : JSON.parse(text);
    } catch (e) { return null; }
  }

  function probe(storage) {
    if (!storage) return false;
    try { storage.setItem('lkmlhub-probe', '1'); storage.removeItem('lkmlhub-probe'); return true; } catch (e) { return false; }
  }

  var seq = 0;
  function newId() { seq++; return 'c' + Date.now().toString(36) + seq.toString(36) + Math.floor(Math.random() * 1296).toString(36); }

  /* The drafts of one series version. Methods: add, update, remove, clear, find, pending, markPrepared, setReview, onchange. */
  function openDrafts(storage, key) {
    var persistent = probe(storage);
    var data = sanitize(persistent ? readStorage(storage, key) : memory[key]);
    var listeners = [];

    function save() {
      var json = JSON.stringify(data);
      if (persistent) {
        try {
          if (data.comments.length || data.review.overall || data.review.picks.length || data.review.scope || data.review.trailer ||
              data.review.state !== 'comment') storage.setItem(key, json);
          else storage.removeItem(key);
        } catch (e) { persistent = false; memory[key] = JSON.parse(json); }
      } else memory[key] = JSON.parse(json);
      listeners.forEach(function (fn) { fn(); });
    }

    var api = {
      get persistent() { return persistent; },
      get comments() { return data.comments; },
      get review() { return data.review; },
      onchange: function (fn) { listeners.push(fn); },
      add: function (c) {
        if (data.comments.length >= MAX_COMMENTS) throw new Error('At most ' + MAX_COMMENTS + ' comments per review');
        var item = { id: newId(), patch: c.patch, kind: c.kind === 'msg' ? 'msg' : 'diff', path: c.path || '', ix: c.ix,
          line: typeof c.line === 'string' ? c.line : null, text: String(c.text).slice(0, MAX_TEXT), t: Math.floor(Date.now() / 1000), prepared: 0 };
        data.comments.push(item);
        save();
        return item;
      },
      find: function (id) { return data.comments.filter(function (c) { return c.id === id; })[0] || null; },
      update: function (id, text) {
        var c = api.find(id);
        if (!c) return null;
        c.text = String(text).slice(0, MAX_TEXT);
        c.prepared = 0;     // an edited comment is pending again
        save();
        return c;
      },
      remove: function (id) {
        var n = data.comments.length;
        data.comments = data.comments.filter(function (c) { return c.id !== id; });
        if (data.comments.length !== n) save();
        return data.comments.length !== n;
      },
      clear: function () {
        data = sanitize(null);
        if (persistent) { try { storage.removeItem(key); } catch (e) { /* nothing more to do */ } }
        delete memory[key];
        listeners.forEach(function (fn) { fn(); });
      },
      pending: function () { return data.comments.filter(function (c) { return !c.prepared; }); },
      markPrepared: function (ids) {
        var now = Math.floor(Date.now() / 1000);
        data.comments.forEach(function (c) { if (ids.indexOf(c.id) >= 0) c.prepared = now; });
        save();
      },
      setReview: function (patch) {
        Object.keys(patch).forEach(function (k) { data.review[k] = patch[k]; });
        data.review = sanitize({ review: data.review }).review;
        save();
      }
    };
    return api;
  }

  /* The reviewer's name and e-mail. */
  function identity(storage) {
    var persistent = probe(storage);
    var cur = { name: '', email: '' };
    if (persistent) {
      var r = readStorage(storage, IDENTITY_KEY);
      if (r && typeof r === 'object') cur = { name: str(r.name, 200), email: str(r.email, 254) };
    } else if (memory[IDENTITY_KEY]) cur = memory[IDENTITY_KEY];
    return {
      get persistent() { return persistent; },
      get: function () { return { name: cur.name, email: cur.email }; },
      set: function (v) {
        cur = { name: str(v && v.name, 200), email: str(v && v.email, 254) };
        if (persistent) { try { storage.setItem(IDENTITY_KEY, JSON.stringify(cur)); } catch (e) { persistent = false; memory[IDENTITY_KEY] = cur; } }
        else memory[IDENTITY_KEY] = cur;
      },
      forget: function () {
        cur = { name: '', email: '' };
        delete memory[IDENTITY_KEY];
        if (persistent) { try { storage.removeItem(IDENTITY_KEY); } catch (e) { /* nothing more to do */ } }
      }
    };
  }

  var api = { draftKey: draftKey, openDrafts: openDrafts, identity: identity, sanitize: sanitize, IDENTITY_KEY: IDENTITY_KEY,
    MAX_COMMENTS: MAX_COMMENTS, MAX_TEXT: MAX_TEXT };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.LHReviewStore = api;
})(typeof window !== 'undefined' ? window : globalThis);
