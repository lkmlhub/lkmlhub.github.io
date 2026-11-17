"""Write tests/fixtures/review/ipe-v2-site.json: the site files of the real ipe v2 series (thread detail, reply shard,
bodies, diffs) as the browser loads them, built from the ipe fixture. The node golden tests of reviewmail.js start from it.

usage: python3 -m tests.make_review_fixture          (tests/test_review_fixture.py checks that it is current)
"""

import json
from pathlib import Path

from . import ipe_fixture
from .helpers import jload

OUT = Path(__file__).parent / "fixtures" / "review" / "ipe-v2-site.json"


def site_files():
    tmp, state, out, source, report = ipe_fixture.build()
    try:
        rows = json.loads((out / "data/lists/kexec/pr-open-0.json").read_text())["rows"]
        n = next(r["n"] for r in rows if r.get("v") == 2)
        detail = jload(out / ("data/threads/kexec/%d.json.gz" % (n // 32)))[str(n)]
        return {
            "home": "kexec", "n": n, "detail": detail,
            "reply": jload(out / ("data/reply/kexec/%d.json.gz" % n)),
            "bodies": jload(out / ("data/bodies/kexec/%d.json.gz" % n))["b"],
            "diffs": jload(out / ("data/diffs/kexec/%d.json.gz" % n))["p"],
        }
    finally:
        tmp.cleanup()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(site_files(), indent=1, sort_keys=True, ensure_ascii=False) + "\n", "utf-8")
    print("wrote", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
