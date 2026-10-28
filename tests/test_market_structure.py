"""
Market structure — trend start/end on a synthetic 1-2-3 path.

Run:
    python -m unittest tests.test_market_structure -v
"""

import unittest

import numpy as np
import pandas as pd

from tools.market_structure import analyze_structure, label_pivots
from tools.zigzag import ZigZag


def _path(moves, bars_per_leg=20, start=1.1000):
    c = [start]
    for m in moves:
        c += list(np.linspace(c[-1], c[-1] + m, bars_per_leg + 1)[1:])
    c = np.array(c)
    return pd.DataFrame({"open": c, "high": c + 0.0001, "low": c - 0.0001, "close": c})


class LabelTest(unittest.TestCase):
    def test_labels(self):
        piv = [{"type": "low", "price": 1.0}, {"type": "high", "price": 2.0},
               {"type": "low", "price": 1.5}, {"type": "high", "price": 2.5},
               {"type": "low", "price": 1.2}, {"type": "high", "price": 2.5}]
        self.assertEqual([p["label"] for p in label_pivots(piv)],
                         [None, None, "HL", "HH", "LL", "EH"])


class StructureTest(unittest.TestCase):
    def setUp(self):
        # Downtrend, LL, then an uptrend (HL + HH ...), then a 1-2-3 top:
        # A, B, 1 (lower), 2 (below B), 3 (between), and a new low.
        moves = [-0.0100, +0.0040, -0.0080,          # down: LH, LL
                 +0.0060, -0.0030, +0.0080, -0.0030,  # up: H, HL, HH, HL
                 +0.0070,                             # A (HH)
                 -0.0040,                             # B
                 +0.0030,                             # 1 (below A)
                 -0.0090,                             # 2 (breaks B)
                 +0.0040,                             # 3
                 -0.0080]                             # new LL
        self.df = _path(moves)
        self.pivots = ZigZag({"point": 0.00001, "depth": 5}).find_pivots(self.df)
        self.r = analyze_structure(self.df, self.pivots)

    def test_uptrend_starts_and_ends(self):
        kinds = [(e["kind"], e["direction"]) for e in self.r["events"]]
        self.assertIn(("trend_start", "up"), kinds)
        self.assertIn(("trend_end", "up"), kinds)
        self.assertLess(kinds.index(("trend_start", "up")), kinds.index(("trend_end", "up")))

    def test_anchors(self):
        ev = {(e["kind"], e["direction"]): e for e in self.r["events"]}
        start, end = ev[("trend_start", "up")], ev[("trend_end", "up")]
        self.assertEqual(start["anchor_index"], 60)                 # the LL the trend started from
        self.assertEqual(end["anchor_index"], 160)                  # A
        self.assertAlmostEqual(end["level"], self.df["low"][180])   # B
        self.assertGreater(end["index"], 200)                       # confirmed after 1, on the way to 2

    def test_downtrend_after_three(self):
        kinds = [(e["kind"], e["direction"]) for e in self.r["events"]]
        last_up_end = max(i for i, k in enumerate(kinds) if k == ("trend_start", "up"))
        self.assertIn(("trend_start", "down"), kinds[last_up_end:])

    def test_no_lookahead(self):
        # Truncating the data must not change earlier states
        cut = 230
        short = analyze_structure(self.df.iloc[:cut].reset_index(drop=True),
                                  ZigZag({"point": 0.00001, "depth": 5}).find_pivots(self.df.iloc[:cut]))
        self.assertEqual(short["states"][: cut - 40], self.r["states"][: cut - 40])


if __name__ == "__main__":
    unittest.main()
