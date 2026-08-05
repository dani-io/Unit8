"""
DIY MetaTrader Bridge — thin REST wrapper around the MetaTrader5 package.

Runs on a Windows VM alongside the MT5 terminal. Unit8 (on Linux)
talks to this bridge over HTTP instead of importing MetaTrader5 directly.

This is a thin wrapper — no business logic, no conversions.
Prices and volumes are returned exactly as MT5 reports them.

Run:
    uvicorn server:app --host 0.0.0.0 --port 8000
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import MetaTrader5 as mt5

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bridge] %(levelname)s: %(message)s")
logger = logging.getLogger("bridge")

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

MAGIC_NUMBER = 143263

# symbol_info.filling_mode is a bitmask of the fill types the broker allows.
# Not exposed as constants by the MetaTrader5 package, so define them here.
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2


# ─── MT5 connection ──────────────────────────────────────────

def mt5_connect() -> bool:
    """Initialize MT5 and log in with credentials from environment."""
    account = int(os.getenv("MT5_ACCOUNT", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize():
        logger.error(f"MT5 init failed: {mt5.last_error()}")
        return False

    if not mt5.login(account, password, server):
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        return False

    info = mt5.account_info()
    logger.info(f"MT5 connected: {info.name} | Balance: {info.balance}")
    return True


def mt5_connected() -> bool:
    """Check if the MT5 terminal connection is alive."""
    return mt5.terminal_info() is not None


def require_mt5():
    """Raise 503 if MT5 is not connected (tries to reconnect once)."""
    if not mt5_connected() and not mt5_connect():
        raise HTTPException(status_code=503, detail="MT5 not connected")


def detect_filling_mode(symbol: str) -> int:
    """
    Pick a fill type the broker actually accepts for this symbol.

    Brokers differ in which fill types they allow — sending an unsupported one
    gets the order rejected with retcode 10030 (invalid fill). symbol_info
    reports the allowed types as a bitmask, so read it instead of guessing.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    modes = info.filling_mode
    if modes & SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if modes & SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not mt5_connect():
        logger.warning("MT5 not connected at startup — will retry on first request")
    yield
    mt5.shutdown()
    logger.info("MT5 shut down")


app = FastAPI(title="MT5 Bridge", version="0.1.0", lifespan=lifespan)

# CORS for development — bridge lives on an internal network
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request models ──────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str
    side: str                     # "buy" or "sell"
    volume: float
    sl: Optional[float] = None
    tp: Optional[float] = None


class CloseRequest(BaseModel):
    ticket: int


class ModifyRequest(BaseModel):
    ticket: int
    sl: Optional[float] = None
    tp: Optional[float] = None


# ─── Endpoints ───────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "mt5_connected": mt5_connected()}


@app.get("/account")
def account():
    require_mt5()
    info = mt5.account_info()
    if info is None:
        raise HTTPException(status_code=503, detail="Failed to get account info")
    return {"balance": info.balance, "equity": info.equity, "margin": info.margin}


@app.get("/tick/{symbol}")
def tick(symbol: str):
    require_mt5()
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        raise HTTPException(status_code=404, detail=f"No tick for {symbol}")
    return {"bid": t.bid, "ask": t.ask, "last": t.last, "time": t.time}


@app.get("/symbol/{symbol}")
def symbol_info(symbol: str):
    require_mt5()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    t = mt5.symbol_info_tick(symbol)
    spread = (t.ask - t.bid) if t else 0
    return {
        "point": info.point,
        "tick_value": info.trade_tick_value,
        "tick_size": info.trade_tick_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "spread": spread,
    }


@app.get("/ohlcv/{symbol}/{timeframe}")
def ohlcv(symbol: str, timeframe: str, count: int = Query(default=512, ge=1, le=10000)):
    require_mt5()
    tf = TIMEFRAMES.get(timeframe)
    if tf is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown timeframe: {timeframe}. Supported: {list(TIMEFRAMES.keys())}",
        )

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data for {symbol} {timeframe}: {mt5.last_error()}",
        )

    return [
        {
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in rates
    ]


@app.get("/positions")
def positions(symbol: Optional[str] = None):
    require_mt5()
    if symbol:
        result = mt5.positions_get(symbol=symbol)
    else:
        result = mt5.positions_get()

    if result is None:
        return []

    return [
        {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "buy" if p.type == 0 else "sell",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "magic": p.magic,
        }
        for p in result
    ]


@app.post("/order")
def place_order(req: OrderRequest):
    require_mt5()
    if req.side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail=f"Invalid side: {req.side}")

    t = mt5.symbol_info_tick(req.symbol)
    if t is None:
        raise HTTPException(status_code=404, detail=f"No tick for {req.symbol}")

    order_type = mt5.ORDER_TYPE_BUY if req.side == "buy" else mt5.ORDER_TYPE_SELL
    price = t.ask if req.side == "buy" else t.bid
    filling = detect_filling_mode(req.symbol)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": req.volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Unit8",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    if req.sl is not None:
        request["sl"] = req.sl
    if req.tp is not None:
        request["tp"] = req.tp

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        detail = f"Order failed: {mt5.last_error()}"
        if result is not None:
            detail += f" (retcode {result.retcode})"
        raise HTTPException(status_code=422, detail=detail)

    logger.info(f"Order placed: {req.symbol} {req.side} {req.volume} @ {price} (fill={filling})")
    return {"ticket": result.order, "price": price}


@app.post("/close")
def close_position(req: CloseRequest):
    require_mt5()
    result = mt5.positions_get(ticket=req.ticket)
    if not result:
        raise HTTPException(status_code=404, detail=f"Position {req.ticket} not found")

    pos = result[0]
    t = mt5.symbol_info_tick(pos.symbol)
    if t is None:
        raise HTTPException(status_code=503, detail=f"No tick for {pos.symbol}")

    # Close = opposite-side deal referencing the position
    order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    price = t.bid if pos.type == 0 else t.ask
    filling = detect_filling_mode(pos.symbol)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": req.ticket,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Unit8 close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=422, detail=f"Close failed: {mt5.last_error()}")

    logger.info(f"Position {req.ticket} closed")
    return {"closed": True, "ticket": req.ticket}


@app.post("/modify")
def modify_position(req: ModifyRequest):
    require_mt5()
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": req.ticket,
    }
    if req.sl is not None:
        request["sl"] = req.sl
    if req.tp is not None:
        request["tp"] = req.tp

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=422, detail=f"Modify failed: {mt5.last_error()}")

    logger.info(f"Position {req.ticket} modified: SL={req.sl} TP={req.tp}")
    return {"modified": True, "ticket": req.ticket, "sl": req.sl, "tp": req.tp}
