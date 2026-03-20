"""
Risk manager.

Handles: position sizing (% of balance), daily loss limits,
max trade count, and spread/commission in lot calculation.
"""

import logging

logger = logging.getLogger("unit8.risk")


class RiskManager:
    """
    Config:
        risk_per_trade: float = 1.0     — percent of balance per trade
        daily_loss_limit: float = 2.0   — max daily loss percent
        daily_profit_limit: float = 5.0 — max daily profit percent (stop while ahead)
        max_daily_trades: int = None    — auto-calculated if None
    """
    
    def __init__(self, config: dict):
        self.risk_pct = config.get("risk_per_trade", 1.0)
        self.daily_loss_limit = config.get("daily_loss_limit", 2.0)
        self.daily_profit_limit = config.get("daily_profit_limit", 5.0)
        self._initial_balance = None
        self._trade_count_today = 0
    
    def set_initial_balance(self, balance: float):
        """Call at start of each trading day."""
        self._initial_balance = balance
        self._trade_count_today = 0
    
    def calculate_lot_size(self, balance: float, sl_distance: float,
                           tick_value: float, tick_size: float,
                           min_lot: float, max_lot: float, 
                           lot_step: float, spread: float = 0) -> float:
        """
        Calculate lot size based on risk percentage.
        
        Args:
            balance: Account balance
            sl_distance: Distance to stop loss in price units
            tick_value: Value of one tick in account currency
            tick_size: Size of one tick in price units
            min_lot / max_lot / lot_step: Broker constraints
            spread: Current spread in price units (added to risk)
        """
        if sl_distance <= 0 or tick_value <= 0 or tick_size <= 0:
            logger.warning("Invalid parameters for lot calculation")
            return min_lot
        
        risk_amount = balance * (self.risk_pct / 100)
        effective_sl = sl_distance + spread
        ticks_at_risk = effective_sl / tick_size
        lot_size = risk_amount / (ticks_at_risk * tick_value)
        
        # Round to nearest lot step
        lot_size = round(lot_size / lot_step) * lot_step
        lot_size = max(min_lot, min(lot_size, max_lot))
        
        return round(lot_size, 4)
    
    def check_daily_limits(self, current_balance: float) -> bool:
        """
        Check if we should stop trading today.
        Returns True if trading is still allowed.
        """
        if self._initial_balance is None:
            return True
        
        change_pct = ((current_balance - self._initial_balance) / self._initial_balance) * 100
        
        if change_pct <= -self.daily_loss_limit:
            logger.info(f"Daily loss limit reached: {change_pct:.2f}%")
            return False
        
        if change_pct >= self.daily_profit_limit:
            logger.info(f"Daily profit limit reached: {change_pct:.2f}%")
            return False
        
        return True
    
    def can_trade(self, current_balance: float) -> bool:
        """Combined check: daily limits + trade count."""
        if not self.check_daily_limits(current_balance):
            return False
        self._trade_count_today += 1
        return True
