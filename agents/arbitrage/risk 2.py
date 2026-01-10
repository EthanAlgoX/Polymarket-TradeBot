"""
Enhanced Risk Manager for Polymarket Arbitrage Bot

Features:
- Circuit breaker with daily P&L limits
- Maximum open positions limit
- Maximum daily trades limit
- Emergency stop functionality
- Position size validation

Ported from Polymarket-Copy-Trading-Bot-develop RiskManager.
"""

import time
import logging
from typing import Optional, Dict, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.arbitrage.types import ArbitrageOpportunity
from agents.arbitrage.config import (
    MIN_PROFIT_SPREAD, MAX_POSITION_SIZE,
    DAILY_PNL_LIMIT, MAX_OPEN_POSITIONS, MAX_DAILY_TRADES,
    CIRCUIT_BREAKER_COOLDOWN, EMERGENCY_STOP_LOSS,
    MARKET_COOLDOWN_DURATION, MIN_TRADE_INTERVAL
)

logger = logging.getLogger("RiskManager")


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: str
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return self.winning_trades / total if total > 0 else 0.0


@dataclass
class RiskMetrics:
    """Current risk metrics snapshot."""
    daily_pnl: float = 0.0
    daily_trades: int = 0
    open_positions: int = 0
    circuit_breaker_active: bool = False
    circuit_breaker_reason: Optional[str] = None
    can_trade: bool = True
    # Enhanced metrics
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    cooldown_markets: int = 0


