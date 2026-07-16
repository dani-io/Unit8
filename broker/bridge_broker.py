"""
Bridge broker — talks to the DIY MetaTrader Bridge over HTTP.

The bridge (bridge/server.py) runs on a Windows VM next to MT5 and
exposes REST endpoints. This broker lets Unit8 on Linux trade through
MT5 without importing the MetaTrader5 package.

Config:
    {"url": "http://192.168.56.101:8000", "timeout": 10}

Or from environment:
    BRIDGE_URL=http://192.168.56.101:8000
"""

import os
import logging
from typing import List, Optional

import pandas as pd
import requests

from .base_broker import BaseBroker, Tick, AccountInfo, SymbolInfo, Position

logger = logging.getLogger("unit8.broker.bridge")


class BridgeBroker(BaseBroker):
    """
    MT5 via the REST bridge.

    Usage:
        broker = BridgeBroker({"url": "http://192.168.56.101:8000"})
        broker.connect()
        df = broker.get_ohlcv("EURUSD", "M15")
    """

    def __init__(self, config: dict = None):
        config = config or {}
        self.url = (config.get("url") or os.getenv("BRIDGE_URL", "http://localhost:8000")).rstrip("/")
        self.timeout = config.get("timeout", 10)
        self._session = requests.Session()

    # ─── HTTP helpers ─────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        try:
            resp = self._session.get(f"{self.url}{path}", params=params, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"GET {path} → {resp.status_code}: {resp.text[:200]}")
                return None
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"GET {path} failed: {e}")
            return None

    def _post(self, path: str, body: dict) -> Optional[dict]:
        try:
            resp = self._session.post(f"{self.url}{path}", json=body, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"POST {path} → {resp.status_code}: {resp.text[:200]}")
                return None
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"POST {path} failed: {e}")
            return None

    # ─── BaseBroker interface ─────────────────────────────────

    def connect(self) -> bool:
        health = self._get("/health")
        if health is None:
            logger.error(f"Bridge unreachable at {self.url}")
            return False
        if not health.get("mt5_connected"):
            logger.error("Bridge is up but MT5 is not connected")
            return False
        logger.info(f"Connected to MT5 bridge at {self.url}")
        return True

    def disconnect(self):
        self._session.close()
        logger.info("Bridge session closed")

    def get_tick(self, symbol: str) -> Optional[Tick]:
        data = self._get(f"/tick/{symbol}")
        if data is None:
            return None
        return Tick(bid=data["bid"], ask=data["ask"], last=data["last"], time=data.get("time"))

    def get_account_info(self) -> Optional[AccountInfo]:
        data = self._get("/account")
        if data is None:
            return None
        return AccountInfo(balance=data["balance"], equity=data["equity"], margin=data["margin"])

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        data = self._get(f"/symbol/{symbol}")
        if data is None:
            return None
        return SymbolInfo(
            point=data["point"],
            tick_value=data["tick_value"],
            tick_size=data["tick_size"],
            volume_min=data["volume_min"],
            volume_max=data["volume_max"],
            volume_step=data["volume_step"],
            spread=data.get("spread", 0),
        )

    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 512) -> Optional[pd.DataFrame]:
        data = self._get(f"/ohlcv/{symbol}/{timeframe}", params={"count": count})
        if not data:
            return None
        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    def get_positions(self, symbol: str = None) -> List[Position]:
        params = {"symbol": symbol} if symbol else None
        data = self._get("/positions", params=params)
        if not data:
            return []
        return [
            Position(
                ticket=p["ticket"],
                symbol=p["symbol"],
                type=p["type"],
                volume=p["volume"],
                price_open=p["price_open"],
                sl=p["sl"],
                tp=p["tp"],
                profit=p["profit"],
                magic=p.get("magic", 0),
            )
            for p in data
        ]

    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = None, tp: float = None) -> Optional[int]:
        data = self._post("/order", {
            "symbol": symbol,
            "side": side,
            "volume": volume,
            "sl": sl,
            "tp": tp,
        })
        if data is None:
            logger.error(f"Order failed: {symbol} {side} {volume}")
            return None
        ticket = data.get("ticket")
        logger.info(f"Order placed via bridge: #{ticket} {symbol} {side} {volume}")
        return ticket

    def close_position(self, ticket: int) -> bool:
        data = self._post("/close", {"ticket": ticket})
        if data is None:
            return False
        logger.info(f"Position #{ticket} closed via bridge")
        return True

    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        data = self._post("/modify", {"ticket": ticket, "sl": sl, "tp": tp})
        if data is None:
            return False
        logger.info(f"Position #{ticket} modified via bridge: SL={sl} TP={tp}")
        return True
