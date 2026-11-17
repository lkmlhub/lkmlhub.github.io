"""Accumulated per-list state: atomic writes, merging, pruning."""

import gzip
import io
import json
import os
import tempfile
import time
from pathlib import Path

RETENTION_DAYS = 180  # at least the backfill horizon; the effective value is max(this, configured horizon)
BODY_DAYS = 60  # discussion bodies (series members always keep theirs)
MAX_MSGS = 10000  # newest messages kept per list, keeps the busiest lists bounded
MAX_BODY_MSGS = 300  # newest discussion messages that keep a body
MAX_DIFF_MSGS = 1000  # newest patch messages per list that keep a diff (whole series, newest first)
STATE_EPOCH = 3  # bump when records or limits change so that old state is walked again, see migrate.py
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
        """Write the messages of a list (one file per UTC day) and the diffs stashed by ingest."""
        self.sync_diffs(name, data)
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

    def sync_diffs(self, name, data):
        """Write the diffs stashed in data["_diffs"] and drop diffs whose message lost its "dz" flag.

        Only the day files in data["_touched"] are rewritten. A record has "dz" exactly when its diff
        is stored, so a diff that is wanted but not found anywhere loses its flag.
        """
        pend = data.pop("_diffs", None) or {}
        touched = set(data.pop("_touched", None) or ())
        if not touched:
            return
        msgs = data["msgs"]
        wanted = {}
        for mid, r in msgs.items():
            if "dz" in r:
                day = day_of(r["t"])
                if day in touched:
                    wanted.setdefault(day, []).append(mid)
        existing = set(self.diff_days(name))
        for day in sorted(touched):
            mids = wanted.get(day, [])
            if not mids:
                self.save_diff_day(name, day, {})
                continue
            stored = self.load_diff_day(name, day) if day in existing else {}
            merged = {}
            for m in mids:
                d = pend.get(m) or stored.get(m)
                if d is not None:
                    merged[m] = d
                else:
                    msgs[m].pop("dz", None)
            self.save_diff_day(name, day, merged)

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


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


DERIVED = ("f", "tr", "ds", "bt")  # recomputed from the full message on every fetch


def merge_records(data, records, now, retention_days=RETENTION_DAYS):
    """Merge {mid: record} into a list state dict in place. Returns the number of new messages.

    A record that is fetched again replaces the stored one (messages are immutable, so only our own
    parsing changes: this is how trimmed bodies of an older format are replaced by complete ones). The
    stored body is kept when the new record has none, and "dz" is kept (see sync_diffs).
    Records older than `retention_days` are not merged; None disables the age check.
    """
    msgs = data["msgs"]
    horizon = None if retention_days is None else now - retention_days * DAY
    added = 0
    for mid, rec in records.items():
        if rec["t"] <= 0 or (horizon is not None and rec["t"] < horizon):
            continue
        old = msgs.get(mid)
        if old is None:
            msgs[mid] = rec
            added += 1
            continue
        for k in DERIVED:
            if k not in rec:
                old.pop(k, None)
        old.update(rec)
        if "b" in rec and "bv" not in rec:
            old.pop("bv", None)
    return added


def stash_diffs(data, new_diffs):
    """Remember parsed diffs of new messages until save_list writes them. Sets "dz" on their records."""
    from .diff import kept_bytes  # local import: diff.py has no state dependency

    pend = data.setdefault("_diffs", {})
    touched = data.setdefault("_touched", set())
    for mid, d in new_diffs.items():
        rec = data["msgs"].get(mid)
        if rec is None:
            continue
        rec["dz"] = kept_bytes(d)
        pend[mid] = d
        touched.add(day_of(rec["t"]))
