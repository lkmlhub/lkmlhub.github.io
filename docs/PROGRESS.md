# Progress

- [x] M0 design, licence, skeleton
- [x] M1 manifest, feed parser, subject/trailer/status parsing, polite fetcher, resumable crawler, state
- [x] M2 model (threads, revisions, versions, status), build pipeline, JSON output
- [x] M3 frontend (home, list, thread, contributor, about, dark mode, mobile)
- [x] M4 tests: 100+ unit and smoke tests, XSS test in headless Chrome, contrast and lint checks
- [x] M5 GitHub Actions workflows (YAML parsed, steps rehearsed against a local bare repo, not run on GitHub)
- [x] M6 README, screenshots, size estimate, final report

## Findings along the way

- `new.atom` holds 25 messages, not ~200. The search Atom (`?q=dt:..&x=A`) returns 200 per page and is
  an optional, budgeted gap fill (`--deep-budget`, off by default).
- No `ETag` or `Last-Modified` from lore, so the manifest `modified` time is the skip signal.
- Message-ids are stored percent-encoded so no address-like text lands in the data or the site.
- `robots.txt` on lore disallows all crawlers. Needs an owner decision, see the report.
- A per-list state file would re-upload about 100 MB per run; state is therefore split per UTC day.
- Whole-lore volume (about 24 000 messages per day, estimated) does not fit 120 days under 300 MB, hence the
  per-list caps in `docs/DESIGN.md`.
