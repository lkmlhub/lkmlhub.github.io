# LoreHub

A GitHub-style view of every mailing list archived on [lore.kernel.org](https://lore.kernel.org/).

* a mailing list is a **repository**
* a patch series is a **pull request**, successive versions (v1, v2, v3) are one PR with a version history
* a non-patch thread is a **discussion** (issue)
* `Reviewed-by`, `Acked-by`, `Tested-by` and NAKs are **review states**
* "Applied, thanks" replies and `[GIT PULL]` threads are **merges**
* a pull request has the tabs **Conversation**, **Commits** and **Files changed**: the unified diff of the
  series with a file tree, per-file cards and "Viewed" boxes, one patch at a time or all combined
* **search** (`#/search?q=ipe keyring`) over titles, patch subjects, authors and lists, from a static index
* a resumable **backfill** walks every list back 90 days, so older series are found too

It is a fully static site: a Python generator (standard library only) writes JSON, and a small
vanilla JS front end renders it. No backend, no database, no tracker, no CDN. Deploys to GitHub Pages
under any sub-path (all links are relative, routes are hash based).

![Home page](docs/screenshots/home-desktop.png)

| List page | Patch series page |
| --- | --- |
| ![List page](docs/screenshots/list-desktop.png) | ![Patch series page](docs/screenshots/pr-desktop.png) |

| Files changed (light) | Files changed (dark) |
| --- | --- |
| ![Files changed](docs/screenshots/files-desktop.png) | ![Files changed, dark](docs/screenshots/files-dark.png) |

More screenshots (mobile, commits tab with diffstat, search, home page with the backfill note, contributor page,
served under a sub-path) are in [docs/screenshots](docs/screenshots). They were taken with headless Chrome from
real lore data: `lkml`, `netdev`, `linux-mm`, `bpf`, `linux-fsdevel`, `dri-devel`, `linux-riscv`, `stable`, and the
series "ipe: add the .ipe policy keyring" (RFC v1 2026-10-19, v2 2026-10-29) from linux-security-module and four
lists it was cross-posted to.

## Run it locally

```sh
python3 -m lorehub all --lists lkml,netdev --out public --state state    # crawl, then build
python3 -m http.server -d public 8000                                     # open http://localhost:8000/
```

`all` is `crawl` followed by `build`. `build` alone rebuilds the site from an existing `state/` directory
without touching the network. Requires Python 3.10 or newer, nothing to install.

Useful options:

| Command | Option | Meaning |
| --- | --- | --- |
| crawl, all | `--lists a,b` | only these lists (default: every list in the manifest) |
| crawl, all | `--budget-min 25` | wall clock budget; unfinished lists are done by the next run |
| crawl, all | `--deep-budget N` | max search requests per run to sweep gaps on busy lists (default 12, 0 = off) |
| crawl, all | `--backfill-days 90` | history horizon of the backfill in days, 0 turns it off |
| crawl, all | `--backfill-min 12` | backfill time budget per run (taken from `--budget-min`, the crawl has priority) |
| crawl, all | `--backfill-requests 900`, `--backfill-total 4000` | request caps per run and over all runs |
| crawl, all | `--backfill-list-pages 25` | pages of 200 entries per list, busy lists stop there |
| crawl, all | `--from-cache DIR`, `--save-raw DIR` | replay or record raw HTTP responses, for offline development |
| build, all | `--out public` | output directory |
| build, all | `--base /lorehub/` | base path, used only by `404.html` |
| build, all | `--max-site-mb 300` | site size cap |
| build, all | `--diff-budget-mb 60` | published diff budget (the build lowers it if the site is over its cap) |
| backfill | `--lists a,b --backfill-days 30` | run only the backfill, for chosen lists |

## How the data is gathered

* The list of lists is `https://lore.kernel.org/manifest.js.gz`. Each list has one entry per
  public-inbox epoch; the newest `modified` time says when the list last changed.
* Per list activity comes from `https://lore.kernel.org/<list>/new.atom`. The feed carries, per message,
  author, subject, time, message-id (inside the link), the direct parent (`thr:in-reply-to`) and the
  body. **It holds only the 25 newest messages**, so LoreHub accumulates history across runs and keeps
  120 days (see Limitations for the caps).
* A busy list can produce more than 25 messages between two runs (`lkml` peaks at 25 per 3.5 minutes).
  When a gap is detected the crawler sweeps it with the same host's search Atom, for patch related
  messages only (200 per request, about 1.2 MB), at most 12 requests per run (`--deep-budget`, repository
  variable `LOREHUB_DEEP_BUDGET`).
* **Backfill.** Every run, after the normal crawl, continues a resumable walk back in time through the search
  Atom of each list: `(dt:<from>..<to>) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)`, 200 entries per
  request, newest first, the next request starting where the last one ended (a per-list cursor in
  `state/meta.json`). The horizon is 90 days (`LOREHUB_BACKFILL_DAYS`), small lists go first, at most 12 minutes
  and 900 requests per run and 4 000 in total, each list at most 25 pages (5 000 entries). Full 90 days for all
  359 lists is about 3 300 requests (4 GB raw, 0.9 GB gzip), so a fresh deploy fills in over about five runs; the
  home page shows per list how far its history reaches. Replies keep the tag in their subject, so review threads
  come with the series; a reply whose subject was edited and lost the tag is not found. Details and the request
  math (alternatives: 45 days 2 550 requests, 30 days 2 050) are in [docs/DESIGN.md](docs/DESIGN.md).
* **Diffs.** The feed body carries the complete patch (checked on 1 469 real patches, the largest 1 MB), so no extra
  request is made. The diff is parsed into files (renames, new, deleted, binary, mode changes), cut at 120 KB per
  patch, stripped of e-mail addresses and kept for the newest 300 patches of each list on the data branch.
* Polite by construction: User-Agent `lorehub-bot/0.1 (+https://github.com/lkmlhub/lkmlhub.github.io)`, at most
  2 concurrent requests, at least 0.5 s between request starts, `Retry-After` honoured, exponential
  backoff on 429/503/5xx, a hard stop if the server asks for a long pause, validators
  (`If-None-Match`, `If-Modified-Since`) sent when known, and lists whose manifest `modified` time did
  not change are skipped. lore currently sends no validators, so the manifest time does the work.
* Public-inbox git repositories are never cloned.
* The crawl is resumable: lists are processed least recently fetched first inside a time budget, and a
  failed or partial run never shrinks or corrupts the stored state. A list that could not be refreshed
  keeps its previous state and is flagged stale on the site. One pass over all 359 lists took about 3
  minutes and 60 MB.
* E-mail addresses are never stored or published. Authors are a salted, truncated SHA-256 of the
  address (12 hex characters, used for avatars and contributor pages); bodies are scrubbed; message-ids
  are stored percent-encoded.

## Heuristics

Everything below is guessed from reply text and can be wrong. The same table is on the site's About page.

| Label | Rule |
| --- | --- |
| Merged | a reply from someone other than the author says applied, queued, merged or pulled in its first lines (not a question, not negated), or a `[GIT PULL]` gets such a reply (or a pr-tracker-bot message) |
| Superseded | a newer version exists: same author, same normalised title, higher version, or a RESEND |
| Changes requested | a reply from someone else is a NAK, has a `Nacked-by`, or has inline comments containing requests, and that reviewer has not approved since; only while no newer version exists |
| Reviewed / Acked / Tested | a reply (not quoted) from someone other than the author carries that trailer |
| In review | any reply from someone other than the author |
| Open | no such reply yet |
| Stale | not merged or superseded, no activity for more than 60 days |

Priority: Merged, Superseded, Changes requested, Reviewed, Acked, Tested, In review, Open, then Stale on
top. Tabs show Open, Merged and Closed (superseded or stale) counts per list.

Series are detected from subjects such as `[PATCH v3 2/7]`, `[PATCH net-next v2 0/5]`, `[RFC PATCH]`,
`[RESEND PATCH]`, `[PATCH RFC]`, `[GIT PULL]`, `[PULL]`; `[ANNOUNCE]`, `[BUG]`, `[REGRESSION]` become
discussion labels. A cover letter and its patches form one PR; unthreaded series are joined by author,
version, total and time. A message cross-posted to several lists is one thread (keyed by message-id)
with the other lists shown as chips.

## Deploy to GitHub Pages

1. Push this repository to GitHub (the project page then lives at `https://<user>.github.io/<repo>/`).
2. Settings, Pages, Source: **GitHub Actions**.
3. Settings, Actions, General, Workflow permissions: allow read and write (the workflow also declares
   `contents: write`, `pages: write`, `id-token: write`).
4. Run the `update` workflow once from the Actions tab, or just push to `main`.

`.github/workflows/update.yml` runs every 30 minutes, on manual dispatch and on pushes to `main`. It
checks out `main`, checks out the `data` branch into a subdirectory (creating it on the first run),
runs the tests, crawls for up to 25 minutes, builds the site, force-pushes the data branch as one
squashed commit, uploads the Pages artifact and deploys it. A concurrency group prevents overlapping
runs and the job has a 40 minute timeout. `.github/workflows/ci.yml` runs the tests on pull requests
for Python 3.10, 3.12 and the latest 3.x.

The base path is derived from the repository name (`/<repo>/`, or `/` for a `<user>.github.io`
repository). It only matters for `404.html`, which redirects `/<repo>/lkml/pr/3` to
`/<repo>/#/lkml/pr/3`. Everything else is relative, so moving to a custom domain or another sub-path
needs no code change: pass `--base` and redeploy.

The first run sees the newest 25 messages of each list and starts the backfill, so the site fills in over the
following runs: small lists within the first hours, the busiest ones as far back as their page cap allows.
Optional repository variables: `LOREHUB_BACKFILL_DAYS` (default 90, 0 = off), `LOREHUB_BACKFILL_MINUTES` (12),
`LOREHUB_BACKFILL_TOTAL` (4000), `LOREHUB_DEEP_BUDGET` (12).

**robots.txt:** `https://lore.kernel.org/robots.txt` currently disallows all crawlers (`User-agent: *`,
`Disallow: /`). LoreHub only fetches the manifest and the Atom feeds, which the service does serve, with
an identifying User-Agent and a low request rate, but it does not read or obey robots.txt. Check with
the kernel.org admins that this use is acceptable before publishing, and see the cadence notes in
docs/DESIGN.md. The backfill makes the load heavier than the first version: about 3 300 search requests
(about 0.9 GB over the wire) spread over the first five or so runs, then only the sweep of busy lists
(at most 12 requests per run). To stay with the light crawl, set `LOREHUB_BACKFILL_DAYS=0` and
`LOREHUB_DEEP_BUDGET=0`.

## Tests

```sh
python3 -m unittest            # about 200 tests, 15 to 60 s (headless Chrome tests included when a browser is installed)
for f in site/assets/*.js; do node --check "$f"; done
```

Covered: diff parsing (renames, binary files, `\ No newline`, zero-context hunks, combined diffs, quoted diffs are
not patches), diff caps and truncation flags, the diff state and the fair diff budget, the backfill cursor (fake clock
and fake transport: resume, budgets, caps, failures, order), a real-data acceptance test on the ipe series, the
search index and the pure query function (node), headless Chrome runs of the Files changed tab (Viewed, deep links,
patch selector, no overflow on a phone) and hostile diffs, subject parsing, trailers, status heuristics, version linking, threading, cross-post
de-duplication, manifest and feed parsing, the polite fetcher (rate limit, Retry-After, backoff,
concurrency), state merging and pruning, failed crawls leaving state untouched, a smoke test that builds
the site from saved samples, no e-mail addresses in output, escaping of hostile mail text (including a
headless Chrome run when a browser is installed), a lint against HTML injection sinks and third-party
origins, and a WCAG contrast check of every colour pair in both themes.

## Limitations

* **Statuses are heuristics.** People review off-list, apply patches silently, or phrase things in ways
  the rules do not see. Treat labels as hints, not facts.
* **History is bounded.** `new.atom` carries 25 messages; the backfill adds patch related mail back to 90 days
  (see above) but not threads without a patch tag, not replies whose subject lost the tag, and for the busiest
  lists (`lkml`, `netdev`, ...) only the newest 5 000 entries (a few days). Replies to a patch from before
  the horizon appear as "partial" entries. Every list card says how far its history reaches, every search page
  links to the same query on lore.kernel.org.
* **Diffs are kept for the newest 300 patches per list** and shown within a 60 MB site budget (open series first,
  fair across lists), at most 120 KB per patch and 600 KB per series. Older or evicted series say so and link to
  lore. Inline review comments are not attached to hunks (the Conversation tab shows the replies).
* **The 120 day window is a ceiling, not a promise.** To keep the data branch near 230 MB and the site
  near 280 MB, each list keeps at most 10 000 messages and publishes its newest 300 threads, and only the
  newest 300 messages per list keep a body. Quiet lists get the full 120 days, `lkml` a few days. Backfilled old
  messages of busy lists may show "the text is no longer kept" (the diff is separate).
* **Bodies come from the feed only**, trimmed (6 KB, diffs removed, long quotes collapsed) and kept for
  60 days. For the full message, follow the "View on lore" links.
* Subsystem colours are guessed from list names. Open counts include threads whose first message is
  outside the window.
* The About page, status names and tabs are in English only.

## Layout

```
lorehub/      generator: manifest, fetch, feed, parse, diff, diffshards, model, state, crawl, backfill, search, build, groups, CLI
site/         static front end (index.html, assets/*.js, style.css)
tests/        unittest suite and sanitised fixtures
scripts/      estimate_size.py
docs/         DESIGN.md, PROGRESS.md, screenshots
.github/      update.yml (schedule, crawl, deploy) and ci.yml (tests)
```

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and data model.

## Credits

All mail belongs to its authors and is served by lore.kernel.org, powered by
[public-inbox](https://public-inbox.org/). Related tools with different jobs:
[patchwork](https://patchwork.kernel.org/) (patch state for some lists), [b4](https://b4.docs.kernel.org/)
and [korgalore](https://korg.docs.kernel.org/) (command line tools), and plain archives such as lkml.org
and spinics. LoreHub is not affiliated with any of them.

## Licence

MIT, see [LICENSE](LICENSE).
