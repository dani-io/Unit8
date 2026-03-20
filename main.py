"""
Unit8 — main entry point.

This is how everything connects:
    1. Load config (checklist + credentials)
    2. Create broker connection
    3. Create tools
    4. Build checklist from config
    5. Loop: get data → evaluate checklist → execute if GO → monitor positions
"""

import json
import time
import logging
import os
from datetime import datetime

from unit8.broker.mt5_broker import MT5Broker
from unit8.core.checklist import Checklist
from unit8.core.risk import RiskManager
from unit8.core.execution import ExecutionEngine
from unit8.tools import (
    SwingDetector, Ichimoku, Pattern123,
    TrendlineBreak, DivergenceDetector,
    SupportResistance, SpreadFilter,
)

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("unit8")


def load_checklist_config(path: str = "config/checklist_example.json") -> dict:
    """Load strategy checklist from JSON."""
    with open(path, "r") as f:
        return json.load(f)


def build_tools(checks: list) -> dict:
    """
    Create tool instances from checklist config.
    Each tool gets its own config from the checklist.
    """
    tool_map = {
        "swing_detector": SwingDetector,
        "ichimoku": Ichimoku,
        "pattern_123": Pattern123,
        "trendline_break": TrendlineBreak,
        "divergence": DivergenceDetector,
        "support_resistance": SupportResistance,
        "spread_filter": SpreadFilter,
    }
    
    tools = {}
    for check in checks:
        name = check["tool"]
        cls = tool_map.get(name)
        if cls:
            tools[name] = cls(config=check.get("config", {}))
        else:
            logger.warning(f"Unknown tool: {name}")
    
    return tools


def is_trading_time(schedule: dict) -> bool:
    """Check if we're within allowed trading hours and days."""
    now = datetime.now()
    
    weekdays = schedule.get("allowed_weekdays", [0, 1, 2, 3, 4])
    if now.weekday() not in weekdays:
        return False
    
    hours = schedule.get("trading_hours", [8, 20])
    if not (hours[0] <= now.hour < hours[1]):
        return False
    
    return True


def main():
    # --- 1. Load config ---
    config = load_checklist_config()
    logger.info(f"Loaded strategy: {config['name']}")
    
    # --- 2. Connect broker ---
    broker = MT5Broker()
    if not broker.connect():
        logger.error("Failed to connect to broker")
        return
    
    # --- 3. Build tools ---
    tools = build_tools(config["checks"])
    logger.info(f"Tools loaded: {list(tools.keys())}")
    
    # --- 4. Create checklist + risk + execution ---
    checklist = Checklist(config, tools)
    risk = RiskManager(config.get("risk", {}))
    engine = ExecutionEngine(broker, risk)
    
    # Set initial balance for daily tracking
    account = broker.get_account_info()
    if account:
        risk.set_initial_balance(account.balance)
        logger.info(f"Initial balance: {account.balance}")
    
    # --- 5. Main loop ---
    schedule = config.get("schedule", {})
    interval = schedule.get("interval_seconds", 30)
    symbols = config.get("symbols", [])
    timeframe = config.get("timeframe", "M15")
    
    logger.info(f"Starting loop | {len(symbols)} symbols | {timeframe} | every {interval}s")
    
    try:
        while True:
            if not is_trading_time(schedule):
                logger.debug("Outside trading hours")
                time.sleep(60)
                continue
            
            for symbol in symbols:
                try:
                    # Check for open positions first
                    positions = broker.get_positions(symbol)
                    if positions:
                        engine.check_risk_free(symbol)
                        continue
                    
                    # Get data
                    df = broker.get_ohlcv(symbol, timeframe)
                    if df is None or df.empty:
                        continue
                    
                    # Evaluate checklist
                    decision = checklist.evaluate(df, symbol)
                    
                    if decision.go:
                        logger.info(f"\n{decision.summary()}")
                        engine.execute(decision)
                    
                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}", exc_info=True)
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
