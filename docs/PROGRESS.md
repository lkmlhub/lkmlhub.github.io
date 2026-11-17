# Progress

- [x] M0 design, licence, skeleton
- [x] M1 manifest, feed parser, subject/trailer/status parsing, polite fetcher, resumable crawler, state
- [x] M2 model (threads, revisions, versions, status), build pipeline, JSON output
- [x] M3 frontend (home, list, thread, contributor, about, dark mode, mobile)
- [x] M4 tests: 100+ unit and smoke tests, XSS test in headless Chrome, contrast and lint checks
- [x] M5 GitHub Actions workflows (YAML parsed, steps rehearsed against a local bare repo, not run on GitHub)
- [x] M6 README, screenshots, size estimate, final report

## Follow-up: Files changed, deeper history, search

- [x] M7 diff extraction (`diff.py`), diff state per list and day, fair diff budget, diff shards in the build
- [x] M8 Files changed tab (tree, cards, Viewed, patch selector, deep links), diffstat in the Commits tab
- [x] M9 resumable backfill (cursor per list, small lists first, budgets, caps), sweep of busy lists, workflow inputs
- [x] M10 acceptance on real data: live backfill of linux-security-module, ipe series fixture (five lists)
- [x] M11 sharded search index, search page, header search box, pure query function tested with node
- [x] M12 home page history notes, About text, size estimates, docs, screenshots, report

## Findings along the way

- `new.atom` holds 25 messages, not ~200. The search Atom (`?q=dt:..&x=A`) returns 200 per page and is
  an optional, budgeted gap fill (`--deep-budget`, off by default).
- No `ETag` or `Last-Modified` from lore, so the manifest `modified` time is the skip signal.
- Message-ids are stored percent-encoded so no address-like text lands in the data or the site.
- `robots.txt` on lore disallows all crawlers. Needs an owner decision, see the report.
- A per-list state file would re-upload about 100 MB per run; state is therefore split per UTC day.
- Whole-lore volume (about 24 000 messages per day, estimated) does not fit 120 days under 300 MB, hence the
  per-list caps in `docs/DESIGN.md`.
- The Atom feed carries the complete diff of a patch (largest measured 1 MB, 0 of 1 469 truncated), so no extra
  per-message requests are needed.
- public-inbox: `dt:` needs 14 digits, the lower bound is exclusive and the upper inclusive, `OR` needs parentheses,
  an empty search answers 404.
- 90 days for all lists is about 3 300 search requests with a 25 page cap per list (9 600 without a cap).
- A series was missing because its cover letter had a different title than patch 1: versions link on the cover
  letter title ("IPE: add a dedicated policy keyring"), which is also what the PR is called.
