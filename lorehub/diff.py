"""Unified diff extraction from patch e-mails.

A patch message carries a commit message, a diffstat and a unified diff. This module finds the
diff, parses it into files (path, rename/new/deleted/binary markers, added and removed counts and
the raw hunk text), removes e-mail addresses and control characters and applies a size cap.

The result is a small JSON-friendly dict, stored on the data branch and turned into the lazily
loaded diff shards of the site:

    {"f": [file, ...], "sz": bytes of diff text before capping, "tr": 1 when anything was cut}
    file = {"p": path, "o": old path (rename, copy), "k": "a" new | "d" deleted | "r" renamed | "c" copied,
            "b": 1 binary, "m": [old mode, new mode], "s": similarity percent, "cb": 1 combined diff,
            "a": added lines, "d": removed lines, "x": hunk text (@@ headers and body lines),
            "t": 1 when the hunk text is incomplete or missing because of the cap}

Hunk text is kept as plain unified diff text: the first character of a line is its type, which is
smaller than any per-line structure and trivial to parse in the browser.
"""

import re

from .parse import is_quoted, scrub_emails

MAX_PATCH_BYTES = 120 * 1024  # kept hunk text per patch
MAX_LINE_CHARS = 2000  # longer lines are cut (minified blobs, base64)
MAX_PATH_CHARS = 300

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
COMBINED_HUNK_RE = re.compile(r"^@@@+ ")
CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029]")
GIT_VERSION_RE = re.compile(r"^\d+\.\d+[\w.\-+]*\s*$")
SIMILARITY_RE = re.compile(r"^similarity index (\d+)%")
BINARY_RE = re.compile(r"^Binary files (.*) and (.*) differ$")


def clean_line(line, limit=MAX_LINE_CHARS):
    line = CTRL_RE.sub("", line)
    if len(line) > limit:
        line = line[:limit] + " [line cut]"
    if "@" in line:
        line = scrub_emails(line)
    return line


def clean_path(path):
    path = CTRL_RE.sub("", path.strip())
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1].replace('\\"', '"').replace("\\t", "\t").replace("\\\\", "\\")
        path = CTRL_RE.sub("", path)
    return scrub_emails(path)[:MAX_PATH_CHARS]


def _strip_prefix(path):
    if path in ("/dev/null", ""):
        return path
    path = path.split("\t")[0]
    return path[2:] if path[:2] in ("a/", "b/") else path


def _git_header_paths(rest):
    """Old and new path from the text after 'diff --git '. Ambiguous with spaces, so prefer a symmetric split."""
    if rest.startswith("a/"):
        for m in re.finditer(r" b/", rest):
            old, new = rest[2:m.start()], rest[m.end():]
            if old == new:
                return old, new
        m = rest.find(" b/")
        if m > 0:
            return rest[2:m], rest[m + 3:]
    parts = rest.split(" ")
    if len(parts) == 2:
        return _strip_prefix(parts[0]), _strip_prefix(parts[1])
    half = len(rest) // 2
    return _strip_prefix(rest[:half].strip()), _strip_prefix(rest[half:].strip())


def _starts_diff(lines, i):
    line = lines[i]
    if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
        return True
    return line.startswith("--- ") and i + 2 < len(lines) and lines[i + 1].startswith("+++ ") and \
        bool(HUNK_RE.match(lines[i + 2]))


def find_diff_start(lines):
    """Index of the first diff line of a patch message, or None. Quoted lines never count.

    Like git am, the commit message ends at the first line that is exactly '---'; the diff is searched
    after it so that a sample diff inside the commit message is not mistaken for the patch.
    """
    sep = None
    for i, line in enumerate(lines):
        if not is_quoted(line) and line.rstrip() == "---":
            sep = i
            break
    starts = ([sep] if sep is not None else []) + [0]
    for begin in starts:
        for i in range(begin, len(lines)):
            if not is_quoted(lines[i]) and _starts_diff(lines, i):
                return i
    return None


class _File:
    __slots__ = ("old", "new", "kind", "binary", "modes", "sim", "combined", "a", "d", "hunks", "plain",
                 "rename_from", "rename_to", "copy_from", "copy_to")

    def __init__(self):
        self.old = self.new = ""
        self.kind = ""
        self.binary = False
        self.modes = [None, None]
        self.sim = None
        self.combined = False
        self.a = self.d = 0
        self.hunks = []  # list of lists of lines
        self.plain = False
        self.rename_from = self.rename_to = self.copy_from = self.copy_to = None

    def path(self):
        if self.kind == "d":
            return self.old or self.new
        return self.new or self.old

    def finish(self):
        if self.rename_from is not None and self.rename_to is not None:
            self.old, self.new, self.kind = self.rename_from, self.rename_to, "r"
        elif self.copy_from is not None and self.copy_to is not None:
            self.old, self.new, self.kind = self.copy_from, self.copy_to, "c"
        return self


