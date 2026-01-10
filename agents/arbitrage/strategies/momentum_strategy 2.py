"""
Momentum Trading Strategy for Polymarket

Trades based on price momentum and volume spikes using:
- Moving average analysis
- Breakout detection
- Volume confirmation
- Trailing stop loss

Ported from Polymarket-Copy-Trading-Bot-develop MomentumStrategy.
"""

import time
import logging
from typing import List, Optional, Dict
from dataclasses import dataclass
from enum import Enum

from agents.arbitrage.types import OrderbookSnapshot
from agents.arbitrage.config import (
    MOMENTUM_ENABLED, LOOKBACK_PERIOD, MOMENTUM_THRESHOLD,
    VOLUME_SPIKE_THRESHOLD, BREAKOUT_THRESHOLD, MOMENTUM_MAX_HOLD_TIME,
    TRAILING_STOP_PERCENT, PROFIT_TARGET, STOP_LOSS
)
from agents.arbitrage.position_manager import PositionManager, Position, PositionSide

logger = logging.getLogger("MomentumStrategy")


class MomentumDirection(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class MomentumSignal:
    """Signal from momentum analysis."""
    has_signal: bool
    direction: MomentumDirection
    strength: float
    breakout_price: float
    volume_confirmed: bool


@dataclass
class TradeSignal:
    """Trading signal for execution."""
    market_id: str
    token_id: str
    side: str  # 'BUY' or 'SELL'
    size: float
    price: float
    reason: str
    confidence: float
    timestamp: float


class MomentumStrategy:
    """
    Momentum Trading Strategy.
    
    Detects price momentum and volume spikes to identify breakout
    opportunities. Uses trailing stops for exit management.
    
    Entry Signals:
    - Price momentum exceeds threshold (2% default)
    - Volume spike confirms signal (50% increase)
    - Breakout above/below recent range
    
    Exit Conditions:
    - Trailing stop triggered
    - Profit target reached
    - Max hold time exceeded
    - Stop-loss triggered
    """
    
    def __init__(
        self,
        enabled: bool = MOMENTUM_ENABLED,
        lookback_period: int = LOOKBACK_PERIOD,
        momentum_threshold: float = MOMENTUM_THRESHOLD,
        volume_threshold: float = VOLUME_SPIKE_THRESHOLD,
        breakout_threshold: float = BREAKOUT_THRESHOLD,
        max_hold_time: float = MOMENTUM_MAX_HOLD_TIME,
        trailing_stop_percent: float = TRAILING_STOP_PERCENT,
        profit_target: float = PROFIT_TARGET,
        stop_loss: float = STOP_LOSS
    ):
        self.enabled = enabled
        self.lookback_period = lookback_period
        self.momentum_threshold = momentum_threshold
        self.volume_threshold = volume_threshold
        self.breakout_threshold = breakout_threshold
        self.max_hold_time = max_hold_time
        self.trailing_stop_percent = trailing_stop_percent
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        
        # Price and volume history
        self._price_history: Dict[str, List[float]] = {}
        self._volume_history: Dict[str, List[float]] = {}
        
        # Position tracking
        self.position_manager = PositionManager()
    
    def is_enabled(self) -> bool:
        """Check if strategy is enabled."""
        return self.enabled
    
    def update_history(self, token_id: str, orderbook: OrderbookSnapshot) -> None:
        """Update price and volume history for a token."""
        # Update price history
        if token_id not in self._price_history:
            self._price_history[token_id] = []
        
        mid_price = (orderbook.best_bid + orderbook.best_ask) / 2 if orderbook.best_bid and orderbook.best_ask else 0
        prices = self._price_history[token_id]
        prices.append(mid_price)
        
        # Keep 2x lookback period
        if len(prices) > self.lookback_period * 2:
            self._price_history[token_id] = prices[-self.lookback_period * 2:]
        
        # Update volume history (use depth as proxy)
        if token_id not in self._volume_history:
            self._volume_history[token_id] = []
        
        volume = orderbook.bid_depth + orderbook.ask_depth
        volumes = self._volume_history[token_id]
        volumes.append(volume)
        
        if len(volumes) > self.lookback_period * 2:
            self._volume_history[token_id] = volumes[-self.lookback_period * 2:]
    
    def calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average."""
        if not prices or period <= 0:
            return 0.0
        
        period = min(period, len(prices))
        multiplier = 2 / (period + 1)
        ema = prices[0]
        
        for price in prices[1:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def analyze_momentum(self, token_id: str) -> MomentumSignal:
        """
        Analyze price momentum for a token.
        
        Returns signal with direction and strength.
        """
        prices = self._price_history.get(token_id, [])
        
        if len(prices) < self.lookback_period:
            return MomentumSignal(
                has_signal=False,
                direction=MomentumDirection.NEUTRAL,
                strength=0.0,
                breakout_price=0.0,
                volume_confirmed=False
            )
        
        recent = prices[-self.lookback_period:]
        older = prices[-self.lookback_period*2:-self.lookback_period] if len(prices) >= self.lookback_period*2 else prices[:self.lookback_period]
        
        if not recent or not older:
            return MomentumSignal(
                has_signal=False,
                direction=MomentumDirection.NEUTRAL,
                strength=0.0,
                breakout_price=0.0,
                volume_confirmed=False
            )
        
        # Calculate EMAs
        recent_ema = self.calculate_ema(recent, len(recent) // 2 or 1)
        older_ema = self.calculate_ema(older, len(older) // 2 or 1)
        
        # Calculate momentum (rate of change)
        momentum = (recent_ema - older_ema) / older_ema if older_ema > 0 else 0
        
        # Check for breakouts
        recent_high = max(recent)
        recent_low = min(recent)
        current_price = recent[-1]
        
        bullish_breakout = current_price > recent_high * (1 + self.breakout_threshold)
        bearish_breakout = current_price < recent_low * (1 - self.breakout_threshold)
        
        # Check volume confirmation
        volume_confirmed = self._detect_volume_spike(token_id)
        
        # Determine signal
        if bullish_breakout and momentum > self.momentum_threshold:
            return MomentumSignal(
                has_signal=True,
                direction=MomentumDirection.BULLISH,
                strength=abs(momentum),
                breakout_price=current_price,
                volume_confirmed=volume_confirmed
            )
        
        if bearish_breakout and momentum < -self.momentum_threshold:
            return MomentumSignal(
                has_signal=True,
                direction=MomentumDirection.BEARISH,
                strength=abs(momentum),
                breakout_price=current_price,
                volume_confirmed=volume_confirmed
            )
        
        return MomentumSignal(
            has_signal=False,
            direction=MomentumDirection.NEUTRAL,
            strength=abs(momentum),
            breakout_price=current_price,
            volume_confirmed=volume_confirmed
        )
    
    def _detect_volume_spike(self, token_id: str) -> bool:
        """Detect if there's a significant volume spike."""
        volumes = self._volume_history.get(token_id, [])
        
        if len(volumes) < self.lookback_period:
            return False
        
        recent = volumes[-5:]  # Last 5 readings
        older = volumes[-self.lookback_period:-5]
        
        if not recent or not older:
            return False
        
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        
        if older_avg <= 0:
            return False
        
        volume_ratio = recent_avg / older_avg
        return volume_ratio > self.volume_threshold
    
    def evaluate(
        self,
        market_id: str,
        token_id: str,
        orderbook: OrderbookSnapshot
    ) -> List[TradeSignal]:
        """
        Evaluate market for momentum signals.
        
        Returns list of trade signals.
        """
        if not self.enabled:
            return []
        
        signals: List[TradeSignal] = []
        
        # Update history
        self.update_history(token_id, orderbook)
        
        # Update position prices
        mid_price = (orderbook.best_bid + orderbook.best_ask) / 2
        self.position_manager.update_position_prices(market_id, token_id, mid_price)
        
        # Check exit conditions for existing positions
        exit_signals = self._check_exit_conditions(market_id, token_id, orderbook)
        signals.extend(exit_signals)
        
        # Check for new entry signals (only if no existing position)
        if not self.position_manager.has_position(market_id, token_id):
            momentum = self.analyze_momentum(token_id)
            
            if momentum.has_signal and momentum.volume_confirmed:
                entry_signal = self._create_entry_signal(
                    market_id, token_id, orderbook, momentum
                )
                if entry_signal:
                    signals.append(entry_signal)
        
        return signals
    
    def _check_exit_conditions(
        self,
        market_id: str,
        token_id: str,
        orderbook: OrderbookSnapshot
    ) -> List[TradeSignal]:
        """Check exit conditions for positions."""
        signals: List[TradeSignal] = []
        now = time.time()
        
        position = self.position_manager.get_position(market_id, token_id)
        if not position:
            return signals
        
        current_price = orderbook.best_bid  # Use bid for selling
        pnl_percent = position.pnl_percent
        hold_time = now - position.entry_time
        
        exit_reason = None
        
        # Trailing stop
        if position.highest_price > position.entry_price:
            stop_price = position.highest_price * (1 - self.trailing_stop_percent)
            if stop_price > position.stop_price:
                position.stop_price = stop_price
            
            if current_price <= position.stop_price:
                exit_reason = f"Trailing stop at {current_price:.4f}"
        
        # Profit target
        if pnl_percent >= self.profit_target:
            exit_reason = f"Profit target: {pnl_percent*100:.2f}%"
        
        # Stop loss
        elif pnl_percent <= -self.stop_loss:
            exit_reason = f"Stop-loss: {pnl_percent*100:.2f}%"
        
        # Max hold time
        elif hold_time >= self.max_hold_time:
            exit_reason = f"Max hold time: {hold_time:.0f}s"
        
        if exit_reason:
            signals.append(TradeSignal(
                market_id=market_id,
                token_id=token_id,
                side='SELL' if position.side == PositionSide.LONG else 'BUY',
                size=position.size,
                price=current_price,
                reason=f"Momentum exit: {exit_reason}",
                confidence=0.85,
                timestamp=now
            ))
            logger.info(f"Momentum exit signal: {exit_reason}")
        
        return signals
    
    def _create_entry_signal(
        self,
        market_id: str,
        token_id: str,
        orderbook: OrderbookSnapshot,
        momentum: MomentumSignal
    ) -> Optional[TradeSignal]:
        """Create entry signal based on momentum."""
        now = time.time()
        
        if momentum.direction == MomentumDirection.BULLISH:
            side = 'BUY'
            price = orderbook.best_ask
        elif momentum.direction == MomentumDirection.BEARISH:
            side = 'SELL'
            price = orderbook.best_bid
        else:
            return None
        
        # Calculate confidence based on momentum strength
        confidence = min(momentum.strength * 10, 0.9)
        
        # Default size (would be adjusted by risk manager)
        size = 10.0
        
        logger.info(
            f"Momentum entry signal: {side} {market_id} @ {price} "
            f"(strength: {momentum.strength*100:.2f}%)"
        )
        
        return TradeSignal(
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            reason=f"Momentum {momentum.direction.value}: {momentum.strength*100:.2f}% strength",
            confidence=confidence,
            timestamp=now
        )
    
    def on_order_fill(
        self,
        signal: TradeSignal,
        fill_price: float,
        fill_size: float
    ) -> None:
        """Handle order fill - update position tracking."""
        if signal.side == 'BUY':
            self.position_manager.add_position(
                market_id=signal.market_id,
                token_id=signal.token_id,
                outcome=signal.token_id,
                entry_price=fill_price,
                size=fill_size,
                side=PositionSide.LONG
            )
            logger.info(f"Opened momentum position: {signal.market_id} @ {fill_price}")
        else:
            self.position_manager.close_position(
                market_id=signal.market_id,
                token_id=signal.token_id,
                exit_price=fill_price,
                size=fill_size
            )
            logger.info(f"Closed momentum position: {signal.market_id} @ {fill_price}")
    
    def get_portfolio_summary(self):
        """Get portfolio summary."""
        return self.position_manager.get_portfolio_summary()
    
    def get_active_positions(self) -> List[Position]:
        """Get active positions."""
        return self.position_manager.get_active_positions()
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self.position_manager.cleanup()
        self._price_history.clear()
        self._volume_history.clear()
