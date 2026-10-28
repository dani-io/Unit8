"""
Market structure — trend state from confirmed ZigZag swings.

Rules (Daniel, see project spec "strategy-spec.md"):
    - Uptrend = HH + HL, downtrend = LH + LL (classic).
    - Range  = swings make neither; the market waits between a high and a low.
    - A high/low is broken only by a candle CLOSE beyond it.

State machine, evaluated bar by bar with only the swings known at that bar
(no look-ahead):

    range → up    close above the last high, and the last low is a HL
                  (higher than the low before it). Trend start is anchored
                  at the lowest low since the previous trend ended.
    up    → range close below the most recent low (B in the 1-2-3 picture).
                  Trend end is anchored at the highest high of the trend (A).
    up    → up    close above the last high = continuation (BOS).
    down  → mirror of up.

A ZigZag pivot is treated as known once the NEXT pivot exists (that's when
MT5 stops moving it); the last pivot is never used.
"""

from typing import List, Optional

import pandas as pd

from .base_tool import BaseTool, ToolResult
from .zigzag import ZigZag

UP, DOWN, RANGE = "up", "down", "range"


def label_pivots(pivots: List[dict]) -> List[dict]:
    """Add "label" (HH/LH/EH or HL/LL/EL) comparing each pivot to the
    previous pivot of the same type. The first of each type gets None."""
    out, last = [], {"high": None, "low": None}
    for p in pivots:
        prev = last[p["type"]]
        label = None
        if prev is not None:
            if p["type"] == "high":
                label = "HH" if p["price"] > prev else "LH" if p["price"] < prev else "EH"
            else:
                label = "HL" if p["price"] > prev else "LL" if p["price"] < prev else "EL"
        out.append({**p, "label": label})
        last[p["type"]] = p["price"]
    return out


def analyze_structure(df: pd.DataFrame, pivots: List[dict]) -> dict:
    """
    Args:
        df: OHLCV frame (needs "close"; "time" optional)
        pivots: ZigZag pivots (with "confirmed")

    Returns:
        {
          "pivots": confirmed pivots with "label" and "known_index",
          "events": [{"kind": "trend_start"|"trend_end"|"bos", "direction": "up"|"down",
                      "index": bar where the close confirmed it,
                      "level": broken price,
                      "anchor_index"/"anchor_price": start low / end high (or mirror)}],
          "states": per-bar list of "up" | "down" | "range",
          "state": state at the last bar,
          "trend": the current trend's anchor info (or None),
        }
    """
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)

    confirmed = [p for p in pivots if p.get("confirmed", True)]
    # A pivot is known when the next pivot (confirmed or not) has appeared
    known_at = [pivots[k + 1]["index"] for k in range(len(confirmed))]
    labeled = label_pivots(confirmed)
    for p, k in zip(labeled, known_at):
        p["known_index"] = k

    highs: List[dict] = []
    lows: List[dict] = []
    used = set()            # pivot indexes already broken in a given direction
    state = RANGE
    # Anchors are searched from here; start at the first swing so the very
    # first trend isn't anchored on an arbitrary bar at the edge of the data.
    since = confirmed[0]["index"] if confirmed else 0
    trend: Optional[dict] = None
    events: List[dict] = []
    states: List[str] = []
    k = 0

    for t in range(n):
        while k < len(labeled) and labeled[k]["known_index"] <= t:
            (highs if labeled[k]["type"] == "high" else lows).append(labeled[k])
            k += 1

        if highs and lows:
            c = close[t]
            h, l = highs[-1], lows[-1]

            if state == UP:
                if c < l["price"] and ("dn", l["index"]) not in used:
                    used.add(("dn", l["index"]))
                    a = trend["start_index"] + int(high[trend["start_index"]: t + 1].argmax())
                    events.append({"kind": "trend_end", "direction": UP, "index": t,
                                   "level": l["price"], "anchor_index": a, "anchor_price": float(high[a])})
                    state, since, trend = RANGE, a, None
                elif c > h["price"] and ("up", h["index"]) not in used:
                    used.add(("up", h["index"]))
                    events.append({"kind": "bos", "direction": UP, "index": t, "level": h["price"]})

            elif state == DOWN:
                if c > h["price"] and ("up", h["index"]) not in used:
                    used.add(("up", h["index"]))
                    a = trend["start_index"] + int(low[trend["start_index"]: t + 1].argmin())
                    events.append({"kind": "trend_end", "direction": DOWN, "index": t,
                                   "level": h["price"], "anchor_index": a, "anchor_price": float(low[a])})
                    state, since, trend = RANGE, a, None
                elif c < l["price"] and ("dn", l["index"]) not in used:
                    used.add(("dn", l["index"]))
                    events.append({"kind": "bos", "direction": DOWN, "index": t, "level": l["price"]})

            if state == RANGE:
                if (c > h["price"] and ("up", h["index"]) not in used
                        and len(lows) >= 2 and lows[-1]["price"] > lows[-2]["price"]):
                    used.add(("up", h["index"]))
                    s = since + int(low[since: t + 1].argmin())
                    trend = {"direction": UP, "start_index": s, "start_price": float(low[s])}
                    events.append({"kind": "trend_start", "direction": UP, "index": t,
                                   "level": h["price"], "anchor_index": s, "anchor_price": float(low[s])})
                    state = UP
                elif (c < l["price"] and ("dn", l["index"]) not in used
                        and len(highs) >= 2 and highs[-1]["price"] < highs[-2]["price"]):
                    used.add(("dn", l["index"]))
                    s = since + int(high[since: t + 1].argmax())
                    trend = {"direction": DOWN, "start_index": s, "start_price": float(high[s])}
                    events.append({"kind": "trend_start", "direction": DOWN, "index": t,
                                   "level": l["price"], "anchor_index": s, "anchor_price": float(high[s])})
                    state = DOWN

        states.append(state)

    has_time = "time" in df.columns
    if has_time:
        for e in events:
            e["time"] = df["time"].iloc[e["index"]]
            if "anchor_index" in e:
                e["anchor_time"] = df["time"].iloc[e["anchor_index"]]

    return {"pivots": labeled, "events": events, "states": states,
            "state": states[-1] if states else RANGE, "trend": trend}


class MarketStructure(BaseTool):
    """
    Trend state as a checklist tool.

    Reads zigzag pivots from context["zigzag"] when present, otherwise runs
    ZigZag itself (config keys depth/deviation/backstep are passed through).

    passed = market is trending; signal = "buy" in an uptrend, "sell" in a
    downtrend (direction only — not an entry).
    """

    name = "market_structure"

    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        pivots = None
        # The checklist stores each tool's result.data under its name
        if context and context.get("zigzag"):
            pivots = context["zigzag"].get("pivots")
        if pivots is None:
            point = context["tick"].get("point") if context and context.get("tick") else None
            pivots = ZigZag(self.config).find_pivots(df, point=point)

        result = analyze_structure(df, pivots)
        state = result["state"]
        if state == RANGE:
            return ToolResult(name=self.name, passed=False, data=result, reason="Market in range")
        return self._pass(signal="buy" if state == UP else "sell", data=result,
                          reason=f"{state}trend since bar {result['trend']['start_index']}")
