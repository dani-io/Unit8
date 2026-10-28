"""
Walk-forward backtest of the 1-2-3 setup — the bot's view, bar by bar.

At every bar t the full chain (ZigZag → structure → trendline → 1-2-3) runs
on the last `window` bars only, exactly like the live worker, so ZigZag
repaints and late swing confirmations are paid for honestly.

Entry modes (Daniel hasn't fixed the entry yet, so both are measured):
    a  enter as soon as 3 is confirmed (the first bar the pattern is "ready")
    b  enter once the pattern is seen "completed" (a close beyond 2 after 3).
       If 3 gets confirmed only after that close, the bot can't know earlier
       — it enters at the close of the bar where it first sees it.

    c  stop order AT 2 (classic 1-2-3): armed while 2 is valid (200%, 50%,
       trendline) and price is pulling back toward 3; filled the moment
       price trades through 2. 3 = the pullback extreme so far — no ZigZag
       confirmation needed, so a fast break isn't missed.

Exits: SL = 1 ± 20% AB, TP = 2 ∓ k·|2−3| for each k in tp_multiples.
Prices are bid; buys pay the spread on entry, sells on exit. When SL and TP
are both inside one bar, the SL is assumed first (conservative).

Usage:
    python -m backtest.walkforward_123 data/EURUSD_M15.csv [--spread 0.00015]
"""

import argparse
import json
from typing import Dict, List

import pandas as pd

from tools.market_structure import analyze_structure
from tools.pattern_123 import DEFAULTS, detect_123
from tools.trendline import build_trendlines
from tools.zigzag import ZigZag


def find_signals(df: pd.DataFrame, window: int = 600, point: float = 0.00001,
                 config: dict = None, step: int = 1) -> List[dict]:
    """Bar-by-bar scan. Returns one signal per (pattern, entry mode)."""
    zz_cfg = {k: v for k, v in (config or {}).items() if k in ("depth", "deviation", "backstep")}
    zz = ZigZag({"point": point, **zz_cfg})
    seen = set()
    signals = []
    armed: Dict[tuple, dict] = {}      # mode c: stop orders waiting at 2
    high, low, opn = df["high"].to_numpy(), df["low"].to_numpy(), df["open"].to_numpy()
    for t in range(window, len(df), step):
        # 1) fills of stop orders armed on the previous bar
        for key, o in list(armed.items()):
            sell = o["direction"] == "sell"
            hit = low[t] <= o["two"] if sell else high[t] >= o["two"]
            if hit:
                gap = opn[t] < o["two"] if sell else opn[t] > o["two"]
                fill = opn[t] if gap else o["two"]
                signals.append({**o["sig"], "mode": "c", "bar": t, "time": str(df["time"].iloc[t]),
                                "entry_close": float(fill), "fill_bar_open_beyond": bool(gap),
                                "three": o["three"]})
                seen.add((key, "c"))
                del armed[key]
        armed.clear()   # re-armed below from the current view

        sub = df.iloc[t - window + 1: t + 1].reset_index(drop=True)
        offset = t - window + 1
        piv = zz.find_pivots(sub, point=point)
        st = analyze_structure(sub, piv)
        pats = detect_123(sub, piv, st, build_trendlines(sub, st), config)
        for p in pats:
            key = (p["direction"], int(df["time"].iloc[p["A"]["index"] + offset].value))
            core_ok = all(p["checks"][k]["ok"] for k in ("trendline", "extension_200", "max_trend_retrace"))
            if (core_ok and p["status"] in ("waiting_3", "ready") and p["3"] is not None
                    and p["3"]["inside"] and (key, "c") not in seen):
                armed[key] = {"direction": p["direction"], "two": p["2"]["price"],
                              "three": p["3"]["price"], "sig": _signal(df, p, "c", t, offset)}
            if p["3"] is None or not p["3"]["confirmed"]:
                continue
            if p["status"] in ("ready", "completed") and (key, "a") not in seen:
                # 3 just got confirmed: the pivot after 3 is the newest one
                if piv[-2]["index"] == p["3"]["index"]:
                    seen.add((key, "a"))
                    signals.append(_signal(df, p, "a", t, offset))
            if p["status"] == "completed" and (key, "b") not in seen:
                seen.add((key, "b"))
                signals.append(_signal(df, p, "b", t, offset))
    return signals


