"""
Multi-timeframe backtest: trade simulation and a smoke run.

Run:
    python -m unittest tests.test_mtf_123 -v
"""

import unittest

import numpy as np
import pandas as pd

from backtest.mtf_123 import _simulate, run


def _bars(closes, start="2026-01-05", freq="1min"):
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"time": pd.date_range(start, periods=len(c), freq=freq),
                         "open": c, "high": c + 0.0001, "low": c - 0.0001, "close": c, "volume": 1})


class SimulateTest(unittest.TestCase):
    order = {"direction": "sell", "two": 1.1000, "three": 1.1010, "sl": 1.1030,
             "setup": {"htf_points": {}}, "ltf_points": {}, "ltf_extension": 2.2, "ltf_three_ideal": True}

    def test_sell_tp_k2(self):
        ltf = _bars([1.1000, 1.0995, 1.0990, 1.0985, 1.0975])
        t = _simulate(ltf, 0, 1.1000, self.order, 2.0, 0.00001)
        self.assertEqual(t["result"], "win")
        self.assertAlmostEqual(t["tp"], 1.0980)
        self.assertAlmostEqual(t["planned_rr"], (1.1000 - 1.0980) / (1.1030 - 1.1000))

    def test_sell_sl(self):
        ltf = _bars([1.1000, 1.1010, 1.1035])
        self.assertEqual(_simulate(ltf, 0, 1.1000, self.order, 2.0, 0.00001)["result"], "loss")


class SmokeTest(unittest.TestCase):
    def test_runs_on_random_walk(self):
        rng = np.random.default_rng(1)
        m1 = _bars(1.1 + np.cumsum(rng.normal(0, 0.0002, 15 * 700)))
        m15 = m1.set_index("time").resample("15min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).reset_index()
        r = run(m15, m1.iloc[15 * 520:].reset_index(drop=True), point=0.00001, spread=0.00015)
        self.assertIn("summary", r)
        for t in r["trades"]:
            self.assertIn(t["result"], ("win", "loss", "open", "skip"))


if __name__ == "__main__":
    unittest.main()
