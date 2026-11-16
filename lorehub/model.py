"""Threads, patch series (revisions), versions and status labels from accumulated messages."""

from dataclasses import dataclass, field

from .parse import norm_title, parse_subject

DAY = 86400
STALE_DAYS = 60
SERIES_WINDOW = 2 * 3600
REVIEW_KINDS = ("reviewed", "acked", "tested", "nacked")

LABELS = ("open", "review", "reviewed", "acked", "tested", "changes", "superseded", "merged", "stale")
STATE_OF = {"merged": "merged", "superseded": "closed", "stale": "closed"}


def state_of(label):
    return STATE_OF.get(label, "open")


OPEN_LABELS = {"open", "review", "reviewed", "acked", "tested", "changes"}


@dataclass(slots=True)
class Msg:
    mid: str
    t: int
    a: str
    h: str
    p: str
    s: str
    b: str = ""
    f: str = ""
    tr: list = field(default_factory=list)
    ds: object = None
    lists: set = field(default_factory=set)
    subj: object = None
    top: str = ""


@dataclass
class Entity:
    """A revision (pull request) or a discussion (issue)."""
    kind: str  # pr or issue
    head: str
    members: list
    patches: list = field(default_factory=list)
    comments: list = field(default_factory=list)
    cover: object = None
    title: str = ""
    version: int = 1
    total: int = 0
    tags: list = field(default_factory=list)
    partial: bool = False
    ghost: bool = False
    lists: set = field(default_factory=set)
    author: str = ""
    author_name: str = ""
    opened: int = 0
    updated: int = 0
    home: str = ""
    n: int = 0
    label: str = "open"
    state: str = "open"
    superseded_by: object = None
    versions: list = field(default_factory=list)
    key: object = None
    root: str = ""  # earliest known message, may differ from head when the true head is missing
    rfc: bool = False
    resend: bool = False
    first_merged: int = 0
    pull: bool = False


def load_messages(state, names):
    """Merge the per-list states into one global map, recording every list a message is in."""
    msgs = {}
    for name in names:
        data = state.load_list(name)
        for mid, r in data["msgs"].items():
            m = msgs.get(mid)
            if m is None:
                m = msgs[mid] = Msg(
                    mid=mid, t=r["t"], a=r.get("a", ""), h=r.get("h", ""), p=r.get("p", ""),
                    s=r.get("s", ""), b=r.get("b", ""), f=r.get("f", ""), tr=r.get("tr", []),
                    ds=r.get("ds"),
                )
                m.subj = parse_subject(m.s)
            elif not m.b and r.get("b"):
                m.b = r["b"]
            m.lists.add(name)
    return msgs


def tops(msgs):
    """Annotate each message with the highest known ancestor."""
    for m in msgs.values():
        if m.top:
            continue
        chain = []
        cur = m
        seen = set()
        while True:
            if cur.top:
                top = cur.top
                break
            chain.append(cur)
            seen.add(cur.mid)
            par = msgs.get(cur.p) if cur.p else None
            if par is None or par.mid in seen:
                top = cur.mid
                break
            cur = par
        for c in chain:
            c.top = top


def components(msgs):
    """Group messages into threads. Siblings replying to the same missing parent share a thread."""
    tops(msgs)
    comps = {}
    for m in msgs.values():
        top = msgs[m.top]
        comps.setdefault(top.p or top.mid, []).append(m)
    return comps


def merge_unthreaded_series(comps, msgs):
    """Join components that are the pieces of one series sent without threading."""
    cands = {}
    for top, members in comps.items():
        t = msgs.get(top)
        if t is None:
            continue
        s = t.subj
        if s.is_patch_message and s.kind == "patch" and s.total and s.total > 1 and len(members) == 1:
            cands.setdefault((t.h, s.version, s.total), []).append(t)
    for group in cands.values():
        group.sort(key=lambda m: m.t)
        cluster = []
        for m in group + [None]:
            if cluster and (m is None or m.t - cluster[-1].t > SERIES_WINDOW):
                idx = {c.subj.index for c in cluster}
                if len(cluster) > 1 and len(idx) == len(cluster):
                    first = cluster[0]
                    for c in cluster[1:]:
                        comps[first.mid].extend(comps.pop(c.mid))
                        for x in comps[first.mid]:
                            x.top = first.mid
                cluster = []
            if m is not None:
                cluster.append(m)


