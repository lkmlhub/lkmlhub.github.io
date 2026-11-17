# LoreHub design

LoreHub turns the lore.kernel.org mailing list archives into a GitHub-style
static site. A mailing list is a repository, a patch series is a pull request,
a non-patch thread is a discussion, reviews and acks are review states, and
"Applied, thanks" replies or `[GIT PULL]` replies are merges.

## Findings that shaped the design

These were measured against the live service, not assumed.

* `https://lore.kernel.org/<list>/new.atom` returns only the **25** most recent
  messages (not ~200). `lkml` produces about 25 messages every 3.5 minutes at
  peak, so a 30 minute cadence cannot see everything through `new.atom` alone.
* The same host serves search results as Atom: `/<list>/?q=<query>&x=A`
  returns up to **200** entries per page (about 1.2 MB), newest first, `o=<offset>` pages through them.
  LoreHub uses it for the history backfill and for a small per-run sweep of busy lists (see Backfill).
* Neither `new.atom` nor the manifest sends `ETag` or `Last-Modified`, so
  conditional requests cannot save anything today. The code still sends and
  persists validators in case that changes.
* The manifest lists one entry per public-inbox *epoch* (`/lkml/git/20.git`).
  The list name is the first path segment and its activity time is the max
  `modified` over epochs. `modified` equals the time of the newest message, so
  it is a reliable "has anything changed" signal.
* The feed gives the direct parent only (`thr:in-reply-to`), and the message-id
  is only available inside the `link` href.

* **The feed carries the complete diff.** Measured on 1 469 patch messages from the cached feeds of
  `lkml`, `netdev`, `bpf`, `dri-devel`, `linux-fsdevel`, `linux-mm`, `linux-riscv` and `stable`
  (Atom bodies, `scripts` in the run directory): every hunk is complete, the largest message is 1 059 335
  bytes of diff (116 hunks, `linux-mm`, a KUnit test patch) and still complete, also the big ones from
  `netdev` (210 KB), `dri-devel` (166 KB, 408 hunks) and `bpf` (110 KB). The hunk counts of the `@@` headers
  were checked line by line: 0 messages were cut by the feed. Two messages (of 1 469) have hunks that do
  not add up, both are mail client damage (a hard wrapped `@@` line), not feed truncation. So there is no
  reason for extra per-message requests: the optional capped fetch of `/<list>/<message-id>/raw` that the
  design allowed is **not implemented** (it would be 0 requests by default and would never trigger). The
  Files changed banner "truncated" therefore only ever means LoreHub's own size caps.
* **Search query syntax** (verified live on linux-security-module): `s:` matches subject words, `dt:` needs the
  14 digit form `YYYYMMDDHHMMSS` (the 8 digit form answers 404), the range is exclusive at the lower and
  inclusive at the upper end (a message at time T is found by `dt:<T-1>..<T>` and not by `dt:<T>..<T+1>`),
  and an empty result is HTTP 404. `OR` binds weaker than the implicit `AND`: `s:PATCH OR s:RFC dt:W` is
  `s:PATCH OR (s:RFC AND dt:W)` and returns the newest 200 `PATCH` messages of all time, so the query is
  `(dt:LO..HI) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)`. A 12 hour window returned 148
  messages for the full query, 148 for the date bound alone, 147 for `s:PATCH`: on kernel lists nearly
  everything carries a tag. `s:PATCH` also matches `Re: [PATCH ...]`, so review threads come with the series.
  A reply whose subject was edited and lost the tag is not found (accepted loss), and so is a subject like
  `[PATCHv2]` (one word).
* **`t.atom`** of a message (`/<list>/<message-id>/t.atom`) returns a whole thread in one request. It was used
  once, to capture the acceptance fixture, not by the crawler.

## Pipeline

```
manifest.js.gz --> manifest.py --> lists + modified times
new.atom, search atom sweep and backfill --> feed.py --> message records + parsed diffs (diff.py)
records --> state.py --> state/lists/<list>/<day>.json.gz        (messages, accumulated, pruned)
diffs   --> state.py --> state/diffs/<list>/<day>.json.gz        (newest 300 patch diffs per list)
state --> model.py (threads, revisions, series, status) --> build.py --> public/
public/ = static HTML + JSON + JS + CSS, rendered client side
```

