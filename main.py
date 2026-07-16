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
import sys
import time
import logging
import os
from datetime import datetime

# Make imports work regardless of where main.py is run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from broker.mock_broker import MockBroker
from broker.mt5_broker import MT5Broker
from broker.ctrader_broker import CTraderBroker
from broker.bridge_broker import BridgeBroker
from core.checklist import Checklist
from core.risk import RiskManager
from core.execution import ExecutionEngine
from tools import (
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


def build_broker(config: dict):
    """
    Create the broker specified in config["broker"]["type"].

    Supported types: "mock", "mt5", "ctrader", "bridge".
    The whole broker section is passed to the broker's constructor,
    so broker-specific keys (data_dir, account, ...) live there too.
    """
    broker_cfg = config.get("broker", {})
    broker_type = broker_cfg.get("type", "mock").lower()

    broker_map = {
        "mock": MockBroker,
        "mt5": MT5Broker,
        "ctrader": CTraderBroker,
        "bridge": BridgeBroker,
    }

    cls = broker_map.get(broker_type)
    if cls is None:
        raise ValueError(
            f"Unknown broker type: '{broker_type}'. "
            f"Supported: {list(broker_map.keys())}"
        )

    logger.info(f"Using broker: {broker_type}")
    return cls(broker_cfg)


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
    # --- 1. Load config (optional path as first CLI arg) ---
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/checklist_example.json"
    config = load_checklist_config(config_path)
    logger.info(f"Loaded strategy: {config['name']}")

    # --- 2. Connect broker ---
    broker = build_broker(config)
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

                    # Inject live tick data into context (needed by spread_filter)
                    context = {}
                    tick = broker.get_tick(symbol)
                    sym_info = broker.get_symbol_info(symbol)
                    if tick and sym_info:
                        context["tick"] = {
                            "bid": tick.bid,
                            "ask": tick.ask,
                            "point": sym_info.point,
                        }

                    # Evaluate checklist
                    decision = checklist.evaluate(df, symbol, initial_context=context)
                    
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
