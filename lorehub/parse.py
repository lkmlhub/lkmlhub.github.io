"""Pure text parsing: subjects, trailers, body cleaning and reply classification.

Nothing here touches the network or the disk, so it is easy to unit test.
"""

import hashlib
import re
from dataclasses import dataclass, field

HASH_SALT = b"lorehub:v1:"


def person_hash(email_or_name):
    """Stable 12 hex character identity for a person. The address itself is never kept."""
    key = (email_or_name or "").strip().lower().encode("utf-8", "replace")
    return hashlib.sha256(HASH_SALT + key).hexdigest()[:12]


# ---------------------------------------------------------------- subjects

REPLY_RE = re.compile(r"^\s*(?:(?:re|fwd?|aw|sv)\s*:\s*)+", re.I)
BRACKET_RE = re.compile(r"^\s*\[([^\]]*)\]\s*")
INDEX_RE = re.compile(r"(?<![\w.])(\d+)\s*/\s*(\d+)(?![\w.])")
VERSION_RE = re.compile(r"^V(\d+)(?:\.\d+)*$", re.I)
PATCH_TOKEN_RE = re.compile(r"^(PATCH|PATCHES|PATCHSET)(?:-?V(\d+))?$", re.I)
TREE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,23}$")
RFC_WORDS = {"RFC", "RFT", "WIP", "DRAFT"}
KIND_WORDS = {
    "ANNOUNCE": "announce",
    "ANN": "announce",
    "BUG": "bug",
    "REGRESSION": "regression",
}


@dataclass(slots=True)
class Subject:
    raw: str
    title: str = ""
    kind: str = "other"  # patch, pull, announce, bug, regression, rfc, other
    is_reply: bool = False
    version: int = 1
    index: object = None
    total: object = None
    rfc: bool = False
    resend: bool = False
    trees: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    @property
    def is_patch_message(self):
        """True for a message that carries a patch or a pull request (not a reply)."""
        return self.kind in ("patch", "pull") and not self.is_reply

    @property
    def is_cover(self):
        return self.kind == "patch" and self.index == 0


def parse_subject(raw):
    raw = " ".join((raw or "").split())
    s = Subject(raw=raw)
    rest = raw
    m = REPLY_RE.match(rest)
    if m:
        s.is_reply = True
        rest = rest[m.end():]
    saw_patch = saw_pull = saw_git = False
    kind_word = None
    for _ in range(6):
        m = BRACKET_RE.match(rest)
        if not m:
            break
        rest = rest[m.end():]
        inner = m.group(1)
        im = INDEX_RE.search(inner)
        if im and s.index is None:
            s.index, s.total = int(im.group(1)), int(im.group(2))
            inner = inner[:im.start()] + " " + inner[im.end():]
        for tok in re.split(r"[\s,:/]+", inner):
            if not tok:
                continue
            up = tok.upper()
            pm = PATCH_TOKEN_RE.match(tok)
            vm = VERSION_RE.match(tok)
            if pm:
                saw_patch = True
                if pm.group(2):
                    s.version = int(pm.group(2))
                s.tags.append("PATCH")
            elif up == "GIT":
                saw_git = True
            elif up in ("PULL", "PULL-REQUEST") or (saw_git and up == "REQUEST"):
                saw_pull = True
                s.tags.append("GIT PULL")
            elif up == "REQUEST":
                continue
            elif vm:
                s.version = int(vm.group(1))
            elif up in RFC_WORDS:
                s.rfc = True
                s.tags.append(up)
            elif up == "RESEND":
                s.resend = True
                s.tags.append("RESEND")
            elif up in KIND_WORDS:
                kind_word = kind_word or KIND_WORDS[up]
                s.tags.append(up)
            elif TREE_RE.match(tok) and not tok.isdigit():
                if tok not in s.trees:
                    s.trees.append(tok)
    s.title = rest.strip()
    if saw_pull:
        s.kind = "pull"
    elif saw_patch:
        s.kind = "patch"
    elif kind_word:
        s.kind = kind_word
    elif s.rfc:
        s.kind = "rfc"
    if s.kind not in ("patch", "pull"):
        s.index = s.total = None
    return s


def norm_title(title):
    """Normalised series title used to link successive versions."""
    t = REPLY_RE.sub("", title or "")
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"[^0-9a-z]+", " ", t.lower())
    return t.strip()


# ---------------------------------------------------------------- emails

