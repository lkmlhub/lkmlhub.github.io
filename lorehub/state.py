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