### Crawling (crawl.py, feed.py)

* Fetcher: standard library `urllib`, at most 2 concurrent requests, at least
  0.5 s between request starts per host, `Retry-After` honoured for 429/503,
  exponential backoff with jitter, gzip accepted, fixed User-Agent.
* Lists are processed least-recently-checked first inside a wall clock budget
  (default 25 minutes). Anything not reached is simply done by the next run, so
  the crawl is resumable by construction. Lists whose manifest `modified` is
  unchanged since the last successful check are skipped.
* A failed fetch never touches the stored state. The list is flagged `stale`
  in its metadata and on the site.
* Sweep: if the oldest entry of `new.atom` is newer than the newest message
  already stored, messages were missed (busy lists lose messages between runs). The crawler then asks
  the search Atom for the patch related messages since the newest stored one, busiest lists first, at
  most 12 requests per run and 2 pages per list (`--deep-budget`, `--deep-pages`). Discussions without a
  patch tag on a list that busy are still lost in such a gap.
* Backfill: see below. It runs after the normal crawl, which has priority.

### State (state.py)

One gzip JSON file per list **and UTC day** on the orphan `data` branch:
`state/lists/<list>/<YYYY-MM-DD>.json.gz`. Files are written atomically (temp file, `os.replace`) and
only when their bytes changed; the gzip header is deterministic. Old days therefore never change, so the
single squashed commit pushed each run only uploads today's segments instead of every list (a per-list
file would re-upload roughly 100 MB every 30 minutes). A message record:

```
mid -> {t: unix time, a: author name, h: author hash, p: parent mid or "",
        s: subject, b: trimmed body, f: flags, tr: trailers [[kind, name, hash]],
        ds: [files, added, removed]}
```

Flags (`A` applied/merged phrase, `I` inline review request, `N` NAK) and trailers are computed at ingest
from the full body, so they survive when the body itself is later dropped. Message-ids are stored
percent-encoded (`@` becomes `%40`), which lore accepts, so no address-like text is kept anywhere.

Diffs are not in these records. A patch record has `ds` (files, added, removed) and `dz` (bytes of the
stored diff); `dz` is present exactly when the diff is in `state/diffs`.

Retention: 120 days, at most 10 000 newest messages per list, bodies only for the newest 300 messages and
only within 60 days. These caps exist because the unbounded numbers do not fit (see Sizes). Quiet lists
keep the full 120 days, the busiest lists keep a few days.

`state/meta.json` keeps per list crawl bookkeeping (validators, last fetch and check, last error,
manifest modified), `state/manifest.json` the parsed manifest and `state/numbers.json` the stable `#N`
numbers. The data branch is rewritten as a single orphan commit on every run so history does not grow.

### Model (model.py)

1. Merge all list states into one global message map keyed by message-id. A
   message present in several lists is one message with several `lists`
   (cross-post de-duplication).
2. Components: follow parent links through known messages. A component whose
   top message has an unknown parent is `partial` (older than our window).
3. Patch messages are non-reply messages whose subject carries a PATCH or PULL
   tag. They are grouped into **revisions** by (author, version). Replies are
   attached to the revision of their nearest patch ancestor. Out-of-thread
   series (`git send-email --no-thread`) are merged when author, version, total
   and a 2 hour window match. A component without patch messages is a
   **discussion**; a partial component whose subject is a patch becomes a
   revision with no patches ("ghost").
4. Revisions with the same author and normalised title are chained into a
   **series** ordered by version; only the newest is the headline, older ones
   are `superseded` with a version history.
5. Status labels are computed from the revision's replies (see About page for
   the exact rules): merged > superseded > changes requested > reviewed >
   acked > tested > in review > open, then `stale` after 60 idle days.
