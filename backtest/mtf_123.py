"""
Multi-timeframe 1-2-3 — Daniel's entry, walk-forward.

    HTF (M15) confirms:  a 1-2-3 is valid up to point 2 (200%, 50% of the
                         trend, trendline) and price is pulling back toward 3,
                         still below HTF 1 (sell; mirror for buy).
    LTF (M1) enters:     that pullback is an uptrend on M1. A complete M1
                         1-2-3 in the HTF direction (same rules) forms inside
                         it; a stop order at M1 point 2 opens the trade.
    SL / TP (fixed):     from the M1 pattern — SL = M1 1 ± 20% of M1 AB,
                         TP = M1 2 ∓ k·|M1 2 − M1 3|.  No trailing, no
                         break-even, no partial close (to be decided later).

One trade per HTF setup. Each M15 bar is evaluated when it closes; M1 runs
bar by bar while an HTF setup is armed. Every step only sees bars that
already closed. Prices are bid; buys pay the spread on entry, sells on exit;
SL is assumed first when SL and TP share an M1 bar.

Usage:
    python -m backtest.mtf_123 data/EURUSD-M15.csv data/EURUSD-M1.csv --point 0.00001
"""

import argparse
import json
from typing import Dict, List

import numpy as np
import pandas as pd

from tools.market_structure import analyze_structure
from tools.pattern_123 import detect_123
from tools.trendline import build_trendlines
from tools.zigzag import ZigZag

HTF_MINUTES = 15
CORE = ("trendline", "extension_200", "max_trend_retrace")


def _patterns(df: pd.DataFrame, zz: ZigZag, point: float, config: dict = None):
    piv = zz.find_pivots(df, point=point)
    st = analyze_structure(df, piv)
    return detect_123(df, piv, st, build_trendlines(df, st), config)


def htf_setups(htf: pd.DataFrame, zz: ZigZag, point: float, config: dict = None) -> List[dict]:
    """HTF patterns valid up to 2 whose pullback toward 3 is still running."""
    out = []
    last_close = htf["close"].iloc[-1]
    for p in _patterns(htf, zz, point, config):
        if p["status"] not in ("waiting_3", "ready"):
            continue
        if not all(p["checks"][k]["ok"] for k in CORE):
            continue
        sell = p["direction"] == "sell"
        p1, p2 = p["1"]["price"], p["2"]["price"]
        # still between 2 and 1 (not completed, not invalid)
        if not (p2 < last_close < p1 if sell else p1 < last_close < p2):
            continue
        out.append({
            "key": (p["direction"], str(htf["time"].iloc[p["A"]["index"]])),
            "direction": p["direction"],
            "htf_1": p1, "htf_2": p2, "htf_B": p["B"]["price"],
            "htf_2_time": htf["time"].iloc[p["2"]["index"]],
            "htf_points": {k: {"time": str(htf["time"].iloc[p[k]["index"]]), "price": p[k]["price"]}
                           for k in ("A", "B", "1", "2")},
        })
    return out


def run(htf: pd.DataFrame, ltf: pd.DataFrame, point: float, spread: float,
        htf_window: int = 512, ltf_window: int = 512, ltf_context: int = 120,
        tp_multiples=(1.0, 2.0), config: dict = None) -> dict:
    zz = ZigZag({"point": point})
    lt = ltf["time"].to_numpy()
    lh, ll, lo = (ltf[c].to_numpy() for c in ("high", "low", "open"))

    traded = set()
    trades: List[dict] = []
    setups_seen: Dict[tuple, dict] = {}

    first_ltf = ltf["time"].iloc[0]
    start_h = max(htf_window, int(np.searchsorted(htf["time"].to_numpy(), np.datetime64(first_ltf))) + 1)

    armed: Dict[tuple, dict] = {}       # M1 stop orders, carried across HTF bars
    for h in range(start_h, len(htf)):
        sub = htf.iloc[h - htf_window + 1: h + 1].reset_index(drop=True)
        setups = [s for s in htf_setups(sub, zz, point, config) if s["key"] not in traded]
        for s in setups:
            setups_seen.setdefault(s["key"], s)
        keys = {s["key"] for s in setups}
        armed = {k: v for k, v in armed.items() if k in keys}   # HTF setup gone → cancel
        if not setups:
            continue

        # M1 bars that close during the NEXT HTF bar (after HTF bar h closed)
        t_from = htf["time"].iloc[h] + pd.Timedelta(minutes=HTF_MINUTES)
        t_to = t_from + pd.Timedelta(minutes=HTF_MINUTES)
        m_from = int(np.searchsorted(lt, np.datetime64(t_from)))
        m_to = int(np.searchsorted(lt, np.datetime64(t_to)))

        for m in range(m_from, m_to):
            # 1) stop orders armed on the previous M1 bar
            for key, o in list(armed.items()):
                sell = o["direction"] == "sell"
                if (ll[m] <= o["two"]) if sell else (lh[m] >= o["two"]):
                    gap = (lo[m] < o["two"]) if sell else (lo[m] > o["two"])
                    fill = float(lo[m] if gap else o["two"])
                    traded.add(key)
                    for k in tp_multiples:
                        trades.append(_simulate(ltf, m, fill, o, k, spread))
                    armed.pop(key)
            armed.clear()

            # 2) re-arm from the M1 view at this bar
            live = [s for s in setups if s["key"] not in traded]
            if not live:
                break
            oldest = min(s["htf_2_time"] for s in live)
            m0 = max(0, min(m - ltf_window + 1, int(np.searchsorted(lt, np.datetime64(oldest))) - ltf_context))
            m0 = max(m0, m - 3000)
            lsub = ltf.iloc[m0: m + 1].reset_index(drop=True)
            close_now = lsub["close"].iloc[-1]
            for lp in _patterns(lsub, zz, point, config):
                if lp["status"] not in ("waiting_3", "ready") or lp["3"] is None or not lp["3"]["inside"]:
                    continue
                if not all(lp["checks"][k]["ok"] for k in CORE):
                    continue
                for s in live:
                    if lp["direction"] != s["direction"] or s["key"] in armed:
                        continue
                    sell = s["direction"] == "sell"
                    a_time = lsub["time"].iloc[lp["A"]["index"]]
                    # the M1 pattern must live inside the HTF pullback, below HTF 1
                    if a_time < s["htf_2_time"]:
                        continue
                    if (lp["1"]["price"] >= s["htf_1"]) if sell else (lp["1"]["price"] <= s["htf_1"]):
                        continue
                    if not (s["htf_2"] < close_now < s["htf_1"] if sell else s["htf_1"] < close_now < s["htf_2"]):
                        continue
                    armed[s["key"]] = {
                        "direction": s["direction"], "two": lp["2"]["price"], "three": lp["3"]["price"],
                        "sl": lp["sl"], "setup": s,
                        "ltf_points": {k: {"time": str(lsub["time"].iloc[lp[k]["index"]]), "price": lp[k]["price"]}
                                       for k in ("A", "B", "1", "2", "3")},
                        "ltf_extension": lp["extension"], "ltf_three_ideal": bool(lp["3"]["ideal"]),
                    }

    return {"summary": summarize(trades), "trades": trades, "htf_setups": len(setups_seen)}


