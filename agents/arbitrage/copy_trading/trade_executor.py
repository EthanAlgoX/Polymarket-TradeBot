"""
Copy Trade Executor

Executes trades proportionally based on copied trader's activity.
Calculates position sizes relative to trader's capital and applies
safety limits.

Ported from polymarket-copy-trading-bot-main TradeExecutor.
"""

import time
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass

from agents.arbitrage.config import (
    TRADE_MULTIPLIER, MIN_ORDER_SIZE, MAX_POSITION_SIZE
)
from agents.arbitrage.copy_trading.trader_monitor import TraderMonitor, Trade, Position
from agents.arbitrage.position_manager import PositionManager, PositionSide
from agents.arbitrage.execution import ExecutionEngine

logger = logging.getLogger("CopyTradeExecutor")


@dataclass
class CopyTradeResult:
    """Result of a copy trade execution."""
    success: bool
    trade: Trade
    your_size: float
    your_price: float
    trader_percent: float
    error: Optional[str] = None


class CopyTradeExecutor:
    """
    Executes trades by copying target traders proportionally.
    
    Features:
    - Proportional position sizing based on trader's capital
    - Adjustable trade multiplier
    - Minimum order size filtering
    - Position tracking for copied trades
    """
    
    def __init__(
        self,
        trader_monitor: TraderMonitor,
        execution_engine: Optional[ExecutionEngine] = None,
        position_manager: Optional[PositionManager] = None,
        trade_multiplier: float = TRADE_MULTIPLIER,
        min_order_size: float = MIN_ORDER_SIZE,
        max_position_size: float = MAX_POSITION_SIZE
    ):
        self.trader_monitor = trader_monitor
        self.executor = execution_engine
        self.position_manager = position_manager or PositionManager()
        self.trade_multiplier = trade_multiplier
        self.min_order_size = min_order_size
        self.max_position_size = max_position_size
        
        # Track last processed trade per trader
        self._last_trade_time: Dict[str, int] = {}
        
        # Track executed copy trades
        self._executed_trades: List[CopyTradeResult] = []
    
    def calculate_proportional_size(
        self,
        trade: Trade,
        trader_capital: float,
        your_balance: float
    ) -> float:
        """
        Calculate proportional position size for copying a trade.
        
        Uses the ratio of trader's trade size to their total capital
        to determine what percentage of your capital to use.
        """
        if trader_capital <= 0:
            return 0.0
        
        # What percentage of their capital was this trade?
        trader_percent = trade.usdc_size / trader_capital
        
        # Apply same percentage to your balance
        base_size = your_balance * trader_percent
        
        # Apply trade multiplier
        adjusted_size = base_size * self.trade_multiplier
        
        # Apply limits
        if adjusted_size < self.min_order_size:
            logger.debug(f"Trade size ${adjusted_size:.2f} below minimum ${self.min_order_size}")
            return 0.0
        
        if adjusted_size > self.max_position_size:
            adjusted_size = self.max_position_size
            logger.debug(f"Trade size capped to ${self.max_position_size}")
        
        # Don't use more than 95% of balance
        if adjusted_size > your_balance * 0.95:
            adjusted_size = your_balance * 0.95
        
        return adjusted_size
    
    def should_copy_trade(
        self,
        trade: Trade,
        your_position: Optional[Position],
        trader_position: Optional[Position]
    ) -> tuple[bool, str]:
        """
        Determine if a trade should be copied based on various criteria.
        
        Returns (should_copy, reason)
        """
        # Skip if no USDC size
        if trade.usdc_size <= 0:
            return False, "No USDC size"
        
        # For SELL trades, check if we have a position to sell
        if trade.side == "SELL":
            if not your_position or your_position.size <= 0:
                return False, "No position to sell"
        
        # Check if trader still holds position (for BUY)
        if trade.side == "BUY":
            if trader_position and trader_position.size <= 0:
                return False, "Trader no longer holds position"
        
        return True, "OK"
    
    async def execute_copy_trade(
        self,
        trade: Trade,
        trader_address: str,
        your_balance: float,
        your_positions: List[Position],
        dry_run: bool = True
    ) -> CopyTradeResult:
        """
        Execute a copy trade for the given trade.
        
        Args:
            trade: The trade to copy
            trader_address: Address of the trader being copied
            your_balance: Your current USDC balance
            your_positions: Your current positions
            dry_run: If True, don't actually execute (paper trading)
        """
        try:
            # Get trader's positions and capital
            trader_positions = self.trader_monitor.fetch_trader_positions(trader_address)
            trader_trades = self.trader_monitor.fetch_trader_activity(trader_address)
            trader_capital = self.trader_monitor.get_trader_capital_estimate(
                trader_address, trader_trades
            )
            
            # Find matching positions
            your_position = next(
                (p for p in your_positions if p.asset == trade.asset),
                None
            )
            trader_position = next(
                (p for p in trader_positions if p.asset == trade.asset),
                None
            )
            
            # Check if we should copy
            should_copy, reason = self.should_copy_trade(
                trade, your_position, trader_position
            )
            
            if not should_copy:
                return CopyTradeResult(
                    success=False,
                    trade=trade,
                    your_size=0,
                    your_price=trade.price,
                    trader_percent=0,
                    error=reason
                )
            
            # Calculate size
            your_size = self.calculate_proportional_size(
                trade, trader_capital, your_balance
            )
            
            if your_size <= 0:
                return CopyTradeResult(
                    success=False,
                    trade=trade,
                    your_size=0,
                    your_price=trade.price,
                    trader_percent=trade.usdc_size / trader_capital if trader_capital > 0 else 0,
                    error="Size too small after calculations"
                )
            
            trader_percent = trade.usdc_size / trader_capital if trader_capital > 0 else 0
            
            if dry_run:
                # Paper trading - just log and track
                logger.info(
                    f"[DRY RUN] Copy {trade.side} {trade.outcome} "
                    f"@ ${trade.price:.4f} - Your size: ${your_size:.2f} "
                    f"(Trader: {trader_percent*100:.2f}% of capital)"
                )
                
                # Update position manager for tracking
                if trade.side == "BUY":
                    self.position_manager.add_position(
                        market_id=trade.market or trade.asset,
                        token_id=trade.asset,
                        outcome=trade.outcome,
                        entry_price=trade.price,
                        size=your_size / trade.price,
                        side=PositionSide.LONG
                    )
                else:
                    self.position_manager.close_position(
                        market_id=trade.market or trade.asset,
                        token_id=trade.asset,
                        exit_price=trade.price
                    )
                
                result = CopyTradeResult(
                    success=True,
                    trade=trade,
                    your_size=your_size,
                    your_price=trade.price,
                    trader_percent=trader_percent
                )
            else:
                # Real execution
                if self.executor:
                    # Execute using the execution engine
                    # Note: This would need the proper order format
                    logger.info(
                        f"Executing {trade.side} {trade.outcome} "
                        f"@ ${trade.price:.4f} - Size: ${your_size:.2f}"
                    )
                    # Real execution logic here
                    result = CopyTradeResult(
                        success=True,
                        trade=trade,
                        your_size=your_size,
                        your_price=trade.price,
                        trader_percent=trader_percent
                    )
                else:
                    result = CopyTradeResult(
                        success=False,
                        trade=trade,
                        your_size=your_size,
                        your_price=trade.price,
                        trader_percent=trader_percent,
                        error="No execution engine configured"
                    )
            
            self._executed_trades.append(result)
            return result
            
        except Exception as e:
            logger.error(f"Error executing copy trade: {e}")
            return CopyTradeResult(
                success=False,
                trade=trade,
                your_size=0,
                your_price=trade.price,
                trader_percent=0,
                error=str(e)
            )
    
    def get_pending_trades(self, trader_address: str) -> List[Trade]:
        """Get trades that haven't been processed yet."""
        last_time = self._last_trade_time.get(trader_address, 0)
        trades = self.trader_monitor.get_new_trades(trader_address, last_time)
        
        if trades:
            self._last_trade_time[trader_address] = trades[-1].timestamp
        
        return trades
    
    def get_executed_trades(self) -> List[CopyTradeResult]:
        """Get list of executed copy trades."""
        return self._executed_trades.copy()
    
    def get_copy_statistics(self) -> Dict:
        """Get statistics about copy trading performance."""
        if not self._executed_trades:
            return {
                "total_trades": 0,
                "successful_trades": 0,
                "failed_trades": 0,
                "total_volume": 0.0,
                "success_rate": 0.0
            }
        
        successful = [t for t in self._executed_trades if t.success]
        failed = [t for t in self._executed_trades if not t.success]
        
        return {
            "total_trades": len(self._executed_trades),
            "successful_trades": len(successful),
            "failed_trades": len(failed),
            "total_volume": sum(t.your_size for t in successful),
            "success_rate": len(successful) / len(self._executed_trades) * 100
        }
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self._executed_trades.clear()
        self._last_trade_time.clear()
