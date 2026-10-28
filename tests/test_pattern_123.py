"""
1-2-3 on synthetic paths built to match Daniel's picture.

Path (20 bars per leg): uptrend from the low at bar 60, A at 160, B at 180,
1 at 200 (just under A), 2 at 220 (231% of AB), 3 at 240 (below B), then a
new leg at 260.

Run:
    python -m unittest tests.test_pattern_123 -v
"""

import unittest

from tools.market_structure import analyze_structure
from tools.pattern_123 import Pattern123, detect_123
from tools.trendline import build_trendlines
from tools.zigzag import ZigZag
from tests.test_market_structure import _path

BASE = [-0.0100, +0.0040, -0.0080,                     # down into the low at 60
        +0.0100, -0.0040, +0.0120, -0.0040, +0.0100,   # uptrend → A at 160
        -0.0040,                                        # B
        +0.0035,                                        # 1 (5 pips under A)
        -0.0090,                                        # 2
        +0.0045,                                        # 3 (below B)
        -0.0080]                                        # leg away from 3


def run(moves, **cfg):
    df = _path(moves)
    piv = ZigZag({"point": 0.00001, "depth": 5}).find_pivots(df)
    st = analyze_structure(df, piv)
    return df, piv, detect_123(df, piv, st, build_trendlines(df, st), cfg)


class Pattern123Test(unittest.TestCase):
    def test_ready_sell(self):
        _, _, pats = run(BASE)
        p = pats[-1]
        self.assertEqual(p["direction"], "sell")
        self.assertIn(p["status"], ("ready", "completed"), p["checks"])
        self.assertEqual([p[k]["index"] for k in ("A", "B", "1", "2", "3")], [160, 180, 200, 220, 240])
        self.assertAlmostEqual(p["extension"], 0.0097 / 0.0042, places=6)
        self.assertTrue(p["3"]["ideal"])
        self.assertEqual(p["sl"], p["1"]["price"])

    def test_completed_then_never_invalid(self):
        # Breaks 2 after 3, then much later rallies above 1: stays completed
        _, _, pats = run(BASE + [+0.0300])
        p = next(p for p in pats if p["A"]["index"] == 160)
        self.assertEqual(p["status"], "completed")
        self.assertIsNone(p["invalid_at"])

    def test_mirror_buy(self):
        _, _, pats = run([-m for m in BASE])
        p = pats[-1]
        self.assertEqual(p["direction"], "buy")
        self.assertIn(p["status"], ("ready", "completed"), p["checks"])

    def test_invalid_when_closing_above_1(self):
        _, _, pats = run(BASE[:-1] + [+0.0080])
        self.assertEqual(pats[-1]["status"], "invalid")
        self.assertIsNotNone(pats[-1]["invalid_at"])

    def test_rejected_when_2_short_of_200(self):
        moves = BASE[:10] + [-0.0060, +0.0030, -0.0080]
        _, _, pats = run(moves)
        p = pats[-1]
        self.assertEqual(p["status"], "rejected")
        self.assertFalse(p["checks"]["extension_200"]["ok"])

    def test_rejected_when_beyond_half_the_trend(self):
        _, _, pats = run(BASE, max_trend_retrace=0.3)
        self.assertFalse(pats[-1]["checks"]["max_trend_retrace"]["ok"])
        self.assertEqual(pats[-1]["status"], "rejected")

    def test_point_1_too_far_from_a(self):
        # 1 at 15 pips under A (> 20% of AB = 8.4 pips) → not a candidate
        moves = BASE[:9] + [+0.0025] + BASE[10:]
        _, _, pats = run(moves)
        self.assertFalse(any(p["A"]["index"] == 160 for p in pats))

    def test_tool_signal_only_on_the_leg_after_3(self):
        df = _path(BASE)
        r = Pattern123({"point": 0.00001, "depth": 5}).check(df)
        self.assertTrue(r.passed, r.reason)
        self.assertEqual(r.signal, "sell")
        self.assertNotIn("tp", r.data)      # no TP rules yet → engine can't trade
        # One more leg later the setup is stale
        r2 = Pattern123({"point": 0.00001, "depth": 5}).check(_path(BASE + [+0.0030, -0.0030]))
        self.assertFalse(r2.passed)


if __name__ == "__main__":
    unittest.main()
