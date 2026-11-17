"""scripts/estimate_size.py: the numbers are monotonic and the limits it reports are the ones in the code."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from lorehub.state import MAX_DIFF_MSGS

from .helpers import NOW, make_state, raw

SPEC = importlib.util.spec_from_file_location("estimate_size", Path(__file__).resolve().parent.parent / "scripts/estimate_size.py")
est = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(est)


class EstimateTests(unittest.TestCase):
    def setUp(self):
        lists = {}
        for n, step in (("busy", 60), ("calm", 7200), ("quiet", 86400)):
            lists[n] = [raw("%s%d" % (n, i), "[PATCH] change %d" % i, t=NOW - i * step) for i in range(30)]
        st, self.tmp = make_state(lists)
        self.addCleanup(self.tmp.cleanup)
        self.state = st

    def test_rates_and_scenarios_are_ordered(self):
        rate = est.rates(self.state)
        self.assertGreater(rate["busy"], rate["calm"])
        low, exp, high = (est.estimate(rate, lv, f) for lv, f in ((0, 0.5), (1, 1.0), (2, 1.5)))
        for key in ("data", "site", "patches"):
            self.assertLess(low[key], exp[key], key)
            self.assertLess(exp[key], high[key], key)

    def test_per_list_limit_and_levers_bound_the_estimate(self):
        rate = {"huge": 5000.0}
        e = est.estimate(rate)
        self.assertEqual(e["patches"], MAX_DIFF_MSGS)  # capped at 1000 patch messages per list
        self.assertEqual(e["lists_at_cap"], 1)
        small = est.estimate(rate, max_diff=100)
        self.assertLess(small["site"], e["site"])
        self.assertLess(small["data"], e["data"])
        self.assertLess(est.estimate(rate, gz_threads=True)["site"], e["site"])

    def test_backfill_requests_stop_early_on_busy_lists_and_respect_the_page_cap(self):
        rate = {"huge": 3000.0, "small": 2.0}
        with_fill, capped, filled = est.backfill_requests(rate, 180, 60, fill=True)
        no_fill, capped2, _ = est.backfill_requests(rate, 180, 60, fill=False)
        self.assertLess(with_fill, no_fill)
        self.assertEqual(filled, 1)
        self.assertEqual(capped2, 1)
        self.assertEqual(est.backfill_requests({"x": 0.0}, 180, 60)[0], 1)  # at least one request per list

    def test_main_prints_the_three_scenarios_and_the_request_math(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            est.main(str(self.state.root))
        out = buf.getvalue()
        for word in ("low", "expected", "high", "data branch MB", "site MB", "default (180 days", "levers"):
            self.assertIn(word, out)


if __name__ == "__main__":
    unittest.main()
