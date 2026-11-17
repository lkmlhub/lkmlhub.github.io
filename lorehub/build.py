"""Turn accumulated state into the static site: JSON data plus the HTML/JS/CSS shell."""

import html
import json
import os
import shutil
import time
from pathlib import Path

from .crawl import manifest_from_json
from .diffshards import SERIES_CAP, cap_series, est_size, pick
from .feed import lore_url
from .groups import group_colors, group_for
from .model import (
    DAY,
    REVIEW_KINDS,
    build_entities,
    compute_status,
    finish_entity,
    link_versions,
    load_messages,
    review_summary,
    state_of,
)
from .state import RETENTION_DAYS, StateError, day_of, dumps

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
PAGE_SIZE = 300
SHARD = 32
TOP_CONTRIBUTORS = 50
MAX_AUTHOR_PROFILES = 4000
MAX_ENTITIES = 300  # newest threads published per list
SPARK_DAYS = 30
STALE_AFTER = 6 * 3600
DIFF_BUDGET = 80_000_000  # bytes of published diff JSON for the whole site


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
        if e.pull:
            r["pu"] = 1
        rv = []
        for t in review_summary(e):
            if t["kind"] in REVIEW_KINDS and not t["carried"] and len(rv) < 4:
                rv.append([t["h"], t["kind"], t["name"]])
        if rv:
            r["rv"] = rv
        if e.superseded_by is not None:
            r["sb"] = e.superseded_by.version
    if e.tags:
        r["tg"] = e.tags
    others = sorted(e.lists - {list_name})
    if others:
        r["x"] = others[:5]
    if e.partial:
        r["pa"] = 1
    return r


def body_out(m, cutoff, limit=None):
    if not m.b or m.t < cutoff:
        return ""
    return m.b if limit is None or len(m.b) <= limit else m.b[:limit].rstrip() + "\n[truncated]"


def msg_out(m, home, cutoff, parent_name="", limit=None):
    o = {"a": m.a, "ah": m.h, "o": m.t, "mid": m.mid}
    ml = home if home in m.lists else sorted(m.lists)[0]
    if ml != home:
        o["ml"] = ml
    b = body_out(m, cutoff, limit)
    if b:
        o["b"] = b
    if m.tr:
        o["tr"] = [t for t in m.tr if t[0] != "codev"]
    if m.ds:
        o["ds"] = m.ds
    if m.f:
        o["f"] = m.f
    if parent_name:
        o["re"] = parent_name
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


def make_detail(e, msgs, cutoff, diffs=None):
    by_mid = {m.mid: m for m in e.members}
    first = e.cover or (e.patches[0] if e.patches else None)
    d = {"k": e.kind, "n": e.n, "home": e.home, "t": e.title, "a": e.author_name, "ah": e.author,
         "o": e.opened, "u": e.updated, "l": e.label, "s": e.state, "lists": sorted(e.lists),
         "tg": e.tags, "c": len(e.comments)}
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
        d["cover"] = msg_out(first, e.home, cutoff)
        if e.cover is None:
            d["cover"]["single"] = 1
    if e.kind == "pr":
        d["patches"] = [
            dict(msg_out(m, e.home, cutoff, limit=1800), i=m.subj.index or 1, t=m.subj.title or m.subj.raw)
            for m in e.patches
        ]
        if e.patches and not e.pull:
            fc, fa, fd = diff_summary(e, diffs)
            if fc:
                d["fc"], d["fa"], d["fd"] = fc, fa, fd
            if diffs:
                d["df"] = 1
    d["cm"] = []
    for m in e.comments:
        par = msgs.get(m.p)
        d["cm"].append(msg_out(m, e.home, cutoff, par.a if par and par.mid in by_mid else ""))
    d["rv"] = review_summary(e)
    if len(e.versions) > 1:
        d["vs"] = [
            {"n": v.n, "home": v.home, "v": v.version, "o": v.opened, "l": v.label, "rs": int(v.resend)}
            for v in e.versions
        ]
    if e.superseded_by is not None:
        s = e.superseded_by
        d["sb"] = {"n": s.n, "home": s.home, "v": s.version}
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


