"""Parse the lore.kernel.org manifest (a gzipped JSON mapping of epoch repos)."""

import gzip
import json
import re
from dataclasses import dataclass, field

KEY_RE = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._+-]*)/git/(\d+)\.git$")
EPOCH_SUFFIX_RE = re.compile(r"\s*\[epoch \d+\]\s*$")


@dataclass
class ListInfo:
    name: str
    description: str = ""
    modified: int = 0
    epochs: list = field(default_factory=list)


def parse_manifest(data):
    """Return {list name: ListInfo}. Accepts gzip bytes, JSON bytes/str or a dict."""
    if isinstance(data, (bytes, bytearray)):
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        data = json.loads(data.decode("utf-8"))
    elif isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("manifest is not a JSON object")
    lists = {}
    best_epoch = {}
    for key, val in data.items():
        m = KEY_RE.match(key)
        if not m or not isinstance(val, dict):
            continue
        name, epoch = m.group(1), int(m.group(2))
        info = lists.setdefault(name, ListInfo(name))
        info.epochs.append(epoch)
        try:
            info.modified = max(info.modified, int(val.get("modified") or 0))
        except (TypeError, ValueError):
            pass
        if epoch >= best_epoch.get(name, -1):
            best_epoch[name] = epoch
            desc = EPOCH_SUFFIX_RE.sub("", str(val.get("description") or "")).strip()
            info.description = desc
    for info in lists.values():
        info.epochs.sort()
    return lists
