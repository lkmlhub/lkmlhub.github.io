"""Turn accumulated state into the static site: JSON data plus the HTML/JS/CSS shell."""

import html
import json
import os
import shutil
import time
from collections import OrderedDict
from pathlib import Path

from .backfill import history_start, horizon_ts, is_done, stop_reason
from .complete import coverage
from .crawl import manifest_from_json
from .diffshards import SERIES_CAP, cap_series, est_size, pick
from .feed import lore_url
from .groups import group_colors, group_for
from .model import (
    DAY,
    LABELS,
    REVIEW_KINDS,
    build_entities,
    compute_status,
    finish_entity,
    link_versions,
    load_messages,
    review_summary,
    state_of,
)
from .parse import BODY_VERSION
from .retention import effective_retention
from .search import build_index
from .state import StateError, day_of, dumps, gzip_bytes

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
PAGE_SIZE = 300
SHARD = 32
TOP_CONTRIBUTORS = 50
MAX_AUTHOR_PROFILES = 4000
MAX_ENTITIES = 100  # newest discussions published per list (patch series are limited by the stored diffs)
SPARK_DAYS = 30
STALE_AFTER = 6 * 3600
MAX_SITE_MB = 700  # GitHub Pages allows 1 GB; this keeps a safety margin
# Estimates used to choose series before anything is written (gzip of the JSON, see scripts/estimate_size.py)
GZ_DIFF = 0.27
GZ_BODY = 0.30
LIGHT_PER_ENTITY = 2600  # rows, detail, search, profile share of one thread
DIFF_BUDGET = None  # optional extra cap in bytes on diff and body files (tests, experiments)


def pick_home(lists):
    return sorted(lists, key=lambda l: (l == "lkml", l))[0]


def assign_numbers(entities, msgs, numbers, prune):
    """Stable '#N' per home list. Re-rooting inherits the old number via any known member."""
    keys, nxt = numbers.setdefault("keys", {}), numbers.setdefault("next", {})
    used = set()
    for e in sorted(entities, key=lambda e: (e.opened, e.head)):
        found = None
        for mid in [e.head] + [m.mid for m in e.members]:
            cand = keys.get(mid)
            if cand and tuple(cand) not in used:
                if found is None or cand[1] < found[1]:
                    found = cand
                if mid == e.head:
                    found = cand
                    break
        if found is None:
            home = pick_home(msgs[e.root].lists or e.lists)
            n = nxt.get(home, 0) + 1
            nxt[home] = n
            found = [home, n]
        used.add(tuple(found))
        keys[e.head] = found
        e.home, e.n = found
    if prune:
        referenced = set(msgs) | {m.p for m in msgs.values() if m.p}
        for mid in [k for k in keys if k not in referenced]:
            del keys[mid]


def make_row(e, list_name):
    r = {"n": e.n, "k": e.kind, "t": e.title[:200], "a": e.author_name, "ah": e.author,
         "o": e.opened, "u": e.updated, "l": e.label, "c": len(e.comments)}
    if e.home != list_name:
        r["hl"] = e.home
    if e.kind == "pr":
        r["v"] = e.version
        if len(e.versions) > 1:
            r["nv"] = len(e.versions)
        r["p"] = e.total or len(e.patches)
        rv = []
        for t in review_summary(e):
            if t["kind"] in REVIEW_KINDS and not t["carried"] and len(rv) < 4:
                rv.append([t["h"], t["kind"], t["name"]])
        if rv:
            r["rv"] = rv
        if e.superseded_by is not None:
            r["sb"] = e.superseded_by.version
    if e.pull:
        r["pu"] = 1
    if e.tags:
        r["tg"] = e.tags
    others = sorted(e.lists - {list_name})
    if others:
        r["x"] = others[:5]
    if e.partial:
        r["pa"] = 1
    return r


def has_body(m):
    """A body is stored for the message (an empty body of the new format counts)."""
    return bool(m.b) or m.bv >= BODY_VERSION


