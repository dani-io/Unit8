"""
1-2-3 Pattern detector.

Detects the classic reversal pattern after a trendline break:
    1 = Last swing extreme (high in uptrend, low in downtrend)
    2 = First swing in new direction after break
    3 = Failed retest (lower high or higher low)

Entry: when price breaks point 2.
SL: beyond point 3.
"""

import pandas as pd
from .base_tool import BaseTool, ToolResult


class Pattern123(BaseTool):
    """
    Detects 1-2-3 reversal pattern using swing points.
    
    Requires SwingDetector results as input.
    
    Config:
        min_retracement: float = 0.3  — point 3 must retrace at least 30% of 1→2
        max_retracement: float = 0.9  — point 3 must not exceed 90% of 1→2
    """
    
    name = "pattern_123"
    
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.min_retracement = self.config.get("min_retracement", 0.3)
        self.max_retracement = self.config.get("max_retracement", 0.9)
    
    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        """
        Detect 1-2-3 pattern from swing points.

        Args:
            df: OHLCV data
            context: Shared tool results — reads swings from swing_detector
        """
        swings = context.get("swing_detector", {}).get("swings", []) if context else []
        if not swings or len(swings) < 3:
            return self._fail("Need at least 3 swing points")
        
        # TODO: Implement pattern detection logic
        # 1. Take last 3 swings
        # 2. Check if they form a valid 1-2-3
        # 3. Validate retracement ratios
        # 4. Return points and entry/SL/TP levels
        
        return self._fail("Not yet implemented")
