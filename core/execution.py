"""
Execution engine.

Takes a ChecklistResult → validates with RiskManager → places order via Broker.
Also monitors open positions for risk-free opportunities.
"""

import logging
from broker.base_broker import BaseBroker
from core.risk import RiskManager
from core.checklist import ChecklistResult

logger = logging.getLogger("unit8.execution")


class ExecutionEngine:
    """
    The bridge between analysis (checklist) and action (broker).
    
    Flow:
        ChecklistResult → RiskManager validates → Broker places order
    """
    
    def __init__(self, broker: BaseBroker, risk: RiskManager):
        self.broker = broker
        self.risk = risk
    
    def execute(self, decision: ChecklistResult) -> bool:
        """
        Execute a trade based on checklist decision.
        Returns True if trade was placed successfully.
        """
        if not decision.go:
            return False
        
        symbol = decision.symbol
        side = decision.signal  # "buy" or "sell"
        
        # Get account and symbol info
        account = self.broker.get_account_info()
        sym_info = self.broker.get_symbol_info(symbol)
        if not account or not sym_info:
            logger.error(f"Failed to get info for {symbol}")
            return False
        
        # Check daily limits
        if not self.risk.can_trade(account.balance):
            logger.info("Risk manager blocked trade")
            return False
        
        # Get SL/TP from checklist results
        sl, tp = self._extract_levels(decision)
        if sl is None:
            logger.warning(f"No SL found in results for {symbol}")
            return False
        
        # Calculate position size
        tick = self.broker.get_tick(symbol)
        if not tick:
            return False
        
        entry_price = tick.ask if side == "buy" else tick.bid
        sl_distance = abs(entry_price - sl)
        
        lot_size = self.risk.calculate_lot_size(
            balance=account.balance,
            sl_distance=sl_distance,
            tick_value=sym_info.tick_value,
            tick_size=sym_info.tick_size,
            min_lot=sym_info.volume_min,
            max_lot=sym_info.volume_max,
            lot_step=sym_info.volume_step,
            spread=sym_info.spread,
        )
        
        # Place order
        ticket = self.broker.place_order(symbol, side, lot_size, sl=sl, tp=tp)
        if ticket:
            logger.info(f"Trade placed: {symbol} {side} {lot_size} | SL={sl} TP={tp}")
            return True
        
        return False
    
    def _extract_levels(self, decision: ChecklistResult):
        """Extract SL and TP from tool results."""
        for result in decision.results:
            if "sl" in result.data and "tp" in result.data:
                return result.data["sl"], result.data["tp"]
        return None, None
    
    def check_risk_free(self, symbol: str):
        """
        Check open positions and apply risk-free (partial close) 
        when R:R reaches 1:1.
        """
        positions = self.broker.get_positions(symbol)
        
        for pos in positions:
            entry = pos.price_open
            sl = pos.sl
            
            tick = self.broker.get_tick(pos.symbol)
            if not tick:
                continue
            
            current = tick.bid if pos.type == "buy" else tick.ask
            
            risk_distance = abs(entry - sl)
            profit_distance = abs(current - entry)
            
            # If profit >= risk (1:1), close half
            if profit_distance >= risk_distance and risk_distance > 0:
                sym_info = self.broker.get_symbol_info(pos.symbol)
                if not sym_info:
                    continue
                
                half_volume = max(pos.volume / 2, sym_info.volume_min)
                
                # Close half
                close_side = "sell" if pos.type == "buy" else "buy"
                self.broker.place_order(pos.symbol, close_side, half_volume)
                
                # Move SL to breakeven
                self.broker.modify_position(pos.ticket, sl=entry)
                
                logger.info(f"Risk-free applied: {pos.symbol} | Closed {half_volume}")
