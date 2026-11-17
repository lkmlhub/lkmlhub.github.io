# LkmlHub

A GitHub-style view of every mailing list archived on [lore.kernel.org](https://lore.kernel.org/).

* a mailing list is a **repository**
* a patch series is a **pull request**, successive versions (v1, v2, v3) are one PR with a version history
* a non-patch thread is a **discussion** (issue)
* `Reviewed-by`, `Acked-by`, `Tested-by` and NAKs are **review states**
* "Applied, thanks" replies and `[GIT PULL]` threads are **merges**
* a pull request has the tabs **Conversation**, **Commits** and **Files changed**: the diff of the
  series (**Unified** or **Split**, see below) with a file tree, per-file cards and "Viewed" boxes, one patch at a time or all combined. **Only series with
  a stored diff are shown**, so "Files changed" is always there and always real (a series whose diff is not stored is
  not listed at all, in no count, feed, contributor page or search result)
* the **Conversation** is the whole thread: cover letter, every patch and every reply with its complete text (quotes
  collapsed behind a "Show quoted text" toggle, trailers highlighted), nested under the message it answers, replies to a
  patch under that patch, "in reply to" context, message count and participants
* conversations are **completed from lore's thread feeds**: series that look incomplete (a reply without its parent, a
  missing patch 3/7, a missing cover letter) are fetched whole, one request per series, from
  `https://lore.kernel.org/<list>/<message-id>/t.atom`. A series still waiting says "conversation still being completed"