def msg_out(m, home, parent_name="", pi=None):
    """Message metadata for the thread detail. The text itself is in the bodies file (see bodies_out)."""
    o = {"a": m.a, "ah": m.h, "o": m.t, "mid": m.mid}
    ml = home if home in m.lists else sorted(m.lists)[0]
    if ml != home:
        o["ml"] = ml
    if m.tr:
        o["tr"] = [t for t in m.tr if t[0] != "codev"]
    if m.ds:
        o["ds"] = m.ds
    if m.f:
        o["f"] = m.f
    if parent_name:
        o["re"] = parent_name
    if pi is not None:
        o["pi"] = pi
    if m.bt:
        o["bt"] = 1
    if has_body(m) and m.bv < BODY_VERSION:
        o["lb"] = 1  # an older, trimmed copy: the complete text is fetched again by the walk
    return o


def diff_summary(e, diffs):
    """(files changed, added, removed) over a series. `diffs` maps patch message-id to its stored diff."""
    paths, miss, add, rem = set(), 0, 0, 0
    for m in e.patches:
        d = diffs.get(m.mid) if diffs else None
        if d is not None:
            paths.update(f["p"] for f in d["f"])
            add += sum(f["a"] for f in d["f"])
            rem += sum(f["d"] for f in d["f"])
        elif m.ds:
            miss += m.ds[0]
            add += m.ds[1]
            rem += m.ds[2]
    return len(paths) + miss, add, rem


def thread_order(e):
    """The messages of a thread in the order used by the detail JSON and the bodies file:
    the opening message (the cover letter of a series, the first message of a discussion), the patches
    of a series, then the replies. A series without a cover letter opens with its first patch."""
    if e.kind == "pr":
        first, middle = e.cover, list(e.patches)
    else:
        first, middle = e.cover or (e.patches[0] if e.patches else None), []
    return first, ([first] if first is not None else []) + middle + list(e.comments)


def bodies_out(order):
    """Texts aligned with thread_order: the stored text, or None when none is kept."""
    return {"b": [m.b if has_body(m) else None for m in order]}


def make_detail(e, msgs, diffs=None, shown=None, now=0):
    first, order = thread_order(e)
    idx = {m.mid: i for i, m in enumerate(order)}
    d = {"k": e.kind, "n": e.n, "home": e.home, "t": e.title, "a": e.author_name, "ah": e.author,
         "o": e.opened, "u": e.updated, "l": e.label, "s": e.state, "lists": sorted(e.lists),
         "tg": e.tags, "c": len(e.comments), "mc": len(order)}
    if e.partial:
        d["pa"] = 1
    if e.kind == "pr":
        d["v"] = e.version
        d["total"] = e.total
    if e.pull:
        d["pu"] = 1
    head_msg = msgs[e.root]
    hl = e.home if e.home in head_msg.lists else sorted(head_msg.lists)[0]
    d["lore"] = lore_url(hl, e.root)
    if first is not None:
        d["cover"] = msg_out(first, e.home)
        if e.kind == "pr" and e.cover is None:
            d["cover"]["single"] = 1
    if e.kind == "pr":
        d["patches"] = []
        for m in e.patches:
            par = msgs.get(m.p)
            p = dict(msg_out(m, e.home, par.a if par and par.mid in idx else "", idx.get(m.p)),
                     i=m.subj.index or 1, t=m.subj.title or m.subj.raw)
            if diffs is not None and m.mid not in diffs:
                p["nd"] = 1  # the diff of this patch is not stored
            d["patches"].append(p)
        fc, fa, fd = diff_summary(e, diffs)
        if fc:
            d["fc"], d["fa"], d["fd"] = fc, fa, fd
        if diffs:
            d["df"] = 1
        d["cp"], gaps = coverage(e, msgs, now)
        if gaps:
            d["gp"] = gaps
    d["cm"] = []
    for m in e.comments:
        par = msgs.get(m.p)
        known = par is not None and par.mid in idx
        d["cm"].append(msg_out(m, e.home, par.a if known else "", idx.get(m.p) if known else None))
    d["rv"] = review_summary(e)
    if shown is not None and e.kind == "pr":
        chain = [v for v in e.versions if id(v) in shown]
        if len(chain) > 1:
            d["vs"] = [
                {"n": v.n, "home": v.home, "v": v.version, "o": v.opened, "l": v.label, "rs": int(v.resend),
                 "c": len(v.comments)}
                for v in chain
            ]
        newer = [v for v in e.versions if v.version > e.version or (v.version == e.version and v.opened > e.opened)]
        newer_shown = [v for v in newer if id(v) in shown]
        if newer_shown:
            s = newer_shown[0]
            d["sb"] = {"n": s.n, "home": s.home, "v": s.version}
        elif e.superseded_by is not None:
            d["sbh"] = e.superseded_by.version  # superseded by a version that is not shown (no diff stored)
    return d


