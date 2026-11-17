// Loads the browser scripts into a bare context (no DOM) so their pure functions can be tested with node.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const SITE = path.join(__dirname, '..', '..', 'site', 'assets');

module.exports = function load(files) {
  const ctx = { window: {}, console };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  files.forEach((f) => vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f }));
  return ctx.window;
};
