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
- In the real acceptance series the cover letter ("IPE: add a dedicated policy keyring") is titled differently from
  patch 1 ("ipe: add the .ipe policy keyring"): versions link on the cover letter title, the pull request is named
  after it, and the search index also holds the patch subjects so both names find it.

## Follow-up 3: complete conversations, only series with "Files changed", 180 days, 1000 diffs per list

- [x] D1 `MAX_DIFF_MSGS` 1000, kept by whole series, newest first (`retention.py`)
- [x] D2 horizon 180 days everywhere (code, CLI, workflow, README, About, docs); retention follows the horizon
- [x] D3 build time invariant: only series with a stored diff are shown, in no list, count, feed, profile or search entry
      (`tests/invariants.py`); partial diffs get a notice; git pull requests are discussions
- [x] D4 complete bodies (128 KB cap, quotes, trailers), thread tree (`pi`, "in reply to"), replies under their patch, collapsed
      quotes, participants, message count; gap detection and the resumable thread completion queue (`complete.py`, `t.atom`)
- [x] D5 sizes measured on real data (`scripts/estimate_size.py`), thread shards gzip, 100 discussions per list, eviction by whole
      series, site cap 700 MB, data budget 650 MB, hourly workflow with incremental pushes and a daily squash
- [x] D6 state epoch migration (old state kept, backfill cursors restarted, converges), per list walk stops when the newest 1000
      patches are stored, page cap 60
- [x] D7 polite as before: 2 concurrent requests, 0.5 s apart, Retry-After; 113 live requests in this task
- [x] screenshots of the PR page (Conversation, Commits, Files changed; light and dark; desktop and phone) from the ipe fixture

## Follow-up 4: Unified / Split diff layout

- [x] E1 pure pairing module `site/assets/diffpair.js` (rows of `{left, right}`, no newline marker, intra-line marks), node tests
      `tests/js/test_diffpair.js` (equal, more removed, more added, added only, removed only, interleaved, context only, marker,
      empty hunk, hostile line, surrogate pairs)
- [x] E2 segmented Unified | Split control (aria-pressed buttons), `localStorage` key `lkmlhub-diff-layout`, `?layout=` in the hash
      route (wins over storage, never written to it), default Unified
- [x] E3 split table: two gutters, hatched placeholders, spanning hunk headers, theme colours with contrast checks, horizontal
      scroll inside the card, 900 px breakpoint with the switch disabled below it
- [x] E4 changed characters highlight in both layouts (prefix and suffix trimmed, skipped over 500 characters or with nothing in common)
- [x] E5 large files: 2 000 rows at a time with "Show N more lines" in both layouts
- [x] E6 headless Chrome tests (`tests/test_diff_layout_browser.py`: ipe series at 1280, 900, 899 and 390 px, a 5 000 line file with
      3 000 character lines), XSS test extended with `layout=split` routes
- [x] E7 screenshots of Files changed: unified and split, light and dark, desktop; unified on a phone; README and DESIGN updated

## Findings along the way (follow-up 3)

- A new orphan commit per run re-sends the whole data branch (git cannot use the server's copy of unrelated history). The earlier claim
  that day segmented files made the squashed push small was wrong; measured and fixed with incremental commits and a daily squash.
- The search walk is almost complete for tagged mail: 0 of 83 fetched threads (6 lists) lacked a message inside the walked window.
  Flagged gaps were window edges or patches sent to other lists only.
- v2 of the ipe series really has 6 replies; the 26 replies belong to v1, a separate thread on lore (32 across versions).
- A reply whose parent is missing is not part of the series' thread in the model (it forms an entity of its own), so such gaps
  are found by matching its subject to the series' patches.

## Follow-up 5: review by e-mail

- [x] F1 sender addresses kept apart: `state/addrs/<list>.json.gz` (merge on ingest, pruned, atomic, deterministic), posting address from
      the `new.atom` feed id, reply shards `data/reply/<list>/<n>.json.gz` for published threads, address scan tests allow exactly these
      two kinds of files and assert both ways; no state epoch bump
- [x] F2 opt out: `--no-reply-addresses` / `LOREHUB_REPLY_ADDRESSES=off` (no collecting, no publishing, stored addresses deleted)
- [x] F3 thread completion queue also fetches the thread of displayed open series whose people have no address (cap 100 per run, once per
      series, skipped when everybody became known meanwhile, cross-posts once)
- [x] F4 `reviewmail.js`: pure e-mail generation (quoting, regions, `[...]`, wrapping, tags, headers, RFC 2047, References, Cc, mailto,
      git send-email, .eml), golden tests from the ipe v2 series (patch 1, patch 3, cover), mapping, hostile input
- [x] F5 `reviewstore.js` (drafts per list, number and version, identity, memory fallback), node tests
- [x] F6 line comments in both layouts (+ on hover and focus, arrow keys, edit, delete, Pending/Prepared, badge, commit message lines),
      review dialog (focus trap, aria-modal, Esc, tags and scope), result panels, Reply composer in the Conversation tab
- [x] F7 headless Chrome through the DevTools protocol with real input (`tests/js/cdp.js`, `review_e2e.js`): both layouts, reload,
      prepare, golden comparison, download, copy, discard, identity, reply, 390 px, no localStorage, hostile input
- [x] F8 README "Reviewing by email", DESIGN, About page, screenshots (`scripts/review_shots.js`)

## Findings along the way (follow-up 5)

- Only `new.atom` carries the posting address of the list (`<id>mailto:...`); thread and search feeds have a `urn:uuid` id.
- Headless Chrome reports `hover: none` and no pointer, so the hover rule of the + must not depend on `(hover: none)` alone; it uses
  `(hover: none) and (pointer: coarse)`.
- Mail sent through the b4 web relay comes from a `devnull+...@kernel.org` address that discards replies; the real author is the
  Signed-off-by with the same name.
- The size of a reply shard is about 57 bytes per message after gzip.
