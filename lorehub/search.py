"""Static search index: sharded token postings plus sharded compact documents.

Files (under data/search/):

    index.json       {"v": 1, "docs": N, "doc_shard": 128, "lists": [...], "labels": [...],
                      "split": {"ne": ["nea", ...]}}   (token shards that were split by a third letter)
    t/<key>.json     {token: [first id, delta, delta, ...]}   token postings, ids ascending
    d/<k>.json       [[n, list, kind, title, author, author hash, opened, updated, label, comments, version], ...]

A document is a published thread (pull request or discussion). Ids are assigned newest activity first,
so ascending ids are a newest-first result order and a result page touches few document shards. A
token shard is named by the first two letters of its tokens; a query word is matched as a prefix
inside the shard of its first two letters, so a query loads one or two small files. The tokenizer
must stay identical to the one in site/assets/searchcore.js (tests compare them).
"""

import re
import unicodedata

from .state import dumps

DOC_SHARD = 128
MAX_TOKEN_SHARD = 300_000  # bytes, larger shards are split by the third letter
MAX_DOC_TOKENS = 48
MAX_PATCH_TITLES = 8
MIN_TOKEN = 2

TOKEN_RE = re.compile(r"[^\W_]+")


def fold(text):
    """Lower case and strip accents so "Müller" matches "muller"."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def tokenize(text):
    return TOKEN_RE.findall(fold(text))


def doc_tokens(title, patch_titles, author, lists, tags):
    seen = []
    def add(text):
        for t in tokenize(text):
            if len(t) >= MIN_TOKEN and t not in seen:
                seen.append(t)
    add(title)
    add(author)
    for l in lists:
        add(l)
    for t in tags:
        add(t)
    for pt in patch_titles[:MAX_PATCH_TITLES]:
        add(pt)
    return seen[:MAX_DOC_TOKENS]


def shard_key(token, split=None):
    key = token[:2]
    if split and key in split and len(token) > 2:
        return token[:3]
    return key


def delta(ids):
    out, prev = [], 0
    for i in ids:
        out.append(i - prev)
        prev = i
    return out


def build_index(docs, lists, labels):
    """docs: [{"n","l" (home list),"k","t","a","ah","o","u","lb","c","v","pt" (patch titles),"x" (other lists),"tg"}].

    Returns {relative path: object} for every file of the index. Docs are re-ordered newest first.
    """
    docs = sorted(docs, key=lambda d: (-d["u"], d["l"], d["n"]))
    list_ids = {name: i for i, name in enumerate(lists)}
    label_ids = {name: i for i, name in enumerate(labels)}
    postings = {}
    packed = []
    for i, d in enumerate(docs):
        packed.append([d["n"], list_ids[d["l"]], 1 if d["k"] == "pr" else 0, d["t"][:200], d["a"][:60], d["ah"],
                       d["o"], d["u"], label_ids.get(d["lb"], 0), d["c"], d.get("v", 0)])
        for tok in doc_tokens(d["t"], d.get("pt", []), d["a"], [d["l"]] + list(d.get("x", [])), d.get("tg", [])):
            postings.setdefault(tok, []).append(i)
    files = {}
    by_key = {}
    for tok, ids in postings.items():
        by_key.setdefault(tok[:2], {})[tok] = delta(ids)
    split = {}
    for key, toks in sorted(by_key.items()):
        if len(dumps(toks)) <= MAX_TOKEN_SHARD:
            files["t/%s.json" % key_file(key)] = toks
            continue
        children = {}
        for tok, p in toks.items():
            children.setdefault(tok[:3] if len(tok) > 2 else key, {})[tok] = p
        split[key] = sorted(children)
        for ck, ctoks in children.items():
            files["t/%s.json" % key_file(ck)] = ctoks
    for s in range(0, len(packed), DOC_SHARD):
        files["d/%d.json" % (s // DOC_SHARD)] = packed[s:s + DOC_SHARD]
    files["index.json"] = {"v": 1, "docs": len(docs), "doc_shard": DOC_SHARD, "lists": lists, "labels": labels,
                           "split": split}
    return files


def key_file(key):
    """File name of a token shard. Letters outside a-z0-9 are hex encoded behind "_" (never in a token)."""
    if re.fullmatch(r"[a-z0-9]+", key):
        return key
    return "_" + key.encode("utf-8").hex()
