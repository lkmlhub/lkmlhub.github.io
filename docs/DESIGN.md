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
* The same host serves search results as Atom: `/<list>/?q=dt:<time>..&x=A`
  returns up to **200** entries per page (about 1.4 MB). It is used only as an
  optional, budgeted "gap fill" (see Crawling). It is off by default, in the CLI and in the workflow.
* Neither `new.atom` nor the manifest sends `ETag` or `Last-Modified`, so
  conditional requests cannot save anything today. The code still sends and
  persists validators in case that changes.
* The manifest lists one entry per public-inbox *epoch* (`/lkml/git/20.git`).
  The list name is the first path segment and its activity time is the max
  `modified` over epochs. `modified` equals the time of the newest message, so
  it is a reliable "has anything changed" signal.
* The feed gives the direct parent only (`thr:in-reply-to`), and the message-id
  is only available inside the `link` href.

## Pipeline

```
manifest.js.gz --> manifest.py --> lists + modified times
new.atom (+ optional search atom gap fill) --> feed.py --> message records
message records --> state.py --> state/lists/<list>.json.gz   (accumulated, pruned)
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
* Gap fill: if the oldest entry of `new.atom` is newer than the newest message
  already stored, messages were missed. When a deep budget is configured the
  crawler requests search Atom pages (`dt:<since>..`, 200 per page) for the
  busiest lists first, capped per list and per run.

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
which is why the caps exist.

### Frontend (site/)

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
