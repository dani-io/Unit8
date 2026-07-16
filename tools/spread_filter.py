"""
Spread filter.

Checks if current spread is within acceptable range before trading.
Ported from Katana.py with improvements.
"""

from .base_tool import BaseTool, ToolResult


class SpreadFilter(BaseTool):
    """
    Checks spread before allowing a trade.
    
    Config:
        max_spread_points: int = 20
        overrides: dict = {"XAUUSD": 40}  — per-symbol limits
    """
    
    name = "spread_filter"
    
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.max_spread = self.config.get("max_spread_points", 20)
        self.overrides = self.config.get("overrides", {})
    
    def check(self, df, symbol: str = "", context: dict = None) -> ToolResult:
        """
        Checklist entry point. Uses live tick data from context if available
        (context["tick"] = {"bid": ..., "ask": ..., "point": ...}),
        otherwise fails gracefully — spread can't be derived from OHLCV alone.
        """
        tick = (context or {}).get("tick", {})
        bid, ask, point = tick.get("bid"), tick.get("ask"), tick.get("point")
        if bid is None or ask is None or point is None:
            return self._fail("No tick data in context (need bid/ask/point)")
        return self.check_spread(symbol, bid, ask, point)

    def check_spread(self, symbol: str, bid: float, ask: float, point: float) -> ToolResult:
        """
        Check if spread is acceptable.
        
        Args:
            symbol: Trading symbol
            bid: Current bid price
            ask: Current ask price
            point: Symbol's point size (e.g., 0.00001 for EURUSD)
        """
        if point <= 0:
            return self._fail("Invalid point size")
        
        spread_points = (ask - bid) / point
        limit = self.overrides.get(symbol, self.max_spread)
        
        if spread_points > limit:
            return self._fail(
                reason=f"Spread {spread_points:.1f} > limit {limit}"
            )
        
        return self._pass(
            data={"spread_points": round(spread_points, 2)},
            reason=f"Spread {spread_points:.1f} within limit {limit}"
        )
