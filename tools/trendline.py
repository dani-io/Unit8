"""
Trendline — drawn the way Daniel draws it by eye.

Uptrend (downtrend is the mirror, using highs):
    - Starts at the low the trend started from (market_structure's anchor).
    - Goes under the higher lows: of all lines from the anchor through a
      later swing low, take the one with the smallest slope, so every
      other swing low sits on or above it (the lower envelope).
    - Only lows formed AFTER the trend was confirmed count. The HL that
      gave birth to the trend sits right next to the anchor and is ignored
      by eye (Daniel's 13-Oct example: the line goes from the start low to B
      and that early HL falls below it). Until such a low exists, the
      earlier lows are used as a temporary line.
    - Re-fitted each time a new swing low becomes known; frozen when the
      trend ends (no more lows are added — point 2 must not bend it).
    - Extended until the next trend starts (or the end of data).

Prices are the ZigZag pivot prices (wicks). Slope is per BAR, not per
second, so weekends don't bend the line — same as MT5's chart.

A close beyond the line is reported as a "cross". Whether a cross is a
real BREAK (price reaching 200% of AB) is decided by pattern_123.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from .base_tool import BaseTool, ToolResult
from .market_structure import analyze_structure
from .zigzag import ZigZag


def _fit(anchor_i: int, anchor_p: float, points: List[dict], up: bool) -> Optional[dict]:
    """Envelope line from the anchor: min slope (up) / max slope (down)."""
    best = None
    for p in points:
        di = p["index"] - anchor_i
        if di <= 0:
            continue
        slope = (p["price"] - anchor_p) / di
        if best is None or (slope < best["slope"] if up else slope > best["slope"]):
            best = {"slope": slope, "through_index": p["index"], "through_price": p["price"]}
    return best


def build_trendlines(df: pd.DataFrame, structure: dict) -> List[dict]:
    """
    One trendline per trend found by analyze_structure().

    Returns:
        [{
          "direction": "up" | "down",
          "anchor_index", "anchor_price",       — trend start
          "through_index", "through_price",     — the swing the line rests on
          "slope",                              — price per bar
          "valid_from": bar the line first existed (no look-ahead),
          "frozen_at": bar the trend ended (None if still running),
          "end_index", "end_price",             — where drawing stops
          "refits": how many times the line was redrawn,
          "touches": swing indexes lying on the line (within 2% of the trend's range),
          "crosses": [bars where the close crossed the line valid AT THAT BAR]
                     — what a live bot would have seen; use this for logic,
          "final_crosses": [bars where the close crossed the final line] — for display,
        }, ...]
    """
    close = df["close"].to_numpy(dtype=float)
    n = len(df)
    events = structure["events"]
    pivots = structure["pivots"]          # confirmed, with known_index

    starts = [e for e in events if e["kind"] == "trend_start"]
    lines = []

    for k, st in enumerate(starts):
        up = st["direction"] == "up"
        kind = "low" if up else "high"
        a_i, a_p = st["anchor_index"], st["anchor_price"]
        end_ev = next((e for e in events if e["kind"] == "trend_end"
                       and e["direction"] == st["direction"] and e["index"] >= st["index"]), None)
        frozen_at = end_ev["index"] if end_ev else None
        stop = starts[k + 1]["index"] - 1 if k + 1 < len(starts) else n - 1

        candidates = sorted(
            (p for p in pivots if p["type"] == kind and p["index"] > a_i
             and (frozen_at is None or p["known_index"] <= frozen_at)),
            key=lambda p: p["known_index"],
        )

        known: List[dict] = []      # all known swing lows/highs since the anchor
        after: List[dict] = []      # ... formed after the trend was confirmed
        line = None
        valid_from = None
        refits = 0
        crosses = []
        c = 0
        prev_side = None
        for t in range(st["index"], stop + 1):
            changed = False
            while c < len(candidates) and candidates[c]["known_index"] <= t:
                known.append(candidates[c])
                if candidates[c]["index"] >= st["index"]:
                    after.append(candidates[c])
                c += 1
                changed = True
            if changed:
                new = _fit(a_i, a_p, after or known, up)
                if new and (line is None or new["through_index"] != line["through_index"]):
                    refits += line is not None
                    line = new
                    if valid_from is None:
                        valid_from = t
            if line is None:
                continue
            value = a_p + line["slope"] * (t - a_i)
            side = bool(close[t] >= value if up else close[t] <= value)   # True = trend side
            if prev_side is True and side is False:
                crosses.append(t)
            prev_side = side

        if line is None:
            continue

        final_crosses = []
        start = max(valid_from, line["through_index"])
        for t in range(start + 1, stop + 1):
            v0, v1 = line_value_raw(a_i, a_p, line["slope"], t - 1), line_value_raw(a_i, a_p, line["slope"], t)
            was = close[t - 1] >= v0 if up else close[t - 1] <= v0
            now = close[t] >= v1 if up else close[t] <= v1
            if was and not now:
                final_crosses.append(t)

        span = max(abs(p["price"] - a_p) for p in known) or 1e-12
        touches = [p["index"] for p in known
                   if abs(p["price"] - (a_p + line["slope"] * (p["index"] - a_i))) <= 0.02 * span]
        lines.append({
            "direction": st["direction"],
            "anchor_index": a_i, "anchor_price": a_p,
            "through_index": line["through_index"], "through_price": line["through_price"],
            "slope": line["slope"],
            "valid_from": valid_from,
            "frozen_at": frozen_at,
            "end_index": stop,
            "end_price": a_p + line["slope"] * (stop - a_i),
            "refits": refits,
            "touches": touches,
            "crosses": crosses,
            "final_crosses": final_crosses,
        })

    if "time" in df.columns:
        for ln in lines:
            for key in ("anchor", "through", "end"):
                ln[f"{key}_time"] = df["time"].iloc[ln[f"{key}_index"]]
            ln["cross_times"] = [df["time"].iloc[i] for i in ln["crosses"]]
            ln["final_cross_times"] = [df["time"].iloc[i] for i in ln["final_crosses"]]
    return lines


def line_value_raw(anchor_index: int, anchor_price: float, slope: float, index: int) -> float:
    return anchor_price + slope * (index - anchor_index)


def line_value(line: dict, index: int) -> float:
    """Price of a trendline at a bar index."""
    return line["anchor_price"] + line["slope"] * (index - line["anchor_index"])


class TrendlineBreak(BaseTool):
    """
    Current trendline and whether price has closed across it.

    Uses context["market_structure"] when the checklist already ran it,
    otherwise runs ZigZag + market structure itself (config passes through).

    passed = the latest trendline was crossed by a close (signal = direction
    of the cross). This is a raw cross — the 200% rule that turns it into a
    real break lives in pattern_123.
    """

    name = "trendline_break"

    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        structure = context.get("market_structure") if context else None
        if not structure or "events" not in structure:
            pivots = None
            if context and context.get("zigzag"):
                pivots = context["zigzag"].get("pivots")
            if pivots is None:
                point = context["tick"].get("point") if context and context.get("tick") else None
                pivots = ZigZag(self.config).find_pivots(df, point=point)
            structure = analyze_structure(df, pivots)

        lines = build_trendlines(df, structure)
        if not lines:
            return self._fail("No trendline yet")
        last = lines[-1]
        data = {"trendlines": lines, "trendline": last}
        if not last["crosses"]:
            return ToolResult(name=self.name, passed=False, data=data,
                              reason=f"{last['direction']} trendline intact")
        signal = "sell" if last["direction"] == "up" else "buy"
        return self._pass(signal=signal, data=data,
                          reason=f"{last['direction']} trendline crossed at bar {last['crosses'][0]}")