class RiskManager:
    """
    Enhanced Risk Manager with circuit breaker and emergency controls.
    
    Features:
    - Daily P&L limit monitoring (-$100 default)
    - Maximum concurrent positions (5 default)
    - Maximum daily trades (50 default)
    - Circuit breaker with cooldown
    - Emergency close all functionality
    """
    
    def __init__(
        self,
        daily_pnl_limit: float = DAILY_PNL_LIMIT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
        max_daily_trades: int = MAX_DAILY_TRADES,
        circuit_breaker_cooldown: float = CIRCUIT_BREAKER_COOLDOWN,
        market_cooldown_duration: float = MARKET_COOLDOWN_DURATION,
        min_trade_interval: float = MIN_TRADE_INTERVAL
    ):
        self.min_profit = MIN_PROFIT_SPREAD
        self.max_position_size = MAX_POSITION_SIZE
        self.daily_pnl_limit = daily_pnl_limit
        self.max_open_positions = max_open_positions
        self.max_daily_trades = max_daily_trades
        self.circuit_breaker_cooldown = circuit_breaker_cooldown
        self.market_cooldown_duration = market_cooldown_duration
        self.min_trade_interval = min_trade_interval
        
        # State tracking
        self.daily_pnl = 0.0
        self.daily_trades_count = 0
        self.open_positions_count = 0
        self._current_date = self._get_today()
        
        # Circuit breaker state
        self.circuit_breaker_triggered = False
        self.circuit_breaker_reason: Optional[str] = None
        self.circuit_breaker_time: Optional[float] = None
        
        # Daily stats history
        self.daily_stats: Dict[str, DailyStats] = {}
        
        # Event callbacks
        self._on_circuit_breaker: Optional[Callable[[bool, str], None]] = None
        
        # Enhanced tracking (from riskManager.ts)
        self._cooldown_markets: Dict[str, float] = {}  # market_id -> cooldown_end_time
        self._last_trade_time: float = 0.0
        self._max_drawdown: float = 0.0
        self._peak_pnl: float = 0.0
    
    def _get_today(self) -> str:
        """Get today's date string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def _check_day_rollover(self) -> None:
        """Check and handle day rollover."""
        today = self._get_today()
        if today != self._current_date:
            # Save yesterday's stats
            if self._current_date not in self.daily_stats:
                self.daily_stats[self._current_date] = DailyStats(date=self._current_date)
            stats = self.daily_stats[self._current_date]
            stats.total_pnl = self.daily_pnl
            stats.trades_count = self.daily_trades_count
            
            # Reset for new day
            self._current_date = today
            self.daily_pnl = 0.0
            self.daily_trades_count = 0
            
            # Reset circuit breaker if cooldown passed
            if self.circuit_breaker_triggered:
                self._reset_circuit_breaker()
            
            logger.info(f"Day rollover: reset daily stats for {today}")
    
    def check_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        current_balance: float
    ) -> bool:
        """
        Validates if an opportunity is safe to execute.
        
        Checks:
        1. Circuit breaker not active
        2. Market not in cooldown
        3. Trade frequency limit
        4. Daily trade limit not reached
        5. Open positions limit not reached
        6. Minimum profit threshold
        7. Sufficient balance
        """
        self._check_day_rollover()
        
        # 1. Check circuit breaker
        if self.circuit_breaker_triggered:
            if not self._check_circuit_breaker_cooldown():
                logger.warning(f"Trade blocked: Circuit breaker active - {self.circuit_breaker_reason}")
                return False
        
        # 2. Check market cooldown (new)
        if self._is_market_in_cooldown(opportunity.market_id):
            logger.warning(f"Trade blocked: Market {opportunity.market_id[:20]}... in cooldown")
            return False
        
        # 3. Check trade frequency (new)
        now = time.time()
        if now - self._last_trade_time < self.min_trade_interval:
            logger.debug(f"Trade blocked: Trade frequency limit (min {self.min_trade_interval}s)")
            return False
        
        # 4. Check daily trade limit
        if self.daily_trades_count >= self.max_daily_trades:
            logger.warning(f"Trade blocked: Daily trade limit reached ({self.max_daily_trades})")
            return False
        
        # 5. Check open positions limit
        if self.open_positions_count >= self.max_open_positions:
            logger.warning(f"Trade blocked: Max open positions reached ({self.max_open_positions})")
            return False
        
        # 6. Check minimum profit
        if opportunity.potential_profit < self.min_profit:
            logger.debug(f"Trade blocked: Profit {opportunity.potential_profit:.4f} < Min {self.min_profit}")
            return False
        
        # 7. Check balance
        required_capital = opportunity.total_cost * opportunity.max_volume
        if required_capital > current_balance:
            logger.warning(f"Trade blocked: Insufficient balance. Need {required_capital}, have {current_balance}")
            return False
        
        # 8. Check max volume is positive
        if opportunity.max_volume <= 0:
            return False
        
        return True
    
    def calculate_safe_size(
        self,
        opportunity: ArbitrageOpportunity,
        current_balance: float
    ) -> float:
        """
        Determines the safe execution size (volume) for this trade.
        
        Considers:
        - Available liquidity
        - Maximum capital per trade
        - Available balance
        - Emergency stop loss buffer
        """
        # Limit by available liquidity
        size = opportunity.max_volume
        
        # Limit by max capital per trade
        if opportunity.total_cost > 0:
            max_units_by_capital = self.max_position_size / opportunity.total_cost
            size = min(size, max_units_by_capital)
        
        # Limit by available balance (leave 5% buffer)
        max_units_by_balance = (current_balance * 0.95) / opportunity.total_cost if opportunity.total_cost > 0 else 0
        size = min(size, max_units_by_balance)
        
        return size
    
    def record_trade(self, pnl: float, is_winner: bool, market_id: Optional[str] = None) -> None:
        """Record a completed trade and update stats."""
        self._check_day_rollover()
        
        self.daily_pnl += pnl
        self.daily_trades_count += 1
        self._last_trade_time = time.time()
        
        # Update max drawdown tracking
        if self.daily_pnl > self._peak_pnl:
            self._peak_pnl = self.daily_pnl
        current_drawdown = self._peak_pnl - self.daily_pnl
        if current_drawdown > self._max_drawdown:
            self._max_drawdown = current_drawdown
        
        # Update daily stats
        if self._current_date not in self.daily_stats:
            self.daily_stats[self._current_date] = DailyStats(date=self._current_date)
        
        stats = self.daily_stats[self._current_date]
        stats.trades_count += 1
        if is_winner:
            stats.winning_trades += 1
        else:
            stats.losing_trades += 1
            # Apply market cooldown on loss (new)
            if market_id:
                self._apply_market_cooldown(market_id)
        stats.realized_pnl += pnl
        
        # Check if circuit breaker should trigger
        self._check_pnl_limit()
    
    def update_open_positions(self, count: int) -> None:
        """Update open positions count."""
        self.open_positions_count = count
    
    def _check_pnl_limit(self) -> None:
        """Check if daily P&L limit is breached."""
        if self.daily_pnl <= self.daily_pnl_limit:
            self._trigger_circuit_breaker(f"Daily P&L limit breached: ${self.daily_pnl:.2f} <= ${self.daily_pnl_limit:.2f}")
    
    def _trigger_circuit_breaker(self, reason: str) -> None:
        """Trigger the circuit breaker."""
        if self.circuit_breaker_triggered:
            return  # Already triggered
        
        self.circuit_breaker_triggered = True
        self.circuit_breaker_reason = reason
        self.circuit_breaker_time = time.time()
        
        logger.error(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        
        if self._on_circuit_breaker:
            self._on_circuit_breaker(True, reason)
    
    def _check_circuit_breaker_cooldown(self) -> bool:
        """Check if circuit breaker cooldown has passed."""
        if not self.circuit_breaker_triggered or self.circuit_breaker_time is None:
            return True
        
        elapsed = time.time() - self.circuit_breaker_time
        if elapsed >= self.circuit_breaker_cooldown:
            self._reset_circuit_breaker()
            return True
        
        return False
    
    def _reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker."""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_reason = None
        self.circuit_breaker_time = None
        logger.info("Circuit breaker reset")
        
        if self._on_circuit_breaker:
            self._on_circuit_breaker(False, "")
    
    def force_trigger_circuit_breaker(self, reason: str = "Manual trigger") -> None:
        """Manually trigger circuit breaker."""
        self._trigger_circuit_breaker(reason)
    
    def get_risk_metrics(self) -> RiskMetrics:
        """Get current risk metrics snapshot."""
        self._check_day_rollover()
        self._cleanup_expired_cooldowns()
        
        can_trade = (
            not self.circuit_breaker_triggered and
            self.daily_trades_count < self.max_daily_trades and
            self.open_positions_count < self.max_open_positions
        )
        
        # Calculate win rate
        stats = self.daily_stats.get(self._current_date)
        win_rate = 0.0
        if stats:
            total = stats.winning_trades + stats.losing_trades
            win_rate = stats.winning_trades / total if total > 0 else 0.0
        
        return RiskMetrics(
            daily_pnl=self.daily_pnl,
            daily_trades=self.daily_trades_count,
            open_positions=self.open_positions_count,
            circuit_breaker_active=self.circuit_breaker_triggered,
            circuit_breaker_reason=self.circuit_breaker_reason,
            can_trade=can_trade,
            win_rate=win_rate,
            max_drawdown=self._max_drawdown,
            cooldown_markets=len(self._cooldown_markets)
        )
    
    def get_daily_stats(self, date: Optional[str] = None) -> Optional[DailyStats]:
        """Get daily stats for a specific date or today."""
        target_date = date or self._current_date
        return self.daily_stats.get(target_date)
    
    def on_circuit_breaker(self, callback: Callable[[bool, str], None]) -> None:
        """Register callback for circuit breaker events."""
        self._on_circuit_breaker = callback
    
    # =========================================================================
    # Market Cooldown Methods (Inspired by riskManager.ts)
    # =========================================================================
    
    def _is_market_in_cooldown(self, market_id: str) -> bool:
        """Check if a market is in cooldown period."""
        if market_id not in self._cooldown_markets:
            return False
        
        cooldown_end = self._cooldown_markets[market_id]
        if time.time() >= cooldown_end:
            del self._cooldown_markets[market_id]
            return False
        
        return True
    
    def _apply_market_cooldown(self, market_id: str) -> None:
        """Apply cooldown to a market after a loss."""
        cooldown_end = time.time() + self.market_cooldown_duration
        self._cooldown_markets[market_id] = cooldown_end
        logger.info(f"Applied {self.market_cooldown_duration}s cooldown to market: {market_id[:20]}...")
    
    def _cleanup_expired_cooldowns(self) -> None:
        """Remove expired cooldowns from tracking."""
        now = time.time()
        expired = [mid for mid, end_time in self._cooldown_markets.items() if now >= end_time]
        for mid in expired:
            del self._cooldown_markets[mid]
            logger.debug(f"Cooldown expired for market: {mid[:20]}...")
    
    def get_cooldown_markets(self) -> Dict[str, float]:
        """Get all markets currently in cooldown with remaining time."""
        self._cleanup_expired_cooldowns()
        now = time.time()
        return {mid: max(0, end_time - now) for mid, end_time in self._cooldown_markets.items()}
    
    def clear_market_cooldown(self, market_id: str) -> None:
        """Manually clear cooldown for a specific market."""
        if market_id in self._cooldown_markets:
            del self._cooldown_markets[market_id]
            logger.info(f"Cleared cooldown for market: {market_id[:20]}...")
    
    def emergency_close_all(self, close_func: Callable) -> None:
        """Emergency close all positions."""
        logger.error("EMERGENCY CLOSE ALL POSITIONS")
        self._trigger_circuit_breaker("Emergency close triggered")
        
        try:
            close_func()
            logger.info("Emergency close completed")
        except Exception as e:
            logger.error(f"Emergency close failed: {e}")

