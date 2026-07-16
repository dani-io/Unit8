"""
Ichimoku analysis tool.

Calculates all Ichimoku components and provides signals.
Remember: Tenkan ≈ Stochastic, Kijun ≈ MACD, Span B ≈ slow MACD.
"""

import pandas as pd
from .base_tool import BaseTool, ToolResult


class Ichimoku(BaseTool):
    """
    Ichimoku cloud analysis.
    
    Calculates: Tenkan-sen, Kijun-sen, Span A, Span B, Chikou.
    Can check: TK cross, price vs cloud, Kijun as dynamic S/R.
    
    Config:
        tenkan_period: int = 9
        kijun_period: int = 26
        span_b_period: int = 52
        shift: int = 26
    """
    
    name = "ichimoku"
    
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.tenkan_period = self.config.get("tenkan_period", 9)
        self.kijun_period = self.config.get("kijun_period", 26)
        self.span_b_period = self.config.get("span_b_period", 52)
        self.shift = self.config.get("shift", 26)
    
    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        """Analyze current Ichimoku state. (context accepted but not required.)"""
        df = self.calculate(df)
        
        # TODO: Implement signal logic based on checklist needs
        # For now, just return the calculated data
        return self._pass(
            data={"ichimoku_df": df},
            reason="Ichimoku calculated"
        )
    
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Ichimoku columns to DataFrame."""
        df = df.copy()
        
        # Tenkan-sen (conversion line)
        df["tenkan"] = (
            df["high"].rolling(self.tenkan_period).max() +
            df["low"].rolling(self.tenkan_period).min()
        ) / 2
        
        # Kijun-sen (base line)
        df["kijun"] = (
            df["high"].rolling(self.kijun_period).max() +
            df["low"].rolling(self.kijun_period).min()
        ) / 2
        
        # Span A (leading span A)
        df["span_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(self.shift)
        
        # Span B (leading span B)
        df["span_b"] = (
            (df["high"].rolling(self.span_b_period).max() +
             df["low"].rolling(self.span_b_period).min()) / 2
        ).shift(self.shift)
        
        # Chikou (lagging span)
        df["chikou"] = df["close"].shift(-self.shift)
        
        return df
    
    def is_above_cloud(self, df: pd.DataFrame) -> bool:
        """Check if current price is above the Kumo cloud."""
        df = self.calculate(df)
        last = df.iloc[-1]
        cloud_top = max(last["span_a"], last["span_b"])
        return last["close"] > cloud_top
    
    def is_below_cloud(self, df: pd.DataFrame) -> bool:
        """Check if current price is below the Kumo cloud."""
        df = self.calculate(df)
        last = df.iloc[-1]
        cloud_bottom = min(last["span_a"], last["span_b"])
        return last["close"] < cloud_bottom