def build_site(state, out, now, names=None, base="/", max_site_mb=300, log=print, max_entities=MAX_ENTITIES,
               diff_budget=DIFF_BUDGET):
    cap = int(max_site_mb * 1e6)
    budget = int(cap * 0.35)
    report = None
    for attempt in range(4):
        report = _build_once(state, out, now, names, base, budget, log, save_numbers=(attempt == 0),
                             max_entities=max_entities, diff_budget=diff_budget)
        if report["bytes"] <= cap:
            break
        log("site is %.1f MB, over the %.0f MB cap: lowering the body budget" % (report["bytes"] / 1e6, max_site_mb))
        budget //= 2
        max_entities //= 2
        diff_budget //= 2
    report["over_cap"] = report["bytes"] > cap
    return report


def select_diffs(state, published, diff_budget, log):
    """Pick the series whose diffs are published and load them: {id(entity): {message-id: diff}}."""
    days = {}
    cands, have_by = [], {}
    for e in published:
        if e.kind != "pr" or e.pull or e.ghost:
            continue
        have = []
        for m in e.patches:
            if m.dz is None or not m.dl:
                continue
            if m.dl not in days:
                days[m.dl] = set(state.diff_days(m.dl))
            if day_of(m.t) in days[m.dl]:
                have.append(m)
        if have:
            have_by[id(e)] = have
            cands.append((e.home, e.state, e.updated, est_size(sum(m.dz for m in have), len(have)), id(e)))
    chosen = pick(cands, diff_budget)
    need = {}
    for e in published:
        if id(e) in chosen:
            for m in have_by[id(e)]:
                need.setdefault((m.dl, day_of(m.t)), set()).add(m.mid)
    store = {}
    for (name, day), mids in sorted(need.items()):
        try:
            data = state.load_diff_day(name, day)
        except StateError as err:
            log("skipping unreadable diffs: %s" % err)
            continue
        for mid in mids:
            if mid in data:
                store[mid] = data[mid]
    out = {}
    for e in published:
        if id(e) in chosen:
            capped = cap_series([store.get(m.mid) for m in e.patches], SERIES_CAP)
            got = {m.mid: d for m, d in zip(e.patches, capped) if d is not None}
            if got:
                out[id(e)] = got
    return out, len(cands) - len(chosen)


