# LkmlHub design

LkmlHub turns the lore.kernel.org mailing list archives into a GitHub-style
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
  LkmlHub uses it for the history backfill and for a small per-run sweep of busy lists (see Backfill).
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
  Files changed banner "truncated" therefore only ever means LkmlHub's own size caps.
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
* **`t.atom`** of a message (`/<list>/<message-id>/t.atom`) returns a whole thread in one request, whichever message of the
  thread is named (cover letter, patch or reply: same bytes), with the percent-encoded message-id as well. The thread completion
  queue (see "Thread completion") uses it for series that look incomplete.

## Pipeline

```
manifest.js.gz --> manifest.py --> lists + modified times
new.atom, search atom sweep and backfill, thread atoms --> feed.py --> message records (complete bodies) + parsed diffs (diff.py)
records --> retention.py (whole series, newest first) --> state.py --> state/lists/<list>/<day>.json.gz   (messages)
diffs   --> retention.py --> state.py --> state/diffs/<list>/<day>.json.gz          (diffs of the kept series, 1000 patch messages per list)
state --> model.py (threads, revisions, series, status) --> build.py (shows only series with a diff) --> public/
public/ = static HTML + JSON + gzip JSON + JS + CSS, rendered client side
sender addresses --> state.py --> state/addrs/<list>.json.gz  --> reply.py --> public/data/reply/<list>/<n>.json.gz   (reply feature only)
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

### State (state.py, retention.py, migrate.py)

One gzip JSON file per list **and UTC day** on the orphan `data` branch:
`state/lists/<list>/<YYYY-MM-DD>.json.gz`. Files are written atomically (temp file, `os.replace`) and
only when their bytes changed; the gzip header is deterministic, so unchanged days produce no git change. A message record:

```
mid -> {t: unix time, a: author name, h: author hash, p: parent mid or "",
        s: subject, b: the complete body, bv: 3, bt: 1 if cut at 128 KB, f: flags, tr: trailers [[kind, name, hash]],
        ds: [files, added, removed], dz: bytes of the stored diff, tc/tn: thread fetched at / times}
```

**Complete bodies (epoch 3).** `b` is the whole text of the message: quoted lines, trailers and signature included. Removed
are only e-mail addresses, trailing white space, runs of blank lines and, for a patch message, the diff (stored separately).
A body above 128 KB is cut at a line and flagged `bt`. `bv: 3` marks a complete body; a record without it is an older trimmed
copy (state before epoch 3) and is shown with a note until a fetch replaces it. Flags (`A` applied, `I` inline review, `N`
NAK) and trailers are computed at ingest from the full body. Message-ids are stored percent-encoded (`@` becomes `%40`).
A record that is fetched again **replaces** the stored one (messages are immutable; only our parsing changes), except that the
stored body is kept when the new fetch has none and `dz` is kept.

**Retention by whole series** (retention.py, run by `finalize_list` after every ingest). The series of a list are ranked newest
first and kept while their patch messages add up to at most `MAX_DIFF_MSGS` = 1000 per list (the first series is always kept, even
if it alone is longer; the per patch and per series caps of diff.py still apply). A kept series keeps ALL its messages (cover
letter, every patch, every reply at any age: a series is alive while any of its messages is inside the retention) with their
bodies and the diffs of its patches. A dropped series loses everything at once: its messages, bodies and diff day-file entries.
A series none of whose patch messages has a diff is dropped too (it could not be shown). Other messages (discussions, replies
whose patch is unknown, git pull requests) have their own limits: at most 3 000 per list (`MAX_MSGS`), the newest 300 keep a
body, for 60 days. The age limit follows the configured horizon: `max(180, --backfill-days)` days. New diffs wait in memory
until `save_list` writes the day files, so a day file is written once per ingest and the diffs of dropped series never reach disk.

**State budget.** `trim_to_budget` (run at the end of `all`, `--max-state-mb`, default 650) lowers the per list patch limit when
the day files exceed the budget, keeps the new limit in `meta.json` (`limits.max_diff_msgs`) and applies it to every list at once,
again by whole series, newest first.

**Epochs.** `meta.json` holds `epoch` (3 now). When a run finds an older state (or none) it keeps every record, diff and
message, marks every list's backfill cursor not done (restart from now, `bf = {pg: 0, done: false}`), resets the request counter, and
records `backfill.rebuild` (shown on the home page as "history is being rebuilt"). Re-fetched records replace the old trimmed ones,
so the walk and the completion queue converge over several runs; until then series with an older stored diff are still shown, with
their older bodies flagged `lb` ("shortened copy, the complete text is being fetched again", series note "conversation still being
completed"). A fresh deployment is stamped without a rebuild. A bug-free build of a partly migrated state is a unit test.

`state/meta.json` keeps per list crawl bookkeeping (validators, last fetch and check, last error, manifest modified, the stored
thread completion queue `cq`), `state/manifest.json` the parsed manifest and `state/numbers.json` the stable `#N` numbers.

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
6. A `[GIT PULL]` request has no diff, so it is not a pull request here but a discussion (kind `issue`, flag `pull`) that can
   still be merged. Only revisions with patches can be pull requests (series).