6. Numbers: every revision or discussion gets `#N` in its *home list*
   (preferring a list other than lkml when cross-posted). Numbers are persisted
   so URLs are stable, and survive re-rooting when an older message arrives.

### Output (build.py)

```
public/index.html            shell with pre-rendered, escaped list of lists (noscript)
public/404.html              redirects /<base>/x/y to /<base>/#/x/y
public/assets/*              app JS, CSS
public/data/lists.json       all lists, small
public/data/recent.json      global activity feed
public/data/lists/<l>/meta.json                 counts, daily chart, contributors, timeline
public/data/lists/<l>/<pr|issue>-<state>-<k>.json   paged rows (300 per page)
public/data/threads/<l>/<n//32>.json            thread detail, sharded by number
public/data/authors/<xx>.json                   contributor profiles, sharded by hash prefix
```

Deviations from the suggested layout, with reasons: rows are paged per state so
that tab counts and "Open/Merged/Closed" tabs map to files and no file grows
past about 1 MB for lkml; contributor profiles are sharded by hash prefix
instead of one file per author to avoid tens of thousands of tiny files.

Size control: patch bodies are cut at the diff (the commit message and the diffstat numbers are
kept), quoted blocks are collapsed, bodies are capped at 6 KB, only the newest 300 per list keep one, and
only the newest 300 threads per list are published. If the built site still exceeds the cap (default
300 MB) the build halves the body budget and the per-list thread cap and tries again.

### Sizes

Measured on the 8 development lists (3 445 messages, 1 353 threads): rows, list metadata and author
profiles cost about 0.5 KB per thread, thread detail without bodies about 1.1 KB per thread, bodies
about 1.45 KB per message. State costs about 70 to 100 bytes per message (gzip) plus about 440 bytes per
body. One pass over all 359 lists (25 messages each, 3 minutes, 60 MB downloaded, no failures) gives a
per-list message rate. `scripts/estimate_size.py` extrapolates that: with the default caps about
140 MB for the data branch and about 200 MB for the site. Without caps it would be about 0.9 GB and 4 GB,
which is why the caps exist. The diffs and the search index add to this, see the sections below and the
estimate in `scripts/estimate_size.py`.

### Diff storage (diff.py, diffshards.py)

**What is stored.** For a patch message (a non-reply with a `PATCH` tag, not a pull request) the unified
diff is found (after the first `---` line, like `git am`; quoted lines never count), parsed into files and
kept as a small dict: per file `p` path, `o` old path for renames and copies, `k` new/deleted/renamed/copied,
`b` binary, `m` mode change, `s` similarity, `cb` combined diff, `a` and `d` line counts, `x` the hunk text
and `t` when that text is incomplete. Combined (merge) diffs are listed but not shown, binary payloads
(`GIT binary patch`) are skipped, `\ No newline at end of file` lines are kept, hunks are read by their
line counts so the `-- ` mail signature and wrong counts do not leak into the diff.

**Decision: raw hunk text per file, parsed in the browser** (not line arrays). Measured on the same 1 469 real
patches, gzip of each patch alone: raw hunk text in JSON 1 522 bytes on average, per line `[type, text]` arrays
1 554, plain text 1 430; many patches in one gzip (a day file) 1 229 bytes per patch. The first character of a
line is its type, so a line array only adds quotes and brackets. The browser needs about 40 lines of
code to number the lines from the `@@` headers, and the file tree needs no parsing because the file list
(with counts) is stored beside the text. Parsing at crawl time costs 0.2 ms per patch (0.3 s for all 1 469).

**Caps.** 120 KB of hunk text per patch (files after the cap keep their name and counts, flagged `t`;
4 of 1 469 patches were capped), 2 000 characters per line, 600 KB per series when published. Control characters
are removed, e-mail addresses in diff lines, paths and commit messages are replaced (`[email]`, the same
function as for bodies), so MAINTAINERS entries and tags are covered. A mailing list can only be hostile,
so diff text only ever reaches the page through `textContent`.

