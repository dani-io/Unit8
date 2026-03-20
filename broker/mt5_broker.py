"""
MetaTrader5 broker implementation.

Wraps the MetaTrader5 Python package behind the BaseBroker interface.
Credentials come from environment variables — never hardcode them.
"""

import os
import logging
from typing import List, Optional
import pandas as pd

from .base_broker import BaseBroker, Tick, AccountInfo, SymbolInfo, Position

logger = logging.getLogger("unit8.broker.mt5")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed — MT5Broker unavailable")


class MT5Broker(BaseBroker):
    """
    MT5 broker.
    
    Credentials from environment:
        MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER
    
    Or pass config dict:
        {"account": 12345, "password": "xxx", "server": "BrokerName-ECN"}
    """
    
    # Map string timeframes to MT5 constants
    TIMEFRAMES = {
        "M1": mt5.TIMEFRAME_M1 if MT5_AVAILABLE else 1,
        "M5": mt5.TIMEFRAME_M5 if MT5_AVAILABLE else 5,
        "M15": mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
        "M30": mt5.TIMEFRAME_M30 if MT5_AVAILABLE else 30,
        "H1": mt5.TIMEFRAME_H1 if MT5_AVAILABLE else 60,
        "H4": mt5.TIMEFRAME_H4 if MT5_AVAILABLE else 240,
        "D1": mt5.TIMEFRAME_D1 if MT5_AVAILABLE else 1440,
    }
    
    def __init__(self, config: dict = None):
        config = config or {}
        self.account = config.get("account") or int(os.getenv("MT5_ACCOUNT", "0"))
        self.password = config.get("password") or os.getenv("MT5_PASSWORD", "")
        self.server = config.get("server") or os.getenv("MT5_SERVER", "")
        self.magic_number = config.get("magic_number", 143263)
    
    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 package not available")
            return False
        
        if not mt5.initialize():
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False
        
        if not mt5.login(self.account, self.password, self.server):
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            return False
        
        info = mt5.account_info()
        logger.info(f"Connected: {info.name} | Balance: {info.balance}")
        return True
    
    def disconnect(self):
        if MT5_AVAILABLE:
            mt5.shutdown()
    
    def get_tick(self, symbol: str) -> Optional[Tick]:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return Tick(bid=tick.bid, ask=tick.ask, last=tick.last, time=tick.time)
    
    def get_account_info(self) -> Optional[AccountInfo]:
        info = mt5.account_info()
        if info is None:
            return None
        return AccountInfo(balance=info.balance, equity=info.equity, margin=info.margin)
    
    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        tick = mt5.symbol_info_tick(symbol)
        spread = (tick.ask - tick.bid) if tick else 0
        return SymbolInfo(
            point=info.point,
            tick_value=info.trade_tick_value,
            tick_size=info.trade_tick_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            spread=spread,
        )
    
    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 512) -> Optional[pd.DataFrame]:
        tf = self.TIMEFRAMES.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None
        
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            logger.error(f"Failed to get data for {symbol} {timeframe}: {mt5.last_error()}")
            return None
        
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df
    
    def get_positions(self, symbol: str = None) -> List[Position]:
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        
        if not positions:
            return []
        
        return [
            Position(
                ticket=p.ticket,
                symbol=p.symbol,
                type="buy" if p.type == 0 else "sell",
                volume=p.volume,
                price_open=p.price_open,
                sl=p.sl,
                tp=p.tp,
                profit=p.profit,
                magic=p.magic,
            )
            for p in positions
        ]
    
    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = None, tp: float = None) -> Optional[int]:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "buy" else tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": self.magic_number,
            "comment": "Unit8",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {mt5.last_error()}")
            return None
        
        logger.info(f"Order placed: {symbol} {side} {volume} @ {price}")
        return result.order
    
    def close_position(self, ticket: int) -> bool:
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        
        pos = positions[0]
        side = "sell" if pos.type == 0 else "buy"
        return self.place_order(pos.symbol, side, pos.volume) is not None
    
    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp,
        }
        result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
