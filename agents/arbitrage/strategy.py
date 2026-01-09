"""
Enhanced Arbitrage Strategy for Polymarket

Features:
- Negative Risk / Spread Arbitrage detection
- Position tracking with entry/exit management
- Profit target and stop-loss mechanisms
- Max hold time configuration
- Trailing stop support

Ported from Polymarket-Copy-Trading-Bot-develop SpreadArbitrageStrategy.
"""

import time
import logging
from typing import List, Optional, Dict
from dataclasses import dataclass
from enum import Enum

from agents.arbitrage.types import OrderbookSnapshot, ArbitrageOpportunity, SpreadOpportunity
from agents.arbitrage.config import (
    MIN_PROFIT_SPREAD, PROFIT_TARGET, STOP_LOSS, MAX_HOLD_TIME,
    TRAILING_STOP_PERCENT, FEE_RATE
)
from agents.arbitrage.position_manager import PositionManager, Position, PositionSide

logger = logging.getLogger("ArbitrageStrategy")


class SignalType(Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass
class TradeSignal:
    """Trading signal for execution."""
    signal_type: SignalType
    market_id: str
    token_id: str
    side: str  # 'BUY' or 'SELL'
    size: float
    price: float
    reason: str
    confidence: float
    timestamp: float


class ArbitrageStrategy:
    """
    Enhanced Arbitrage Strategy with position management.
    
    Detects negative risk opportunities and manages positions with:
    - Profit targets (default 1%)
    - Stop-loss (default 2%)
    - Max hold time (default 5 minutes)
    - Trailing stops
    """
    
    def __init__(
        self,
        min_profit: float = MIN_PROFIT_SPREAD,
        profit_target: float = PROFIT_TARGET,
        stop_loss: float = STOP_LOSS,
        max_hold_time: float = MAX_HOLD_TIME,
        trailing_stop_percent: float = TRAILING_STOP_PERCENT
    ):
        self.min_profit = min_profit
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.max_hold_time = max_hold_time
        self.trailing_stop_percent = trailing_stop_percent
        self.fee = FEE_RATE
        
        # Position tracking
        self.position_manager = PositionManager()
        
        # Price history for trailing stops
        self._price_history: Dict[str, List[float]] = {}
    
    def detect_arbitrage(
        self,
        market_id: str,
        orderbooks: List[OrderbookSnapshot]
    ) -> Optional[ArbitrageOpportunity]:
        """
        Detects Negative Risk / Spread Arbitrage opportunities.
        
        Logic: If sum(BestAsk for all outcomes) < 1.0, we can buy all 
        outcomes and guarantee a payout of 1.0.
        """
        if not orderbooks:
            return None

        # Ensure all orderbooks are valid and have at least one ask
        for ob in orderbooks:
            if not ob.asks:
                return None

        # Extract best asks and their potential max volume
        best_asks = [ob.best_ask for ob in orderbooks]

        # Calculate total cost to buy 1 unit of each outcome
        total_cost = sum(best_asks)

        # Adjust for potential fees
        total_cost_with_fee = total_cost * (1 + self.fee)

        # Profit calculation: Payout (1.0) - Cost
        potential_profit = 1.0 - total_cost_with_fee

        # Check if profit meets threshold
        if potential_profit >= self.min_profit:
            # Calculate max executable volume based on top level liquidity
            top_volumes = [ob.asks[0].size for ob in orderbooks]
            max_volume = min(top_volumes)

            return ArbitrageOpportunity(
                market_id=market_id,
                timestamp=orderbooks[0].timestamp,
                outcomes=[ob.asset_id for ob in orderbooks],
                prices=best_asks,
                total_cost=total_cost,
                potential_profit=potential_profit,
                max_volume=max_volume
            )

        return None
    
    def detect_spread_opportunity(
        self,
        market_id: str,
        orderbooks: List[OrderbookSnapshot]
    ) -> Optional[SpreadOpportunity]:
        """
        Detects spread arbitrage on individual outcomes (from spreadArb.ts).
        
        Logic:
        - Buy YES if: best_ask_YES < (1 - best_bid_NO) - fee
        - Buy NO if: best_ask_NO < (1 - best_bid_YES) - fee
        
        This detects profitable single-side trades when the spread is mispriced.
        """
        if len(orderbooks) != 2:
            return None
        
        # Identify YES and NO orderbooks by outcome naming convention
        yes_ob = None
        no_ob = None
        
        for ob in orderbooks:
            # Check if this is YES or NO based on token ID patterns
            # In Polymarket, typically the first token is YES, second is NO
            if yes_ob is None:
                yes_ob = ob
            else:
                no_ob = ob
        
        if not yes_ob or not no_ob:
            return None
        
        # Ensure both have valid bid/ask
        if not yes_ob.asks or not no_ob.asks:
            return None
        if not yes_ob.bids or not no_ob.bids:
            return None
        
        now = time.time()
        
        # Check: Buy YES if best_ask_YES < (1 - best_bid_NO) - fee
        yes_arb_price = 1.0 - no_ob.best_bid - self.fee
        yes_profit = yes_arb_price - yes_ob.best_ask
        
        if yes_profit > self.min_profit:
            max_volume = min(yes_ob.asks[0].size, no_ob.bids[0].size)
            confidence = min(yes_profit * 10, 0.9)  # Scale confidence
            
            return SpreadOpportunity(
                market_id=market_id,
                timestamp=now,
                token_id=yes_ob.asset_id,
                side='YES',
                entry_price=yes_ob.best_ask,
                expected_profit=yes_profit,
                confidence=confidence,
                max_volume=max_volume,
                opposite_bid=no_ob.best_bid
            )
        
        # Check: Buy NO if best_ask_NO < (1 - best_bid_YES) - fee
        no_arb_price = 1.0 - yes_ob.best_bid - self.fee
        no_profit = no_arb_price - no_ob.best_ask
        
        if no_profit > self.min_profit:
            max_volume = min(no_ob.asks[0].size, yes_ob.bids[0].size)
            confidence = min(no_profit * 10, 0.9)
            
            return SpreadOpportunity(
                market_id=market_id,
                timestamp=now,
                token_id=no_ob.asset_id,
                side='NO',
                entry_price=no_ob.best_ask,
                expected_profit=no_profit,
                confidence=confidence,
                max_volume=max_volume,
                opposite_bid=yes_ob.best_bid
            )
        
        return None
    
    def evaluate(
        self,
        market_id: str,
        orderbooks: List[OrderbookSnapshot]
    ) -> List[TradeSignal]:
        """
        Evaluate market for entry and exit signals.
        
        Returns list of trade signals to execute.
        """
        signals: List[TradeSignal] = []
        
        # Update position prices
        for ob in orderbooks:
            self.position_manager.update_position_prices(market_id, ob.asset_id, ob.best_ask)
            self._update_price_history(ob.asset_id, ob.best_ask)
        
        # Check for exit conditions on existing positions
        exit_signals = self._check_exit_conditions(market_id, orderbooks)
        signals.extend(exit_signals)
        
        # Check for new arbitrage opportunities (only if no existing position)
        if not self._has_market_position(market_id):
            # First try full negative risk arbitrage
            opportunity = self.detect_arbitrage(market_id, orderbooks)
            if opportunity:
                entry_signals = self._create_entry_signals(opportunity)
                signals.extend(entry_signals)
            else:
                # If no full arb, try single-side spread opportunity
                spread_opp = self.detect_spread_opportunity(market_id, orderbooks)
                if spread_opp:
                    entry_signal = self._create_spread_entry_signal(spread_opp)
                    if entry_signal:
                        signals.append(entry_signal)
        
        return signals
    
    def _create_spread_entry_signal(self, spread_opp: SpreadOpportunity) -> Optional[TradeSignal]:
        """Create entry signal for a spread opportunity."""
        now = time.time()
        
        return TradeSignal(
            signal_type=SignalType.ENTRY,
            market_id=spread_opp.market_id,
            token_id=spread_opp.token_id,
            side='BUY',
            size=spread_opp.max_volume,
            price=spread_opp.entry_price,
            reason=f"Spread Arb ({spread_opp.side}): {spread_opp.expected_profit*100:.2f}% profit",
            confidence=spread_opp.confidence,
            timestamp=now
        )
    
    def _has_market_position(self, market_id: str) -> bool:
        """Check if we have any position in this market."""
        for pos in self.position_manager.get_active_positions():
            if pos.market_id == market_id:
                return True
        return False
    
    def _update_price_history(self, token_id: str, price: float) -> None:
        """Track price history for trailing stops."""
        if token_id not in self._price_history:
            self._price_history[token_id] = []
        
        history = self._price_history[token_id]
        history.append(price)
        
        # Keep last 100 prices
        if len(history) > 100:
            self._price_history[token_id] = history[-100:]
    
    def _check_exit_conditions(
        self,
        market_id: str,
        orderbooks: List[OrderbookSnapshot]
    ) -> List[TradeSignal]:
        """
        Check exit conditions for all positions in this market.
        
        Exit conditions:
        1. Profit target reached (1%)
        2. Stop-loss triggered (2%)
        3. Max hold time exceeded (5 minutes)
        4. Trailing stop triggered
        """
        signals: List[TradeSignal] = []
        now = time.time()
        
        for pos in self.position_manager.get_active_positions():
            if pos.market_id != market_id:
                continue
            
            # Find orderbook for this position
            ob = next((o for o in orderbooks if o.asset_id == pos.token_id), None)
            if not ob:
                continue
            
            current_price = ob.best_bid  # Use bid for selling
            pnl_percent = pos.pnl_percent
            hold_time = now - pos.entry_time
            
            exit_reason = None
            
            # 1. Check profit target
            if pnl_percent >= self.profit_target:
                exit_reason = f"Profit target reached: {pnl_percent*100:.2f}%"
            
            # 2. Check stop-loss
            elif pnl_percent <= -self.stop_loss:
                exit_reason = f"Stop-loss triggered: {pnl_percent*100:.2f}%"
            
            # 3. Check max hold time
            elif hold_time >= self.max_hold_time:
                exit_reason = f"Max hold time reached: {hold_time:.0f}s"
            
            # 4. Check trailing stop
            elif self._check_trailing_stop(pos, current_price):
                exit_reason = f"Trailing stop triggered at {current_price:.4f}"
            
            if exit_reason:
                signals.append(TradeSignal(
                    signal_type=SignalType.EXIT,
                    market_id=pos.market_id,
                    token_id=pos.token_id,
                    side='SELL',
                    size=pos.size,
                    price=current_price,
                    reason=exit_reason,
                    confidence=0.9,
                    timestamp=now
                ))
                logger.info(f"Exit signal: {exit_reason}")
        
        return signals
    
    def _check_trailing_stop(self, pos: Position, current_price: float) -> bool:
        """Check if trailing stop is triggered."""
        if pos.highest_price <= pos.entry_price:
            return False  # No profit yet, don't use trailing stop
        
        # Calculate trailing stop price
        stop_price = pos.highest_price * (1 - self.trailing_stop_percent)
        
        # Update position's stop price if new high
        if stop_price > pos.stop_price:
            pos.stop_price = stop_price
        
        return current_price <= pos.stop_price
    
    def _create_entry_signals(self, opportunity: ArbitrageOpportunity) -> List[TradeSignal]:
        """Create entry signals for arbitrage opportunity."""
        signals: List[TradeSignal] = []
        now = time.time()
        
        for i, outcome_id in enumerate(opportunity.outcomes):
            signals.append(TradeSignal(
                signal_type=SignalType.ENTRY,
                market_id=opportunity.market_id,
                token_id=outcome_id,
                side='BUY',
                size=opportunity.max_volume,
                price=opportunity.prices[i],
                reason=f"Arbitrage: {opportunity.potential_profit*100:.2f}% profit",
                confidence=min(opportunity.potential_profit * 10, 0.95),
                timestamp=now
            ))
        
        return signals
    
    def on_order_fill(self, signal: TradeSignal, fill_price: float, fill_size: float) -> None:
        """Handle order fill - update position tracking."""
        if signal.signal_type == SignalType.ENTRY:
            self.position_manager.add_position(
                market_id=signal.market_id,
                token_id=signal.token_id,
                outcome=signal.token_id,  # Using token_id as outcome identifier
                entry_price=fill_price,
                size=fill_size,
                side=PositionSide.LONG
            )
            logger.info(f"Opened position: {signal.market_id} @ {fill_price}")
        
        elif signal.signal_type == SignalType.EXIT:
            self.position_manager.close_position(
                market_id=signal.market_id,
                token_id=signal.token_id,
                exit_price=fill_price,
                size=fill_size
            )
            logger.info(f"Closed position: {signal.market_id} @ {fill_price}")
    
    def get_portfolio_summary(self):
        """Get current portfolio summary."""
        return self.position_manager.get_portfolio_summary()
    
    def get_active_positions(self) -> List[Position]:
        """Get all active positions."""
        return self.position_manager.get_active_positions()
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self.position_manager.cleanup()
        self._price_history.clear()