**Measurements** (1 469 unique patch messages in the cached feeds): mean diff 5 753 bytes, median 1 421,
90th percentile 11 314, maximum 1 059 335. Kept after the 120 KB cap: mean 4 939 bytes (7.3 MB in total).
43 percent of all messages on these lists are patches with a diff. Published JSON per patch about 5.45 KB on average.

**State (data branch).** `state/diffs/<list>/<YYYY-MM-DD>.json.gz`, one deterministic gzip file per list and
day, written atomically, the same layout as the messages so unchanged days produce no git change. Each list
keeps the diffs of its **newest 300 patch messages** (`MAX_DIFF_MSGS`); an older diff is removed from its day file
(and `dz` from its record) when a newer one arrives, empty day files are deleted. A cross-posted patch is
stored once per list that carries it. Cost: 1.2 to 1.5 KB per patch.

**Site.** `data/diffs/<home list>/<n>.json` per pull request (one file per series, so opening the Files tab
loads exactly that series), never in list, thread-summary or author JSON (tests check that no diff text
leaks into them). The build chooses which series get a file: a global budget of **60 MB** (`DIFF_BUDGET`,
`--diff-budget-mb`; the first draft was 80 MB and was lowered because the estimate then sat at 296 MB of
the 300 MB site cap). Selection is fair across lists (water filling: every list gets the same byte quota,
quiet lists keep everything, busy lists share the rest), inside a list open series come first, then merged,
then closed, newest first. When the whole site is over its 300 MB cap the build halves the diff budget together
with the body budget and the per-list thread cap and tries again. A series without a published diff still has
its file count and `+/-` totals (from the patches' own diffstat), and the tab explains why and links to lore.

**Extrapolation** (`scripts/estimate_size.py`, central rates; 309 lists with sample data): state diffs about 85 MB
(range 73 to 89), so the data branch is about **228 MB** (range 178 to 249), below the 400 MB limit. Published
diffs before the budget would be about 355 MB (range 306 to 372), so the 60 MB budget binds and about
five sixths of the series lose their published diff (busy lists share the budget, quiet lists keep theirs); site
total about **276 MB** with the 60 MB budget and 12 MB of search index (range 247 to 286), and the build still
enforces the 300 MB cap.

**Inline review comments (not done).** Replies that quote a hunk and comment under it could be attached to the
hunk. The stored message bodies collapse quotes and drop most of them, so that would need new state
(quoted context lines plus comment text per reply) and a matching step against the diff that is only
reliable when the quoted lines are unique within the hunk. I did not do it, the plain timeline is untouched
and the Conversation tab shows the replies with their (collapsed) quotes.

### Backfill (backfill.py)

Goal: patch related history for every list down to a horizon (default 90 days), as part of every run.

**Query and paging.** One request per page: `(dt:LO..HI) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)`
with 200 entries per page, newest first. The page cursor is a time, not an offset: `HI` is the oldest message
time reached so far (inclusive upper bound, so no message is skipped, the repeated oldest entry is merged
away), `LO` the horizon. This is resumable across runs without drift (an `o=` offset would shift when mail
arrives between two runs) and it needs no state beyond one number per list. Progress guard: the cursor always
moves at least one second back, so a burst at a single second cannot loop. The first request of a list uses
`HI = now + 1 h`, so the backfill also fills holes left in busy lists by the 25 message `new.atom`.

**State.** `state/meta.json`, per list `bf = {cur: oldest time reached, hz: horizon, done, cap, pg: pages, err}` and
globally `backfill = {requests, days, last}`. `meta.json` is written atomically at every commit (every 100
pages and at the end); list messages and diffs are saved before the cursor, so a crash repeats at most a few
pages (merging is idempotent). A failed request stores `bf.err` and leaves the list for the next run, a
server `Retry-After` beyond the limit aborts the backfill, a failed or killed run never corrupts or shrinks
state. A list is done when a page has fewer than 200 entries or the cursor reached the horizon; a list that
hits the **page cap** (25 pages = 5 000 entries, `--backfill-list-pages`) is done and flagged capped (its history
reaches back to `cur`, shown on the site). Raising the cap or the horizon reopens lists.

**Order and budget.** Smallest lists first (messages per day estimated from the feed, or from the stored
messages for state written before the backfill existed), so most lists are complete after a few runs and the
huge ones (`lkml`, `netdev`, ...) go last and get as far back as the budget allows. At most 2 requests are in
flight (always on different lists) and the shared fetcher keeps 0.5 s between request starts. Per run: the
normal crawl first (it has priority), then the sweep, then the backfill with `min(12 minutes, what is left of the
25 minute budget)`, at most 900 requests per run (`--backfill-requests`) and 4 000 requests over all runs
(`--backfill-total`, a hard stop; the counter lives in `meta.json`).

**Measured, live** (linux-security-module, selinux, netdev, lkml; 7 day window 2026-11-09 to 2026-11-16, found by probing
page offsets with exponential then binary search, 33 requests in total, saved under the run directory):

| list | patch related entries in 7 days | pages | rate based estimate for the same week |
| --- | --- | --- | --- |
| selinux | 376 | 2 | 366 |
| linux-security-module | 789 | 4 | 435 |
| netdev | 4 939 | 25 | 5 600 |
| lkml | 22 266 | 112 | 12 600 |

A search page is about 0.9 to 1.2 MB of XML (about 250 to 300 KB gzip on the wire, estimated with gzip -6; the
fetcher asks for gzip) and takes 0.2 to 2 s. The rate based estimate (messages per day from the 25 newest messages
of each list, times 0.87 patch related) is within a factor of 2 either way, so the table below is given for
three rate factors. A real run: backfilling linux-security-module 29 days took 13 requests and 2 351 messages
(the real acceptance run, see below).

**Request math for all lists** (359 manifest lists, 309 with rate samples, one request each for the rest;
`python3 scripts/estimate_size.py STATE`):

| days | cap 50 pages | cap 25 pages (default) | cap 10 pages | no cap |
| --- | --- | --- | --- | --- |
| 14 | 1 532 | 1 350 | 1 044 | 1 722 |
| 30 | 2 543 | 2 050 | 1 384 | 3 376 |
| 45 | 3 280 | 2 552 | 1 515 | 4 956 |
| 60 | 3 872 | 2 888 | 1 603 | 6 510 |
| 90 | 4 868 | **3 319** | 1 725 | 9 654 |

With rates 0.5x and 1.8x the default (90 days, cap 25) is 2 552 and 3 768 requests. So the **default is
90 days with a cap of 25 pages per list: about 3 300 requests (range 2 550 to 3 770), about 4 GB raw or 0.9 GB
over the wire, 91 lists hit the cap**. That is below the 4 000 request threshold, so the horizon was not lowered. At
roughly 1.3 requests per second it is about 5 runs of 12 minutes, so the first deploy is complete in about 2.5 hours
(a single huge list is walked by one request at a time). Without the page cap 90 days would need 9 654 requests, which
is why the cap exists (state keeps 10 000 messages per list anyway). Alternatives for the owner: 45 days (2 552),
30 days (2 050), cap 10 pages (1 725).

**Sweep.** After the backfill is done, busy lists would again lose messages between runs, so the crawl
sweeps lists whose `new.atom` shows a gap: up to 12 requests per run, 2 pages per list, same query.

### Search index (search.py, searchcore.js)

A static index built with the site, searched in the browser: `data/search/index.json`, token shards
`t/<two letters>.json` and document shards `d/<k>.json` (128 documents each, newest activity first). A document
is a published thread: title, patch subjects of a series (at most 8), author name, home list and cross-posted
lists and tags (RFC, RESEND, trees) are tokenised (lower case, accents folded, letters and digits, two characters
minimum), postings are delta coded document ids. A query word is matched as a prefix inside the shard of its
first two letters (a shard larger than 300 KB is split by the third letter, listed in `index.json`), all words
must match (intersection), results come out newest first and only the document shards of the results on
screen are loaded. A query therefore loads one or two token shards (a few KB to about 100 KB) and a handful of
document shards of about 13 KB. The tokenizer exists twice (Python and JavaScript) and a test compares them
on awkward input. The query code (`searchcore.js`) is pure, loads shards through an injected function and is
unit tested with node, also against a real built index (`ipe keyring` and `.ipe` find both versions of the
ipe series).

Size: 190 bytes per thread (112 documents, 77 postings) measured on 1 353 threads (256 KB), so about **12 MB
for 65 000 threads**, below the 15 MB target. The page also lists matching lists and always ends with the link
"Search lore.kernel.org for this" (`https://lore.kernel.org/all/?q=...`).

### Acceptance on real data

`tests/fixtures/ipe` holds the real thread feeds of the series "ipe: add the .ipe policy keyring"
(RFC v1 2026-10-19, v2 2026-10-29) as carried by `linux-security-module`, `keyrings`, `linux-doc`, `lkml` and `kexec`
(captured with `t.atom`, e-mail addresses replaced, 71 messages, 245 KB). The test replays them as search pages
through the real backfill (windows, cursor, 10 entries per page) and the real build, and checks versions linked
(v1 superseded by v2), replies counted (26 and 6), cross-post lists, the Files changed totals against an independent
count, and the search queries. Separately, a live backfill of linux-security-module for 29 days (13 requests)
found both versions, 4 + 3 patches, 26 + 6 replies, status "changes requested" for v2 and "superseded" for v1.

### Frontend (site/)

Routes: `#/` home, `#/<list>/<tab>` list, `#/<list>/pr/<n>` series (`?tab=commits`, `?tab=files&patch=<i>&file=<path>`),
`#/<list>/issue/<n>`, `#/search?q=<text>`, `#/u/<hash>`, `#/about`. The Files changed tab (files.js) lazy loads
`data/diffs/<list>/<n>.json`, renders one collapsible card per file (line numbers from the `@@` headers, a table
inside a horizontally scrolling box so wide lines never widen the page), a file tree that scrolls to the file, a "Viewed"
box per file kept in `localStorage` (`lh-viewed:<list>/<n>`), a patch selector (all patches combined, or one
patch with its commit message), and banners for truncated or missing diffs. Files of the first 2 500 lines are
expanded, the others render when opened. The diff colours are `color-mix` of the theme variables `--add`, `--del`
and `--bg` (checked for contrast in light and dark by a test). The version selector keeps the tab, each version
has its own diff.

Vanilla JS, no build step, no modules, no `innerHTML`: all nodes are created
with `document.createElement`/`textContent` through one helper (`h()`), so mail
text can never become markup. Routes are hash based (`#/lkml/pr/12`) so deep
links work on GitHub Pages under any sub-path. Every URL is relative; the one
configurable value is `--base`, used only by `404.html`.

## Politeness and robots.txt

`https://lore.kernel.org/robots.txt` disallows every crawler (`User-agent: *`, `Disallow: /`). The task
fixed the data source to the manifest and the Atom feeds, so LoreHub fetches only those, with an
identifying User-Agent, at most 2 concurrent requests, 0.5 s between request starts, Retry-After
honoured and unchanged lists skipped. It does not read robots.txt. This needs an explicit decision from
the owner (ask the kernel.org admins, or lower the cadence). Rough load of the scheduled workflow with the
gap fill off: one 21 KB manifest request per run plus one `new.atom` request (about 170 KB raw, gzip on
the wire) for each list whose archive changed since the previous run. A full pass over all 359 lists took
3 minutes; in steady state a run touches only the lists that received mail in the last 30 minutes.

The backfill raises the load for the first hours: about 3 300 search requests of about 1.2 MB (0.9 GB gzip on the
wire) in total over the first five runs, then at most 12 sweep requests per run for busy lists. It keeps the same
limits (2 concurrent requests, 0.5 s between request starts, Retry-After honoured, a 12 minute budget per run, a
900 request cap per run and 4 000 over all runs). `LOREHUB_BACKFILL_DAYS=0` and `LOREHUB_DEEP_BUDGET=0` return to the
light crawl of the first version, and the owner accepted a light, polite crawl only. This is the number to decide on.
