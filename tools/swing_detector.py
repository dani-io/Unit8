"""
Swing point detector.

Finds swing highs and swing lows in price data.
This is the foundation — patterns, S/R levels, and SL/TP all depend on swings.
"""

import pandas as pd
from .base_tool import BaseTool, ToolResult


class SwingDetector(BaseTool):
    """
    Detects swing highs and swing lows.
    
    A swing high: a candle whose high is higher than `window` candles on each side.
    A swing low: a candle whose low is lower than `window` candles on each side.
    
    Config:
        window: int = 5         — candles to check on each side
        min_distance: int = 5   — minimum bars between two swing points
    """
    
    name = "swing_detector"
    
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.window = self.config.get("window", 5)
        self.min_distance = self.config.get("min_distance", 5)
    
    def check(self, df: pd.DataFrame, symbol: str = "") -> ToolResult:
        """Find all swing points in the data."""
        swings = self.find_swings(df)
        
        if len(swings) < 2:
            return self._fail("Not enough swing points found")
        
        return self._pass(
            data={"swings": swings},
            reason=f"Found {len(swings)} swing points"
        )
    
    def find_swings(self, df: pd.DataFrame) -> list:
        """
        Extract swing highs and lows from OHLCV data.
        
        Returns:
            List of dicts: [
                {"type": "high", "index": 45, "price": 1.0892, "time": ...},
                {"type": "low",  "index": 52, "price": 1.0834, "time": ...},
                ...
            ]
        """
        swings = []
        w = self.window
        
        for i in range(w, len(df) - w):
            high = df["high"].iloc[i]
            low = df["low"].iloc[i]
            
            is_high = all(
                high > df["high"].iloc[i - j] and high > df["high"].iloc[i + j]
                for j in range(1, w + 1)
            )
            is_low = all(
                low < df["low"].iloc[i - j] and low < df["low"].iloc[i + j]
                for j in range(1, w + 1)
            )
            
            if not (is_high or is_low):
                continue
            
            # Skip if too close to previous swing
            if swings and abs(i - swings[-1]["index"]) < self.min_distance:
                continue
            
            swings.append({
                "type": "high" if is_high else "low",
                "index": i,
                "price": high if is_high else low,
                "time": df["time"].iloc[i] if "time" in df.columns else None,
            })
        
        return swings
    
    def get_last_n(self, df: pd.DataFrame, n: int = 4) -> list:
        """Get the last N swing points — useful for pattern detection."""
        all_swings = self.find_swings(df)
        return all_swings[-n:] if len(all_swings) >= n else all_swings
