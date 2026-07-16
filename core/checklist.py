"""
Checklist engine — the heart of Unit8.

A strategy is NOT code. A strategy is a JSON checklist.
This engine loads the checklist, runs each tool, and decides: go or no-go.

Example checklist config:
{
    "name": "ichimoku_123_reversal",
    "timeframe": "M15",
    "entry_timeframe": "M1",
    "checks": [
        {"tool": "swing_detector", "required": true},
        {"tool": "trendline_break", "required": true},
        {"tool": "pattern_123", "required": true},
        {"tool": "ichimoku", "required": true},
        {"tool": "divergence", "required": false},
        {"tool": "spread_filter", "required": true}
    ]
}

If required=true and tool fails → no trade.
If required=false and tool fails → trade still possible, just noted.
"""

import logging
from typing import Dict, List
from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("unit8.checklist")


class Checklist:
    """
    Runs a list of tools and returns a go/no-go decision.
    
    Usage:
        checklist = Checklist(config, tools)
        decision = checklist.evaluate(df, symbol)
        if decision.go:
            execute_trade(decision)
    """
    
    def __init__(self, config: dict, tools: Dict[str, BaseTool]):
        """
        Args:
            config: Checklist config with "checks" list
            tools: Dict mapping tool names to tool instances
        """
        self.name = config.get("name", "unnamed_strategy")
        self.checks = config.get("checks", [])
        self.tools = tools
    
    def evaluate(self, df, symbol: str, initial_context: dict = None) -> "ChecklistResult":
        """
        Run all checks and return a decision.

        Args:
            df: OHLCV DataFrame
            symbol: Trading symbol
            initial_context: Optional pre-populated context (e.g., live tick
                data under "tick" for spread_filter). Tool results are
                accumulated on top of it.

        Returns:
            ChecklistResult with go/no-go and all tool results
        """
        results: List[ToolResult] = []
        all_required_passed = True
        signal = "none"
        # Shared tool results — lets later tools use earlier output
        context = dict(initial_context) if initial_context else {}

        for check in self.checks:
            tool_name = check["tool"]
            required = check.get("required", True)

            tool = self.tools.get(tool_name)
            if tool is None:
                logger.warning(f"Tool '{tool_name}' not found, skipping")
                if required:
                    all_required_passed = False
                continue

            result = tool.check(df, symbol, context=context)
            context[tool_name] = result.data
            results.append(result)
            
            if result.passed and result.signal != "none":
                signal = result.signal
            
            if required and not result.passed:
                all_required_passed = False
                logger.info(f"[{symbol}] Required check '{tool_name}' failed: {result.reason}")
        
        go = all_required_passed and signal != "none"
        
        return ChecklistResult(
            go=go,
            signal=signal,
            results=results,
            checklist_name=self.name,
            symbol=symbol,
        )


class ChecklistResult:
    """Decision from the checklist engine."""
    
    def __init__(self, go: bool, signal: str, results: List[ToolResult],
                 checklist_name: str, symbol: str):
        self.go = go
        self.signal = signal
        self.results = results
        self.checklist_name = checklist_name
        self.symbol = symbol
    
    def summary(self) -> str:
        """Human-readable summary of all checks."""
        lines = [f"[{self.symbol}] {self.checklist_name}: {'GO' if self.go else 'NO-GO'} ({self.signal})"]
        for r in self.results:
            status = "✓" if r.passed else "✗"
            lines.append(f"  {status} {r.name}: {r.reason}")
        return "\n".join(lines)