def _build_once(state, out, now, names, base, body_budget, log, save_numbers=True, max_entities=MAX_ENTITIES,
                diff_budget=DIFF_BUDGET):
    manifest = manifest_from_json(state.load_manifest())
    meta = state.load_meta().get("lists", {})
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

    # publish at most max_entities newest threads per list (a thread is kept if any of its lists keeps it)
    per_list = {}
    for e in entities:
        for l in e.lists:
            per_list.setdefault(l, []).append(e)
    keep_ids, totals_by_list, list_keep = set(), {}, {}
    for l, es in per_list.items():
        es.sort(key=lambda e: (-e.opened, e.head))
        totals_by_list[l] = len(es)
        list_keep[l] = {id(e) for e in es[:max_entities]}
        keep_ids.update(list_keep[l])
    published = [e for e in entities if id(e) in keep_ids]
    trimmed = len(entities) - len(published)

    # bodies: keep the newest ones that fit in the size budget
    total, cutoff = 0, 0
    for t, n in sorted(((m.t, len(m.b)) for m in msgs.values() if m.b), reverse=True):
        total += n + 60
        if total > body_budget:
            cutoff = t + 1
            break

    diffs_by, diffs_evicted = select_diffs(state, published, diff_budget, log)

    tmp = Path(str(out) + ".new")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    data = tmp / "data"

    def write(rel, obj):
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(dumps(obj))
        return path.stat().st_size

    # per list aggregates, from the messages each list itself holds
    day0 = (now // DAY) * DAY - (RETENTION_DAYS - 1) * DAY
    agg = {}
    for m in msgs.values():
        patch = m.subj.is_patch_message and not m.subj.is_cover
        review = any(t[0] in REVIEW_KINDS for t in m.tr) and not m.subj.is_patch_message
        for l in m.lists:
            a = agg.setdefault(l, {"days": {}, "auth": {}, "last": 0, "msgs": 0, "m30": 0})
            a["msgs"] += 1
            a["last"] = max(a["last"], m.t)
            di = (m.t - day0) // DAY
            if 0 <= di < RETENTION_DAYS:
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

    rows, events_by_list, all_events = {}, {}, []
    for e in published:
        evs = make_events(e)
        all_events.extend(evs)
        for l in e.lists:
            if id(e) not in list_keep[l]:
                continue
            rows.setdefault(l, []).append(make_row(e, l))
            events_by_list.setdefault(l, []).extend(evs)

    colors = group_colors()
    lists_json = []
    size_by_list = {}
    shard_max = 0
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
        daily = {"start": day0, "msgs": [days.get(i, [0, ()])[0] for i in range(RETENTION_DAYS)],
                 "authors": [len(days.get(i, [0, ()])[1]) for i in range(RETENTION_DAYS)]}
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
        last = (a["last"] if a else 0) or (info.modified if info else 0)
        lm = {
            "name": name, "desc": desc, "g": group, "gc": colors[group], "counts": counts, "labels": labels,
            "pages": pages, "pagesize": PAGE_SIZE, "daily": daily, "contributors": contributors,
            "events": ev, "crawl": crawl, "checked": st.get("checked", 0), "err": st.get("err", ""),
            "last": last, "msgs": a["msgs"] if a else 0, "threads_total": totals_by_list.get(name, 0),
            "epochs": info.epochs if info else [], "window_days": RETENTION_DAYS,
        }
        sz += write("lists/%s/meta.json" % name, lm)
        size_by_list[name] = sz
        lists_json.append({
            "n": name, "d": desc, "g": group, "sp": daily["msgs"][-SPARK_DAYS:], "open": counts["pr"]["open"],
            "disc": counts["issue"]["open"], "la": last, "m30": a["m30"] if a else 0,
            "st": crawl, "tot": a["msgs"] if a else 0,
        })

    # diffs: one lazily loaded file per series, only fetched when the Files tab is opened
    diff_bytes = 0
    for e in published:
        got = diffs_by.get(id(e))
        if got:
            sz = write("diffs/%s/%d.json" % (e.home, e.n), {"p": got})
            diff_bytes += sz
            size_by_list[e.home] = size_by_list.get(e.home, 0) + sz

    # thread detail, sharded by number within the home list
    shards = {}
    for e in published:
        shards.setdefault((e.home, e.n // SHARD), {})[str(e.n)] = make_detail(e, msgs, cutoff, diffs_by.get(id(e)))
    for (home, idx), content in shards.items():
        sz = write("threads/%s/%d.json" % (home, idx), content)
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

    profiles = _profiles(published, msgs, agg, now)
    shard_files = {}
    for h, p in profiles.items():
        shard_files.setdefault(h[:2], {})[h] = p
    for pre, content in shard_files.items():
        write("authors/%s.json" % pre, content)

    pending = sum(1 for l in lists_json if l["st"] == "pending")
    stale = sum(1 for l in lists_json if l["st"] == "stale")
    write("lists.json", {
        "generated": now, "lists": lists_json, "groups": colors, "window_days": RETENTION_DAYS,
        "totals": {"lists": len(lists_json), "pending": pending, "stale": stale,
                   "messages": len(msgs), "threads": len(published)},
    })

    _write_shell(tmp, SITE_DIR, lists_json, base, now)
    total = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file())
    if Path(out).exists():
        shutil.rmtree(out)
    os.replace(tmp, out)
    return {"lists": len(lists_json), "bytes": total, "messages": len(msgs), "threads": len(published),
            "size_by_list": size_by_list, "max_shard_bytes": shard_max, "cutoff": cutoff,
            "profiles": len(profiles), "pending": pending, "stale": stale, "trimmed": trimmed,
            "diff_bytes": diff_bytes, "diff_series": len(diffs_by), "diff_evicted": diffs_evicted}


def _profiles(entities, msgs, agg, now):
    """Bounded set of contributor profiles: recently active authors plus every list's top authors."""
    recent_count, names, per_list = {}, {}, {}
    for m in msgs.values():
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