def make_events(e):
    ev = []
    base = {"k": e.kind, "n": e.n, "hl": e.home, "t": e.title[:160]}
    if not e.ghost and not (e.kind == "issue" and e.partial):
        ev.append(dict(base, ty="opened", w=e.author_name, wh=e.author, ts=e.opened))
    seen = set()
    for c in e.comments:
        if c.h == e.author:
            continue
        for kind in [t[0] for t in c.tr if t[0] in REVIEW_KINDS]:
            if (c.h, kind) not in seen:
                seen.add((c.h, kind))
                ev.append(dict(base, ty=kind, w=c.a, wh=c.h, ts=c.t))
        if "A" in c.f and ("merged", 0) not in seen:
            seen.add(("merged", 0))
            ev.append(dict(base, ty="merged", w=c.a, wh=c.h, ts=c.t))
    return ev


def build_site(state, out, now, names=None, base="/", max_site_mb=MAX_SITE_MB, log=print, max_entities=MAX_ENTITIES,
               diff_budget=DIFF_BUDGET):
    """Build the site. When it is over `max_site_mb` whole series are left out, oldest closed ones first."""
    cap = int(max_site_mb * 1e6)
    shrink, scale, report = 0, 1.0, None
    for attempt in range(4):
        report = _build_once(state, out, now, names, base, log, save_numbers=(attempt == 0), max_entities=max_entities,
                             cap=cap, shrink=shrink, scale=scale, diff_budget=diff_budget)
        if report["bytes"] <= cap:
            break
        over = report["bytes"] - cap
        log("site is %.1f MB, over the %.0f MB cap: leaving out whole series (%.1f MB)" % (report["bytes"] / 1e6, max_site_mb, over / 1e6))
        shrink += int(over * 1.1) + 1
        if report.get("est_heavy") and report.get("heavy_bytes"):
            scale *= max(0.5, min(2.0, report["heavy_bytes"] / report["est_heavy"]))
    report["over_cap"] = report["bytes"] > cap
    return report


class DiffReader:
    """Loads diff day files one at a time with a small LRU cache (a series' patches sit in one or two days)."""

    def __init__(self, state, size=4, log=print):
        self.state, self.size, self.log = state, size, log
        self.cache = OrderedDict()
        self.days = {}

    def has(self, m):
        """True when the stored diff of patch message m exists on disk (its record says so and the day file is there)."""
        if m.dz is None or not m.dl:
            return False
        if m.dl not in self.days:
            self.days[m.dl] = set(self.state.diff_days(m.dl))
        return day_of(m.t) in self.days[m.dl]

    def get(self, m):
        key = (m.dl, day_of(m.t))
        if key not in self.cache:
            try:
                self.cache[key] = self.state.load_diff_day(*key)
            except StateError as err:
                self.log("skipping unreadable diffs: %s" % err)
                self.cache[key] = {}
            while len(self.cache) > self.size:
                self.cache.popitem(last=False)
        else:
            self.cache.move_to_end(key)
        return self.cache[key].get(m.mid)


def est_series(e, have):
    """Estimated bytes on the site of a series: gzip of its diff shard and bodies file, plus the light JSON."""
    diff = est_size(sum(m.dz for m in have), len(have))
    body = sum(len(m.b) + 60 for m in e.members)
    return diff * GZ_DIFF, body * GZ_BODY + LIGHT_PER_ENTITY