URL_RE = re.compile(r"(https?://\S+)")
ANGLE_EMAIL_RE = re.compile(r"[ \t]*<[^<>\s@]+@[^<>\s]+>")
BARE_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+=-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def scrub_emails(text):
    """Drop e-mail addresses from free text. In URLs (lore message links) only the @ is encoded."""
    out = []
    for i, part in enumerate(URL_RE.split(text)):
        if i % 2 == 0:
            part = ANGLE_EMAIL_RE.sub("", part)
            part = BARE_EMAIL_RE.sub("[email]", part)
        else:
            part = part.replace("@", "%40")
        out.append(part)
    return "".join(out)


# ---------------------------------------------------------------- trailers

TRAILER_RE = re.compile(
    r"^\s*(Reviewed|Acked|Tested|Reported|Nacked|Naked|Signed-off|Suggested|Co-developed|"
    r"Reported-and-tested|Reviewed-and-tested|Tested-and-reviewed|Acked-and-tested)-by"
    r"\s*:\s*(.+?)\s*$",
    re.I,
)
TRAILER_KINDS = {
    "reviewed": ["reviewed"],
    "acked": ["acked"],
    "tested": ["tested"],
    "reported": ["reported"],
    "nacked": ["nacked"],
    "naked": ["nacked"],
    "signed-off": ["signed"],
    "suggested": ["suggested"],
    "co-developed": ["codev"],
    "reported-and-tested": ["reported", "tested"],
    "reviewed-and-tested": ["reviewed", "tested"],
    "tested-and-reviewed": ["reviewed", "tested"],
    "acked-and-tested": ["acked", "tested"],
}
NAME_EMAIL_RE = re.compile(r"^(.*?)\s*<([^<>]*)>\s*(?:#.*|\(.*\))?$")


def is_quoted(line):
    return line.lstrip().startswith(">")


def extract_trailers(text):
    """Trailers in unquoted lines: [[kind, name, hash], ...]. Emails are hashed and dropped."""
    found = []
    for line in text.splitlines():
        if is_quoted(line):
            continue
        m = TRAILER_RE.match(line)
        if not m:
            continue
        who = m.group(2)
        nm = NAME_EMAIL_RE.match(who)
        if nm:
            name, addr = nm.group(1).strip().strip('"'), nm.group(2).strip()
        else:
            name, addr = who.strip().strip('"'), ""
        if "@" in name:
            name = ""
        key = addr or name
        if not key:
            continue
        name = name or addr.split("@")[0]
        h = person_hash(key)
        for kind in TRAILER_KINDS[m.group(1).lower()]:
            item = [kind, name[:80] if "@" not in name else "", h]
            if item not in found:
                found.append(item)
    return found


# ---------------------------------------------------------------- classification

APPLIED_POS = re.compile(
    r"(?:^|[\s,.;:(])(?:applied|queued|merged|pulled)\b"
    r"|thanks,? applied|has been (?:applied|queued|merged|pulled)"
    r"|(?:i|we)(?:'ve| have|'ll have)? (?:applied|queued|merged|pulled)\b",
    re.I,
)
APPLIED_NEG = re.compile(
    r"\b(?:not|never|cannot|can't|won't|didn't|doesn't|isn't|wasn't|hasn't|haven't|"
    r"fail\w*|unable|if|when|once|should|would|could|can|may|might|please|before|after|"
    r"until|whether)\b"
    r"[^.\n]*\b(?:applied|queued|merged|pulled)\b",
    re.I,
)
APPLIED_STRONG = re.compile(
    r"^\W*(?:applied|queued|merged|pulled)\b|thanks,? applied|applied,? thanks|"
    r"\bapplied to\b|has been (?:merged|applied|queued|pulled) (?:into|to|in)\b|"
    r"\b(?:i|we)(?:'ve| have)? (?:applied|queued|merged|pulled)\b",
    re.I,
)
GREETING_RE = re.compile(r"^(hi|hello|hey|dear|on .* wrote:?|.* wrote:)\b", re.I)
SIG_RE = re.compile(r"^--\s*$")
NAK_RE = re.compile(r"^\W*(?:NAK|NACK|NAKed|NACKed|Nacked)\b|\bNAK(?:ed)?\b|\bNACK(?:ed)?\b")
SIGNOFF_LINE_RE = re.compile(
    r"^\W*(thanks|thank you|regards|best regards|cheers|best|kind regards|br|"
    r"-?\s*[A-Z][a-z]+(\s[A-Z][a-z]+)?)\W*$",
    re.I,
)


def unquoted_lines(text):
    return [ln for ln in text.splitlines() if not is_quoted(ln)]