def _simulate(ltf: pd.DataFrame, m: int, fill: float, o: dict, k: float, spread: float) -> dict:
    sell = o["direction"] == "sell"
    entry = fill if sell else fill + spread
    two, three, sl = o["two"], o["three"], o["sl"]
    tp = two - k * abs(three - two) if sell else two + k * abs(three - two)
    risk = (sl - entry) if sell else (entry - sl)
    reward = (entry - tp) if sell else (tp - entry)
    s = o["setup"]
    base = {
        "direction": o["direction"], "k": k, "time": str(ltf["time"].iloc[m]),
        "entry": entry, "sl": sl, "tp": tp, "risk": risk,
        "planned_rr": reward / risk if risk > 0 else None,
        "htf_points": s["htf_points"], "ltf_points": o["ltf_points"],
        "ltf_extension": o["ltf_extension"], "ltf_three_ideal": o["ltf_three_ideal"],
    }
    if risk <= 0 or reward <= 0:
        return {**base, "result": "skip", "r": 0.0, "exit_time": None}
    high, low = ltf["high"].to_numpy(), ltf["low"].to_numpy()
    for j in range(m, len(ltf)):
        if j == m:   # fill bar: only a TP in the direction of the move counts
            if (sell and low[j] + spread <= tp) or (not sell and high[j] >= tp):
                return {**base, "result": "win", "r": reward / risk, "exit_time": str(ltf["time"].iloc[j])}
            continue
        if sell:
            if high[j] + spread >= sl:
                return {**base, "result": "loss", "r": -1.0, "exit_time": str(ltf["time"].iloc[j])}
            if low[j] + spread <= tp:
                return {**base, "result": "win", "r": reward / risk, "exit_time": str(ltf["time"].iloc[j])}
        else:
            if low[j] <= sl:
                return {**base, "result": "loss", "r": -1.0, "exit_time": str(ltf["time"].iloc[j])}
            if high[j] >= tp:
                return {**base, "result": "win", "r": reward / risk, "exit_time": str(ltf["time"].iloc[j])}
    return {**base, "result": "open", "r": 0.0, "exit_time": None}


def summarize(trades: List[dict]) -> Dict[str, dict]:
    out = {}
    for k in sorted({t["k"] for t in trades}):
        g = [t for t in trades if t["k"] == k]
        closed = [t for t in g if t["result"] in ("win", "loss")]
        wins = [t for t in closed if t["result"] == "win"]
        out[f"k={k:g}"] = {
            "trades": len(g), "closed": len(closed), "wins": len(wins),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "total_r": round(sum(t["r"] for t in closed), 2),
            "avg_r": round(sum(t["r"] for t in closed) / len(closed), 2) if closed else None,
            "avg_planned_rr": round(float(np.mean([t["planned_rr"] for t in closed])), 2) if closed else None,
            "skipped": sum(t["result"] == "skip" for t in g), "open": sum(t["result"] == "open" for t in g),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("htf_csv")
    ap.add_argument("ltf_csv")
    ap.add_argument("--point", type=float, default=0.00001)
    ap.add_argument("--spread", type=float)
    ap.add_argument("--out")
    a = ap.parse_args()
    htf = pd.read_csv(a.htf_csv, parse_dates=["time"])
    ltf = pd.read_csv(a.ltf_csv, parse_dates=["time"])
    spread = a.spread if a.spread is not None else 15 * a.point
    r = run(htf, ltf, a.point, spread)
    print(json.dumps({"htf_setups": r["htf_setups"], **r["summary"]}, indent=2))
    if a.out:
        json.dump(r, open(a.out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
