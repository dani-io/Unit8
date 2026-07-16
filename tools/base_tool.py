"""
Base class for all Unit8 analysis tools.

Every tool follows the same contract:
    - Takes a DataFrame (OHLCV data)
    - Returns a ToolResult with passed/failed + data

This makes tools interchangeable in the checklist.
"""

from dataclasses import dataclass, field
from typing import Any
import pandas as pd


@dataclass
class ToolResult:
    """Standard output from any tool."""
    name: str              # Tool name (e.g., "swing_detector")
    passed: bool           # Did this check pass?
    signal: str = "none"   # "buy", "sell", or "none"
    data: dict = field(default_factory=dict)  # Extra data (levels, points, etc.)
    reason: str = ""       # Human-readable explanation


class BaseTool:
    """
    All tools inherit from this.
    
    Usage:
        tool = SwingDetector(config)
        result = tool.check(df, symbol)
        if result.passed:
            print(result.signal)
    """
    
    name: str = "base_tool"
    
    def __init__(self, config: dict = None):
        self.config = config or {}
    
    def check(self, df: pd.DataFrame, symbol: str = "", context: dict = None) -> ToolResult:
        """
        Run the tool's analysis on the given data.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume, time
            symbol: Trading symbol (e.g., "EURUSD")
            context: Shared dict of previous tool results, keyed by tool name.
                     Filled by the checklist as it runs — lets tools build on
                     each other (e.g., pattern_123 reads swing_detector's swings).

        Returns:
            ToolResult with pass/fail and relevant data
        """
        raise NotImplementedError("Each tool must implement check()")
    
    def _fail(self, reason: str = "") -> ToolResult:
        """Shortcut to return a failed result."""
        return ToolResult(name=self.name, passed=False, reason=reason)
    
    def _pass(self, signal: str = "none", data: dict = None, reason: str = "") -> ToolResult:
        """Shortcut to return a passed result."""
        return ToolResult(name=self.name, passed=True, signal=signal, data=data or {}, reason=reason)
