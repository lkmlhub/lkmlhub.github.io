import re
import shutil
import subprocess
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
JS = sorted((SITE / "assets").glob("*.js"))


class JsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_scripts_parse(self):
        for f in JS:
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, "%s: %s" % (f.name, r.stderr))

    def test_no_html_injection_sinks(self):
        bad = [r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write", r"\beval\(",
               r"new Function", r"\.srcdoc", r"setAttribute\(\s*['\"]on", r"createContextualFragment",
               r"DOMParser", r"javascript:"]
        for f in JS:
            text = f.read_text()
            for pat in bad:
                self.assertIsNone(re.search(pat, text), "%s uses %s" % (f.name, pat))

    def test_fetch_only_in_one_place_and_only_relative(self):
        for f in JS:
            text = f.read_text()
            if f.name != "util.js":
                self.assertNotIn("fetch(", text, f.name)
            self.assertIsNone(re.search(r"fetch\(\s*['\"]/", text))
            self.assertIsNone(re.search(r"XMLHttpRequest|sendBeacon|WebSocket|importScripts", text))

    def test_no_third_party_origins_loaded(self):
        allowed = ("https://lore.kernel.org", "https://subspace.kernel.org", "https://public-inbox.org",
                   "https://patchwork.kernel.org", "https://b4.docs.kernel.org", "https://korg.docs.kernel.org",
                   "https://github.com/lkmlhub/lkmlhub.github.io", "http://www.w3.org/2000/svg")
        for f in JS + [SITE / "index.html", SITE / "assets/style.css"]:
            for url in re.findall(r"https?://[^\s'\"<>)]+", f.read_text()):
                self.assertTrue(url.startswith(allowed), "%s: %s" % (f.name, url))
        index = (SITE / "index.html").read_text()
        for tag in re.findall(r"<(?:script|link|img|iframe)\b[^>]*>", index):
            self.assertNotIn("http", tag, tag)

    def test_html_has_landmarks_and_labels(self):
        index = (SITE / "index.html").read_text()
        for needle in ['lang="en"', "<main", "<header", "<footer", 'class="skip-link"', 'aria-label="Toggle dark mode"',
                       'name="viewport"']:
            self.assertIn(needle, index)
        for f in JS:
            text = f.read_text()
            if f.name in ("home.js", "list.js"):
                self.assertIn("aria-label", text)


def lum(hexcolor):
    c = [int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert m, selector
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


class ContrastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        css = (SITE / "assets/style.css").read_text()
        cls.themes = {
            "light": tokens(css, ":root"),
            "dark": tokens(css, ':root[data-theme="dark"]'),
        }
        m = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme=\"light\"\]\) \{(.*?)\n  \}", css, re.S)
        cls.auto_dark = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))

    def test_system_dark_matches_explicit_dark(self):
        self.assertEqual(self.auto_dark, self.themes["dark"])

    def test_text_pairs_meet_aa(self):
        pairs = [("fg", "bg"), ("fg", "bg-subtle"), ("fg", "bg-inset"), ("fg-muted", "bg"), ("fg-muted", "bg-subtle"),
                 ("link", "bg"), ("link", "bg-subtle"), ("accent-fg", "accent-bg"),
                 ("attention-fg", "attention-bg"), ("add", "bg"), ("del", "bg"), ("fg-muted", "bg-inset")]
        pairs += [("pill-%s-fg" % n, "pill-%s-bg" % n) for n in
                  ("open", "review", "reviewed", "acked", "tested", "changes", "superseded", "merged", "stale")]
        for theme, t in self.themes.items():
            for fg, bg in pairs:
                self.assertGreaterEqual(ratio(t[fg], t[bg]), 4.5, "%s: %s on %s = %.2f" % (theme, fg, bg, ratio(t[fg], t[bg])))

    def test_state_colours_meet_graphics_contrast(self):
        for theme, t in self.themes.items():
            for c in ("open", "merged", "closed", "bar", "line"):
                self.assertGreaterEqual(ratio(t[c], t["bg"]), 3.0, "%s: %s" % (theme, c))

    def test_diff_colours_are_readable_on_added_and_removed_lines(self):
        """The diff backgrounds are color-mix(var(--add|--del) N%, var(--bg)), see style.css."""
        css = (SITE / "assets/style.css").read_text()
        found = re.findall(r"\.d-(add|del) \.(code, \.d-\w+ \.mk|ln) \{ background: color-mix\(in srgb, var\(--(?:add|del)\) (\d+)%, var\(--bg\)\)", css)
        self.assertEqual(len(found), 4, found)

        def mix(a, b, pct):
            ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
            cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
            return "#%02x%02x%02x" % tuple(round(x * pct / 100 + y * (100 - pct) / 100) for x, y in zip(ca, cb))

        for theme, t in self.themes.items():
            for kind, what, pct in found:
                bg = mix(t[kind], t["bg"], int(pct))
                self.assertGreaterEqual(ratio(t["fg"], bg), 4.5, "%s: fg on %s %s%% (%s)" % (theme, kind, pct, what))
                if what != "ln":
                    self.assertGreaterEqual(ratio(t[kind], bg), 3.0, "%s: %s marker on its own background" % (theme, kind))

    def test_chip_text_on_page_background(self):
        for theme, t in self.themes.items():
            self.assertGreaterEqual(ratio(t["link"], t["bg"]), 4.5)
            for c in ("open", "closed", "merged"):  # used as text in .chip-* and review marks
                self.assertGreaterEqual(ratio(t[c], t["bg"]), 4.5, "%s: %s as text" % (theme, c))


if __name__ == "__main__":
    unittest.main()