def _signal(df, p, mode, t, offset):
    pts = {k: {"time": str(df["time"].iloc[p[k]["index"] + offset]), "price": p[k]["price"]}
           for k in ("A", "B", "1", "2", "3")}
    return {
        "mode": mode, "direction": p["direction"], "bar": t, "time": str(df["time"].iloc[t]),
        "completed_time": str(df["time"].iloc[p["completed_at"] + offset]) if p.get("completed_at") is not None else None,
        "entry_close": float(df["close"].iloc[t]),
        "points": pts, "ab": p["ab"], "extension": p["extension"],
        "three_ideal": bool(p["3"]["ideal"]), "sl": p["sl"],
        "two": p["2"]["price"], "three": p["3"]["price"],
    }


def simulate(df: pd.DataFrame, sig: dict, k: float, spread: float) -> dict:
    """Run one trade forward from the signal bar (mode c: from the fill bar,
    which may already reach TP if price kept going through 2)."""
    sell = sig["direction"] == "sell"
    entry = sig["entry_close"] if sell else sig["entry_close"] + spread
    sl = sig["sl"]
    tp = sig["two"] - k * abs(sig["three"] - sig["two"]) if sell else sig["two"] + k * abs(sig["three"] - sig["two"])
    risk = (sl - entry) if sell else (entry - sl)
    reward = (entry - tp) if sell else (tp - entry)
    res = {**sig, "k": k, "entry": entry, "tp": tp, "risk": risk, "planned_rr": reward / risk if risk > 0 else None}
    if risk <= 0 or reward <= 0:
        return {**res, "result": "skip", "r": 0.0, "exit_time": None}

    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    first = sig["bar"] if sig["mode"] == "c" else sig["bar"] + 1
    for j in range(first, len(df)):
        if j == sig["bar"]:
            # Fill bar: price was moving through 2 toward TP — count TP only
            if (sell and low[j] + spread <= tp) or (not sell and high[j] >= tp):
                return {**res, "result": "win", "r": reward / risk, "exit_time": str(df["time"].iloc[j])}
            continue
        if sell:
            if high[j] + spread >= sl:
                return {**res, "result": "loss", "r": -1.0, "exit_time": str(df["time"].iloc[j])}
            if low[j] + spread <= tp:
                return {**res, "result": "win", "r": (entry - tp) / risk, "exit_time": str(df["time"].iloc[j])}
        else:
            if low[j] <= sl:
                return {**res, "result": "loss", "r": -1.0, "exit_time": str(df["time"].iloc[j])}
            if high[j] >= tp:
                return {**res, "result": "win", "r": (tp - entry) / risk, "exit_time": str(df["time"].iloc[j])}
    return {**res, "result": "open", "r": 0.0, "exit_time": None}


def summarize(trades: List[dict]) -> Dict[str, dict]:
    out = {}
    for mode in sorted({t["mode"] for t in trades}):
        for k in sorted({t["k"] for t in trades}):
            g = [t for t in trades if t["mode"] == mode and t["k"] == k]
            closed = [t for t in g if t["result"] in ("win", "loss")]
            wins = [t for t in closed if t["result"] == "win"]
            out[f"{mode}/k={k:g}"] = {
                "signals": len(g),
                "closed": len(closed),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(closed), 3) if closed else None,
                "total_r": round(sum(t["r"] for t in closed), 2),
                "avg_r": round(sum(t["r"] for t in closed) / len(closed), 2) if closed else None,
                "skipped": sum(t["result"] == "skip" for t in g),
                "open": sum(t["result"] == "open" for t in g),
            }
    return out


def run(df: pd.DataFrame, spread: float = 0.00015, point: float = 0.00001,
        window: int = 600, tp_multiples=(1.0, 2.0), config: dict = None) -> dict:
    signals = find_signals(df, window=window, point=point, config=config)
    trades = [simulate(df, s, k, spread) for s in signals for k in tp_multiples]
    return {"config": {**DEFAULTS, **(config or {}), "window": window, "spread": spread},
            "summary": summarize(trades), "trades": trades}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--spread", type=float, default=0.00015)
    ap.add_argument("--point", type=float, default=0.00001)
    ap.add_argument("--window", type=int, default=600)
    ap.add_argument("--out")
    a = ap.parse_args()
    df = pd.read_csv(a.csv, parse_dates=["time"])
    r = run(df, spread=a.spread, point=a.point, window=a.window)
    print(json.dumps(r["summary"], indent=2))
    if a.out:
        json.dump(r, open(a.out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