7. Numbers: every revision or discussion gets `#N` in its *home list*
   (preferring a list other than lkml when cross-posted). Numbers are persisted
   so URLs are stable, and survive re-rooting when an older message arrives.

### What is shown (build.py): the build time invariant

**Only series with a stored diff are shown.** A pull request here is a patch series and its "Files changed" tab must always have
real diffs, so the build lists a series if and only if at least one patch of it has a stored, readable diff with at least one file
(`DiffReader.has`, then the diff is actually loaded). A series without one is in no list page and no count (Patches tab, Open /
Merged / Closed, list totals, home totals), not in the activity feed, not on contributor pages, not in the right-hand lists of the
list page (contributors, events), not in the search index. The tables are computed from the messages of the shown threads only.
If only some patches of a shown series have a diff, the detail marks the others `nd` and the Files changed tab says
"Diffs for patches 4-7 are not stored, see lore". Versions: each version is its own row and a version without a diff is not shown;
`vs` (the version picker) lists shown versions only; an older version whose newer version is not shown keeps its "superseded"
label with a banner (`sbh`) instead of a link. `tests/invariants.py` asserts this over every emitted file (every PR row has a
thread detail, a diff shard with at least one file and a bodies file; every count equals the rows; feed, profiles, sidebars and
the search index only refer to shown rows), on a mixed synthetic state, on the real ipe fixture and after size eviction.

### Output (build.py)

```
public/index.html            shell with pre-rendered, escaped list of lists (noscript)
public/404.html              redirects /<base>/x/y to /<base>/#/x/y
public/assets/*              app JS, CSS
public/data/lists.json       all lists, small
public/data/recent.json      global activity feed
public/data/lists/<l>/meta.json                 counts, daily chart, contributors, timeline
public/data/lists/<l>/<pr|issue>-<state>-<k>.json   paged rows (300 per page)
public/data/threads/<l>/<n//32>.json.gz         thread detail (message metadata, thread tree), sharded by number
public/data/bodies/<l>/<n>.json.gz              the texts of one thread, same order as the detail
public/data/diffs/<l>/<n>.json.gz               the diffs of one series
public/data/authors/<xx>.json                   contributor profiles, sharded by hash prefix
public/data/search/...                          sharded search index
```

The heavy files (threads, bodies, diffs) are stored gzip compressed and unpacked in the browser (`LH.getGz`: fetch, sniff the
gzip magic, `DecompressionStream`; if a server already decoded the file the plain text is parsed instead, so it works whether
GitHub Pages sends the bytes as they are or with a `Content-Encoding`). This is the measured lever that keeps the site under its
cap: thread shards were 66 percent of the "light" JSON and compress to about 30 percent.