def _build_once(state, out, now, names, base, log, save_numbers=True, max_entities=MAX_ENTITIES, cap=MAX_SITE_MB * 10**6,
                shrink=0, scale=1.0, diff_budget=DIFF_BUDGET):
    manifest = manifest_from_json(state.load_manifest())
    full_meta = state.load_meta()
    meta = full_meta.get("lists", {})
    have_state = set(state.list_names())
    if names:
        listed = sorted(n for n in names if n in manifest or n in have_state)
        have_state &= set(names)
    else:
        listed = sorted(set(manifest) | have_state)
    msgs = load_messages(state, sorted(have_state))
    log("loaded %d messages from %d lists" % (len(msgs), len(have_state)))

    entities = build_entities(msgs)
    for e in entities:
        finish_entity(e)
    link_versions([e for e in entities if e.kind == "pr"])
    for e in entities:
        compute_status(e, now)
    numbers = state.load_numbers()
    assign_numbers(entities, msgs, numbers, prune=not names)
    if save_numbers:
        state.save_numbers(numbers)

    bfm = full_meta.get("backfill", {})
    bf_days = bfm.get("days") or 0
    window = effective_retention(bf_days)

    tmp = Path(str(out) + ".new")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    data = tmp / "data"

    def write(rel, obj, gz=False):
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip_bytes(dumps(obj)) if gz else dumps(obj))
        return path.stat().st_size

    # patch series: only those with a stored diff are shown. Choose them (whole series) within the size budget.
    reader = DiffReader(state, log=log)
    discussions = [e for e in entities if e.kind == "issue"]
    cands, have_by, ent_by = [], {}, {}
    for e in entities:
        if e.kind != "pr" or e.ghost or not e.patches:
            continue
        have = [m for m in e.patches if reader.has(m)]
        if have:
            have_by[id(e)], ent_by[id(e)] = have, e
            d_est, b_est = est_series(e, have)
            cands.append((e.home, e.state, e.updated, int((d_est + b_est) * scale), id(e)))
    per_list = {}
    for e in discussions:
        for l in e.lists:
            per_list.setdefault(l, []).append(e)
    keep_ids, list_keep = set(), {}
    for l, es in per_list.items():
        es.sort(key=lambda e: (-e.opened, e.head))
        list_keep[l] = {id(e) for e in es[:max_entities]}
        keep_ids.update(list_keep[l])
    shown_disc = [e for e in discussions if id(e) in keep_ids]
    disc_cost = sum(LIGHT_PER_ENTITY + (sum(len(m.b) + 60 for m in e.members) * GZ_BODY) for e in shown_disc) * scale
    budget = cap - 600_000 - int(disc_cost) - shrink
    if diff_budget is not None:
        budget = min(budget, diff_budget)
    chosen = pick(cands, max(0, budget))
    evicted = len(cands) - len(chosen)

    diffs_by, shown_prs, heavy_bytes, no_files, size_by_list = {}, [], 0, 0, {}
    order = sorted((ent_by[k] for k in chosen), key=lambda e: (have_by[id(e)][0].dl, have_by[id(e)][0].t))
    for e in order:
        got = {m.mid: reader.get(m) for m in have_by[id(e)]}
        capped = cap_series([got.get(m.mid) for m in e.patches], SERIES_CAP)
        got = {m.mid: d for m, d in zip(e.patches, capped) if d is not None}
        if not any(d["f"] for d in got.values()):
            no_files += 1
            continue
        diffs_by[id(e)] = got
        shown_prs.append(e)
        sz = write("diffs/%s/%d.json.gz" % (e.home, e.n), {"p": got}, gz=True)
        sz += write("bodies/%s/%d.json.gz" % (e.home, e.n), bodies_out(thread_order(e)[1]), gz=True)
        heavy_bytes += sz
        size_by_list[e.home] = size_by_list.get(e.home, 0) + sz
    shown_ids = {id(e) for e in shown_prs}
    est_heavy = sum(int(c[3]) for c in cands if c[4] in chosen) - LIGHT_PER_ENTITY * len(chosen)
    for e in shown_disc:
        sz = write("bodies/%s/%d.json.gz" % (e.home, e.n), bodies_out(thread_order(e)[1]), gz=True)
        heavy_bytes += sz
        est_heavy += int(sum(len(m.b) + 60 for m in e.members) * GZ_BODY * scale)
        size_by_list[e.home] = size_by_list.get(e.home, 0) + sz
    published = shown_prs + shown_disc
    trimmed = len(discussions) - len(shown_disc)
    log("showing %d patch series (%d left out for size, %d without files) and %d discussions" % (
        len(shown_prs), evicted, no_files, len(shown_disc)))

    def kept(e, l):
        return e.kind == "pr" or id(e) in list_keep.get(l, ())

    # per list aggregates, from the messages that are shown
    day0 = (now // DAY) * DAY - (window - 1) * DAY
    agg = {}
    shown_msgs = {}
    for e in published:
        for m in e.members:
            shown_msgs[m.mid] = m
            patch = m.subj.is_patch_message and not m.subj.is_cover
            review = any(t[0] in REVIEW_KINDS for t in m.tr) and not m.subj.is_patch_message
            for l in m.lists:
                if not kept(e, l):
                    continue
                a = agg.setdefault(l, {"days": {}, "auth": {}, "last": 0, "first": m.t, "msgs": 0, "m30": 0})
                a["msgs"] += 1
                a["last"] = max(a["last"], m.t)
                a["first"] = min(a["first"], m.t)
                di = (m.t - day0) // DAY
                if 0 <= di < window:
                    d = a["days"].setdefault(di, [0, set()])
                    d[0] += 1
                    d[1].add(m.h)
                if now - m.t <= SPARK_DAYS * DAY:
                    a["m30"] += 1
                c = a["auth"].setdefault(m.h, {"h": m.h, "name": m.a, "m": 0, "p": 0, "r": 0, "v": 0, "t": 0})
                c["m"] += 1
                if m.t >= c["t"]:
                    c["t"], c["name"] = m.t, m.a
                if patch:
                    c["p"] += 1
                elif not m.subj.is_patch_message:
                    c["r"] += 1
                if review:
                    c["v"] += 1

    rows, events_by_list, all_events, totals_by_list = {}, {}, [], {}
    for e in published:
        evs = make_events(e)
        all_events.extend(evs)
        for l in e.lists:
            if not kept(e, l):
                continue
            rows.setdefault(l, []).append(make_row(e, l))
            events_by_list.setdefault(l, []).extend(evs)
            totals_by_list[l] = totals_by_list.get(l, 0) + 1

    hz = horizon_ts(now, bf_days) if bf_days else 0
    bf_done = 0
    colors = group_colors()
    lists_json = []
    shard_max = 0
    waiting = sum(len(m.get("cq", [])) for m in meta.values())
    for name in listed:
        info = manifest.get(name)
        st = meta.get(name, {})
        a = agg.get(name)
        desc = info.description if info else ""
        r = sorted(rows.get(name, []), key=lambda x: (-x["o"], -x["n"]))
        counts = {"pr": {"open": 0, "merged": 0, "closed": 0}, "issue": {"open": 0, "merged": 0, "closed": 0}}
        labels = {"pr": {}, "issue": {}}
        buckets = {}
        for row in r:
            s = state_of(row["l"])
            counts[row["k"]][s] += 1
            labels[row["k"]][row["l"]] = labels[row["k"]].get(row["l"], 0) + 1
            buckets.setdefault((row["k"], s), []).append(row)
        pages, sz = {}, 0
        for (k, s), items in buckets.items():
            chunks = [items[i:i + PAGE_SIZE] for i in range(0, len(items), PAGE_SIZE)]
            pages["%s-%s" % (k, s)] = len(chunks)
            for i, chunk in enumerate(chunks):
                sz += write("lists/%s/%s-%s-%d.json" % (name, k, s, i), {"rows": chunk})
        days = a["days"] if a else {}
        daily = {"start": day0, "msgs": [days.get(i, [0, ()])[0] for i in range(window)],
                 "authors": [len(days.get(i, [0, ()])[1]) for i in range(window)]}
        contributors = sorted(a["auth"].values(), key=lambda c: (-c["m"], c["name"]))[:TOP_CONTRIBUTORS] if a else []
        ev = sorted(events_by_list.get(name, []), key=lambda x: -x["ts"])[:60]
        fresh = st.get("checked") and now - st["checked"] <= STALE_AFTER
        if name not in have_state and not st.get("fetched"):
            crawl = "pending"
        elif st.get("err") or not fresh:
            crawl = "stale"
        else:
            crawl = "ok"
        group = group_for(name)
        bf = st.get("bf") or {}
        hc = None
        if bf and bf_days:
            finished = is_done(bf, hz)
            hc = ({"": 1, "cap": 2, "fill": 3}[stop_reason(bf)]) if finished else 0
            bf_done += finished
        hs = (history_start(bf, hz) if bf and bf_days else 0) or (a["first"] if a else 0)
        last = (a["last"] if a else 0) or (info.modified if info else 0)
        lm = {
            "name": name, "desc": desc, "g": group, "gc": colors[group], "counts": counts, "labels": labels,
            "pages": pages, "pagesize": PAGE_SIZE, "daily": daily, "contributors": contributors,
            "events": ev, "crawl": crawl, "checked": st.get("checked", 0), "err": st.get("err", ""),
            "last": last, "msgs": a["msgs"] if a else 0, "threads_total": totals_by_list.get(name, 0),
            "epochs": info.epochs if info else [], "window_days": window, "hs": hs,
        }
        if hc is not None:
            lm["hc"] = hc
        sz += write("lists/%s/meta.json" % name, lm)
        size_by_list[name] = size_by_list.get(name, 0) + sz
        lists_json.append({
            "n": name, "d": desc, "g": group, "sp": daily["msgs"][-SPARK_DAYS:], "open": counts["pr"]["open"],
            "disc": counts["issue"]["open"], "la": last, "m30": a["m30"] if a else 0,
            "st": crawl, "tot": a["msgs"] if a else 0, "hs": hs,
        })
        if hc is not None:
            lists_json[-1]["hc"] = hc

    # thread detail (no message text, see bodies/), sharded by number within the home list
    shards = {}
    for e in published:
        shards.setdefault((e.home, e.n // SHARD), {})[str(e.n)] = make_detail(
            e, msgs, diffs_by.get(id(e)) if e.kind == "pr" else None, shown_ids, now)
    for (home, idx), content in shards.items():
        sz = write("threads/%s/%d.json.gz" % (home, idx), content, gz=True)
        shard_max = max(shard_max, sz)
        size_by_list[home] = size_by_list.get(home, 0) + sz

    # recent activity: newest first, at most 8 per list so lkml cannot drown everything
    recent, per, seen = [], {}, set()
    for ev in sorted(all_events, key=lambda x: -x["ts"]):
        key = (ev["hl"], ev["n"], ev["ty"])
        if key in seen or per.get(ev["hl"], 0) >= 8:
            continue
        seen.add(key)
        per[ev["hl"]] = per.get(ev["hl"], 0) + 1
        recent.append(ev)
        if len(recent) >= 80:
            break
    write("recent.json", {"generated": now, "events": recent})

    search_bytes = 0
    docs = [{"n": e.n, "l": e.home, "k": e.kind, "t": e.title, "a": e.author_name, "ah": e.author, "o": e.opened,
             "u": e.updated, "lb": e.label, "c": len(e.comments), "v": e.version if e.kind == "pr" else 0,
             "pt": [t for t in dict.fromkeys(m.subj.title or m.subj.raw for m in e.patches) if t != e.title],
             "x": sorted(e.lists - {e.home}), "tg": e.tags} for e in published]
    for rel, obj in build_index(docs, sorted({d["l"] for d in docs}), list(LABELS)).items():
        search_bytes += write("search/" + rel, obj)

    profiles = _profiles(published, agg, now)
    shard_files = {}
    for h, p in profiles.items():
        shard_files.setdefault(h[:2], {})[h] = p
    for pre, content in shard_files.items():
        write("authors/%s.json" % pre, content)

    pending = sum(1 for l in lists_json if l["st"] == "pending")
    stale = sum(1 for l in lists_json if l["st"] == "stale")
    rebuild = bfm.get("rebuild")
    write("lists.json", {
        "generated": now, "lists": lists_json, "groups": colors, "window_days": window,
        "totals": {"lists": len(lists_json), "pending": pending, "stale": stale,
                   "messages": len(shown_msgs), "threads": len(published)},
        "backfill": {"days": bf_days, "horizon": hz, "done": bf_done, "lists": len(lists_json),
                     "requests": bfm.get("requests", 0), "rebuild": 1 if rebuild and bf_done < len(lists_json) else 0,
                     "waiting": waiting},
    })

    _write_shell(tmp, SITE_DIR, lists_json, base, now)
    total = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file())
    if Path(out).exists():
        shutil.rmtree(out)
    os.replace(tmp, out)
    return {"lists": len(lists_json), "bytes": total, "messages": len(shown_msgs), "threads": len(published),
            "size_by_list": size_by_list, "max_shard_bytes": shard_max, "profiles": len(profiles),
            "pending": pending, "stale": stale, "trimmed": trimmed, "heavy_bytes": heavy_bytes,
            "est_heavy": est_heavy, "diff_bytes": heavy_bytes, "diff_series": len(shown_prs), "diff_evicted": evicted,
            "series": len(shown_prs), "series_left_out": evicted, "discussions": len(shown_disc),
            "search_bytes": search_bytes, "search_docs": len(docs)}


def _profiles(entities, agg, now):
    """Bounded set of contributor profiles from the messages that are shown: recently active authors plus every
    list's top authors."""
    recent_count, names, per_list = {}, {}, {}
    for e in entities:
        for m in e.members:
            if not m.h:
                continue
            if m.h not in names or m.t >= names[m.h][0]:
                names[m.h] = (m.t, m.a)
            if now - m.t <= 30 * DAY:
                recent_count[m.h] = recent_count.get(m.h, 0) + 1
            pl = per_list.setdefault(m.h, {})
            for l in m.lists:
                pl[l] = pl.get(l, 0) + 1
    keep = set(sorted(recent_count, key=lambda h: -recent_count[h])[:MAX_AUTHOR_PROFILES])
    for a in agg.values():
        keep.update(sorted(a["auth"], key=lambda h: -a["auth"][h]["m"])[:TOP_CONTRIBUTORS])
    items, replies = {}, {}
    for e in sorted(entities, key=lambda e: -e.opened):
        row = {"n": e.n, "hl": e.home, "k": e.kind, "t": e.title[:160], "o": e.opened, "l": e.label}
        if e.author in keep and not e.ghost and not e.partial and len(items.setdefault(e.author, [])) < 30:
            items[e.author].append(row)
        seen = set()
        for c in e.comments:
            if c.h in keep and c.h != e.author and c.h not in seen:
                seen.add(c.h)
                lst = replies.setdefault(c.h, [])
                if len(lst) < 15:
                    lst.append(dict(row, o=c.t))
    out = {}
    for h in keep:
        if h not in names:
            continue
        pl = per_list.get(h, {})
        out[h] = {
            "name": names[h][1], "msgs": sum(pl.values()),
            "lists": dict(sorted(pl.items(), key=lambda kv: -kv[1])[:10]),
            "items": items.get(h, []), "replies": sorted(replies.get(h, []), key=lambda r: -r["o"]),
        }
    return out


def _write_shell(tmp, site, lists_json, base, now):
    shutil.copytree(site, tmp, dirs_exist_ok=True)
    index = (tmp / "index.html").read_text("utf-8")
    top = sorted(lists_json, key=lambda l: -l["la"])
    rows = "\n".join(
        '<li><a href="https://lore.kernel.org/%s/">%s</a> - %s</li>'
        % (html.escape(l["n"], quote=True), html.escape(l["n"]), html.escape(l["d"]))
        for l in top
    )
    noscript = (
        '<noscript><div class="container"><h1>LoreHub</h1>'
        "<p>LoreHub needs JavaScript to render the pull-request style view. Without it, these are the "
        "mailing lists it covers, linking to their canonical archive on lore.kernel.org.</p><ul>\n"
        + rows + "\n</ul></div></noscript>"
    )
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    index = index.replace("<!--LOREHUB:NOSCRIPT-->", noscript).replace("<!--LOREHUB:GENERATED-->", html.escape(stamp))
    (tmp / "index.html").write_text(index, "utf-8")
    base_js = json.dumps(base if base.startswith("/") else "/" + base).replace("</", "<\\/")
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>LoreHub</title>'
        '<meta name="robots" content="noindex"></head><body>'
        '<p>Redirecting to <a href="./">LoreHub</a>...</p><script>'
        "(function(){var b=%s;if(b.slice(-1)!=='/')b+='/';var p=location.pathname;"
        "var rest=p.indexOf(b)===0?p.slice(b.length):p.replace(/^\\//,'');"
        "rest=rest.replace(/index\\.html$/,'').replace(/\\/$/,'');"
        "location.replace(b+(rest?'#/'+rest:''));})();</script></body></html>"
    ) % base_js
    (tmp / "404.html").write_text(page, "utf-8")
    (tmp / ".nojekyll").write_text("", "utf-8")