* **review by e-mail**: leave GitHub-style line comments on the diff, pick Approve, Comment or Request changes, and LkmlHub
  prepares the plain text replies a kernel list expects, threaded and ready to send from your own mail client (see
  [Reviewing by email](#reviewing-by-email)). It never sends anything.
* **search** (`#/search?q=ipe keyring`) over titles, patch subjects, authors and lists, from a static index
* a resumable **backfill** walks every list back **180 days**, so older series are found too

It is a fully static site: a Python generator (standard library only) writes JSON, and a small
vanilla JS front end renders it. No backend, no database, no tracker, no CDN. Deploys to GitHub Pages
under any sub-path (all links are relative, routes are hash based).

![Home page](docs/screenshots/home-desktop.png)

The series "ipe: add the .ipe policy keyring" (RFC v1 2026-10-19 with 31 messages, v2 2026-10-29), real data:

| Conversation (31 messages, light) | Conversation (dark) |
| --- | --- |
| ![Conversation](docs/screenshots/pr-conversation-light-desktop.png) | ![Conversation, dark](docs/screenshots/pr-conversation-dark-desktop.png) |

| Commits | Files changed |
| --- | --- |
| ![Commits](docs/screenshots/pr-commits-light-desktop.png) | ![Files changed](docs/screenshots/pr-files-light-desktop.png) |

| Conversation (phone) | Commits (phone, dark) | Files changed (phone, dark) |
| --- | --- | --- |
| ![Conversation phone](docs/screenshots/pr-conversation-light-mobile.png) | ![Commits phone](docs/screenshots/pr-commits-dark-mobile.png) | ![Files phone](docs/screenshots/pr-files-dark-mobile.png) |

More screenshots (light and dark, desktop and mobile, of the three tabs; list page, search, contributor page,
served under a sub-path) are in [docs/screenshots](docs/screenshots). They were taken with headless Chrome from
real lore data: `lkml`, `netdev`, `linux-mm`, `bpf`, `linux-fsdevel`, `dri-devel`, `linux-riscv`, `stable`, and the
ipe series from linux-security-module and four lists it was cross-posted to (the acceptance fixture).

### Files changed: Unified and Split

The toolbar of the Files changed tab has a **Unified | Split** switch like GitHub's. It applies to every file card of the page at once.

* **Unified**: one column, old and new line numbers side by side (the default).
* **Split**: laid out like GitHub's split view. Four columns per row: old number, old code, new number, new code. The two code
  columns are equally wide and share all the width the 50 px number columns leave; numbers are muted and right aligned, with a red
  tint on the number of a removed line and a green tint on an added one. Long lines wrap inside their cell (monospace 12px on 20px
  lines, tab size 4, words are kept whole, a single token longer than the cell breaks anywhere), so there is no horizontal scrollbar.
  The `+` / `-` marker is outside the text flow (absolutely positioned in the cell's left padding), so wrapped lines never run under
  it and are not copied with the code. Both halves of a row are as tall as the taller one, and the shorter side keeps its colour (red,
  green, or the hatched grey of an empty cell) for the full height. Context lines show on both sides; in a change block the removed
  lines go left and the added lines right, paired by position. Hunk headers are a full width bluish row with a small expand icon in
  the gutter, `\ No newline at end of file` stays on the side it belongs to. The changed characters inside a paired removed/added
  line get a stronger background (in both layouts; lines over 500 characters and pairs with nothing in common are left alone).
  Clicking in one half selects the code of that half only (numbers and markers are never selected).
* **Wide page.** The Files changed tab uses a wider container (`min(1600px, 96vw)` instead of 1280 px), the file tree is 240 px
  and starts collapsed when it would leave less than 450 px for each half (a window under about 1340 px); file headers are sticky
  while a long file scrolls.
* The choice is remembered in `localStorage` (`lkmlhub-diff-layout`) for all series. A link can carry it: `...?tab=files&layout=split`
  (the URL wins over the stored choice and does not overwrite it).
* Below **900 px** window width only Unified is shown and the switch is disabled (with a tooltip saying why); the stored choice is
  kept and applies again on a wide screen.
* Wide unified lines scroll inside the file card, never the page (split lines wrap instead). A file renders its first 2 000 rows,
  "Show N more lines" adds the next chunk (both layouts); collapsed files are rendered only when opened.

| Unified | Split |
| --- | --- |
| ![Unified](docs/screenshots/pr-files-light-desktop.png) | ![Split](docs/screenshots/pr-files-split-light-desktop.png) |

## Reviewing by email

The site is static, so it cannot send mail. Instead the Files changed tab is a review tool that prepares the e-mails.

1. Open a series, go to **Files changed**, hover a line (added, removed or context, Unified or Split) and press **+**, or reach
   it with the keyboard (Tab into the table, arrow keys between lines, Enter). Type the comment, **Add review comment**.
   Comments are saved in your browser per list, series number and version, survive a reload, show as **Pending** and are
   counted on the **Review changes** button. A single patch view also lets you comment on lines of the commit message.
2. **Review changes** opens a dialog: your name and address (kept in this browser only, with a Forget me button), an overall
   comment, **Approve** (a Reviewed-by, Acked-by or Tested-by tag, Reviewed-by by default, for the whole series or only for the
   patches you pick), **Comment**, or **Request changes** (no tag, an explicit Nacked-by is offered but never preselected). The
   dialog says: "Nothing is sent. Your drafts and identity stay in this browser."
3. **Prepare e-mails** shows one panel per e-mail: one reply per patch with comments, one reply to the cover letter (overall
   comment and series wide tag). For each: **Download .eml**, **Copy whole text**, **Open in mail app** (a `mailto:` link, only
   when the text is short, about 1800 characters), **Copy git send-email command** and **Open on lore** (the message page with
   the reply instructions). The comments become **Prepared**.
4. The **Reply** button on every message of the Conversation tab opens a composer with the quoted message (attribution line, `> `
   prefix) and the same output panel.

What the e-mail looks like (the ipe series, patch 1 of v2, comments on two lines; `tests/fixtures/review/golden/patch-1.eml`):

```
From: Jane Reviewer <jane@example.test>
To: Manuel Huber via B4 Relay <user1@example.test>
Cc: kexec@lists.example.test, ..., Fan Wu <user20@example.test>
Subject: Re: [PATCH v2 1/3] ipe: add the .ipe policy keyring
In-Reply-To: <user45@example.test>
References: <user44@example.test> <user45@example.test>
Date: Tue, 17 Nov 2026 14:03:09 +0000
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: 8bit
X-Unsent: 1

On Thu, 29 Oct 2026 00:31:21 +0000, Manuel Huber via B4 Relay wrote:
> diff --git a/security/ipe/keyring.c b/security/ipe/keyring.c
> --- /dev/null
> +++ b/security/ipe/keyring.c
> @@ -0,0 +1,30 @@
> +int __init ipe_keyring_init(void)
> +{
> +	ipe_keyring =
> +		keyring_alloc(".ipe", GLOBAL_ROOT_UID, GLOBAL_ROOT_GID,

Why not KEY_ALLOC_BUILT_IN here? The keyring is never freed.

[...]

> +				KEY_USR_SEARCH | KEY_USR_SETATTR,
> ...
```

Kernel conventions followed: plain text, UTF-8, an attribution line, `> ` quoting, your comment unquoted right below the line it
refers to between blank lines, `[...]` between quoted regions that are not adjacent, prose wrapped at 72 columns while quoted
diff lines and code are never reflowed, tags on their own lines after your comment, no signature, `Re:` only once, `In-Reply-To`
and a `References` chain built from the thread, `X-Unsent: 1` so that Outlook and Thunderbird open the `.eml` as a draft.
Names and subjects are sanitised against header injection and non ASCII names are RFC 2047 encoded.

**Limits.**

* Nothing is sent; there is no way to send from the site. The e-mail is yours to check and send.
* **To and Cc come only from people LkmlHub has seen in the thread** (the author of the message you reply to, the other
  participants, the posting address of the lists the series went to). Maintainers who never replied are not added; add
  them yourself (the message page on lore shows the usual reply-all recipients). If an address is not known, To is left empty and
  the panel says so; the e-mail still has everything else.
* **Address coverage grows over time.** Addresses are collected from the feeds since this feature exists; for older series the
  thread completion fetches the thread of displayed open series (at most 100 requests per run) to learn them, so old or closed
  series can miss addresses for a long time.
* `mailto:` links hide above about 1800 characters and cannot set the sender; `git send-email` takes From, Subject and the text
  from the saved file and the recipients from the command line (the command was written from the git documentation, git send-email
  was not run in the development environment; try it with `--dry-run`).
* Comments are on single lines, not ranges; the quoted diff is rebuilt from the stored hunks (no `index` lines, no diffstat).
* Mail that was sent through the b4 web relay (a `devnull+...@kernel.org` sender) is replied to the real author when that person's
  address is known, otherwise To stays empty.

**Privacy: sender addresses.** The Atom feeds carry the sender address of every message. LkmlHub used to hash and drop them. Because
reply-all needs them it now keeps the addresses of the people in a thread, the same ones lore.kernel.org already publishes, in
separate files used only by this feature: `state/addrs/<list>.json.gz` on the data branch and `data/reply/<list>/<n>.json.gz` on
the site (loaded only when you open the review dialog or a Reply button). They never appear on a page, in a message text, a diff,
the thread data, the search index, a contributor page or a list page; the tests scan every other file for addresses. To opt out
run with `--no-reply-addresses` or set the repository variable `LOREHUB_REPLY_ADDRESSES=off`: nothing is collected or published
(stored addresses are deleted on the next run) and the reply e-mails get the list address plus a clear "add Cc" hint. Addresses
can be removed on request. Your own name and address live only in your browser (`localStorage` key `lkmlhub-identity`).

| Line comment (Unified, light) | Line comment (Split, dark) |
| --- | --- |
| ![Comment box, unified](docs/screenshots/review-comment-light-desktop.png) | ![Comment box, split, dark](docs/screenshots/review-comment-split-dark-desktop.png) |

| Review dialog | Prepared e-mails |
| --- | --- |
| ![Review dialog](docs/screenshots/review-dialog-light-desktop.png) | ![Prepared e-mails](docs/screenshots/review-result-light-desktop.png) |

| Reply composer (dark) | Review dialog (phone, dark) | Review dialog (dark) |
| --- | --- | --- |
| ![Reply composer](docs/screenshots/review-reply-dark-desktop.png) | ![Dialog on a phone](docs/screenshots/review-dialog-dark-mobile.png) | ![Review dialog, dark](docs/screenshots/review-dialog-dark-desktop.png) |

The screenshots are made by `node scripts/review_shots.js` from the ipe fixture site (`python3 -m tests.ipe_fixture OUT`), a fresh
Chrome profile per shot.

Split with long, wrapping code lines (light and dark, real `selinux` series, 1400 px window):

| Light | Dark |
| --- | --- |
| ![Split, wrapping lines](docs/screenshots/pr-files-split-wrap-light-desktop.png) | ![Split, wrapping lines, dark](docs/screenshots/pr-files-split-wrap-dark-desktop.png) |

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
| crawl, all | `--backfill-days 180` | history horizon in days, 0 turns the backfill off; the retention follows it (never below 180) |
| crawl, all | `--backfill-min 14` | backfill time budget per run (taken from `--budget-min`, the crawl has priority) |
| crawl, all | `--backfill-requests 900`, `--backfill-total 12000` | request caps per run and over all runs |
| crawl, all | `--backfill-list-pages 60` | pages of 200 entries per list, a safety cap (busy lists stop earlier, see below) |
| crawl, all | `--max-diff-msgs 1000` | patch messages per list that keep a diff, whole series newest first |
| crawl, all | `--complete-requests 300`, `--complete-min 8` | thread requests and minutes per run to complete incomplete series (0 = off) |
| crawl, all, complete | `--complete-addr-requests 100` | of those requests, at most this many only to learn sender addresses of open series for the reply feature (0 = off, variable `LOREHUB_COMPLETE_ADDR_REQUESTS`) |
| all commands | `--no-reply-addresses` | do not collect or publish sender addresses (also `LOREHUB_REPLY_ADDRESSES=off`), see Reviewing by email |
| crawl, all | `--max-state-mb 650` | data branch budget, above it the per list patch limit is lowered (0 = off) |
| complete | `--lists a,b --complete-requests 50` | run only the thread completion queue |
| crawl, all | `--from-cache DIR`, `--save-raw DIR` | replay or record raw HTTP responses, for offline development |
| build, all | `--out public` | output directory |
| build, all | `--base /lkmlhub/` | base path, used only by `404.html` |
| build, all | `--max-site-mb 700` | site size cap: above it whole series are left out, oldest closed ones first |
| backfill | `--lists a,b --backfill-days 30` | run only the backfill, for chosen lists |

## How the data is gathered

* The list of lists is `https://lore.kernel.org/manifest.js.gz`. Each list has one entry per
  public-inbox epoch; the newest `modified` time says when the list last changed.
* Per list activity comes from `https://lore.kernel.org/<list>/new.atom`. The feed carries, per message,
  author, subject, time, message-id (inside the link), the direct parent (`thr:in-reply-to`) and the
  complete text. **It holds only the 25 newest messages**, so LkmlHub accumulates history across runs and keeps
  **180 days** (the retention follows the configured horizon, never less than 180 days).
* A busy list can produce more than 25 messages between two runs (`lkml` peaks at 25 per 3.5 minutes).
  When a gap is detected the crawler sweeps it with the same host's search Atom, for patch related
  messages only (200 per request, about 1.2 MB), at most 12 requests per run (`--deep-budget`, repository
  variable `LOREHUB_DEEP_BUDGET`).
* **Backfill.** Every run, after the normal crawl, continues a resumable walk back in time through the search
  Atom of each list: `(dt:<from>..<to>) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)`, 200 entries per
  request, newest first, the next request starting where the last one ended (a per-list cursor in
  `state/meta.json`). The horizon is **180 days** (`LOREHUB_BACKFILL_DAYS`), small lists go first, at most 14 minutes
  and 900 requests per run and 12 000 in total. A list is finished when it reaches the horizon, or as soon as its newest
  1 000 patch messages with a diff are stored (older series would be dropped anyway), or at the safety cap of 60 pages.
  The full walk of all 359 lists is **about 2 900 requests** (3.4 GB raw, 0.83 GB on the wire), so a fresh deploy fills
  in over about four runs; the home page shows per list how far its history reaches. Replies keep the tag in their
  subject, so review threads come with the series. Request math and alternatives: [docs/DESIGN.md](docs/DESIGN.md).
* **Complete conversations (thread completion).** The search walk misses replies whose subject lost the patch tags
  and messages that fell into a gap. LkmlHub therefore detects incomplete series (a reply whose parent is not stored,
  numbered patches missing such as 3/7 of 1/7, 2/7, 4/7, no cover letter although the patches say N > 1 and one points to
  a missing parent) and fetches the whole thread of each from `https://lore.kernel.org/<list>/<message-id>/t.atom`, one
  request per series, newest and open series first, resumable, with its own budget (300 requests and 8 minutes per run,
  `LOREHUB_COMPLETE_REQUESTS`). A series waiting for that shows "conversation still being completed" and a link to the
  whole thread on lore; "Some messages could not be found on lore" means the thread was fetched and lore has no more.
  Measured: about 3 % of the series (1.6 to 6 % on six lists) are flagged, about 1 500 requests in total for 180 days.
* **Whole messages, whole series.** Every message of a shown series keeps its complete text (up to 128 KB per message, then
  flagged and linked to lore): quotes, trailers and signature included, only e-mail addresses removed. Retention works on
  whole series, newest first: a list keeps the newest series whose patches add up to 1 000 patch messages (first series
  always), with all their messages, bodies and diffs, or drops a series completely.
* **Only series with a diff are shown.** The "Files changed" tab of every pull request must have the real diff, so a
  series for which no diff is stored is not listed anywhere (not in the Patches tab or any count, the home page, the activity
  feed, contributor pages or search). If only some patches of a series have a diff the tab says which ("Diffs for patches 4-7
  are not stored, see lore"). `[GIT PULL]` requests have no diff and are listed as discussions.
* **Diffs.** The feed body carries the complete patch (checked on 1 469 real patches, the largest 1 MB), so no extra
  request is made. The diff is parsed into files (renames, new, deleted, binary, mode changes), cut at 120 KB per
  patch, stripped of e-mail addresses and kept on the data branch for the series of the newest 1 000 patch messages of each list.
* Polite by construction: User-Agent `lkmlhub-bot/0.1 (+https://github.com/lkmlhub/lkmlhub.github.io)`, at most
  2 concurrent requests, at least 0.5 s between request starts, `Retry-After` honoured, exponential
  backoff on 429/503/5xx, a hard stop if the server asks for a long pause, validators
  (`If-None-Match`, `If-Modified-Since`) sent when known, and lists whose manifest `modified` time did
  not change are skipped. lore currently sends no validators, so the manifest time does the work.
* Public-inbox git repositories are never cloned.
* The crawl is resumable: lists are processed least recently fetched first inside a time budget, and a
  failed or partial run never shrinks or corrupts the stored state. A list that could not be refreshed
  keeps its previous state and is flagged stale on the site. One pass over all 359 lists took about 3
  minutes and 60 MB.
* E-mail addresses are never part of the main state or of any page. Authors are a salted, truncated SHA-256 of the
  address (12 hex characters, used for avatars and contributor pages); bodies are scrubbed; message-ids
  are stored percent-encoded. The one exception is the reply feature (see Reviewing by email): the sender addresses of thread
  participants, which lore already publishes, are kept in `state/addrs/` and published in `data/reply/` only, and that can be turned off.

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

`.github/workflows/update.yml` runs in **chained cycles**: GitHub drops scheduled runs on busy days (one scheduled run in
13 hours was observed), so the last job of each run waits until a minimum cycle time (10 minutes, `LOREHUB_MIN_CYCLE_MINUTES`)
has passed since the run started and then dispatches the next run (`workflow_dispatch` from `GITHUB_TOKEN` is allowed to start
workflows). A cron entry every 20 minutes is only a watchdog that restarts the chain if it breaks; pushes to `main` and manual
dispatch also work. After a failed run the wait doubles. Stop the chain with the repository variable `LOREHUB_CHAIN=off` or the
`chain=false` dispatch input. Each run checks out `main`, checks out the `data` branch into a subdirectory (creating it on the
first run), runs the tests (on pushes only), crawls for up to 12 minutes (crawl, then backfill, then thread completion), builds
the site, pushes the data branch, uploads the Pages artifact and deploys it. A concurrency group prevents overlapping runs (one
running and at most one pending) and the build job has a **60 minute** timeout. Public repositories get unlimited free minutes
on standard runners and Actions based Pages deployments are not subject to the 10 builds per hour soft limit, so the cycle is
limited by the crawl politeness, not by CI minutes. **Data branch pushes:** a new orphan commit each run re-sends the whole
branch (git cannot use the server's copy of unrelated history: measured, 100 percent of the objects are sent again), which at
about 540 MB is too much, so a run adds one ordinary commit on top of `data` (only the changed day files are uploaded) and the
branch is squashed into one orphan commit once per UTC day, or earlier when the incremental commits since the last squash add up
to 300 MB (`LOREHUB_SQUASH_MB`; marker `state/.squashed` holds the date and the bytes sent). `.github/workflows/ci.yml`
runs the tests on pull requests for Python 3.10, 3.12 and the latest 3.x.

The base path is derived from the repository name (`/<repo>/`, or `/` for a `<user>.github.io`
repository). It only matters for `404.html`, which redirects `/<repo>/lkml/pr/3` to
`/<repo>/#/lkml/pr/3`. Everything else is relative, so moving to a custom domain or another sub-path
needs no code change: pass `--base` and redeploy.

The first run sees the newest 25 messages of each list and starts the backfill, so the site fills in over the
following runs: small lists within the first hours, the busiest ones last. **An already deployed `data` branch of an older
format is migrated, not discarded:** the state epoch (`state/meta.json`) is older, so every list's backfill cursor restarts from now,
records fetched again replace the old trimmed ones, series that have an old diff stay visible (with a "shortened copy" note on
messages that are not replaced yet), series without a diff are simply not shown until their list is walked again, and the home
page says that history is being rebuilt. The walk converges in about four runs and the thread completion in about five more.
Optional repository variables: `LOREHUB_BACKFILL_DAYS` (default 180, 0 = off), `LOREHUB_BACKFILL_MINUTES` (6), `LOREHUB_BUDGET_MINUTES` (12), `LOREHUB_MIN_CYCLE_MINUTES` (10), `LOREHUB_CHAIN` (on), `LOREHUB_SQUASH_MB` (300),
`LOREHUB_BACKFILL_TOTAL` (12000), `LOREHUB_BACKFILL_PAGES` (60), `LOREHUB_DEEP_BUDGET` (12), `LOREHUB_COMPLETE_REQUESTS` (200),
`LOREHUB_MAX_SITE_MB` (700), `LOREHUB_MAX_STATE_MB` (650), `LOREHUB_COMPLETE_ADDR_REQUESTS` (100), `LOREHUB_REPLY_ADDRESSES` (on, `off` disables collecting and publishing sender addresses).

**robots.txt:** `https://lore.kernel.org/robots.txt` currently disallows all crawlers (`User-agent: *`,
`Disallow: /`). LkmlHub only fetches the manifest and the Atom feeds, which the service does serve, with
an identifying User-Agent and a low request rate, but it does not read or obey robots.txt. Check with
the kernel.org admins that this use is acceptable before publishing, and see the cadence notes in
docs/DESIGN.md. The backfill makes the load heavier than the first version: about 2 900 search requests
(about 0.83 GB over the wire) spread over the first four runs plus about 1 500 thread requests (13 KB each on the wire),
then the sweep of busy lists (at most 12 requests per run) and a handful of thread requests. To stay with the light
crawl, set `LOREHUB_BACKFILL_DAYS=0`, `LOREHUB_COMPLETE_REQUESTS=0` and `LOREHUB_DEEP_BUDGET=0`.

## Tests

```sh
python3 -m unittest            # about 330 tests, 30 to 90 s (headless Chrome tests included when a browser and node are installed)
for f in site/assets/*.js; do node --check "$f"; done
```

Covered: diff parsing (renames, binary files, `\ No newline`, zero-context hunks, combined diffs, quoted diffs are
not patches), diff caps and truncation flags, complete bodies (quotes, trailers, the 128 KB cap and its flag), retention by whole
series (newest first, never partial, the 1000 patch limit, age follows the horizon, the state budget), the build time invariants
(every PR has a diff shard with a file, all counts equal the shown rows, nothing hidden leaks into the feed, contributors or
search, partial diffs, versions, size eviction by whole series), gap detection, the thread atom (a real `t.atom` fixture), the
completion queue (fake transport and fake clock: order, budgets, resume, failures), the state epoch migration (old state in, new behaviour
out, a partly migrated state builds a valid site), the backfill cursor (fake clock and fake transport: resume, budgets,
caps, failures, order), a real-data acceptance test on the ipe series (the reply counts equal the real thread: v1 26, v2 6;
Files changed with diffs for all patches of both versions), the size estimate script, the search index and the pure query function
(node), headless Chrome runs of the conversation (nesting, collapsed quotes, no overlap, no overflow at 1280 and 390 px) and of
the Files changed tab (unified and split layout, the stored preference and the URL parameter, the phone fallback, a 5 000 line file, and the split geometry measured from the DOM: equal code columns, equal row heights, marker outside the text flow, wrapped lines, tab size, one very long token, wide container, tree that leaves room, sticky headers, one sided selection), the pure pairing function of the split layout (node), hostile mail and diffs, subject parsing, trailers, status heuristics, version linking, threading, cross-post
de-duplication, manifest and feed parsing, the polite fetcher (rate limit, Retry-After, backoff,
concurrency), state merging, failed crawls leaving state untouched, a smoke test that builds
the site from saved samples, no e-mail addresses in state, bodies, diffs, thread data or output except in the reply shards and the
address map (gzip files are unpacked for the scan, asserted both ways), escaping of hostile mail text, a lint against HTML injection
sinks and third-party origins, and a WCAG contrast check of every colour pair in both themes. The review feature has golden tests
of the e-mails (node), a store test, pipeline tests (address map, reply shards, the address queue with a fake transport and clock,
the opt out) and headless Chrome runs with real mouse and keyboard events through the DevTools protocol (`tests/js/cdp.js`):
comments in both layouts, the dialog, the prepared e-mails against the golden files, download, reload, discard, the reply composer,
a 390 px phone, a browser without localStorage and hostile input.

## Limitations

* **Statuses are heuristics.** People review off-list, apply patches silently, or phrase things in ways
  the rules do not see. Treat labels as hints, not facts.
* **History is bounded.** `new.atom` carries 25 messages; the backfill adds patch related mail back to 180 days
  (see above) but not threads without a patch tag, and not replies whose subject lost the tag unless the thread
  completion fetches them (it does for series that look incomplete; a series that looks complete but misses an
  edited-subject reply is not noticed: 0 of 83 sampled threads, but it is not impossible). Completeness is per list archive.
  The busiest lists (`lkml`, `netdev`, ...) keep only their newest 1 000 patch messages, a day or two for `lkml`. Every list
  card says how far its history reaches, every search page links to the same query on lore.kernel.org.
* **Reviewing by email has its own limits**, listed in [Reviewing by email](#reviewing-by-email) (To and Cc only from people seen, address
  coverage grows over time, nothing is sent).
* **Series without a stored diff are not shown.** That includes old series of busy lists, series evicted when the site would
  pass 700 MB (oldest closed ones first, whole series), series whose patches carry no parseable diff, and the series of a list
  that the walk has not reached yet after an update of the data format. Diffs are at most 120 KB per patch and 600 KB per series
  (cut ones say so). Inline review comments in the replies are not attached to hunks (the Conversation tab shows the replies); your own review comments are made in the Files changed tab.
* **Limits.** Per list: the newest series with up to 1 000 patch messages with a diff (all their messages and bodies), 3 000 other
  messages, 100 published discussions. Expected size 540 MB data branch and 650 MB site (low 250 and 260, high 960 and 1170, where the
  caps (650 MB state budget, 700 MB site) trim whole series). Details in [docs/DESIGN.md](docs/DESIGN.md).
* **Messages** are complete up to 128 KB (longer ones are cut and say so). A message of an older format that has not been
  replaced yet is a shortened copy and says so. A reply sent to another list only is not in this list's thread.
* Subsystem colours are guessed from list names. Open counts include threads whose first message is
  outside the window.
* The About page, status names and tabs are in English only.

## Layout

```
lorehub/      generator: manifest, fetch, feed, parse, diff, diffshards, model, state, retention, migrate, crawl, backfill, complete, reply, search, build, groups, CLI
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
and spinics. LkmlHub is not affiliated with any of them.

## Licence

MIT, see [LICENSE](LICENSE).