**Thread detail.** `d.cover` (the cover letter, or absent for a series without one), `d.patches`, `d.cm` (the replies, oldest
first). Each message has author, time, trailers, flags, `mid`, and for replies `pi` (the index of the message it answers in the order
cover, patches, replies) and `re` (that author's name). `d.mc` is the total message count, `d.c` the replies, `d.cp`/`d.gp` the
coverage state (below), `d.vs` the shown versions with their reply counts. The bodies file is `{"b": [text or null, ...]}` in the
same order (`null` when no text is kept for that message).

Deviations from the suggested layout, with reasons: rows are paged per state so that tab counts and "Open/Merged/Closed" tabs map
to files; contributor profiles are sharded by hash prefix instead of one file per author to avoid tens of thousands of tiny files;
bodies and diffs are one file per thread so that opening a page loads exactly what it shows.

**Size eviction.** The build estimates the size of every candidate series before writing (gzip of its diff and bodies, plus the
light JSON), chooses series within `max_site_mb - shell - discussions` with the fair water filling of diffshards.pick (every list
gets the same byte quota, quiet lists keep everything; inside a list open series first, then merged, then closed, newest first, so
closed and old series go first) and writes only the chosen ones. The real size is checked after writing; if it is over, the
estimate is scaled by the measured ratio and the build repeats (at most 4 times). A series is left out as a whole: its row, detail,
bodies, diff shard, search document and counts all go together (`tests/test_invariants.py::EvictionTests`).

### Sizes

**Measured unit costs** (real data: the cached search pages of selinux 376, linux-security-module 789 and 2 351, netdev 1 939, lkml
2 466 messages, ingested with the real code, retention and build; `scratch/measure_unit.py` in the run directory):

| | selinux | lsm | netdev | lkml | used for the estimate (low, expected, high) |
| --- | --- | --- | --- | --- | --- |
| state, bytes per message with its body (gzip files) | 422 | 548 | 640 | 449 | 420, 530, 640 |
| state, bytes per patch diff (gzip) | 582 | 1 951 | 1 254 | 1 006 | 580, 1 170, 1 950 |
| site, bytes per patch diff (`.json.gz`) | 694 | 2 021 | 1 344 | 1 103 | 700, 1 260, 2 000 |
| site, bytes per shown message in bodies (`.json.gz`) | 229 | 379 | 715 | 509 | 230, 520, 715 |
| site, thread detail JSON per message | 266 | 264 | 400 | 429 | 264, 350, 430 (x 0.30 compressed) |
| shown series per patch with a diff | 0.16 | 0.21 | 0.34 | 0.37 | 0.27 |
| shown messages per patch with a diff | 2.4 | 2.7 | 1.5 | 1.3 | 1.7, 2.6, 3.5 |

The netdev and lkml samples are sparse (some pages of the week), so their message-per-patch ratios understate the replies.
A full synthetic scale state (24 lists, 14 508 patches with a diff, 37 152 messages, built from the same real entries stretched over
180 days): state 41.2 MB (2.84 KB per patch), site 38.3 MB (2.64 KB per patch with discussions), the build took 6.8 s and 296 MB
RSS (see the report for the larger run).

**Extrapolation** (`scripts/estimate_size.py`, 309 lists with a rate sample from one pass over all lists, peak-hour rates; 135 of
them reach the 1000 patch limit): about 182 000 patch messages with a diff.

| | low | expected | high | ceiling |
| --- | --- | --- | --- | --- |
| data branch | 250 MB | **542 MB** | 959 MB | 700 MB |
| site before leaving out series (thread shards gzip, 100 discussions per list) | 263 MB | **646 MB** | 1 173 MB | 700 MB |

Levers used and why: (1) thread shards stored gzip (site 776 to 646 MB expected: they were 66 percent of the light JSON);
(2) discussions published per list 300 to 100 (they are not what the site is about now, 160 MB to 55 MB); (3) other messages in
state 10 000 to 3 000 per list (the old cap was for the age based retention); (4) retention by whole series with the 1000 patch
limit; (5) when the data branch exceeds 650 MB `trim_to_budget` lowers the per list limit; (6) when the site exceeds 700 MB the
build leaves out whole series. Levers measured and **not** used: quoted lines deduplicated against the parent message
(measured on the lsm month: bodies gzip 1.46 MB to 1.20 MB, 17 percent, about 45 MB of the site, not needed once the thread shards
are compressed; it would need the parent's text on the client and a marker format); bodies of versions beyond the newest two (small
and it breaks "the full conversation"); fewer than 1000 patches per list (the owner's decision D1). In the high case the site cap
leaves out about 40 percent of the series of the busiest lists (the oldest closed ones) and the data budget trims to 650 MB.

**Timing and push.** Pushing a new orphan commit (the old design: one squashed commit per run) re-sends 100 percent of the objects
every time: measured with a local bare repository, 4 022 objects, 27 MB again after changing 60 files; at 540 MB that would be
every run. The workflow now adds one ordinary commit per run (measured: 200 changed files of 20 KB are 250 objects, 5 MB) and
squashes once per UTC day, with an hourly schedule and a 60 minute timeout. See the README and the report.

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
keeps the diffs of its newest series, **1000 patch messages** (`MAX_DIFF_MSGS`), by whole series (see Retention): a diff
leaves its day file (and `dz` its record) only together with its series, empty day files are deleted. A cross-posted patch is
stored once per list that carries it. Cost: 0.6 to 2 KB per patch (mean 1.2).

**Site.** `data/diffs/<home list>/<n>.json.gz` per pull request (one file per series, so opening the Files tab
loads exactly that series), never in list, thread-summary or author JSON (tests check that no diff text
leaks into them). There is no separate diff budget any more: a series is shown with its diff or not at all, and the
whole site is bounded by the 700 MB cap (see "Size eviction"). A series with only some stored patch diffs shows the others as
"not stored" (`nd`) with a notice. Cost: 0.7 to 2 KB per patch (mean 1.26).

**Inline review comments (not done).** Replies that quote a hunk and comment under it could be attached to the
hunk. The stored message bodies collapse quotes and drop most of them, so that would need new state
(quoted context lines plus comment text per reply) and a matching step against the diff that is only
reliable when the quoted lines are unique within the hunk. I did not do it, the plain timeline is untouched
and the Conversation tab shows the replies with their (collapsed) quotes.

### Backfill (backfill.py)

Goal: patch related history for every list down to a horizon (default **180 days**), as part of every run.

**Query and paging.** One request per page: `(dt:LO..HI) AND (s:PATCH OR s:RFC OR s:PULL OR s:RESEND OR s:ANNOUNCE)`
with 200 entries per page, newest first. The page cursor is a time, not an offset: `HI` is the oldest message
time reached so far (inclusive upper bound, so no message is skipped, the repeated oldest entry is merged
away), `LO` the horizon. This is resumable across runs without drift (an `o=` offset would shift when mail
arrives between two runs) and it needs no state beyond one number per list. Progress guard: the cursor always
moves at least one second back, so a burst at a single second cannot loop. The first request of a list uses
`HI = now + 1 h`, so the backfill also fills holes left in busy lists by the 25 message `new.atom`.

**State.** `state/meta.json`, per list `bf = {cur: oldest time reached, hz: horizon, done, cap, fill, pg: pages, err}` and
globally `backfill = {requests, days, last, rebuild}`. `meta.json` is written atomically at every commit (every 100
pages and at the end); list messages and diffs are saved (through the retention) before the cursor, so a crash repeats at
most a few pages (merging is idempotent). A failed request stores `bf.err` and leaves the list for the next run, a
server `Retry-After` beyond the limit aborts the backfill, a failed or killed run never corrupts or shrinks state.

**When a list is done.** (1) a page has fewer than 200 entries or the cursor reached the horizon (complete); (2) **fill**: the
newest `1.1 x 1000` patch messages with a stored diff are all newer than the cursor, so older series would be dropped by the
retention anyway and fetching them is wasted load (this is what makes 180 days affordable: a busy list stops after about 17
pages); (3) the **page cap** (60 pages = 12 000 entries, `--backfill-list-pages`, a safety cap: nothing in the estimate reaches it).
Raising the horizon or a cap reopens lists. The page cap needed for the 1000 patch limit: 1 100 patch messages with a diff are
about 3 search entries each (covers and tagged replies: 3.1 on lsm, 2.8 on selinux), so 3 300 entries or 17 pages; 60 pages is
3.5 times that, enough for lists whose threads have many more replies.

**Order and budget.** Smallest lists first (messages per day estimated from the feed, or from the stored
messages for state written before the backfill existed), so most lists are complete after a few runs and the
huge ones (`lkml`, `netdev`, ...) go last. At most 2 requests are in flight (always on different lists) and the shared
fetcher keeps 0.5 s between request starts. Per run: the normal crawl first (it has priority), then the sweep, then the backfill with
`min(14 minutes, what is left of the budget)`, at most 900 requests per run (`--backfill-requests`) and 12 000 over all
runs (`--backfill-total`, a hard stop; the counter lives in `meta.json` and is reset by a state epoch change), then the thread
completion (below).

**Measured, live** (2026-11-17): 24 backfill requests for netdev, lkml, linux-mm, bpf (5 pages each) and selinux (4 pages)
took 15.5 s (1.5 requests per second); a page is 1.16 MB of XML on average (24 pages) and 287 KB gzip (ratio 0.247, gzip -6).

**Request math for all lists** (359 manifest lists, 309 with rate samples, one request each for the rest;
`python3 scripts/estimate_size.py STATE`, peak-hour sample rates):

| days | cap 60 + fill (default) | cap 60 | cap 25 | no cap |
| --- | --- | --- | --- | --- |
| 30 | 1 782 | 2 661 | 2 050 | 3 376 |
| 90 | 2 517 | 5 263 | 3 319 | 9 654 |
| 180 | **2 899** | 7 268 | 3 864 | 19 058 |

The default (180 days, page cap 60, fill) is **about 2 900 requests** (low about 1 800, high about 4 000 with rates 0.5x and 1.5x), 134
lists stop early because their newest 1 000 patches are stored, none hits the page cap, about 3.4 GB of XML and 0.83 GB on the wire. At
the per run cap of 900 requests (1.5 requests per second allows about 1 300 in the 14 minute budget) the full walk takes **4 runs**,
and with the hourly schedule about 4 hours. Without the `fill` rule 180 days would be 7 268 requests (cap 60) or 19 058 (no cap), too
many for the light crawl the owner accepted. The earlier 25 page cap could not reach 1 000 patch messages plus their replies on a
busy list (25 pages = 5 000 entries is about 1 600 patch messages on a list where every patch gets 2 replies, but a few days of
lkml); with `fill` the busy lists need 17 pages, the cap is only a safety net.

**Sweep.** After the backfill is done, busy lists would again lose messages between runs, so the crawl
sweeps lists whose `new.atom` shows a gap: up to 12 requests per run, 2 pages per list, same query.

### Thread completion (complete.py)

The search based walk finds a reply when its subject still has the patch tags. To make every shown series carry its WHOLE
conversation, the update also looks for series that look incomplete and fetches their thread from lore in one request:

* **URL.** `https://lore.kernel.org/<list>/<message-id>/t.atom` (public-inbox thread Atom). Verified live on 2026-11-17 against
  the ipe series: the cover letter, a patch and a reply of the same thread all return the same document (10 entries for v2, 31
  for v1, same bytes), the percent-encoded message-id (`%40`) works and returns identical bytes, each entry carries `thr:in-reply-to`
  and the full text. A 144 message thread (linux-security-module, "fs: don't use pseudo filesystem files as storage") came back whole,
  319 KB. The feed holds the messages of that list's archive only (a reply sent to another list only is not in it).
* **Gaps** (`find_gaps`, pure): `p` a reply or a non-first patch whose parent is not in the state, or a reply to a patch whose
  parent is missing (it forms an entity of its own; it is matched to its series by version, number, total and normalised title);
  `n` numbered patches missing (1/7, 2/7, 4/7: 3/7 is missing; compared with the highest N seen); `c` no cover letter although the
  patches say N > 1 and a patch points to a parent that is not known (a series without any cover letter and without such a
  pointer is normal and is not a gap); `l` only an older trimmed body, counted once the walk of its list is done.
* **Queue.** Per list the candidates (displayed series with a gap that are due) are scanned when the list changed in this run (or
  has no stored queue) and kept in `meta.json` (`cq`), so a normal run does not rescan every list. All candidates are sorted newest
  first with open series before closed ones, and processed with 2 requests in flight, 0.5 s apart, within their own budget per run
  (`--complete-requests`, default 300, `--complete-min` 8 minutes). The fetched thread is ingested into that list (complete bodies,
  diffs, any age, see retention), the first message of the series gets `tc` (time) and `tn` (count). A fetch that finds nothing new is
  final (the gaps are lore's); one that adds messages may be repeated after a day, at most 3 times. The run is resumable: what is
  left stays in `cq`; failures stay in the queue; a server back-off stops the run.
* **UI.** `coverage` gives the series note: 0 complete, 1 "Conversation still being completed" (waiting for a fetch, or its older
  bodies for the walk), 2 "Some messages of this thread could not be found on lore" (fetched, gaps remain). Both link to the full thread.

**Measured on real data** (live, 2026-11-17; 6 lists; the walked window of each list; `scratch/measure_gaps.py`,
`measure_threads.py` in the run directory): incomplete series after the search walk: linux-security-module 9 of 171 (5.3 %), bpf 4 of 65
(6.2 %), selinux 1 of 43 (2.3 %), netdev 2 of 113 (1.8 %), lkml 2 of 127 (1.6 %), linux-mm 0 of 71. 83 whole threads were fetched
(the 17 flagged series, 66 complete-looking ones as a control; 116 requests of the 200 allowed in total): **0 messages inside the walked
window were missing** (18 messages were older than the walked window of a 29 day walk, from an earlier send of the same series: they
are inside the 180 day horizon and come with the full walk). So the search walk is complete for tagged mail, the flagged gaps were
boundary effects or patches that were sent to other lists only, and replies with an edited subject were not found in this sample
(0 of about 1 000 messages). Extrapolation to 180 days: about 49 000 shown series (182 000 patches x 0.27), 3 % flagged (range 1.5 to 6 %)
is **about 1 500 thread requests** in total (range 700 to 3 000), at 58 KB each (13 KB gzip on the wire) about 20 MB. At 300 per run
that is 5 runs; in steady state only the new series of an hourly run are checked (a handful of requests).

### Review by e-mail (reviewmail.js, reviewstore.js, review.js, reply.py, state/addrs)

The site is static and cannot send mail, so the Files changed tab becomes a review tool that PREPARES the replies a kernel
list expects. Nothing is sent, nothing leaves the browser.

**Anchors.** A line comment names its line by `(patch message-id, file path, ix)` where `ix` is the index of the line in the
file's stored hunk text (`x.split("\n")`, the `@@` line is ix 0). `parseHunks` gives every row its `ix` and `diffpair.js` keeps it on
the cells, so the unified and the split layout name the same line the same way (a context line that shows on both sides of the
split is one anchor). A comment also keeps the text of its line; if the stored diff no longer has that text the comment is left
out of the e-mail with a warning. A comment on a commit message line is `kind: msg` with the line index of the stored message.
Click-drag range comments are not implemented.

**Drafts** are in `localStorage` under `lkmlhub-review:<list>:<number>:v<version>` (every version of a series is its own pull
request with its own number, so comments are per version), the identity under `lkmlhub-identity`. Stored data is validated on
read (types, sizes, at most 500 comments, unknown fields dropped). Without a usable localStorage the drafts live in memory until
the page is reloaded and the page says so. A comment is "Pending" until the e-mails are prepared, then "Prepared" (an edit makes
it pending again); "Discard review" clears everything of that version.

**Email format decisions** (`reviewmail.js`, pure, golden tests in `tests/js/test_reviewmail.js`, golden files in
`tests/fixtures/review/golden`):

* One reply per patch that has line comments (or a picked tag), one reply to the cover letter when there is an overall comment or
  a series wide tag, in the order patches, cover. A series without a cover letter puts the overall comment into the reply to
  the first patch and cannot offer a series wide tag.
* Body: attribution `On Thu, 29 Oct 2026 00:31:21 +0000, Name wrote:` (UTC, name only), then quoted regions and comments
  separated by blank lines, then the overall comment and the tag lines. Quoting is `> ` + line, an empty line is `>` (no trailing
  white space), already quoted text becomes `> > `. A commented diff line is quoted with up to 3 lines BEFORE it (never above its
  hunk header), the comment follows; consecutive comments in one hunk continue the same quote, a gap gets a line `[...]`;
  the three header lines of a file (`diff --git`, `---`, `+++`) and the hunk header are quoted once each; the commit message is
  quoted only when it is commented on. The message is rebuilt from the stored pieces (commit message from the bodies shard,
  hunk text from the diff shard): the `index` and mode lines and the diffstat are not stored and not quoted.
* Prose is wrapped at 72 columns (counted in characters), short lines are never joined, quoted lines, indented code (4 spaces or a
  tab) and long words such as URLs are never reflowed.
* Tags: Approve gives Reviewed-by (default), Acked-by or Tested-by, Request changes none unless Nacked-by is ticked (never
  preselected), Comment none. Scope: the whole series (the tag goes into the cover letter reply, which b4 applies to every patch)
  or the picked patches (the tag goes into the reply to each). Name and address in the tag come from the identity.
* Headers: From, To (author of the replied message), Cc (list addresses, then the other participants, each address once,
  without the reviewer and without To), Subject (`Re: ` once), In-Reply-To, References (ancestors oldest first, then the replied
  message, from the parent Message-IDs of the reply shard), Date (UTC), MIME-Version, Content-Type UTF-8, X-Unsent: 1.
  Header values are one line: control characters, bidi overrides and line separators become spaces, display names are quoted or
  RFC 2047 encoded (base64, words of at most 60 characters, folded at 78 columns), addresses and Message-IDs are checked
  (anything that is not an addr-spec is dropped). To is omitted, not guessed, when the address is unknown.
* Encoding: `Content-Transfer-Encoding: 8bit` and LF line ends, so that "Copy whole text" is readable and pastes cleanly;
  quoted-printable only when a line is longer than 998 bytes (the SMTP limit), then for the whole email, with a warning.
* Outputs: `.eml` (Blob, `message/rfc822`, object URL revoked when the dialog closes or after 10 s), copy, `mailto:` (RFC 6068: to,
  cc, subject, In-Reply-To, body with CRLF; hidden above 1800 characters of URL, with a hint) and
  `git send-email --from=... --in-reply-to='<id>' --no-thread --suppress-cc=all --8bit-encoding=UTF-8 --to=... --cc=... file.eml`.
  From git-send-email(1): the file is read as a patch mail, most headers and the MIME formatting are ignored, so the recipients and
  the message it answers come from the options; `--no-thread` keeps `--in-reply-to` for the one mail, `--suppress-cc=all` stops it
  from adding people found in the text, `--from` avoids a second `From:` line inside the body. The man page is not installed on
  the development machine; the command follows the online manual and was not run.
* Mail sent through the b4 web endpoint comes from a `devnull+...@kernel.org` address that discards replies: the reply shard names
  the real author (the Signed-off-by with the same name) when that person's address is known, and To is left empty with a warning
  when it is not.

**Addresses.** The Atom feeds carry the sender address of every message and the posting address of a list (`<id>mailto:...`,
`new.atom` only: thread and search feeds have a `urn:uuid` id). They used to be hashed and dropped. Owner decision: reply-all needs
them, so they are kept in separate data used only by the reply feature:

* `state/addrs/<list>.json.gz` = `{"v": 1, "post": list address, "a": {author hash: sender address}}`, written atomically and
  deterministically with the list (`State.sync_addrs`), merged on every ingest, pruned to the senders of the messages that are
  stored. The main state files stay address free. The state epoch is NOT bumped: history ingested before the feature simply has no
  addresses, nothing is re-walked.
* `data/reply/<list>/<n>.json.gz` per published thread (series and discussions): `{"addr": 1|0, "lists": {list: posting
  address}, "m": [{"i": Message-ID, "p": parent Message-ID, "n": name, "h": hash, "t": time, "s": subject, "ah": real author hash for
  relay mail}], "who": {hash: {"n": name, "a": address}}}`, in the order of the bodies file. It is fetched only when the review dialog
  or a Reply button is opened (a test asserts that no page script mentions the path). About 57 bytes per message after gzip, counted
  as 80 in the size estimate. Message-IDs may contain addresses by nature, which is fine there.
* Scan tests: addresses are allowed ONLY in these two kinds of files (`tests/helpers.is_address_file`), asserted both ways.
* **Learning.** Every message of every newly fetched feed (new.atom, search windows, thread feeds) adds its sender. Series shown
  before that have unknown addresses until their people post again, so the completion queue also schedules a thread feed fetch
  (one request) for displayed OPEN series with a participant without an address: per list queue `aq` in `meta.json` (item:
  first message-id, last activity, up to 6 hashes still missing, 1 when more), series already fetched for addresses are recorded in
  `meta["lists"][<list>]["ad"]` and never fetched again, a run does at most `--complete-addr-requests` (default 100, variable
  `LOREHUB_COMPLETE_ADDR_REQUESTS`) of its thread requests for this, after the real completion items, a cross-posted series is
  fetched once, and an item is skipped without a request when everybody on it became known through other fetches in the meantime
  (addresses are global per author hash). Series that are merged, superseded or stale are not scheduled.
* **Opt out.** `--no-reply-addresses` or `LOREHUB_REPLY_ADDRESSES=off` (workflow variable): senders are neither collected nor
  published, stored sender addresses are deleted at the next run (the list posting addresses stay: they are not personal), shards
  are still written with `"addr": 0` and the list addresses, so the e-mails get the list in Cc and a hint to add the rest.
* Request estimate for the deployed state: see REPORT5 in the run notes; in short up to the number of displayed open series
  (an estimated 12 000 to 25 000 of 38 000 to 56 000 shown series, before the global learning removes the ones whose people are
  known), 100 per run, so days at one run per hour.

**Dialogs.** `review.js`: one modal helper (role=dialog, aria-modal, labelled, the rest of the page `inert`, Tab trapped, Esc and
route changes close, focus returns to the opener), the review form (identity, overall comment, state, tag, scope, the list of
comments with links that scroll to the line), the result panels (headers summary, warnings, buttons, the complete text), the
Reply composer (the stored body quoted with an attribution; says so when the stored text is a shortened or cut copy or missing).
All DOM through `createElement`/`textContent`.

**Tests.** node golden tests (3 e-mails of the ipe v2 series: patch 1, patch 3, cover), mapping, wrapping, tags, threading, Cc,
hostile input, quoted-printable, mailto, git send-email; store tests; pipeline tests (address map, shards, queue, opt out,
relay); a headless Chrome flow with real input events through the DevTools protocol (`tests/js/cdp.js`), both layouts.

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

### Acceptance on real data (updated for the full conversation)

`tests/fixtures/ipe` holds the real thread feeds of the series "ipe: add the .ipe policy keyring"
(RFC v1 2026-10-19, v2 2026-10-29) as carried by `linux-security-module`, `keyrings`, `linux-doc`, `lkml` and `kexec`
(captured with `t.atom`, e-mail addresses replaced, 71 messages, 245 KB). The test replays them as search pages
through the real backfill (windows, cursor, 10 entries per page) and the real build, and checks versions linked
(v1 superseded by v2), replies counted (26 and 6), cross-post lists, the Files changed totals against an independent
count, and the search queries. Separately, a live backfill of linux-security-module for 29 days (13 requests)
found both versions, 4 + 3 patches, 26 + 6 replies, status "changes requested" for v2 and "superseded" for v1.

The reference of the full conversation test is `tests/fixtures/ipe/real-threads.json`: the thread Atom of both cover letters
as lore served it on 2026-11-17 (re-fetched live with `t.atom` that day and identical to the earlier capture): **v1 has 31
messages (cover, 4 patches, 26 replies), v2 has 10 (cover, 3 patches, 6 replies), 32 replies across both versions**. The
v2 page of the live site showed 6 replies because the replies to v1 (26) belong to the v1 page: the two versions are separate
threads on lore (v2 does not reply to v1). `tests/test_ipe.py` asserts, per version, that the shown message ids equal the real
thread exactly, the reply and message counts equal it, every message has its complete text in the bodies file, all patches of
v1 (4) and v2 (3) have a diff in the Files changed shard, and that nothing is waiting for completion. The v2 page links to v1
and shows its 26 replies in the sidebar ("Versions and their replies"). `tests/test_thread_browser.py` renders both pages in
Chrome (31 and 10 cards, nesting, collapsed quotes, no overlap, no overflow at 1280 and 390 px).

### Frontend (site/)

Routes: `#/` home, `#/<list>/<tab>` list, `#/<list>/pr/<n>` series (`?tab=commits`, `?tab=files&patch=<i>&file=<path>`),
`#/<list>/issue/<n>`, `#/search?q=<text>`, `#/u/<hash>`, `#/about`.

**Conversation tab (thread.js).** The timeline is the whole thread: the cover letter card, the replies to it nested under it,
then for every patch in order a patch card (number, subject, author, time, diffstat, "View changes", "patch on lore", the commit
message behind a toggle) with the replies to that patch nested under it (depth up to 4, then flat), then replies whose parent is not
in the archive. Every reply card has author, time, "in reply to <author>" (a link that scrolls to and flashes that card),
trailers as highlighted chips, "lore" and the text. Quoted blocks of more than 5 lines are collapsed behind "Show quoted text (N
lines)"; a message longer than 45 lines is clipped; "Show full message" (or "Show all quoted text") opens everything, "Show less"
closes it again. A message cut at 128 KB says so and links to lore, an older shortened copy says so. The header shows the total
message count and the replies; the sidebar lists reviewers and trailers, labels, lists, **participants with their message counts**,
the versions with their reply counts, and a link to the thread on lore. A series that waits for its thread fetch shows a small muted
note with a link to the whole thread.

**Files changed tab (files.js).** Lazy loads `data/diffs/<list>/<n>.json.gz`, one collapsible card per file, unified or split (see below; line numbers from the `@@`
headers, a table inside a horizontally scrolling box so wide lines never widen the page), a file tree that scrolls to the file, a "Viewed"
box per file kept in `localStorage` (`lh-viewed:<list>/<n>`), a patch selector (all patches combined, or one patch with its commit
message), a notice for patches without a stored diff ("Diffs for patches 4-7 are not stored, see lore") and banners for truncated
diffs. The tab is always there for a series; its "no diff" empty state is defensive and unreachable in a normal build (a test
asserts the DOM never shows it for the ipe series). Files of the first 2 500 lines are expanded, the others render when opened.
The diff colours are `color-mix` of the theme variables `--add`, `--del` and `--bg` (checked for contrast in light and dark by a test).
The version selector keeps the tab, each version has its own diff.

**Unified and Split layout (files.js, diffpair.js).** A segmented control in the toolbar (two real buttons with `aria-pressed`) switches
every file card at once. The choice is `choice = URL ?layout= (split|unified) || localStorage lkmlhub-diff-layout || unified`: the URL
parameter wins and is never written to storage, a click writes storage and updates the URL (`history.replaceState`, also the file
and tree links of the page) so the link opens the same layout. Invalid values are ignored.

*Pairing (`LHDiffPair.pairRows`, pure, tested with node in `tests/js/test_diffpair.js`).* Input: the lines of `parseHunks` (`hunk`, `ctx`,
`del`, `add`, `note`). Output: rows `{left, right}` with cells `{t: ctx|del|add|empty|note, no, s, hl}`, or `{span: true, left, right}`
(same cell on both sides) for hunk headers. A context line is one row with both numbers. A change block is the run of `del` and `add` lines
between context lines; its i-th removed line is paired with its i-th added line, the shorter side gets `{t: 'empty'}` cells (no number, no
text). A `\ No newline` marker belongs to the line before it: after a removed line it sits on the left, after an added line on the right,
after a context line on both sides; it follows the row of its line (never paired with an unrelated line); with nothing before it, it
spans both columns. Notes in the data can never change the pairing of the lines around them.

*Changed characters (`intraline`, `markIntraline`).* For each paired removed/added line the common prefix and suffix are trimmed and the
middle gets `<span class="ih">` with a stronger background (40 percent of the add/del colour instead of 15). Skipped for identical lines,
lines of more than 500 characters and pairs with no common prefix or suffix; never cuts inside a surrogate pair. The same marks are used
in the unified layout (its removed lines are paired with the added lines of the same block in the same way).

*Responsive rule.* The split layout needs `min-width: 900px`. Below it the unified layout is rendered whatever the stored choice is, the
two buttons are disabled with the title "The split layout needs a window at least 900 px wide, the unified layout is shown", and the
stored choice stays untouched. A `matchMedia` listener re-renders the open cards when the window crosses the line. On a medium window
(under 1100 px) the file tree starts hidden when Split is active, to give the table room.

*Rendering and width.* One `<table class="diff-split">` per file part with four cells per row (gutter, code, gutter, code), the `+` / `-`
marker is a CSS `::before` (not copied with the text). Placeholders are hatched grey (`--bg-subtle` and `--border`). Colours are
`color-mix` of `--add`, `--del` and `--bg`, checked for contrast in both themes (`tests/test_frontend.py`). The table is `width: 100%`
with a `760px` minimum and `360px` minimum per code column, automatic table layout, inside the `.diff-scroll` box with `overflow-x: auto`:
a long line widens its column, the card scrolls, the page never does. Binary, rename-only, mode-only and too-large files have no table, the
note is a paragraph across the whole card, so it spans both columns in both layouts.

*Performance rule.* Collapsed files are not rendered until opened and cards are expanded only until 2 500 lines are on the page (as before).
Within a file part at most 2 000 rows (table rows, either layout) are created at once, then a "Show N more lines" button adds the next
2 000. The patch cap (120 KB per patch, 600 KB per series) bounds the total. Switching layout rebuilds only the cards that are open and
built, discards the DOM of collapsed ones, and keeps the card at the top of the window in place.

Vanilla JS, no build step, no modules, no `innerHTML`: all nodes are created
with `document.createElement`/`textContent` through one helper (`h()`), so mail
text can never become markup. Routes are hash based (`#/lkml/pr/12`) so deep
links work on GitHub Pages under any sub-path. Every URL is relative; the one
configurable value is `--base`, used only by `404.html`.

## Politeness and robots.txt

`https://lore.kernel.org/robots.txt` disallows every crawler (`User-agent: *`, `Disallow: /`). The task
fixed the data source to the manifest and the Atom feeds, so LkmlHub fetches only those, with an
identifying User-Agent, at most 2 concurrent requests, 0.5 s between request starts, Retry-After
honoured and unchanged lists skipped. It does not read robots.txt. This needs an explicit decision from
the owner (ask the kernel.org admins, or lower the cadence). Rough load of the scheduled workflow with the
gap fill off: one 21 KB manifest request per run plus one `new.atom` request (about 170 KB raw, gzip on
the wire) for each list whose archive changed since the previous run. A full pass over all 359 lists took
3 minutes; in steady state a run touches only the lists that received mail in the last 30 minutes.

The backfill raises the load for the first hours: about 2 900 search requests of about 1.2 MB (0.83 GB gzip on the
wire) in total over the first four runs, plus about 1 500 thread requests of about 13 KB gzip each, then at most 12 sweep
requests per run for busy lists and a handful of thread requests per run. It keeps the same limits (2 concurrent requests,
0.5 s between request starts, Retry-After honoured, a 14 minute backfill budget and an 8 minute completion budget per run, a
900 request cap per run for the walk and 300 for the thread completion). `LOREHUB_BACKFILL_DAYS=0` and `LOREHUB_DEEP_BUDGET=0` return to the
light crawl of the first version, and the owner accepted a light, polite crawl only. This is the number to decide on.
