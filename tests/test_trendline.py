"""
Trendline on the synthetic 1-2-3 path from test_market_structure.

Run:
    python -m unittest tests.test_trendline -v
"""

import unittest

from tools.market_structure import analyze_structure
from tools.trendline import TrendlineBreak, build_trendlines, line_value
from tools.zigzag import ZigZag
from tests.test_market_structure import _path

MOVES = [-0.0100, +0.0040, -0.0080,
         +0.0060, -0.0030, +0.0080, -0.0030,
         +0.0070, -0.0040, +0.0030, -0.0090, +0.0040, -0.0080]


class TrendlineTest(unittest.TestCase):
    def setUp(self):
        self.df = _path(MOVES)
        self.pivots = ZigZag({"point": 0.00001, "depth": 5}).find_pivots(self.df)
        self.structure = analyze_structure(self.df, self.pivots)
        self.lines = build_trendlines(self.df, self.structure)
        self.up = next(l for l in self.lines if l["direction"] == "up")

    def test_starts_at_trend_anchor(self):
        self.assertEqual(self.up["anchor_index"], 60)

    def test_rests_on_b_ignoring_birth_hl(self):
        # Lows after the anchor: 100 (the HL that gave birth to the trend,
        # before it was confirmed at bar 120), 140 (HL), 180 (B).
        # Like Daniel's 13-Oct drawing, the line goes from the start low to B
        # and the birth HL falls below it.
        self.assertEqual(self.up["through_index"], 180)
        self.assertGreater(self.df["low"][140], line_value(self.up, 140))
        self.assertLess(self.df["low"][100], line_value(self.up, 100))

    def test_crossed_on_the_way_to_point_2(self):
        # Final line: crossed after point 1 (200), on the way to point 2 (220)
        self.assertTrue(self.up["final_crosses"])
        self.assertTrue(200 < self.up["final_crosses"][0] <= 220)
        # A live bot also sees a cross (against the line it knew then)
        self.assertTrue(self.up["crosses"])

    def test_frozen_at_trend_end(self):
        # Point 2 (bar 220) must not pull the line down
        self.assertIsNotNone(self.up["frozen_at"])
        self.assertNotEqual(self.up["through_index"], 220)

    def test_tool(self):
        r = TrendlineBreak({"depth": 5, "point": 0.00001}).check(self.df.iloc[:225].reset_index(drop=True))
        self.assertTrue(r.passed)
        self.assertEqual(r.signal, "sell")


if __name__ == "__main__":
    unittest.main()