def build_entities(msgs):
    """Split components into revisions (pull requests) and discussions (issues)."""
    comps = components(msgs)
    merge_unthreaded_series(comps, msgs)
    entities = []
    for key, members in comps.items():
        members.sort(key=lambda m: (m.t, m.mid))
        partial = key not in msgs  # the real first message is not in our data
        root = members[0] if partial else msgs[key]
        patch_msgs = [m for m in members if m.subj.is_patch_message]
        if not patch_msgs:
            if root.subj.kind in ("patch", "pull"):
                entities.append(_ghost(key, root, members))
            else:
                entities.append(_discussion(key, root, members, partial))
            continue
        groups = {}
        for m in patch_msgs:
            groups.setdefault((m.h, m.subj.version, m.subj.kind), []).append(m)
        owner = {}
        revs = []
        for (h, version, kind), pms in groups.items():
            rev = _revision(pms, kind)
            revs.append(rev)
            for m in pms:
                owner[m.mid] = rev
        loose = []
        for m in members:
            if m.mid in owner:
                continue
            cur, seen, target = m, set(), None
            while cur is not None and cur.mid not in seen:
                seen.add(cur.mid)
                cur = msgs.get(cur.p) if cur.p else None
                if cur is not None and cur.mid in owner:
                    target = owner[cur.mid]
                    break
            if target is None:
                loose.append(m)
            else:
                target.comments.append(m)
                target.members.append(m)
        if loose:
            has_root = not partial and any(m.mid == key for m in loose)
            entities.append(_discussion(key if has_root else loose[0].mid, root if has_root else loose[0], loose, not has_root))
        entities.extend(revs)
    return entities


def _common(e, members):
    e.opened = min(m.t for m in members)
    e.updated = max(m.t for m in members)
    return e


def _discussion(head, root, members, partial):
    s = root.subj
    e = Entity(kind="issue", head=head, root=root.mid, members=list(members), partial=partial)
    e.title = s.title or s.raw
    e.tags = _chips(s)
    if not partial:
        e.cover = root
        e.comments = [m for m in members if m.mid != root.mid]
        e.author, e.author_name = root.h, root.a
        e.lists = set(root.lists)
    else:
        e.comments = list(members)
        e.lists = set().union(*[m.lists for m in members])
    _common(e, members)
    return e


def _ghost(head, root, members):
    """Replies to a patch whose first message is older than our window."""
    s = root.subj
    e = Entity(kind="pr", head=head, root=root.mid, members=list(members), comments=list(members),
               partial=True, ghost=True)
    e.title = s.title or s.raw
    e.version = s.version
    e.total = s.total or 0
    e.tags = _chips(s)
    e.rfc, e.resend, e.pull = s.rfc, s.resend, s.kind == "pull"
    e.lists = set().union(*[m.lists for m in members])
    _common(e, members)
    return e


def _revision(pms, kind):
    pms = sorted(pms, key=lambda m: (m.t, m.mid))
    cover = next((m for m in pms if m.subj.index == 0), None)
    numbered = {}
    for m in pms:
        if m.subj.index and m is not cover:
            numbered[m.subj.index] = m  # a later copy (resend) replaces an earlier one
    unnumbered = [m for m in pms if m.subj.index is None]
    patches = sorted(numbered.values(), key=lambda m: m.subj.index) + unnumbered
    if cover is None and not patches:
        patches = [pms[0]]
    first = cover or (patches[0] if patches else pms[0])
    s = first.subj
    e = Entity(kind="pr", head=first.mid, root=first.mid, members=list(pms), patches=patches, cover=cover)
    e.title = s.title or s.raw
    e.version = s.version
    e.total = max([m.subj.total or 0 for m in pms] + [len(patches)])
    e.partial = e.total > 1 and len(numbered) < e.total
    e.tags = _chips(s)
    e.rfc = any(m.subj.rfc for m in pms)
    e.resend = any(m.subj.resend for m in pms)
    e.pull = kind == "pull"
    e.lists = set().union(*[m.lists for m in pms])
    e.author, e.author_name = first.h, first.a
    e.key = (first.h, norm_title(e.title))
    _common(e, pms)
    return e


