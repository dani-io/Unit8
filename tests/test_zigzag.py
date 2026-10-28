"""
ZigZag port — structural checks on synthetic and sample data.

Run:
    python -m unittest tests.test_zigzag -v
"""

import os
import unittest

import numpy as np
import pandas as pd

from tools.zigzag import ZigZag, zigzag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wave(legs, bars_per_leg=20, start=1.1000):
    """Piecewise-linear price path: legs = list of moves in price units."""
    closes = [start]
    for move in legs:
        closes += list(np.linspace(closes[-1], closes[-1] + move, bars_per_leg + 1)[1:])
    c = np.array(closes)
    return pd.DataFrame({"high": c + 0.0001, "low": c - 0.0001, "close": c})


class ZigZagTest(unittest.TestCase):
    def test_finds_clean_turns(self):
        df = _wave([+0.0100, -0.0060, +0.0080, -0.0120])
        pivots = ZigZag({"point": 0.00001}).find_pivots(df)
        turn_bars = [20, 40, 60]
        found = [p["index"] for p in pivots]
        for t in turn_bars:
            self.assertIn(t, found)
        self.assertEqual([p["type"] for p in pivots if p["index"] in turn_bars], ["high", "low", "high"])

    def test_alternates_and_only_last_is_unconfirmed(self):
        path = os.path.join(ROOT, "data", "EURUSD_M15.csv")
        df = pd.read_csv(path, parse_dates=["time"])
        pivots = ZigZag({"point": 0.00001}).find_pivots(df)
        self.assertGreater(len(pivots), 10)
        types = [p["type"] for p in pivots]
        self.assertTrue(all(a != b for a, b in zip(types, types[1:])))
        self.assertEqual([p["confirmed"] for p in pivots].count(False), 1)
        self.assertFalse(pivots[-1]["confirmed"])
        for p in pivots:
            col = "high" if p["type"] == "high" else "low"
            self.assertEqual(df[col].iloc[p["index"]], p["price"])

    def test_short_input(self):
        self.assertFalse(zigzag(np.ones(5), np.ones(5), depth=12).any())


if __name__ == "__main__":
    unittest.main()
