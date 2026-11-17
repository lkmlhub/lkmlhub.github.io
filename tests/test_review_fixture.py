"""The site files that the node golden tests of reviewmail.js start from must be what the build really produces."""

import json
import unittest

from . import make_review_fixture as mk


class ReviewFixtureTests(unittest.TestCase):
    def test_fixture_matches_a_fresh_build_of_the_ipe_series(self):
        stored = json.loads(mk.OUT.read_text("utf-8"))
        fresh = json.loads(json.dumps(mk.site_files(), sort_keys=True, ensure_ascii=False))
        self.assertEqual(stored, fresh, "run: python3 -m tests.make_review_fixture")

    def test_the_series_is_v2_with_a_cover_and_three_patches(self):
        d = json.loads(mk.OUT.read_text("utf-8"))["detail"]
        self.assertEqual((d["v"], d["total"], len(d["patches"]), bool(d["cover"])), (2, 3, 3, True))


if __name__ == "__main__":
    unittest.main()
