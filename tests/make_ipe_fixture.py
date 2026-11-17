"""Build tests/fixtures/ipe from real lore responses (run by hand, not by the tests).

usage: python3 tests/make_ipe_fixture.py CACHE2_DIR [CACHE3_DIR]

With CACHE3_DIR (live thread Atoms of the two cover letters fetched on 2026-11-17, ipe-v1-cover-t.atom and
ipe-v2-cover-t.atom) the script also writes real-threads.json: for each version the message count of the REAL thread
as lore served it (cover, patches, replies and their message-ids), the reference of the acceptance test.

Input (saved live responses): thread Atom feeds of the "ipe: add the .ipe policy keyring" series
(v1 RFC posted 2026-10-19, v2 posted 2026-10-29) as carried by five lists, and the pages of a real
linux-security-module backfill, from which a few unrelated patch messages are taken as decoys.
Output: entries.atom (every message once) and membership.json (which list carries which message).
Every e-mail-like string, including inside message-ids, is replaced by a stable fake address on
example.test, so no real address is committed.
"""

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorehub.feed import ENTRY_RE, mid_from_href  # noqa: E402

HERE = Path(__file__).parent / "fixtures" / "ipe"
EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
LISTS = {"linux-security-module": "ipe-v{v}-thread.atom"}
for _l in ("keyrings", "linux-doc", "lkml", "kexec"):
    LISTS[_l] = "ipe-v{v}-thread-%s.atom" % _l


def entries_of(path):
    text = Path(path).read_text("utf-8")
    return ENTRY_RE.findall(text)


def mid_of(entry):
    return mid_from_href(re.search(r'<link\s+href="([^"]+)"', entry).group(1))


def main(cache2, cache3=None):
    cache2 = Path(cache2)
    fake = {}

    def sub(m):
        return fake.setdefault(m.group(0), "user%d@example.test" % (len(fake) + 1))

    entries, member = {}, {}
    for name, pattern in LISTS.items():
        for v in (1, 2):
            f = cache2 / pattern.format(v=v)
            if not f.exists():
                continue
            for e in entries_of(f):
                e = EMAIL.sub(sub, e)
                mid = mid_of(e)
                entries[mid] = e
                member.setdefault(name, []).append(mid)
    if cache3:
        real = {}
        for v in (1, 2):
            f = Path(cache3) / ("ipe-v%d-cover-t.atom" % v)
            mids, replies, patches = [], 0, 0
            for e in entries_of(f):
                e = EMAIL.sub(sub, e)
                mids.append(mid_of(e))
                title = re.search(r"<title[^>]*>([^<]*)</title>", e).group(1)
                if title.lstrip().lower().startswith("re:"):
                    replies += 1
                else:
                    patches += 1
            real["v%d" % v] = {"entries": len(mids), "cover_and_patches": patches, "replies": replies, "mids": sorted(mids)}
        (HERE / "real-threads.json").write_text(json.dumps(real, indent=1), "utf-8")
        print({k: (r["entries"], r["replies"]) for k, r in real.items()})
    ipe = set(entries)
    decoys = 0
    for f in sorted(glob.glob(str(cache2 / "lsm-bf" / "*.atom"))):
        for e in entries_of(f):
            if decoys >= 30:
                break
            e = EMAIL.sub(sub, e)
            mid = mid_of(e)
            tm = re.search(r"<title[^>]*>([^<]*)</title>", e)
            title = tm.group(1) if tm else ""
            pre = re.search(r"<pre[^>]*>(.*?)</pre>", e, re.S)
            if mid in ipe or "[PATCH" not in title or "ipe" in title.lower() or not pre or len(pre.group(1)) > 2500:
                continue
            ts = re.search(r"<updated>([^<]*)</updated>", e).group(1)
            if not ("2026-10-19" <= ts[:10] <= "2026-11-06"):
                continue
            entries[mid] = e
            member.setdefault("linux-security-module", []).append(mid)
            decoys += 1
    head = ('<?xml version="1.0" encoding="us-ascii"?>\n<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:thr="http://purl.org/syndication/thread/1.0"><title>ipe fixture</title>'
            '<updated>2026-11-16T00:00:00Z</updated>')
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "entries.atom").write_text(head + "".join(entries.values()) + "</feed>\n", "utf-8")
    (HERE / "membership.json").write_text(json.dumps({k: sorted(set(v)) for k, v in member.items()}, indent=1), "utf-8")
    print(len(entries), "entries,", decoys, "decoys,", {k: len(set(v)) for k, v in member.items()})


if __name__ == "__main__":
    main(*sys.argv[1:3])
