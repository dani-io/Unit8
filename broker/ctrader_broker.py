"""
cTrader Open API broker implementation.

Uses the official Spotware Python SDK (ctrader-open-api) with Protobuf/TCP.
Works natively on Linux — no Wine, no VM, no MT5 needed.

Authentication flow:
    1. Register an app at https://openapi.ctrader.com (free)
    2. Get Client ID + Client Secret
    3. Authorize your trading account via OAuth2
    4. Save the access token in .env

Credentials from environment:
    CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN
    CTRADER_ACCOUNT_ID, CTRADER_HOST_TYPE (live/demo)
"""

import os
import time
import logging
import threading
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import calendar

import pandas as pd

from .base_broker import BaseBroker, Tick, AccountInfo, SymbolInfo, Position

logger = logging.getLogger("unit8.broker.ctrader")

try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
        ProtoHeartbeatEvent,
    )
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOATraderReq,
        ProtoOAReconcileReq,
        ProtoOASubscribeSpotsReq,
        ProtoOAUnsubscribeSpotsReq,
        ProtoOAGetTrendbarsReq,
        ProtoOANewOrderReq,
        ProtoOAClosePositionReq,
        ProtoOAAmendPositionSLTPReq,
        ProtoOASymbolsListReq,
        ProtoOASymbolByIdReq,
        ProtoOAAssetListReq,
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAOrderType,
        ProtoOATradeSide,
        ProtoOATrendbarPeriod,
        ProtoOAQuoteType,
        ProtoOAPositionStatus,
    )
    from twisted.internet import reactor as twisted_reactor
    CTRADER_AVAILABLE = True
except ImportError:
    CTRADER_AVAILABLE = False
    logger.warning("ctrader-open-api package not installed — CTraderBroker unavailable")
    logger.warning("Install with: pip install ctrader-open-api")


