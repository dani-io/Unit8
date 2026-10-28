"""
Bridge broker — talks to the DIY MetaTrader Bridge over HTTP.

The bridge (bridge/server.py) runs on a Windows VM next to MT5 and
exposes REST endpoints. This broker lets Unit8 on Linux trade through
MT5 without importing the MetaTrader5 package.

The bridge is published through a Cloudflare Tunnel guarded by Cloudflare
Access (see bridge/CLOUDFLARE.md), so every request carries:
    Authorization: Bearer <BRIDGE_TOKEN>        — checked by the bridge
    CF-Access-Client-Id / CF-Access-Client-Secret — checked by Cloudflare
                                                    (only when configured)

Config:
    {
        "url": "https://bridge.example.com",
        "token": "...",
        "cf_access_client_id": "....access",
        "cf_access_client_secret": "...",
        "timeout": 10
    }

Or from environment:
    BRIDGE_URL, BRIDGE_TOKEN, CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET
"""

import os
import logging
from typing import Any, List, Optional
from urllib.parse import urlparse

import pandas as pd
import requests

from .base_broker import BaseBroker, Tick, AccountInfo, SymbolInfo, Position

logger = logging.getLogger("unit8.broker.bridge")


def _is_cf_access_rejection(resp: requests.Response) -> bool:
    """
    True if Cloudflare Access answered instead of the bridge.

    Access either redirects to its login page (…cloudflareaccess.com or
    /cdn-cgi/access/login) or serves an HTML block page — never JSON.
    """
    if resp.is_redirect:
        location = resp.headers.get("location", "")
        host = urlparse(location).hostname or ""
        return host.endswith("cloudflareaccess.com") or "/cdn-cgi/access/" in location

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        return False
    body = resp.text[:4000].lower()
    return "cloudflare access" in body or "cloudflareaccess.com" in body or "cdn-cgi/access" in body


class BridgeBroker(BaseBroker):
    """
    MT5 via the REST bridge.

    Usage:
        broker = BridgeBroker({"url": "https://bridge.example.com", "token": "..."})
        broker.connect()
        df = broker.get_ohlcv("EURUSD", "M15")
    """

    def __init__(self, config: dict = None):
        config = config or {}
        self.url = (config.get("url") or os.getenv("BRIDGE_URL", "http://localhost:8000")).rstrip("/")
        self.timeout = config.get("timeout", 10)

        token = config.get("token") or os.getenv("BRIDGE_TOKEN", "")
        cf_id = config.get("cf_access_client_id") or os.getenv("CF_ACCESS_CLIENT_ID", "")
        cf_secret = config.get("cf_access_client_secret") or os.getenv("CF_ACCESS_CLIENT_SECRET", "")

        # Session headers ride along on every request. TLS verification stays
        # on (requests' default) — Cloudflare serves a valid certificate.
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        else:
            logger.warning("BRIDGE_TOKEN not set — the bridge will reject every request")

        if cf_id and cf_secret:
            self._session.headers["CF-Access-Client-Id"] = cf_id
            self._session.headers["CF-Access-Client-Secret"] = cf_secret
        elif cf_id or cf_secret:
            logger.warning("Only one of CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET is set — not sending either")

    # ─── HTTP helpers ─────────────────────────────────────────

    def _request(self, method: str, path: str, timeout: float = None, **kwargs) -> Optional[Any]:
        # GETs are retried once on a dropped connection: an idle keep-alive
        # connection can be closed by Cloudflare between polls. POSTs are
        # never retried — the order may already have been placed.
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                # No redirects: the bridge never redirects, so one means
                # Cloudflare Access is bouncing us to its login page.
                resp = self._session.request(
                    method, f"{self.url}{path}",
                    timeout=timeout or self.timeout, allow_redirects=False, **kwargs,
                )
                break
            except requests.ConnectionError as e:
                if attempt + 1 < attempts:
                    logger.warning(f"{method} {path} connection dropped, retrying: {e}")
                    continue
                logger.error(f"{method} {path} failed: {e}")
                return None
            except requests.RequestException as e:
                logger.error(f"{method} {path} failed: {e}")
                return None

        if _is_cf_access_rejection(resp):
            logger.error(
                f"Cloudflare Access rejected the request: {method} {path} → {resp.status_code}. "
                "Check CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET and the Access Service Auth policy."
            )
            return None
        if resp.status_code == 401:
            logger.error(f"Bridge rejected the token: {method} {path} → 401. Check BRIDGE_TOKEN.")
            return None
        if resp.status_code != 200:
            logger.warning(f"{method} {path} → {resp.status_code}: {resp.text[:200]}")
            return None

        try:
            return resp.json()
        except ValueError:
            logger.error(f"{method} {path} returned non-JSON: {resp.text[:200]}")
            return None

    def _get(self, path: str, params: dict = None, timeout: float = None) -> Optional[Any]:
        return self._request("GET", path, timeout=timeout, params=params)

    def _post(self, path: str, body: dict) -> Optional[Any]:
        return self._request("POST", path, json=body)

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

    def get_history(self, symbol: str, timeframe: str, start, end,
                    chunk_days: int = 30) -> Optional[pd.DataFrame]:
        """
        Bars between two times (datetime or unix seconds), fetched in
        chunks from the bridge's /history endpoint. Times are MT5 bar times.
        """
        to_unix = lambda t: int(pd.Timestamp(t).timestamp()) if not isinstance(t, (int, float)) else int(t)
        s, e = to_unix(start), to_unix(end)
        step = chunk_days * 86400
        frames = []
        while s <= e:
            chunk_end = min(s + step - 1, e)
            data = self._get(f"/history/{symbol}/{timeframe}", params={"start": s, "end": chunk_end}, timeout=120)
            if data:
                frames.append(pd.DataFrame(data))
            s = chunk_end + 1
        if not frames:
            return None
        df = pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)
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