def _header_line(cur, line):
    """Handle one extended header line of a file. Returns 'hunk', 'between', 'skip' or None to stay."""
    if line.startswith("--- "):
        old = _strip_prefix(line[4:])
        if old == "/dev/null":
            cur.kind = "a"
        elif old:
            cur.old = old
    elif line.startswith("+++ "):
        new = _strip_prefix(line[4:])
        if new == "/dev/null":
            cur.kind = "d"
        elif new:
            cur.new = new
            if cur.plain and not cur.old:
                cur.old = new
    elif line.startswith("new file mode"):
        cur.kind = "a"
        cur.modes[1] = line.split()[-1]
    elif line.startswith("deleted file mode"):
        cur.kind = "d"
        cur.modes[0] = line.split()[-1]
    elif line.startswith("old mode "):
        cur.modes[0] = line[9:].strip()
    elif line.startswith("new mode "):
        cur.modes[1] = line[9:].strip()
    elif line.startswith("rename from "):
        cur.rename_from = line[12:]
    elif line.startswith("rename to "):
        cur.rename_to = line[10:]
    elif line.startswith("copy from "):
        cur.copy_from = line[10:]
    elif line.startswith("copy to "):
        cur.copy_to = line[8:]
    elif SIMILARITY_RE.match(line):
        cur.sim = int(SIMILARITY_RE.match(line).group(1))
    elif BINARY_RE.match(line):
        cur.binary = True
        return "between"
    elif line.startswith("GIT binary patch"):
        cur.binary = True
        return "skip"
    elif HUNK_RE.match(line):
        return "hunk"
    elif COMBINED_HUNK_RE.match(line):
        cur.combined = True
        return "skip"
    return None


def _read_hunk(lines, i, cur):
    """Read one hunk starting at its header, driven by the line counts. Returns (hunk lines, next index)."""
    n = len(lines)
    m = HUNK_RE.match(lines[i].rstrip("\r"))
    old_left = int(m.group(2)) if m.group(2) is not None else 1
    new_left = int(m.group(4)) if m.group(4) is not None else 1
    hunk = [lines[i].rstrip("\r")]
    i += 1
    while i < n and (old_left > 0 or new_left > 0):
        ln = lines[i].rstrip("\r")
        c = ln[:1]
        if c == "+":
            new_left -= 1
            cur.a += 1
        elif c == "-":
            if ln.rstrip() == "--" and i + 1 < n and GIT_VERSION_RE.match(lines[i + 1]):
                break  # the mail signature after a hunk whose counts were wrong
            old_left -= 1
            cur.d += 1
        elif c == " " or ln == "":
            old_left -= 1
            new_left -= 1
            if ln == "":
                ln = " "  # a mail client stripped the single space of an empty context line
        elif c != "\\":
            break  # not a diff line: the counts were wrong or the mail was mangled
        hunk.append(ln)
        i += 1
    while i < n and lines[i].startswith("\\"):
        hunk.append(lines[i].rstrip("\r"))
        i += 1
    return hunk, i


def parse_diff(lines):
    """Parse diff lines into _File objects. Tolerant: broken hunks end where the text stops making sense."""
    files = []
    cur = None
    i, n = 0, len(lines)
    state = "out"  # out, hdr (file header), between (after a hunk), skip (combined diff or binary data)
    while i < n:
        line = lines[i].rstrip("\r")
        if line.startswith("diff --git "):
            cur = _File()
            files.append(cur)
            cur.old, cur.new = _git_header_paths(line[11:])
            state = "hdr"
            i += 1
        elif line.startswith(("diff --cc ", "diff --combined ")):
            cur = _File()
            cur.combined = True
            cur.new = line.split(" ", 2)[2]
            files.append(cur)
            state = "skip"
            i += 1
        elif state in ("out", "between") and _starts_diff(lines, i) and line.startswith("--- "):
            cur = _File()
            cur.plain = True
            files.append(cur)
            state = "hdr"
        elif state == "hdr":
            nxt = _header_line(cur, line)
            if nxt == "hunk":
                state = "hunk"
            else:
                if nxt:
                    state = nxt
                i += 1
        elif state == "skip":
            i += 1
        elif HUNK_RE.match(line):
            if cur is None:
                cur = _File()
                files.append(cur)
            hunk, i = _read_hunk(lines, i, cur)
            cur.hunks.append(hunk)
            state = "between"
        else:
            if COMBINED_HUNK_RE.match(line) and cur is not None:
                cur.combined = True
                state = "skip"
            i += 1
    return [f.finish() for f in files]


def build_result(files, cap=MAX_PATCH_BYTES):
    """Turn parsed files into the stored dict, scrubbing and applying the byte cap."""
    out, used, cut, total = [], 0, False, 0
    for f in files:
        item = {"p": clean_path(f.path())}
        if f.kind in ("r", "c"):
            item["o"] = clean_path(f.old)
        if f.kind:
            item["k"] = f.kind
        if f.binary:
            item["b"] = 1
        if f.combined:
            item["cb"] = 1
        if f.sim is not None and f.kind in ("r", "c"):
            item["s"] = f.sim
        if f.modes[0] and f.modes[1] and f.modes[0] != f.modes[1] and not f.kind:
            item["m"] = [f.modes[0][-6:], f.modes[1][-6:]]
        item["a"], item["d"] = f.a, f.d
        text_lines, truncated = [], False
        for hunk in f.hunks:
            for line in hunk:
                line = clean_line(line)
                size = len(line.encode("utf-8", "replace")) + 1
                total += size
                if truncated or used + size > cap:
                    truncated = True
                    continue
                used += size
                text_lines.append(line)
        if text_lines:
            item["x"] = "\n".join(text_lines)
        if truncated:
            item["t"] = 1
            cut = True
        out.append(item)
    result = {"f": out, "sz": total}
    if cut:
        result["tr"] = 1
    return result


def stat_of(result):
    f = result["f"]
    return [len(f), sum(x["a"] for x in f), sum(x["d"] for x in f)]


def kept_bytes(result):
    return sum(len(x.get("x", "").encode("utf-8", "replace")) for x in result["f"])


def extract_diff(body, cap=MAX_PATCH_BYTES):
    """Diff of a patch message body, or None when there is none (cover letter, reply without patch)."""
    lines = body.replace("\r\n", "\n").split("\n")
    start = find_diff_start(lines)
    if start is None:
        return None
    files = parse_diff(lines[start:])
    if not files:
        return None
    return build_result(files, cap)
