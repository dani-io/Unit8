"""
1-2-3 reversal — Daniel's definition (project spec "strategy-spec.md").

End of an uptrend → SELL setup (a downtrend's end is the exact mirror → BUY):

    A  last HH of the uptrend (a confirmed swing high while the trend is up)
    B  the corrective low right after A.  AB = |A − B| is the ruler
       (Fib A = 0%, B = 100%)
    1  the high after B, BEFORE the trendline break; within ±20% of AB of A
    2  the low after the trendline break; on the way a candle CLOSES below B
         - must reach at least 200%:      A − 2·AB
         - must not exceed 50% of the previous trend (trend start → A),
           otherwise it may only be a higher-timeframe correction
    3  the pullback between 1 and 2; ideal below B (B flips to resistance),
       above B but below 1 is valid, not ideal
    Invalid: a candle closes above 1 (the uptrend continues).
    SL = 1 + 20% of AB (beyond 1).   TP = 2 − k·|2 − 3|, k = 2 (Daniel's
    training rules; k = 1 = "the size of the 2→3 pullback").

The trendline break counts only through the 200% rule — a close across the
line without reaching 200% is not a break.

Statuses:
    forming    2 still extending, not yet 200%
    waiting_3  2 is valid, 3 not formed/confirmed yet
    ready      3 confirmed inside its range
    completed  after 3, a candle closed beyond 2 (the pattern played out)
    invalid    closed beyond 1 after 2, before the pattern completed
    rejected   failed a rule (see "checks")
"""

from typing import List, Optional

import pandas as pd

from .base_tool import BaseTool, ToolResult
from .market_structure import analyze_structure
from .trendline import build_trendlines, line_value
from .zigzag import ZigZag

DEFAULTS = {
    "p1_tolerance": 0.20,   # |1 − A| ≤ 20% of AB
    "min_extension": 2.0,   # 2 must reach 200% of AB from A
    "max_trend_retrace": 0.5,  # A→2 ≤ 50% of the previous trend
    "sl_buffer": 0.20,      # SL = 1 + 20% of AB beyond it
    "tp_multiple": 2.0,     # TP = 2 + k·|2−3| in the trade direction
}


def _pt(p: dict) -> dict:
    return {"index": p["index"], "price": p["price"], "confirmed": p.get("confirmed", True)}


def detect_123(df: pd.DataFrame, pivots: List[dict], structure: dict,
               trendlines: List[dict], config: dict = None) -> List[dict]:
    """
    Scan every A candidate and evaluate the pattern after it.

    Args:
        pivots: ALL ZigZag pivots (the last, unconfirmed one may be 2 or 3)
        structure: analyze_structure() output (labels + per-bar states)
        trendlines: build_trendlines() output
    """
    cfg = {**DEFAULTS, **(config or {})}
    close = df["close"].to_numpy(dtype=float)
    n = len(df)
    states = structure["states"]
    labels = {p["index"]: p["label"] for p in structure["pivots"]}
    out = []

    for i, a in enumerate(pivots):
        if not a.get("confirmed", True) or i + 3 >= len(pivots):
            continue
        if a["type"] == "high" and labels.get(a["index"]) == "HH" and states[a["index"]] == "up":
            d, direction, trend_dir = -1, "sell", "up"      # new direction: down
        elif a["type"] == "low" and labels.get(a["index"]) == "LL" and states[a["index"]] == "down":
            d, direction, trend_dir = +1, "buy", "down"
        else:
            continue

        b, p1, p2 = pivots[i + 1], pivots[i + 2], pivots[i + 3]
        p3 = pivots[i + 4] if i + 4 < len(pivots) else None
        A, B, P1, P2 = a["price"], b["price"], p1["price"], p2["price"]
        ab = abs(A - B)
        if ab <= 0 or not p1.get("confirmed", True):
            continue

        def past(price, level):          # beyond level in the NEW direction
            return price * d > level * d

        # Candidate gate: 1 near A, and B broken by a close on the way to 2
        if abs(P1 - A) > cfg["p1_tolerance"] * ab:
            continue
        seg_end = p2["index"]
        b_break = next((t for t in range(p1["index"] + 1, seg_end + 1) if past(close[t], B)), None)
        if b_break is None:
            continue

        checks = {}
        # Trendline of the trend that A belongs to
        line = None
        for ln in trendlines:
            if ln["direction"] == trend_dir and ln["anchor_index"] < a["index"] <= ln["end_index"]:
                line = ln
        if line is None:
            checks["trendline"] = (False, "no trendline for this trend")
        else:
            above_at_1 = not past(P1, line_value(line, p1["index"]))
            cross = next((t for t in range(p1["index"] + 1, seg_end + 1)
                          if past(close[t], line_value(line, t))), None)
            ok = above_at_1 and cross is not None
            checks["trendline"] = (ok, "1 before the break, closed across the line on the way to 2"
                                   if ok else "trendline not crossed between 1 and 2")

        ext = abs(A - P2) / ab
        level_200 = A + d * cfg["min_extension"] * ab
        checks["extension_200"] = (ext >= cfg["min_extension"],
                                   f"2 reached {ext * 100:.0f}% of AB (min {cfg['min_extension'] * 100:.0f}%)")

        trend_len = abs(A - line["anchor_price"]) if line else None
        if trend_len:
            r = abs(A - P2) / trend_len
            checks["max_trend_retrace"] = (r <= cfg["max_trend_retrace"],
                                           f"A→2 is {r * 100:.0f}% of the previous trend (max {cfg['max_trend_retrace'] * 100:.0f}%)")
        else:
            checks["max_trend_retrace"] = (False, "previous trend length unknown")

        three = None
        if p3 is not None:
            lo, hi = sorted((P1, P2))
            inside = lo < p3["price"] < hi
            ideal = inside and past(p3["price"], B)
            three = {**_pt(p3), "inside": inside, "ideal": ideal}
            checks["point_3"] = (inside, "3 between 1 and 2" + (", beyond B (ideal)" if ideal else ", not beyond B (valid)")
                                 if inside else "3 outside the 1–2 range")

        # After 2: whichever comes first — a close beyond 1 (invalid) or,
        # once 3 exists, a close beyond 2 (completed). Later price action
        # no longer belongs to this pattern.
        invalid_at = completed_at = None
        for t in range(p2["index"] + 1, n):
            if past(P1, close[t]):
                invalid_at = t
                break
            if three is not None and t > three["index"] and past(close[t], P2):
                completed_at = t
                break

        core_ok = all(checks[k][0] for k in ("trendline", "extension_200", "max_trend_retrace"))
        if not p2.get("confirmed", True) and not checks["extension_200"][0]:
            status = "forming"
        elif not core_ok:
            status = "rejected"
        elif invalid_at is not None:
            status = "invalid"
        elif three is None or not three["confirmed"]:
            status = "waiting_3"
        elif not three["inside"]:
            status = "rejected"
        elif completed_at is not None:
            status = "completed"
        else:
            status = "ready"

        sl = P1 - d * cfg["sl_buffer"] * ab
        tp = P2 + d * cfg["tp_multiple"] * abs(three["price"] - P2) if three else None

        out.append({
            "direction": direction,
            "status": status,
            "A": _pt(a), "B": _pt(b), "1": _pt(p1), "2": _pt(p2), "3": three,
            "ab": ab,
            "level_200": level_200,
            "extension": ext,
            "b_break_index": b_break,
            "invalid_at": invalid_at,
            "completed_at": completed_at,
            "sl": sl,
            "tp": tp,
            "checks": {k: {"ok": v[0], "note": v[1]} for k, v in checks.items()},
        })

    if "time" in df.columns:
        t = df["time"]
        for p in out:
            for k in ("A", "B", "1", "2", "3"):
                if p[k]:
                    p[k]["time"] = t.iloc[p[k]["index"]]
    return out