def _chips(s):
    chips = []
    if s.rfc:
        chips.append("RFC")
    if s.resend:
        chips.append("RESEND")
    if s.kind in ("bug", "regression", "announce"):
        chips.append(s.kind.upper())
    chips.extend(s.trees[:2])
    return chips


def finish_entity(e):
    e.comments.sort(key=lambda m: (m.t, m.mid))
    _common(e, e.members)


def link_versions(revs):
    """Chain revisions of the same series (same author, same normalised title)."""
    by_key = {}
    for e in revs:
        if e.kind == "pr" and not e.ghost and e.key and e.key[1]:
            by_key.setdefault(e.key, []).append(e)
    chains = []
    for group in by_key.values():
        group.sort(key=lambda e: (e.version, e.opened))
        mine = []
        for e in group:
            attach = None
            if mine:
                last = mine[-1][-1]
                if e.version > last.version or (e.version == last.version and e.resend):
                    attach = mine[-1]
            if attach is None:
                mine.append([e])
            else:
                attach.append(e)
        chains.extend(mine)
    for chain in chains:
        if len(chain) < 2:
            continue
        for i, e in enumerate(chain):
            e.versions = chain
            if i + 1 < len(chain):
                e.superseded_by = chain[i + 1]
    return chains


def compute_status(e, now):
    """Heuristic label: merged > superseded > changes > reviewed > acked > tested > review > open."""
    if e.kind == "issue":
        e.label = "stale" if now - e.updated > STALE_DAYS * DAY else "open"
        e.state = state_of(e.label)
        return e
    others = [c for c in e.comments if c.h != e.author]
    merged = next((c for c in others if "A" in c.f), None)
    if merged is not None:
        e.label = "merged"
        e.first_merged = merged.t
    elif e.superseded_by is not None:
        e.label = "superseded"
    else:
        states = {}
        approvals = set()
        for c in others:
            kinds = {t[0] for t in c.tr if t[0] in REVIEW_KINDS and t[2] != e.author}
            if "nacked" in kinds or "N" in c.f:
                state = "changes"
            elif kinds & {"reviewed", "acked", "tested"}:
                state = "approve"
                approvals |= kinds
            elif "I" in c.f:
                state = "changes"
            else:
                state = "comment"
            if state == "comment" and states.get(c.h) in ("approve", "changes"):
                continue
            states[c.h] = state
        if "changes" in states.values():
            e.label = "changes"
        elif "reviewed" in approvals:
            e.label = "reviewed"
        elif "acked" in approvals:
            e.label = "acked"
        elif "tested" in approvals:
            e.label = "tested"
        elif others:
            e.label = "review"
        else:
            e.label = "open"
        if e.label in OPEN_LABELS and now - e.updated > STALE_DAYS * DAY:
            e.label = "stale"
    e.state = state_of(e.label)
    return e


def review_summary(e):
    """Trailers for the sidebar: list of dicts {kind, name, h, t, carried}."""
    out = {}
    for c in e.comments:
        if c.h == e.author:
            continue
        for kind, name, h in c.tr:
            if kind in ("signed", "codev"):
                continue
            if kind in REVIEW_KINDS and h == e.author:
                continue
            out.setdefault((kind, h), {"kind": kind, "name": name or c.a, "h": h, "t": c.t, "carried": False})
    for m in e.patches + ([e.cover] if e.cover else []):
        for kind, name, h in m.tr:
            if kind in REVIEW_KINDS and h == e.author:
                continue
            key = (kind, h)
            if key not in out:
                out[key] = {"kind": kind, "name": name, "h": h, "t": m.t,
                            "carried": kind in REVIEW_KINDS}
    order = {"reviewed": 0, "acked": 1, "tested": 2, "nacked": 3, "reported": 4, "suggested": 5,
             "signed": 6, "codev": 7}
    return sorted(out.values(), key=lambda r: (order.get(r["kind"], 9), r["t"]))
