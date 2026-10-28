"""
ZigZag — a faithful port of MetaTrader 5's built-in ZigZag (ZigZag.mq5).

Same inputs, same algorithm, so the pivots here should match the MT5
indicator drawn on the same chart:

    depth:      bars a high/low must dominate (MT5 InpDepth, default 12)
    deviation:  points price must move to count as a new extreme
                (MT5 InpDeviation, default 5 → 5 * point)
    backstep:   bars within which a better extreme replaces an older one
                (MT5 InpBackstep, default 3)

The LAST pivot is never final — while price keeps extending the current
leg, MT5 moves it (the "repaint" visible on any ZigZag). Every pivot is
returned with `confirmed`: False for the last one, True for all others.
Strategy code must only build on confirmed pivots.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from .base_tool import BaseTool, ToolResult

# Search states (mirrors the MQL5 enum)
_EXTREMUM, _PEAK, _BOTTOM = 0, 1, -1


def _infer_point(prices: np.ndarray) -> float:
    """Guess the symbol's point from price decimals (fallback only)."""
    decimals = 0
    for p in prices[-200:]:
        s = f"{p:.10f}".rstrip("0")
        if "." in s:
            decimals = max(decimals, len(s.split(".")[1]))
    return 10 ** -min(decimals, 8) if decimals else 1.0


def zigzag(high: np.ndarray, low: np.ndarray, depth: int = 12,
           deviation: int = 5, backstep: int = 3, point: float = 0.00001) -> np.ndarray:
    """
    Run the MT5 ZigZag. Returns a buffer the length of the input:
    the pivot price at pivot bars, 0.0 elsewhere.
    """
    n = len(high)
    zz = np.zeros(n)
    if n < depth + 1:
        return zz

    high_map = np.zeros(n)
    low_map = np.zeros(n)
    dev = deviation * point
    last_low = last_high = 0.0

    # --- Pass 1: candidate extremes ---
    for shift in range(depth, n):
        # Lowest low of [shift-depth+1 .. shift]
        val = low[shift - depth + 1: shift + 1].min()
        if val == last_low:
            val = 0.0
        else:
            last_low = val
            if low[shift] - val > dev:
                val = 0.0
            else:
                for back in range(1, backstep + 1):
                    res = low_map[shift - back]
                    if res != 0 and res > val:
                        low_map[shift - back] = 0.0
        low_map[shift] = val if low[shift] == val else 0.0

        val = high[shift - depth + 1: shift + 1].max()
        if val == last_high:
            val = 0.0
        else:
            last_high = val
            if val - high[shift] > dev:
                val = 0.0
            else:
                for back in range(1, backstep + 1):
                    res = high_map[shift - back]
                    if res != 0 and res < val:
                        high_map[shift - back] = 0.0
        high_map[shift] = val if high[shift] == val else 0.0

    # --- Pass 2: pick alternating pivots ---
    state = _EXTREMUM
    last_low = last_high = 0.0
    last_low_pos = last_high_pos = -1

    for shift in range(depth, n):
        if state == _EXTREMUM:
            if last_low == 0.0 and last_high == 0.0:
                if high_map[shift] != 0:
                    last_high, last_high_pos = high[shift], shift
                    state = _BOTTOM
                    zz[shift] = last_high
                if low_map[shift] != 0:
                    last_low, last_low_pos = low[shift], shift
                    state = _PEAK
                    zz[shift] = last_low
        elif state == _PEAK:
            if low_map[shift] != 0 and low_map[shift] < last_low and high_map[shift] == 0:
                zz[last_low_pos] = 0.0
                last_low_pos, last_low = shift, low_map[shift]
                zz[shift] = last_low
            if high_map[shift] != 0 and low_map[shift] == 0:
                last_high_pos, last_high = shift, high_map[shift]
                zz[shift] = last_high
                state = _BOTTOM
        elif state == _BOTTOM:
            if high_map[shift] != 0 and high_map[shift] > last_high and low_map[shift] == 0:
                zz[last_high_pos] = 0.0
                last_high_pos, last_high = shift, high_map[shift]
                zz[shift] = last_high
            if low_map[shift] != 0 and high_map[shift] == 0:
                last_low_pos, last_low = shift, low_map[shift]
                zz[shift] = last_low
                state = _PEAK

    return zz


class ZigZag(BaseTool):
    """
    MT5 ZigZag as a Unit8 tool.

    Config:
        depth: int = 12
        deviation: int = 5     — in points
        backstep: int = 3
        point: float           — symbol point; else context["tick"]["point"],
                                 else inferred from price decimals

    Output data["pivots"]: same shape as swing_detector's swings, plus
    "confirmed":
        [{"type": "high", "index": 45, "price": 1.0892, "time": ..., "confirmed": True}, ...]
    """

    name = "zigzag"

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.depth = int(self.config.get("depth", 12))
        self.deviation = int(self.config.get("deviation", 5))
        self.backstep = int(self.config.get("backstep", 3))
        self.point: Optional[float] = self.config.get("point")

    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        point = self.point
        if point is None and context and context.get("tick"):
            point = context["tick"].get("point")
        pivots = self.find_pivots(df, point=point)
        if len(pivots) < 2:
            return self._fail("Not enough ZigZag pivots")
        confirmed = sum(p["confirmed"] for p in pivots)
        return self._pass(
            data={"pivots": pivots},
            reason=f"{len(pivots)} pivots ({confirmed} confirmed)",
        )

    def find_pivots(self, df: pd.DataFrame, point: float = None) -> List[dict]:
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        if point is None:
            point = self.point or _infer_point(np.concatenate([high, low]))

        zz = zigzag(high, low, self.depth, self.deviation, self.backstep, point)
        has_time = "time" in df.columns

        pivots = []
        for i in np.flatnonzero(zz):
            price = zz[i]
            # A bar can match both; MT5's state machine decides — compare to the
            # previous pivot to label it.
            if high[i] == price and low[i] != price:
                kind = "high"
            elif low[i] == price and high[i] != price:
                kind = "low"
            else:
                kind = "low" if pivots and pivots[-1]["type"] == "high" else "high"
            pivots.append({
                "type": kind,
                "index": int(i),
                "price": float(price),
                "time": df["time"].iloc[i] if has_time else None,
                "confirmed": True,
            })
        if pivots:
            pivots[-1]["confirmed"] = False
        return pivots