class Pattern123(BaseTool):
    """
    Checklist tool. passed = the latest pattern is "ready" and price is still
    on the leg that started at 3 (no newer swing yet), signal = buy/sell.

    data: {"patterns": [...], "pattern": latest, "sl": point 1}
    "tp" is only handed to the execution engine when config "allow_orders"
    is true — the engine needs both sl and tp, so by default the bot only
    analyses and never places an order.

    Config: p1_tolerance, min_extension, max_trend_retrace, sl_buffer,
            tp_multiple, allow_orders (+ ZigZag keys).
    """

    name = "pattern_123"

    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        context = context or {}
        pivots = context.get("zigzag", {}).get("pivots") if context.get("zigzag") else None
        if pivots is None:
            point = context["tick"].get("point") if context.get("tick") else None
            pivots = ZigZag(self.config).find_pivots(df, point=point)
        structure = context.get("market_structure")
        if not structure or "events" not in structure:
            structure = analyze_structure(df, pivots)
        tl = context.get("trendline_break", {}).get("trendlines") if context.get("trendline_break") else None
        if tl is None:
            tl = build_trendlines(df, structure)

        patterns = detect_123(df, pivots, structure, tl, self.config)
        live = [p for p in patterns if p["status"] in ("forming", "waiting_3", "ready", "completed")]
        if not live:
            return self._fail("No active 1-2-3")
        p = live[-1]
        data = {"patterns": patterns, "pattern": p, "sl": p["sl"]}
        if self.config.get("allow_orders") and p["tp"] is not None:
            data["tp"] = p["tp"]
        fresh = p["3"] is not None and pivots[-1]["index"] > p["3"]["index"] and \
            pivots.index(next(q for q in pivots if q["index"] == p["3"]["index"])) == len(pivots) - 2
        # Entry rules are still open: for now the setup counts while price is
        # on the leg that started at 3 (ready, or already closed beyond 2).
        if p["status"] in ("ready", "completed") and fresh:
            return self._pass(signal=p["direction"], data=data,
                              reason=f"1-2-3 {p['direction']} {p['status']}, 2 at {p['extension'] * 100:.0f}% of AB"
                                     + (", 3 ideal" if p["3"]["ideal"] else ""))
        return ToolResult(name=self.name, passed=False, data=data, reason=f"1-2-3 {p['direction']} {p['status']}")