class CTraderBroker(BaseBroker):
    """
    cTrader broker via Open API (Protobuf/TCP).
    
    Usage:
        broker = CTraderBroker()  # reads from .env
        broker.connect()
        df = broker.get_ohlcv("EURUSD", "M15")
        broker.place_order("EURUSD", "buy", 0.01, sl=1.0800, tp=1.0900)
    
    Note: cTrader uses numeric symbol IDs internally.
    This broker auto-resolves symbol names (e.g., "EURUSD") to IDs.
    """

    TIMEFRAME_MAP = {
        "M1": "M1",
        "M5": "M5",
        "M15": "M15",
        "M30": "M30",
        "H1": "H1",
        "H4": "H4",
        "D1": "D1",
    }

    def __init__(self, config: dict = None):
        config = config or {}
        self.client_id = config.get("client_id") or os.getenv("CTRADER_CLIENT_ID", "")
        self.client_secret = config.get("client_secret") or os.getenv("CTRADER_CLIENT_SECRET", "")
        self.access_token = config.get("access_token") or os.getenv("CTRADER_ACCESS_TOKEN", "")
        self.account_id = config.get("account_id") or int(os.getenv("CTRADER_ACCOUNT_ID", "0"))
        self.host_type = config.get("host_type") or os.getenv("CTRADER_HOST_TYPE", "demo")

        self._client = None
        self._connected = False
        self._authenticated = False
        self._reactor_thread = None

        # Caches
        self._symbol_cache = {}       # name -> symbol info dict
        self._symbol_id_map = {}      # name -> symbolId
        self._asset_cache = {}        # assetId -> asset info
        self._spot_cache = {}         # symbolId -> {bid, ask}
        self._response_events = {}    # for sync bridge
        self._response_data = {}      # for sync bridge
        self._account_info = None
        self._positions_cache = []

    # ─── Connection ───────────────────────────────────────────

    def connect(self) -> bool:
        if not CTRADER_AVAILABLE:
            logger.error("ctrader-open-api package not available")
            return False

        if not all([self.client_id, self.client_secret, self.access_token, self.account_id]):
            logger.error("Missing cTrader credentials. Set CTRADER_CLIENT_ID, "
                         "CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID")
            return False

        host = (EndPoints.PROTOBUF_LIVE_HOST if self.host_type == "live"
                else EndPoints.PROTOBUF_DEMO_HOST)

        self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

        # Run Twisted reactor in a background thread
        self._reactor_thread = threading.Thread(target=self._run_reactor, daemon=True)
        self._reactor_thread.start()

        # Wait for connection + auth (max 15 seconds)
        for _ in range(30):
            if self._authenticated:
                logger.info(f"cTrader connected and authenticated (account {self.account_id})")
                self._load_symbols()
                return True
            time.sleep(0.5)

        logger.error("cTrader connection/authentication timed out")
        return False

    def disconnect(self):
        if CTRADER_AVAILABLE and self._connected:
            try:
                twisted_reactor.callFromThread(twisted_reactor.stop)
            except Exception:
                pass
        self._connected = False
        self._authenticated = False
        logger.info("cTrader disconnected")

    def _run_reactor(self):
        """Run Twisted reactor in background thread."""
        self._client.startService()
        twisted_reactor.run(installSignalHandlers=False)

    # ─── Callbacks ────────────────────────────────────────────

    def _on_connected(self, client):
        """Called when TCP connection is established."""
        self._connected = True
        logger.info("cTrader TCP connected, authenticating app...")

        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        deferred = client.send(request)
        deferred.addErrback(self._on_error)

    def _on_disconnected(self, client, reason):
        self._connected = False
        self._authenticated = False
        logger.warning(f"cTrader disconnected: {reason}")

    def _on_message(self, client, message):
        """Central message router."""
        msg = Protobuf.extract(message)
        payload_type = message.payloadType

        # App auth response → authenticate account
        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            logger.info("App authenticated, authorizing account...")
            request = ProtoOAAccountAuthReq()
            request.ctidTraderAccountId = self.account_id
            request.accessToken = self.access_token
            deferred = client.send(request)
            deferred.addErrback(self._on_error)

        # Account auth response
        elif payload_type == ProtoOAAccountAuthRes().payloadType:
            self._authenticated = True
            logger.info(f"Account {self.account_id} authorized")

        # Spot price updates
        elif hasattr(msg, 'bid') and hasattr(msg, 'symbolId'):
            symbol_id = msg.symbolId
            if hasattr(msg, 'bid'):
                self._spot_cache.setdefault(symbol_id, {})["bid"] = msg.bid
            if hasattr(msg, 'ask'):
                self._spot_cache.setdefault(symbol_id, {})["ask"] = msg.ask

        # Heartbeat — ignore
        elif payload_type == ProtoHeartbeatEvent().payloadType:
            pass

        # Store response for sync bridge
        if payload_type in self._response_events:
            self._response_data[payload_type] = msg
            self._response_events[payload_type].set()

    def _on_error(self, failure):
        logger.error(f"cTrader error: {failure}")

    # ─── Sync bridge ──────────────────────────────────────────

    def _send_and_wait(self, request, response_type, timeout: float = 10.0):
        """
        Send a protobuf request and wait synchronously for the response.
        Bridges Twisted async with our sync BaseBroker interface.
        """
        event = threading.Event()
        payload_type = response_type().payloadType
        self._response_events[payload_type] = event

        twisted_reactor.callFromThread(self._client.send, request)

        if event.wait(timeout=timeout):
            result = self._response_data.pop(payload_type, None)
            del self._response_events[payload_type]
            return result

        self._response_events.pop(payload_type, None)
        logger.warning(f"Timeout waiting for {response_type.__name__}")
        return None

    # ─── Symbol resolution ────────────────────────────────────

    def _load_symbols(self):
        """Load symbol list and cache name→ID mapping."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOASymbolsListRes,
            ProtoOAAssetListRes,
        )

        # Load assets first
        asset_req = ProtoOAAssetListReq()
        asset_req.ctidTraderAccountId = self.account_id
        asset_res = self._send_and_wait(asset_req, ProtoOAAssetListRes, timeout=10)
        if asset_res and hasattr(asset_res, 'asset'):
            for asset in asset_res.asset:
                self._asset_cache[asset.assetId] = {
                    "name": asset.name,
                    "digits": asset.digits if hasattr(asset, 'digits') else 5,
                }

        # Load symbols
        request = ProtoOASymbolsListReq()
        request.ctidTraderAccountId = self.account_id
        response = self._send_and_wait(request, ProtoOASymbolsListRes, timeout=10)

        if response and hasattr(response, 'symbol'):
            for sym in response.symbol:
                name = sym.symbolName if hasattr(sym, 'symbolName') else str(sym.symbolId)
                self._symbol_id_map[name] = sym.symbolId
                self._symbol_cache[name] = {
                    "symbolId": sym.symbolId,
                    "name": name,
                    "digits": sym.digits if hasattr(sym, 'digits') else 5,
                    "pipPosition": sym.pipPosition if hasattr(sym, 'pipPosition') else 4,
                    "stepVolume": sym.stepVolume if hasattr(sym, 'stepVolume') else 1000,
                    "minVolume": sym.minVolume if hasattr(sym, 'minVolume') else 1000,
                    "maxVolume": sym.maxVolume if hasattr(sym, 'maxVolume') else 10000000,
                }

            logger.info(f"Loaded {len(self._symbol_id_map)} symbols")
        else:
            logger.warning("Failed to load symbol list")

    def _get_symbol_id(self, symbol: str) -> Optional[int]:
        """Resolve symbol name to numeric ID."""
        sid = self._symbol_id_map.get(symbol)
        if sid is None:
            logger.warning(f"Symbol '{symbol}' not found. Available: {list(self._symbol_id_map.keys())[:10]}...")
        return sid

    # ─── BaseBroker interface ─────────────────────────────────

    def get_tick(self, symbol: str) -> Optional[Tick]:
        symbol_id = self._get_symbol_id(symbol)
        if symbol_id is None:
            return None

        # Subscribe to spot prices if not cached
        if symbol_id not in self._spot_cache:
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            twisted_reactor.callFromThread(self._client.send, request)
            time.sleep(1)  # Wait for first tick

        spot = self._spot_cache.get(symbol_id)
        if not spot:
            return None

        # cTrader prices are in units / 10^digits
        sym_info = self._symbol_cache.get(symbol, {})
        digits = sym_info.get("digits", 5)
        divisor = 10 ** digits

        bid = spot.get("bid", 0) / divisor
        ask = spot.get("ask", 0) / divisor

        return Tick(bid=bid, ask=ask, last=(bid + ask) / 2)

    def get_account_info(self) -> Optional[AccountInfo]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderRes

        request = ProtoOATraderReq()
        request.ctidTraderAccountId = self.account_id
        response = self._send_and_wait(request, ProtoOATraderRes, timeout=5)

        if response and hasattr(response, 'trader'):
            trader = response.trader
            # Balance is in cents (integer), convert to currency
            balance = trader.balance / 100 if hasattr(trader, 'balance') else 0
            return AccountInfo(
                balance=balance,
                equity=balance,  # Equity needs unrealized PnL calculation
                margin=0,
            )
        return None

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        sym = self._symbol_cache.get(symbol)
        if not sym:
            return None

        digits = sym.get("digits", 5)
        pip_pos = sym.get("pipPosition", 4)
        point = 1 / (10 ** digits)

        # Volume in cTrader: 100 = 0.01 lot, 100000 = 1 lot
        min_vol = sym.get("minVolume", 1000) / 100000
        max_vol = sym.get("maxVolume", 10000000) / 100000
        step_vol = sym.get("stepVolume", 1000) / 100000

        tick = self.get_tick(symbol)
        spread = (tick.ask - tick.bid) if tick else 0

        return SymbolInfo(
            point=point,
            tick_value=10.0,  # Approximate, depends on account currency
            tick_size=point,
            volume_min=min_vol,
            volume_max=max_vol,
            volume_step=step_vol,
            spread=spread,
        )

    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 512) -> Optional[pd.DataFrame]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsRes

        symbol_id = self._get_symbol_id(symbol)
        if symbol_id is None:
            return None

        tf = self.TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        # Calculate time range (cTrader uses millisecond timestamps)
        now = datetime.now(timezone.utc)
        # Estimate how far back we need to go for `count` bars
        tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        minutes_back = tf_minutes.get(timeframe, 15) * count * 1.5  # 1.5x for weekends/gaps
        from_time = now - timedelta(minutes=minutes_back)

        request = ProtoOAGetTrendbarsReq()
        request.ctidTraderAccountId = self.account_id
        request.period = ProtoOATrendbarPeriod.Value(tf)
        request.fromTimestamp = int(from_time.timestamp() * 1000)
        request.toTimestamp = int(now.timestamp() * 1000)
        request.symbolId = symbol_id

        response = self._send_and_wait(request, ProtoOAGetTrendbarsRes, timeout=15)

        if not response or not hasattr(response, 'trendbar') or not response.trendbar:
            logger.warning(f"No data returned for {symbol} {timeframe}")
            return None

        # Parse trendbars into DataFrame
        sym = self._symbol_cache.get(symbol, {})
        digits = sym.get("digits", 5)
        divisor = 10 ** digits

        bars = []
        for bar in response.trendbar:
            # cTrader: low is absolute, others are deltas from low
            low = bar.low / divisor
            bars.append({
                "time": datetime.fromtimestamp(bar.utcTimestampInMinutes * 60, tz=timezone.utc),
                "open": low + (bar.deltaOpen / divisor) if hasattr(bar, 'deltaOpen') else low,
                "high": low + (bar.deltaHigh / divisor) if hasattr(bar, 'deltaHigh') else low,
                "low": low,
                "close": low + (bar.deltaClose / divisor) if hasattr(bar, 'deltaClose') else low,
                "volume": bar.volume if hasattr(bar, 'volume') else 0,
            })

        df = pd.DataFrame(bars)

        # Return last `count` bars
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)

        return df

    def get_positions(self, symbol: str = None) -> List[Position]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes

        request = ProtoOAReconcileReq()
        request.ctidTraderAccountId = self.account_id
        response = self._send_and_wait(request, ProtoOAReconcileRes, timeout=5)

        positions = []
        if response and hasattr(response, 'position'):
            for pos in response.position:
                sym_name = self._resolve_symbol_name(pos.tradeData.symbolId)
                if symbol and sym_name != symbol:
                    continue

                sym = self._symbol_cache.get(sym_name, {})
                digits = sym.get("digits", 5)
                divisor = 10 ** digits

                positions.append(Position(
                    ticket=pos.positionId,
                    symbol=sym_name,
                    type="buy" if pos.tradeData.tradeSide == ProtoOATradeSide.Value("BUY") else "sell",
                    volume=pos.tradeData.volume / 100000,  # Convert to lots
                    price_open=pos.price / divisor if hasattr(pos, 'price') else 0,
                    sl=pos.stopLoss / divisor if hasattr(pos, 'stopLoss') and pos.stopLoss else 0,
                    tp=pos.takeProfit / divisor if hasattr(pos, 'takeProfit') and pos.takeProfit else 0,
                    profit=pos.swap / 100 if hasattr(pos, 'swap') else 0,  # Approximate
                ))

        return positions

    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = None, tp: float = None) -> Optional[int]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent

        symbol_id = self._get_symbol_id(symbol)
        if symbol_id is None:
            return None

        sym = self._symbol_cache.get(symbol, {})
        digits = sym.get("digits", 5)
        divisor = 10 ** digits

        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = self.account_id
        request.symbolId = symbol_id
        request.orderType = ProtoOAOrderType.Value("MARKET")
        request.tradeSide = ProtoOATradeSide.Value("BUY" if side == "buy" else "SELL")
        request.volume = int(volume * 100000)  # Convert lots to units

        if sl:
            request.stopLoss = int(sl * divisor)
        if tp:
            request.takeProfit = int(tp * divisor)

        response = self._send_and_wait(request, ProtoOAExecutionEvent, timeout=10)

        if response and hasattr(response, 'position'):
            ticket = response.position.positionId
            logger.info(f"Order placed: #{ticket} {symbol} {side} {volume}")
            return ticket

        logger.error(f"Order failed for {symbol}")
        return None

    def close_position(self, ticket: int) -> bool:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent

        # Find position volume
        positions = self.get_positions()
        pos = next((p for p in positions if p.ticket == ticket), None)
        if not pos:
            return False

        request = ProtoOAClosePositionReq()
        request.ctidTraderAccountId = self.account_id
        request.positionId = ticket
        request.volume = int(pos.volume * 100000)

        response = self._send_and_wait(request, ProtoOAExecutionEvent, timeout=10)
        if response:
            logger.info(f"Position #{ticket} closed")
            return True
        return False

    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent

        # Find symbol for digits
        positions = self.get_positions()
        pos = next((p for p in positions if p.ticket == ticket), None)
        if not pos:
            return False

        sym = self._symbol_cache.get(pos.symbol, {})
        digits = sym.get("digits", 5)
        divisor = 10 ** digits

        request = ProtoOAAmendPositionSLTPReq()
        request.ctidTraderAccountId = self.account_id
        request.positionId = ticket

        if sl is not None:
            request.stopLoss = int(sl * divisor)
        if tp is not None:
            request.takeProfit = int(tp * divisor)

        response = self._send_and_wait(request, ProtoOAExecutionEvent, timeout=5)
        if response:
            logger.info(f"Position #{ticket} modified: SL={sl} TP={tp}")
            return True
        return False

    # ─── Helpers ──────────────────────────────────────────────

    def _resolve_symbol_name(self, symbol_id: int) -> str:
        """Reverse lookup: symbol ID → name."""
        for name, sid in self._symbol_id_map.items():
            if sid == symbol_id:
                return name
        return str(symbol_id)
