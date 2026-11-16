"""Regenerate the sanitised fixtures from raw feeds (run by hand, not by the tests).

usage: python3 tests/make_fixtures.py LKML_ATOM NETDEV_SEARCH_ATOM MANIFEST_GZ

Every e-mail-like string (including inside message-ids) is replaced by a stable fake
address on example.test so no real address is committed. Bodies are capped.
"""

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent / "fixtures"
EMAIL = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def sanitise(text, limit):
    fake = {}

    def sub(m):
        return fake.setdefault(m.group(0), "user%d@example.test" % (len(fake) + 1))

    entries = re.findall(r"<entry>.*?</entry>", text, re.S)[:limit]
    head = text[: text.index("<entry>")]
    out = []
    for e in entries:
        m = re.search(r'(<pre\s+style="[^"]*">)(.*?)(</pre>)', e, re.S)
        if m and len(m.group(2)) > 3500:
            body = m.group(2)[:3500]
            body = body[: body.rfind("\n")] if "\n" in body else body
            body = re.sub(r"<span\s*(class=\"q\")?[^>]*$", "", body)
            open_spans = body.count("<span") - body.count("</span>")
            e = e[: m.start(2)] + body + "</span>" * max(0, open_spans) + e[m.end(2):]
        out.append(e)
    return EMAIL.sub(sub, head + "".join(out) + "</feed>\n")


def main(lkml, netdev, manifest):
    HERE.mkdir(exist_ok=True)
    (HERE / "lkml.atom").write_text(sanitise(Path(lkml).read_text("utf-8"), 25), "utf-8")
    (HERE / "netdev.atom").write_text(sanitise(Path(netdev).read_text("utf-8"), 40), "utf-8")
    shutil.copy(manifest, HERE / "manifest.js.gz")


if __name__ == "__main__":
    main(*sys.argv[1:4])
