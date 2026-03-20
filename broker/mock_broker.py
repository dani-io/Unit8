"""
Mock broker for development and testing.

Reads OHLCV data from CSV files — no MT5 needed.
Perfect for building and testing tools on Linux.

CSV format:
    time,open,high,low,close,volume
    2025-01-02 08:00:00,1.08230,1.08295,1.08180,1.08250,1523
"""

import os
import logging
from typing import List, Optional
import pandas as pd

from .base_broker import BaseBroker, Tick, AccountInfo, SymbolInfo, Position

logger = logging.getLogger("unit8.broker.mock")


class MockBroker(BaseBroker):
    """
    Simulated broker using CSV data.
    
    Usage:
        broker = MockBroker({
            "data_dir": "data/",       # folder with CSV files
            "balance": 10000,
            "symbols": {"EURUSD": {"point": 0.00001, "spread": 0.00015}}
        })
        broker.connect()
        df = broker.get_ohlcv("EURUSD", "M15")
    
    CSV files should be named: {symbol}_{timeframe}.csv
    Example: EURUSD_M15.csv, GBPUSD_H1.csv
    """
    
    def __init__(self, config: dict = None):
        config = config or {}
        self.data_dir = config.get("data_dir", "data/")
        self.balance = config.get("balance", 10000.0)
        self.equity = self.balance
        self.symbols_config = config.get("symbols", {})
        self._positions: List[Position] = []
        self._next_ticket = 1000
        self._data_cache = {}
    
    def connect(self) -> bool:
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            logger.info(f"Created data directory: {self.data_dir}")
        
        # Pre-load available CSV files
        csv_count = 0
        for f in os.listdir(self.data_dir):
            if f.endswith(".csv"):
                csv_count += 1
        
        logger.info(f"MockBroker connected | Balance: {self.balance} | CSV files: {csv_count}")
        return True
    
    def disconnect(self):
        logger.info("MockBroker disconnected")
    
    def get_tick(self, symbol: str) -> Optional[Tick]:
        """Get last price from loaded data."""
        df = self._get_cached_data(symbol)
        if df is None or df.empty:
            return None
        
        last = df.iloc[-1]
        sym_cfg = self.symbols_config.get(symbol, {})
        spread = sym_cfg.get("spread", 0.00015)
        mid = last["close"]
        
        return Tick(
            bid=mid - spread / 2,
            ask=mid + spread / 2,
            last=mid,
        )
    
    def get_account_info(self) -> Optional[AccountInfo]:
        return AccountInfo(
            balance=self.balance,
            equity=self.equity,
            margin=0,
        )
    
    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        sym_cfg = self.symbols_config.get(symbol, {})
        point = sym_cfg.get("point", 0.00001)
        spread = sym_cfg.get("spread", 0.00015)
        
        return SymbolInfo(
            point=point,
            tick_value=sym_cfg.get("tick_value", 10.0),
            tick_size=point,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            spread=spread,
        )
    
    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 512) -> Optional[pd.DataFrame]:
        """
        Load data from CSV file.
        Looks for: {data_dir}/{symbol}_{timeframe}.csv
        """
        filename = f"{symbol}_{timeframe}.csv"
        filepath = os.path.join(self.data_dir, filename)
        
        if not os.path.exists(filepath):
            logger.warning(f"CSV not found: {filepath}")
            return None
        
        try:
            df = pd.read_csv(filepath, parse_dates=["time"])
            
            # Ensure standard column names
            df.columns = [c.strip().lower() for c in df.columns]
            
            # Return last N rows
            if len(df) > count:
                df = df.tail(count).reset_index(drop=True)
            
            return df
        
        except Exception as e:
            logger.error(f"Failed to read {filepath}: {e}")
            return None
    
    def get_positions(self, symbol: str = None) -> List[Position]:
        if symbol:
            return [p for p in self._positions if p.symbol == symbol]
        return self._positions
    
    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = None, tp: float = None) -> Optional[int]:
        tick = self.get_tick(symbol)
        if not tick:
            return None
        
        price = tick.ask if side == "buy" else tick.bid
        ticket = self._next_ticket
        self._next_ticket += 1
        
        pos = Position(
            ticket=ticket,
            symbol=symbol,
            type=side,
            volume=volume,
            price_open=price,
            sl=sl or 0,
            tp=tp or 0,
            profit=0,
        )
        self._positions.append(pos)
        logger.info(f"[MOCK] Order placed: #{ticket} {symbol} {side} {volume} @ {price:.5f}")
        return ticket
    
    def close_position(self, ticket: int) -> bool:
        self._positions = [p for p in self._positions if p.ticket != ticket]
        logger.info(f"[MOCK] Position #{ticket} closed")
        return True
    
    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        for p in self._positions:
            if p.ticket == ticket:
                if sl is not None:
                    p.sl = sl
                if tp is not None:
                    p.tp = tp
                logger.info(f"[MOCK] Position #{ticket} modified: SL={sl} TP={tp}")
                return True
        return False
    
    def _get_cached_data(self, symbol: str, timeframe: str = "M15") -> Optional[pd.DataFrame]:
        """Get data with simple caching."""
        key = f"{symbol}_{timeframe}"
        if key not in self._data_cache:
            self._data_cache[key] = self.get_ohlcv(symbol, timeframe)
        return self._data_cache[key]
