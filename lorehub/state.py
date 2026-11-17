"""Accumulated per-list state: atomic writes, merging, pruning."""

import gzip
import io
import json
import os
import tempfile
import time
from pathlib import Path

RETENTION_DAYS = 120
BODY_DAYS = 60
MAX_MSGS = 10000  # newest messages kept per list, keeps the busiest lists bounded
MAX_BODY_MSGS = 300  # newest messages that keep a body
MAX_DIFF_MSGS = 300  # newest patch messages per list that keep a diff
DAY = 86400


FSYNC = True  # tests switch this off, fsync on a busy disk is slow


class StateError(Exception):
    pass


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            if FSYNC:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def gzip_bytes(raw):
    """Deterministic gzip (no timestamp, no name) so unchanged state gives no git diff."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0, filename="") as g:
        g.write(raw)
    return buf.getvalue()


class State:
    """Per list state, stored as one gzip JSON file per UTC day: lists/<name>/<YYYY-MM-DD>.json.gz.

    Old days never change, so a squashed push of the data branch only uploads today's files.
    """

    def __init__(self, root):
        self.root = Path(root)

    def list_dir(self, name):
        return self.root / "lists" / name

    def segments(self, name):
        d = self.list_dir(name)
        return sorted(d.glob("*.json.gz")) if d.is_dir() else []

    def load_list(self, name):
        msgs = {}
        for path in self.segments(name):
            try:
                seg = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                msgs.update(seg["msgs"])
            except (OSError, EOFError, ValueError, KeyError, TypeError) as e:
                raise StateError("corrupt state for %s (%s): %s" % (name, path.name, e))
        data = {"v": 2, "name": name, "msgs": msgs}
        ts = [r["t"] for r in msgs.values()]
        data["last_seen"] = max(ts) if ts else 0
        return data

    def save_list(self, name, data):
        days = {}
        for mid, rec in data["msgs"].items():
            day = time.strftime("%Y-%m-%d", time.gmtime(rec["t"]))
            days.setdefault(day, {})[mid] = rec
        d = self.list_dir(name)
        keep = set()
        for day, msgs in days.items():
            path = d / (day + ".json.gz")
            keep.add(path.name)
            blob = gzip_bytes(dumps({"v": 2, "msgs": msgs}))
            if not path.exists() or path.read_bytes() != blob:
                atomic_write(path, blob)
        for path in self.segments(name):
            if path.name not in keep:
                path.unlink()

    # ---- diffs: one gzip JSON file per list and UTC day, state/diffs/<list>/<YYYY-MM-DD>.json.gz

    def diff_dir(self, name):
        return self.root / "diffs" / name

    def diff_days(self, name):
        d = self.diff_dir(name)
        return sorted(p.name[:-8] for p in d.glob("*.json.gz")) if d.is_dir() else []

    def load_diff_day(self, name, day):
        path = self.diff_dir(name) / (day + ".json.gz")
        if not path.exists():
            return {}
        try:
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))["d"]
        except (OSError, EOFError, ValueError, KeyError, TypeError) as e:
            raise StateError("corrupt diff state for %s (%s): %s" % (name, day, e))

    def save_diff_day(self, name, day, diffs):
        path = self.diff_dir(name) / (day + ".json.gz")
        if not diffs:
            if path.exists():
                path.unlink()
            return
        blob = gzip_bytes(dumps({"v": 1, "d": diffs}))
        if not path.exists() or path.read_bytes() != blob:
            atomic_write(path, blob)

    def diff_bytes(self):
        d = self.root / "diffs"
        return sum(p.stat().st_size for p in d.rglob("*.json.gz")) if d.is_dir() else 0

    def snapshot(self, name):
        return {p.name: p.read_bytes() for p in self.segments(name)}

    def list_names(self):
        d = self.root / "lists"
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and any(p.glob("*.json.gz")))

    def _json(self, fname, default):
        path = self.root / fname
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return default

    def _save_json(self, fname, obj):
        atomic_write(self.root / fname, dumps(obj))

    def load_meta(self):
        return self._json("meta.json", {"lists": {}})

    def save_meta(self, meta):
        self._save_json("meta.json", meta)

    def load_manifest(self):
        return self._json("manifest.json", {})

    def save_manifest(self, manifest):
        self._save_json("manifest.json", manifest)

    def load_numbers(self):
        return self._json("numbers.json", {"next": {}, "keys": {}})

    def save_numbers(self, numbers):
        self._save_json("numbers.json", numbers)


def merge_records(data, records, now, retention_days=RETENTION_DAYS, body_days=BODY_DAYS):
    """Merge {mid: record} into a list state dict in place and prune. Returns number added."""
    msgs = data["msgs"]
    horizon = now - retention_days * DAY
    added = 0
    for mid, rec in records.items():
        if rec["t"] < horizon or rec["t"] <= 0:
            continue
        old = msgs.get(mid)
        if old is None:
            msgs[mid] = rec
            added += 1
        else:
            for k, v in rec.items():
                if k not in old or (k == "b" and not old.get("b")):
                    old[k] = v
    prune(data, now, retention_days, body_days)
    return added


def prune(data, now, retention_days=RETENTION_DAYS, body_days=BODY_DAYS,
          max_msgs=MAX_MSGS, max_body_msgs=MAX_BODY_MSGS):
    msgs = data["msgs"]
    horizon = now - retention_days * DAY
    body_horizon = now - body_days * DAY
    for mid in [m for m, r in msgs.items() if r["t"] < horizon]:
        del msgs[mid]
    if len(msgs) > max_msgs:
        newest = sorted(msgs.items(), key=lambda kv: kv[1]["t"], reverse=True)
        for mid, _ in newest[max_msgs:]:
            del msgs[mid]
    with_body = sorted((r["t"], mid) for mid, r in msgs.items() if "b" in r)
    keep = {mid for _, mid in with_body[-max_body_msgs:]}
    for mid, r in msgs.items():
        if "b" in r and (r["t"] < body_horizon or mid not in keep):
            del r["b"]
    ts = [r["t"] for r in msgs.values()]
    data["last_seen"] = max(ts) if ts else 0


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def apply_diffs(state, name, data, new_diffs, keep=MAX_DIFF_MSGS):
    """Store new diffs for a list and keep only the newest `keep` patch diffs.

    A record has "dz" (kept diff bytes) exactly when its diff is stored. Records that fall out of the
    newest `keep` lose "dz" and their diff is removed from the day files, so the data branch stays bounded.
    Only the day files that actually change are rewritten.
    """
    from .diff import kept_bytes  # local import: diff.py has no state dependency

    msgs = data["msgs"]
    new = {m: d for m, d in new_diffs.items() if m in msgs}
    cands = sorted(((r["t"], m) for m, r in msgs.items() if "dz" in r or m in new), reverse=True)
    keep_set = {m for _, m in cands[:keep]}
    touched = set()
    for _, mid in cands[keep:]:
        if msgs[mid].pop("dz", None) is not None or mid in new:
            touched.add(day_of(msgs[mid]["t"]))
        new.pop(mid, None)
    for mid, d in new.items():
        msgs[mid]["dz"] = kept_bytes(d)
        touched.add(day_of(msgs[mid]["t"]))
    wanted = {}
    for mid in keep_set:
        wanted.setdefault(day_of(msgs[mid]["t"]), set()).add(mid)
    for day in set(state.diff_days(name)) | touched:
        mids = wanted.get(day, set())
        if not mids:
            state.save_diff_day(name, day, {})
            continue
        if day not in touched:
            continue
        stored = state.load_diff_day(name, day)
        merged = {m: stored[m] for m in mids if m in stored}
        merged.update({m: new[m] for m in mids if m in new})
        for m in mids:
            if m not in merged:
                msgs[m].pop("dz", None)  # diff is gone, do not claim it exists
        state.save_diff_day(name, day, merged)
