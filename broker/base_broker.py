"""
Abstract broker interface.

Every broker (MT5, CCXT, Mock) implements this contract.
The rest of Unit8 only talks to BaseBroker — never to MT5 or CCXT directly.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass
import pandas as pd


@dataclass
class Tick:
    bid: float
    ask: float
    last: float
    time: Optional[object] = None


@dataclass 
class AccountInfo:
    balance: float
    equity: float
    margin: float


@dataclass
class SymbolInfo:
    point: float          # e.g., 0.00001 for EURUSD
    tick_value: float     # value of 1 tick in account currency
    tick_size: float      # size of 1 tick in price units
    volume_min: float
    volume_max: float
    volume_step: float
    spread: float = 0


@dataclass
class Position:
    ticket: int
    symbol: str
    type: str             # "buy" or "sell"
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int = 0


class BaseBroker(ABC):
    """All brokers implement this interface."""
    
    @abstractmethod
    def connect(self) -> bool:
        """Connect and authenticate."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Clean shutdown."""
        pass
    
    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[Tick]:
        pass
    
    @abstractmethod
    def get_account_info(self) -> Optional[AccountInfo]:
        pass
    
    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        pass
    
    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 512) -> Optional[pd.DataFrame]:
        """
        Get OHLCV data as DataFrame.
        Columns: time, open, high, low, close, volume
        """
        pass
    
    @abstractmethod
    def get_positions(self, symbol: str = None) -> List[Position]:
        pass
    
    @abstractmethod
    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = None, tp: float = None) -> Optional[int]:
        """Returns ticket ID or None on failure."""
        pass
    
    @abstractmethod
    def close_position(self, ticket: int) -> bool:
        pass
    
    @abstractmethod
    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        pass