def looks_applied(text):
    """True if the first lines of a reply say the patch or pull request was taken."""
    seen = 0
    for line in unquoted_lines(text):
        line = line.strip()
        if not line or GREETING_RE.match(line):
            continue
        if SIG_RE.match(line):
            break
        seen += 1
        if seen > 8:
            break
        if "?" in line:
            continue
        if APPLIED_NEG.search(line) and not re.match(r"^\W*(?:applied|queued|merged|pulled)\b", line, re.I):
            continue
        if re.search(r"fail\w* to apply|does(?:n't| not) apply|(?:can|will)(?:'t| not) apply", line, re.I):
            continue
        if APPLIED_STRONG.search(line):
            return True
    return False


REQUEST_RE = re.compile(
    r"\b(?:please|pls|should(?:n't)?|needs? to|must|can you|could you|would you|why|instead|"
    r"rather|nit|prefer|wrong|broken|missing|remove|drop|rename|fix|don't|do not|doesn't|isn't|"
    r"unnecessary|useless|suggest|consider|maybe|perhaps|wondering|concern|issue|problem|bug|race|"
    r"leak|overflow|incorrect|typo|checkpatch|warning|error|fail\w*|cannot|can't|won't|not (?:needed|"
    r"necessary|correct|right|sure))\b",
    re.I,
)


def has_review_comments(text):
    """True if the reply interleaves requests or questions after quoted text (an email-style review)."""
    lines = text.splitlines()
    last_quote = -100
    for i, line in enumerate(lines):
        if SIG_RE.match(line):
            break
        if is_quoted(line):
            last_quote = i
            continue
        s = line.strip()
        if not s or last_quote < 0 or i - last_quote > 3:
            continue
        if TRAILER_RE.match(s) or SIGNOFF_LINE_RE.match(s) or GREETING_RE.match(s):
            continue
        if len(s.split()) >= 4 and REQUEST_RE.search(s):
            return True
    return False


def classify(text):
    """Message-intrinsic flags: A applied phrase, I inline review comments, N NAK."""
    flags = ""
    if looks_applied(text):
        flags += "A"
    if has_review_comments(text):
        flags += "I"
    if any(NAK_RE.search(ln) for ln in unquoted_lines(text)[:60]):
        flags += "N"
    return flags


# ---------------------------------------------------------------- bodies

DIFF_START_RE = re.compile(r"^(diff --git |Index: |--- (?:a/|/dev/null)|\+\+\+ b/)")
STAT_SUMMARY_RE = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)
STAT_LINE_RE = re.compile(r"^\s*\S.*\|\s+\d+\s*[+-]*\s*$")
MAX_BODY_BYTES = 128 * 1024  # per message; longer bodies are cut at a line and flagged
BODY_VERSION = 3  # records with a complete body carry "bv": 3; older (trimmed) records do not


def split_diff(text):
    """Return (message text, diffstat tuple or None). The diff itself is dropped."""
    lines = text.splitlines()
    cut = None
    for i, line in enumerate(lines):
        if not is_quoted(line) and line.startswith("diff --git "):
            cut = i
            break
    if cut is None:
        for i, line in enumerate(lines):
            if not is_quoted(line) and DIFF_START_RE.match(line) and line.startswith("--- a/"):
                cut = i
                break
    if cut is None:
        return text, None
    head = lines[:cut]
    added = removed = files = 0
    for line in lines[cut:]:
        if is_quoted(line):
            continue
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    stat = (files, added, removed)
    summary = STAT_SUMMARY_RE.search("\n".join(head[-60:]))
    if summary:
        stat = (int(summary.group(1)), int(summary.group(2) or 0), int(summary.group(3) or 0))
    # keep the commit message, drop the "---" separator and the diffstat file lines
    for i in range(len(head) - 1, -1, -1):
        if head[i].strip() == "---":
            head = head[:i]
            break
    else:
        while head and (STAT_LINE_RE.match(head[-1]) or STAT_SUMMARY_RE.search(head[-1])):
            head.pop()
    return "\n".join(head).rstrip() + "\n", stat


def full_body(text, patch=False, max_bytes=MAX_BODY_BYTES):
    """The complete text of a message for storage: (body, truncated).

    Quoted lines, trailers and signatures are all kept. The only things removed are e-mail addresses,
    trailing white space, runs of blank lines and, for a patch message, the diff (stored separately,
    see diff.py). A body above `max_bytes` is cut at a line boundary and flagged.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(ln.rstrip() for ln in text.split("\n"))
    if patch:
        text, _ = split_diff(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    text = scrub_emails(text)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    cut = raw[:max_bytes].decode("utf-8", "ignore")
    nl = cut.rfind("\n")
    if nl > max_bytes // 2:
        cut = cut[:nl]
    return cut.rstrip(), True
