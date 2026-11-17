import shutil
import subprocess
import unittest
from pathlib import Path

JS_TESTS = sorted((Path(__file__).parent / "js").glob("test_*.js"))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class NodeTests(unittest.TestCase):
    def test_node_unit_tests(self):
        self.assertTrue(JS_TESTS)
        for t in JS_TESTS:
            r = subprocess.run(["node", str(t)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, "%s\n%s%s" % (t.name, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
