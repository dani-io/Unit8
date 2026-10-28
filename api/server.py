"""
Unit8 chart service — read-only analysis API + chart page.

Serves candles together with what Unit8's tools see on them, using the
SAME tool code the trading worker runs, so the chart is the bot's view.

Run:
    uvicorn api.server:app --host 0.0.0.0 --port 8000

Data source (env CHART_SOURCE): "bridge" (MT5 via BRIDGE_URL) or "csv"
(data/{SYMBOL}_{TF}.csv). Defaults to bridge when BRIDGE_URL is set.

No auth here — publish it behind Coolify's HTTP basic auth (or another
proxy-level guard). The service never places or modifies orders.
"""

import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from broker.bridge_broker import BridgeBroker
from broker.mock_broker import MockBroker
from tools import SwingDetector, ZigZag
from tools.market_structure import analyze_structure
from tools.trendline import build_trendlines
from tools.pattern_123 import detect_123

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("unit8.chart")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

app = FastAPI(title="Unit8 Chart", docs_url=None, redoc_url=None, openapi_url=None)

_brokers = {}
_lock = threading.Lock()


def _default_source() -> str:
    return os.getenv("CHART_SOURCE") or ("bridge" if os.getenv("BRIDGE_URL") else "csv")


def _broker(source: str):
    """One shared, lazily connected broker per source."""
    with _lock:
        broker = _brokers.get(source)
        if broker is None:
            if source == "bridge":
                broker = BridgeBroker({})
            elif source == "csv":
                broker = MockBroker({"data_dir": str(ROOT / "data")})
            else:
                raise HTTPException(400, f"Unknown source '{source}' (bridge | csv)")
            if not broker.connect():
                raise HTTPException(502, f"Cannot connect to data source '{source}'")
            _brokers[source] = broker
        return broker


def _ts(value) -> Optional[int]:
    """Timestamp → unix seconds. MT5 times are broker-server time; kept as-is
    so the chart's clock matches the MT5 terminal."""
    if value is None:
        return None
    return int(pd.Timestamp(value).timestamp())


def _points(items: List[dict]) -> List[dict]:
    return [
        {**{k: v for k, v in p.items() if k != "time"}, "time": _ts(p.get("time"))}
        for p in items
    ]


def _compare(zz: List[dict], sd: List[dict], tolerance: int) -> dict:
    """How well swing_detector agrees with ZigZag (taken as the reference)."""
    zz_conf = [p for p in zz if p["confirmed"]]

    def near(p, pool):
        return any(q["type"] == p["type"] and abs(q["index"] - p["index"]) <= tolerance for q in pool)

    matched_zz = sum(near(p, sd) for p in zz_conf)
    matched_sd = sum(near(p, zz_conf) for p in sd)
    same_type_runs = sum(a["type"] == b["type"] for a, b in zip(sd, sd[1:]))
    return {
        "tolerance_bars": tolerance,
        "zigzag_pivots": len(zz_conf),
        "swing_points": len(sd),
        "zigzag_found_by_swing": matched_zz,          # recall
        "swing_on_zigzag": matched_sd,                 # precision
        "swing_consecutive_same_type": same_type_runs,
    }


def _segments(states: List[str], times: List[int]) -> List[dict]:
    """Compress the per-bar trend state into runs: [{state, from, to}]."""
    runs = []
    for st, t in zip(states, times):
        if runs and runs[-1]["state"] == st:
            runs[-1]["to"] = int(t)
        else:
            runs.append({"state": st, "from": int(t), "to": int(t)})
    return runs


@app.get("/api/health")
def health():
    return {"status": "ok", "default_source": _default_source()}


@app.get("/api/chart")
def chart(
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    timeframe: str = Query("M15"),
    count: int = Query(1000, ge=50, le=5000),
    source: Optional[str] = None,
    depth: int = Query(12, ge=2, le=200),
    deviation: int = Query(5, ge=0, le=10000),
    backstep: int = Query(3, ge=1, le=100),
    window: int = Query(5, ge=1, le=100),
    min_distance: int = Query(5, ge=0, le=100),
    tolerance: int = Query(2, ge=0, le=20),
):
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, f"Unknown timeframe. Supported: {TIMEFRAMES}")
    source = source or _default_source()

    broker = _broker(source)
    df = broker.get_ohlcv(symbol, timeframe, count)
    if df is None or df.empty:
        raise HTTPException(404, f"No data for {symbol} {timeframe} from {source}")
    df = df.reset_index(drop=True)

    info = broker.get_symbol_info(symbol)
    point = info.point if info and info.point else None

    zz = ZigZag({"depth": depth, "deviation": deviation, "backstep": backstep}).find_pivots(df, point=point)
    sd = SwingDetector({"window": window, "min_distance": min_distance}).find_swings(df)

    structure = analyze_structure(df, zz)
    trendlines = build_trendlines(df, structure)
    patterns = detect_123(df, zz, structure, trendlines)
    labels = {p["index"]: p["label"] for p in structure["pivots"]}
    zz = [{**p, "label": labels.get(p["index"])} for p in zz]

    times = df["time"].map(_ts)
    candles = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
        for t, o, h, l, c in zip(times, df["open"], df["high"], df["low"], df["close"])
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "point": point,
        "candles": candles,
        "zigzag": _points(zz),
        "swings": _points(sd),
        "comparison": _compare(zz, sd, tolerance),
        "structure": {
            "state": structure["state"],
            "states": _segments(structure["states"], list(times)),
            "events": [
                {
                    k: (_ts(v) if k.endswith("time") else v)
                    for k, v in e.items()
                }
                for e in structure["events"]
            ],
        },
        "trendlines": [
            {
                "direction": ln["direction"],
                "anchor_time": _ts(ln["anchor_time"]), "anchor_price": ln["anchor_price"],
                "through_time": _ts(ln["through_time"]), "through_price": ln["through_price"],
                "end_time": _ts(ln["end_time"]), "end_price": ln["end_price"],
                "frozen": ln["frozen_at"] is not None,
                "refits": ln["refits"],
                "cross_times": [_ts(t) for t in ln["final_cross_times"]],
            }
            for ln in trendlines
        ],
        "patterns": [
            {
                **{k: v for k, v in pt.items() if k not in ("A", "B", "1", "2", "3")},
                "points": {
                    k: {**pt[k], "time": _ts(pt[k]["time"])}
                    for k in ("A", "B", "1", "2", "3") if pt[k]
                },
                "invalid_time": _ts(df["time"].iloc[pt["invalid_at"]]) if pt["invalid_at"] is not None else None,
                "completed_time": _ts(df["time"].iloc[pt["completed_at"]]) if pt["completed_at"] is not None else None,
            }
            for pt in patterns
        ],
    }


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
