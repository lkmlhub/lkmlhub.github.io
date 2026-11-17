/* LoreHub shared helpers. Every node is built with createElement/textContent: mail text is never parsed as HTML. */
(function () {
  'use strict';
  var LH = window.LH = window.LH || {};
  var SVGNS = 'http://www.w3.org/2000/svg';

  function append(node, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { append(node, k); }); return; }
    node.appendChild(typeof kid === 'object' && kid.nodeType ? kid : document.createTextNode(String(kid)));
  }

  function applyProps(node, props) {
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') node.setAttribute('class', v);
      else if (k === 'text') node.textContent = v;
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (k === 'value') node.value = v;
      else node.setAttribute(k, v === true ? '' : String(v));
    });
  }

  LH.h = function (tag, props) {
    var node = document.createElement(tag);
    applyProps(node, props);
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  };

  LH.svg = function (tag, props) {
    var node = document.createElementNS(SVGNS, tag);
    Object.keys(props || {}).forEach(function (k) {
      if (props[k] !== null && props[k] !== undefined) node.setAttribute(k, String(props[k]));
    });
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  };

  LH.clear = function (node) { while (node.firstChild) node.removeChild(node.firstChild); return node; };

  /* ---------------------------------------------------------------- data */
  var cache = {};
  LH.getJSON = function (path) {
    if (!cache[path]) {
      cache[path] = fetch(path, { credentials: 'omit' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + path);
        return r.json();
      });
      cache[path].catch(function () { delete cache[path]; });
    }
    return cache[path];
  };

  /* ---------------------------------------------------------------- time */
  LH.now = function () { return Math.floor(Date.now() / 1000); };
  LH.timeAgo = function (ts) {
    if (!ts) return 'never';
    var s = Math.max(0, LH.now() - ts);
    var units = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]];
    for (var i = 0; i < units.length; i++) {
      var n = Math.floor(s / units[i][1]);
      if (n >= 1) return n + ' ' + units[i][0] + (n > 1 ? 's' : '') + ' ago';
    }
    return 'just now';
  };
  LH.fullDate = function (ts) {
    return new Date(ts * 1000).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
  };
  LH.shortDate = function (ts) {
    if (!ts) return 'unknown';
    var d = new Date(ts * 1000);
    var txt = d.toLocaleString('en', { month: 'short', day: 'numeric', timeZone: 'UTC' });
    return d.getUTCFullYear() === new Date().getUTCFullYear() ? txt : txt + ' ' + d.getUTCFullYear();
  };
  /* One line about how far back a list's history reaches. hc: 1 complete, 2 capped, 0 in progress, undefined no backfill. */
  LH.historyNote = function (hs, hc) {
    if (hc === 1) return { text: 'history back to ' + LH.shortDate(hs), title: 'The backfill finished: patch related mail is complete back to this date' };
    if (hc === 2) return { text: 'history back to ' + LH.shortDate(hs) + ' (capped)', title: 'A busy list: the backfill stopped at its page cap, older mail is on lore' };
    if (hc === 0) return { text: 'history back to ' + LH.shortDate(hs) + ', filling in', title: 'The backfill is still walking back in time over the next runs' };
    return { text: 'history since ' + LH.shortDate(hs), title: 'The oldest message LoreHub holds for this list' };
  };
  LH.timeEl = function (ts) {
    return LH.h('time', { datetime: new Date(ts * 1000).toISOString(), title: LH.fullDate(ts) }, LH.timeAgo(ts));
  };
  LH.plural = function (n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); };

  /* ---------------------------------------------------------------- links */
  LH.route = function () {
    var parts = Array.prototype.slice.call(arguments).map(encodeURIComponent);
    return '#/' + parts.join('/');
  };
  LH.threadHref = function (list, kind, n) { return LH.route(list, kind === 'pr' ? 'pr' : 'issue', n); };
  LH.authorHref = function (hash) { return LH.route('u', hash); };
  LH.loreUrl = function (list, mid) {
    var safe = /^[A-Za-z0-9%._~-]+$/.test(mid) ? mid : encodeURIComponent(mid);
    return 'https://lore.kernel.org/' + encodeURIComponent(list) + '/' + safe + '/';
  };
  LH.extLink = function (href, text, cls) {
    return LH.h('a', { href: href, class: cls || null, rel: 'noopener noreferrer', target: '_blank' }, text);
  };

  /* Turn plain text into text nodes and safe http(s) links. */
  var URL_RE = /(https?:\/\/[^\s<>"']+)/g;
  LH.linkify = function (text) {
    var out = [];
    String(text).split(URL_RE).forEach(function (part, i) {
      if (i % 2 === 0) { if (part) out.push(part); return; }
      var m = /^(.*?)([.,;:!?)\]>]*)$/.exec(part);
      out.push(LH.h('a', { href: m[1], rel: 'noopener noreferrer nofollow', target: '_blank' }, m[1]));
      if (m[2]) out.push(m[2]);
    });
    return out;
  };

  /* ---------------------------------------------------------------- avatars */
  /* Deterministic 5x5 mirrored identicon from the author hash. No network, no e-mail. */
  LH.avatar = function (hash, size, name) {
    size = size || 20;
    hash = String(hash || '000000000000');
    var bits = [];
    for (var i = 0; i < hash.length; i++) {
      var v = parseInt(hash.charAt(i), 16);
      if (isNaN(v)) v = hash.charCodeAt(i) & 15;
      bits.push(v & 1, (v >> 1) & 1, (v >> 2) & 1, (v >> 3) & 1);
    }
    var hue = (parseInt(hash.slice(0, 3), 16) || 0) % 360;
    var fill = 'hsl(' + hue + ',55%,45%)';
    var svg = LH.svg('svg', { viewBox: '0 0 5 5', width: size, height: size, class: 'avatar', role: 'img',
      'aria-label': name ? 'Avatar of ' + name : 'Avatar', focusable: 'false' });
    svg.appendChild(LH.svg('rect', { width: 5, height: 5, fill: 'hsl(' + hue + ',30%,90%)' }));
    var k = 0;
    for (var x = 0; x < 3; x++) {
      for (var y = 0; y < 5; y++) {
        if (bits[(x * 5 + y + 16) % bits.length]) {
          svg.appendChild(LH.svg('rect', { x: x, y: y, width: 1, height: 1, fill: fill }));
          if (x < 2) svg.appendChild(LH.svg('rect', { x: 4 - x, y: y, width: 1, height: 1, fill: fill }));
          k++;
        }
      }
    }
    if (!k) svg.appendChild(LH.svg('rect', { x: 2, y: 2, width: 1, height: 1, fill: fill }));
    return svg;
  };

  /* ---------------------------------------------------------------- icons */
  var ICONS = {
    'pr-open': ['circle:3.5,3.5,1.8', 'circle:3.5,12.5,1.8', 'circle:12.5,12.5,1.8', 'path:M3.5 5.3v5.4M12.5 10.7V6.5A2.5 2.5 0 0 0 10 4H8m0 0 1.8-1.8M8 4l1.8 1.8'],
    'pr-merged': ['circle:3.5,3.5,1.8', 'circle:3.5,12.5,1.8', 'circle:12.5,8,1.8', 'path:M3.5 5.3v5.4M3.5 5.5C3.5 8 6 8 10.7 8'],
    'pr-closed': ['circle:3.5,3.5,1.8', 'circle:3.5,12.5,1.8', 'path:M3.5 5.3v5.4M10 3l4 4M14 3l-4 4M12 9v3.5'],
    'issue-open': ['circle:8,8,6', 'circle:8,8,1.4'],
    'issue-closed': ['circle:8,8,6', 'path:M5.3 8.2l1.9 1.9 3.5-3.7'],
    comment: ['path:M2 3h12v8H8l-3 3v-3H2z'],
    clock: ['circle:8,8,6', 'path:M8 4.5V8l2.5 1.5'],
    commit: ['circle:8,8,2.6', 'path:M1 8h4.4M10.6 8H15'],
    list: ['path:M3 4h10M3 8h10M3 12h10'],
    sun: ['circle:8,8,3', 'path:M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3'],
    moon: ['path:M13.5 9.5A5.8 5.8 0 0 1 6.5 2.5a5.8 5.8 0 1 0 7 7z'],
    check: ['path:M3 8.5l3.2 3.2L13 4.5'],
    x: ['path:M4 4l8 8M12 4l-8 8'],
    external: ['path:M9 2h5v5M14 2 7 9M12 10v4H2V4h4'],
    chevron: ['path:M4 6l4 4 4-4'],
    folder: ['path:M1.5 3.5h4.5l1.5 2h7v7.5h-13z'],
    search: ['circle:7,7,4.5', 'path:M10.5 10.5 14 14']
  };
  LH.icon = function (name, cls) {
    var spec = ICONS[name] || [];
    var svg = LH.svg('svg', { viewBox: '0 0 16 16', width: 16, height: 16, class: 'icon ' + (cls || ''), 'aria-hidden': 'true',
      focusable: 'false', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    spec.forEach(function (s) {
      var i = s.indexOf(':'), kind = s.slice(0, i), arg = s.slice(i + 1);
      if (kind === 'circle') {
        var c = arg.split(',');
        svg.appendChild(LH.svg('circle', { cx: c[0], cy: c[1], r: c[2] }));
      } else {
        svg.appendChild(LH.svg('path', { d: arg }));
      }
    });
    return svg;
  };

  /* ---------------------------------------------------------------- labels */
  LH.LABELS = {
    open: { text: 'Open', hint: 'No review replies yet' },
    review: { text: 'In review', hint: 'Has replies from others' },
    reviewed: { text: 'Reviewed', hint: 'A reply carries a Reviewed-by trailer' },
    acked: { text: 'Acked', hint: 'A reply carries an Acked-by trailer' },
    tested: { text: 'Tested', hint: 'A reply carries a Tested-by trailer' },
    changes: { text: 'Changes requested', hint: 'Review comments or a NAK, and no newer version' },
    superseded: { text: 'Superseded', hint: 'A newer version of this series exists' },
    merged: { text: 'Merged', hint: 'A reply says it was applied, queued or pulled' },
    stale: { text: 'Stale', hint: 'No activity for more than 60 days' }
  };
  LH.pill = function (label) {
    var d = LH.LABELS[label] || { text: label, hint: '' };
    return LH.h('span', { class: 'pill pill-' + label, title: d.hint + ' (heuristic)' }, d.text);
  };
  LH.stateOf = function (label) {
    return label === 'merged' ? 'merged' : (label === 'superseded' || label === 'stale') ? 'closed' : 'open';
  };
  LH.stateIcon = function (kind, label) {
    var s = LH.stateOf(label);
    if (kind === 'issue') return LH.icon(s === 'open' ? 'issue-open' : 'issue-closed', 'state-' + s);
    return LH.icon(s === 'open' ? 'pr-open' : s === 'merged' ? 'pr-merged' : 'pr-closed', 'state-' + s);
  };
  LH.chip = function (text, href, title) {
    return href ? LH.h('a', { class: 'chip', href: href, title: title || null }, text)
      : LH.h('span', { class: 'chip', title: title || null }, text);
  };

  LH.sparkline = function (values, w, hgt) {
    w = w || 120; hgt = hgt || 28;
    var max = Math.max.apply(null, values.concat([1]));
    var step = w / Math.max(1, values.length - 1);
    var pts = values.map(function (v, i) { return (i * step).toFixed(1) + ',' + (hgt - 2 - (v / max) * (hgt - 4)).toFixed(1); });
    var total = values.reduce(function (a, b) { return a + b; }, 0);
    var svg = LH.svg('svg', { viewBox: '0 0 ' + w + ' ' + hgt, width: w, height: hgt, class: 'spark', role: 'img',
      'aria-label': 'Messages per day over the last ' + values.length + ' days, ' + total + ' in total' });
    svg.appendChild(LH.svg('polygon', { points: '0,' + hgt + ' ' + pts.join(' ') + ' ' + w + ',' + hgt, class: 'spark-area' }));
    svg.appendChild(LH.svg('polyline', { points: pts.join(' '), class: 'spark-line', fill: 'none' }));
    return svg;
  };

  /* Five-box +/- proportion bar like GitHub's diffstat. */
  LH.diffBars = function (a, d) {
    var total = a + d, boxes = 5, na = total ? Math.round(a / total * boxes) : 0, nd = total ? boxes - na : 0;
    if (a && !na) { na = 1; nd = boxes - 1; }
    if (d && !nd) { nd = 1; na = boxes - 1; }
    var span = LH.h('span', { class: 'diff-bars', 'aria-hidden': 'true' });
    for (var i = 0; i < boxes; i++) span.appendChild(LH.h('i', { class: i < na ? 'b-add' : i < na + nd ? 'b-del' : 'b-none' }));
    return span;
  };

  LH.errorBox = function (err, what) {
    return LH.h('div', { class: 'card empty', role: 'alert' },
      LH.h('h2', null, 'Could not load ' + (what || 'this page')),
      LH.h('p', { class: 'muted' }, String(err && err.message ? err.message : err)),
      LH.h('p', null, LH.h('a', { href: '#/' }, 'Back to all lists')));
  };
  LH.empty = function (title, text) {
    return LH.h('div', { class: 'card empty' }, LH.h('h3', null, title), text ? LH.h('p', { class: 'muted' }, text) : null);
  };

  LH.color = function (c) { return /^#[0-9a-fA-F]{6}$/.test(c) ? c : '#8b949e'; };

  LH.setTitle = function (t) { document.title = t ? t + ' - LoreHub' : 'LoreHub'; };
})();
