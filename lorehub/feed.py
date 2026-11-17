"""Parse public-inbox Atom feeds (new.atom and search results) into message records."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse

from . import LORE
from .diff import extract_diff, stat_of
from .parse import (
    classify,
    clean_body,
    extract_trailers,
    parse_subject,
    person_hash,
    scrub_emails,
    split_diff,
)

ATOM = "{http://www.w3.org/2005/Atom}"
THR = "{http://purl.org/syndication/thread/1.0}"
XHTML = "{http://www.w3.org/1999/xhtml}"
ENTRY_RE = re.compile(r"<entry>.*?</entry>", re.S)
CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
WRAP_OPEN = (
    '<feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:thr="http://purl.org/syndication/thread/1.0">'
)


def mid_from_href(href):
    """Message-id from a lore link such as https://lore.kernel.org/lkml/<mid>/.

    Returned percent-encoded (including '@' as %40): lore accepts that form, and it keeps
    address-like text out of the stored and published data.
    """
    if not href:
        return ""
    parts = [p for p in urlparse(href).path.split("/") if p != ""]
    if len(parts) < 2:
        return ""
    return quote(unquote(parts[1]), safe="")


def lore_url(list_name, mid):
    return "%s/%s/%s/" % (LORE, list_name, mid)


def parse_time(text):
    text = (text or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return 0


def format_time(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(el, tag):
    child = el.find(ATOM + tag)
    return (child.text or "").strip() if child is not None else ""


def parse_entry(xml):
    """One <entry> string -> raw dict (includes the e-mail address, transient only) or None."""
    try:
        root = ET.fromstring(WRAP_OPEN + xml + "</feed>")
    except ET.ParseError:
        try:
            root = ET.fromstring(WRAP_OPEN + CTRL_RE.sub("", xml) + "</feed>")
        except ET.ParseError:
            return None
    entry = root.find(ATOM + "entry")
    if entry is None:
        return None
    link = entry.find(ATOM + "link")
    mid = mid_from_href(link.get("href") if link is not None else "")
    if not mid:
        return None
    author = entry.find(ATOM + "author")
    name = _text(author, "name") if author is not None else ""
    email = _text(author, "email") if author is not None else ""
    irt = entry.find(THR + "in-reply-to")
    parent = mid_from_href(irt.get("href")) if irt is not None else ""
    pre = None
    content = entry.find(ATOM + "content")
    if content is not None:
        pre = next(content.iter(XHTML + "pre"), None)
    if pre is not None:
        body = "".join(pre.itertext())
    elif content is not None:
        body = "".join(content.itertext())
    else:
        body = ""
    return {
        "mid": mid,
        "parent": parent if parent != mid else "",
        "ts": parse_time(_text(entry, "updated")),
        "name": name,
        "email": email,
        "subject": _text(entry, "title"),
        "body": body,
    }


def parse_feed(data):
    """Parse a feed document. Returns (raw entries list, bad entry count, feed updated ts)."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    entries, bad = [], 0
    seen = set()
    for chunk in ENTRY_RE.findall(data):
        e = parse_entry(chunk)
        if e is None:
            bad += 1
        elif e["mid"] not in seen:
            seen.add(e["mid"])
            entries.append(e)
    head = data[: data.find("<entry>")] if "<entry>" in data else data
    m = re.search(r"<updated>([^<]*)</updated>", head)
    return entries, bad, parse_time(m.group(1)) if m else 0


def to_record(raw, keep_body=True, diffs=None):
    """Turn a raw entry into the stored record. The e-mail address is hashed and dropped.

    When `diffs` is a dict and the message is a patch, its parsed diff is stored there under the message-id.
    """
    body = raw["body"].replace("\r\n", "\n")
    subj = parse_subject(raw["subject"])
    name = " ".join(raw["name"].split())[:80]
    rec = {
        "t": raw["ts"],
        "a": scrub_name(name),
        "h": person_hash(raw["email"] or name),
        "p": raw["parent"],
        "s": scrub_emails(" ".join(raw["subject"].split()))[:300],
    }
    flags = classify(body)
    if flags:
        rec["f"] = flags
    trailers = extract_trailers(body)
    if trailers:
        rec["tr"] = trailers[:12]
    if subj.is_patch_message:
        diff = extract_diff(body) if subj.kind == "patch" else None
        if diff is not None:
            rec["ds"] = stat_of(diff)
            if diffs is not None:
                diffs[raw["mid"]] = diff
        else:
            _, stat = split_diff(body)
            if stat:
                rec["ds"] = list(stat)
    if keep_body:
        rec["b"] = clean_body(body)
    return rec


def scrub_name(name):
    return name.replace("<", "").replace(">", "") if "@" not in name else name.split("@")[0]
