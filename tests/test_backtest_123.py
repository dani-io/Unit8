"""
Walk-forward backtest on the synthetic 1-2-3 path.

Run:
    python -m unittest tests.test_backtest_123 -v
"""

import unittest

import pandas as pd

from backtest.walkforward_123 import find_signals, run, simulate
from tests.test_market_structure import _path
from tests.test_pattern_123 import BASE


def _df(moves):
    df = _path(moves)
    df["time"] = pd.date_range("2026-01-01", periods=len(df), freq="15min")
    return df


class BacktestTest(unittest.TestCase):
    def test_sell_signal_wins_both_modes(self):
        df = _df(BASE + [-0.0100, +0.0020, -0.0050])
        sig = find_signals(df, window=200, config={"depth": 5})
        self.assertEqual({s["mode"] for s in sig}, {"a", "b", "c"})
        c = next(s for s in sig if s["mode"] == "c")
        self.assertLessEqual(c["entry_close"], c["two"])         # sell stop filled at (or through) 2
        for s in sig:
            self.assertEqual(s["direction"], "sell")
            self.assertEqual(simulate(df, s, 2.0, 0.00015)["result"], "win")

    def test_stopped_out_when_price_rallies_through_sl(self):
        # after 3 price turns down a little, then rallies far above 1 + 20% AB
        df = _df(BASE[:-1] + [-0.0010, +0.0200])
        r = run(df, window=200, config={"depth": 5})
        a = [t for t in r["trades"] if t["mode"] == "a"]
        self.assertTrue(a)
        self.assertTrue(all(t["result"] == "loss" for t in a))
        self.assertTrue(all(t["r"] == -1.0 for t in a))

    def test_no_signal_without_pattern(self):
        df = _df([+0.0050, -0.0020] * 8)
        self.assertEqual(find_signals(df, window=150, config={"depth": 5}), [])


if __name__ == "__main__":
    unittest.main()
