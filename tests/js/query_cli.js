// usage: node query_cli.js SITE_DIR QUERY   (reads the built index from disk, prints matching rows as JSON)
// usage: node query_cli.js --tokenize         (reads a JSON array of strings from stdin, prints their tokens)
const fs = require('fs');
const path = require('path');
const S = require('../../site/assets/searchcore.js');

if (process.argv[2] === '--tokenize') {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify(input.map((s) => S.tokenize(s))));
  process.exit(0);
}
const dir = path.join(process.argv[2], 'data', 'search');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(dir, rel), 'utf8'));
const index = read('index.json');
const loads = [];
const loader = (name) => {
  const f = path.join(dir, 't', name + '.json');
  loads.push(name);
  return Promise.resolve(fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, 'utf8')) : null);
};
S.search(process.argv[3], index.split, loader).then((r) => {
  const rows = r.ids.map((id) => S.decodeDoc(read('d/' + Math.floor(id / index.doc_shard) + '.json')[id % index.doc_shard], index));
  let bytes = 0;
  loads.forEach((n) => { const f = path.join(dir, 't', n + '.json'); if (fs.existsSync(f)) bytes += fs.statSync(f).size; });
  process.stdout.write(JSON.stringify({ terms: r.terms, rows: rows, shards: loads, shard_bytes: bytes }));
});
