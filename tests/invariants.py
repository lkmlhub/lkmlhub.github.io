"""The build time invariants of the site, asserted over every emitted file (D3):

* every pull request row of every list JSON has a thread detail, a diff shard with at least one file and a bodies file
* every count (tab counts, list totals, home totals) equals the number of rows that are shown
* nothing that is not shown leaks into the home page feed, contributor pages, the sidebar lists or the search index
"""

import json
from pathlib import Path

from .helpers import jload


def rows_of(out, name):
    meta = jload(Path(out) / ("data/lists/%s/meta.json" % name))
    rows = []
    for key, pages in meta["pages"].items():
        for i in range(pages):
            rows += jload(Path(out) / ("data/lists/%s/%s-%d.json" % (name, key, i)))["rows"]
    return meta, rows


def search_docs(out):
    idx = jload(Path(out) / "data/search/index.json")
    docs = []
    for p in sorted((Path(out) / "data/search/d").glob("*.json")):
        for d in jload(p):
            docs.append((idx["lists"][d[1]], d[0], "pr" if d[2] else "issue"))
    return docs


def check(case, out):
    out = Path(out)
    lists = jload(out / "data/lists.json")
    shown = {}  # (home list, n, kind) of everything shown
    prs = 0
    for l in lists["lists"]:
        name = l["n"]
        meta, rows = rows_of(out, name)
        counts = meta["counts"]
        case.assertEqual(len(rows), sum(sum(v.values()) for v in counts.values()), "counts of %s equal the rows" % name)
        case.assertEqual(meta["threads_total"], len(rows), name)
        for r in rows:
            home = r.get("hl", name)
            shown[(home, r["n"], r["k"])] = r
            if r["k"] != "pr":
                continue
            prs += 1
            d = jload(out / ("data/threads/%s/%d.json.gz" % (home, r["n"] // 32)))[str(r["n"])]
            case.assertEqual(d["k"], "pr")
            diff = jload(out / ("data/diffs/%s/%d.json.gz" % (home, r["n"])))["p"]
            case.assertTrue(diff, "a diff shard exists for %s #%d" % (home, r["n"]))
            case.assertGreaterEqual(sum(len(x["f"]) for x in diff.values()), 1, "at least one file")
            case.assertEqual(d["df"], 1)
            case.assertGreaterEqual(d["fc"], 1)
            case.assertTrue({p["mid"] for p in d["patches"] if "nd" not in p} <= set(diff))
            case.assertEqual({p["mid"] for p in d["patches"] if "nd" in p}, {p["mid"] for p in d["patches"]} - set(diff))
            bodies = jload(out / ("data/bodies/%s/%d.json.gz" % (home, r["n"])))["b"]
            case.assertEqual(len(bodies), d["mc"])
        mm = jload(out / ("data/lists/%s/meta.json" % name))
        case.assertEqual(sum(c["m"] for c in mm["contributors"]) <= mm["msgs"], True, "contributor counts stay within the shown messages")
        for ev in mm["events"]:
            case.assertIn((ev["hl"], ev["n"], ev["k"]), shown, "right-hand list entry is a shown thread")
    case.assertEqual(lists["totals"]["threads"], len({k for k in shown}), "home page thread count equals the shown rows")
    for ev in jload(out / "data/recent.json")["events"]:
        case.assertIn((ev["hl"], ev["n"], ev["k"]), shown, "activity feed entry is a shown thread")
    for p in sorted((out / "data/authors").glob("*.json")):
        for prof in jload(p).values():
            for it in prof["items"] + prof["replies"]:
                case.assertIn((it["hl"], it["n"], it["k"]), shown, "contributor page entry is a shown thread")
    docs = search_docs(out)
    case.assertEqual(sorted(docs), sorted(shown), "the search index holds exactly the shown threads")
    for p in out.rglob("*.json"):
        json.loads(p.read_text())
    return prs
