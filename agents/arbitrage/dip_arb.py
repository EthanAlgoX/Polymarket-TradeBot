"""
DipArb Strategy Service for Polymarket

Based on poly-sdk-main/src/services/dip-arb-service.ts

DipArb (Dip Arbitrage) targets Polymarket's 15-minute crypto UP/DOWN markets.

Strategy Logic:
1. Each market has a "price to beat" (Chainlink price at market open)
2. Settlement rules:
   - UP wins: ending price >= price to beat
   - DOWN wins: ending price < price to beat
3. Arbitrage flow:
   - Leg1: Detect dip → buy dipping side
   - Leg2: Wait for hedge opportunity → buy other side
   - Profit: If total cost < $1, guaranteed profit at settlement

Usage:
    from agents.arbitrage.dip_arb import DipArbStrategy, DipArbSignal
    
    strategy = DipArbStrategy(
        min_profit_rate=0.02,  # 2% minimum profit
        dip_threshold=0.05     # 5% price dip threshold
    )
    
    signal = strategy.analyze(up_ask=0.52, down_ask=0.45)
    if signal:
        print(f"Signal: {signal.signal_type} {signal.side}")
"""

import logging
import time
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("DipArb")


class DipArbPhase(Enum):
    """DipArb round phases."""
    WAITING = "waiting"        # Waiting for dip opportunity
    LEG1_FILLED = "leg1_filled"  # Leg1 executed, waiting for hedge
    COMPLETED = "completed"    # Both legs filled


class DipArbSide(Enum):
    """Market side."""
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class DipArbSignal:
    """Trading signal for DipArb strategy."""
    signal_type: str  # 'leg1' or 'leg2'
    side: DipArbSide
    token_id: str
    target_price: float
    current_price: float
    shares: float
    reason: str
    expected_profit: float
    round_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class DipArbLeg:
    """Executed leg information."""
    side: DipArbSide
    price: float
    shares: float
    timestamp: float
    token_id: str


@dataclass
class DipArbRoundState:
    """State of a DipArb round."""
    round_id: str
    phase: DipArbPhase
    start_time: float
    leg1: Optional[DipArbLeg] = None
    leg2: Optional[DipArbLeg] = None
    total_cost: float = 0.0
    profit: float = 0.0


@dataclass
class DipArbMarketConfig:
    """Configuration for a DipArb market."""
    name: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    underlying: str  # 'BTC', 'ETH', 'SOL'
    duration_minutes: int  # 5 or 15
    end_time: Optional[float] = None
    slug: Optional[str] = None


@dataclass
class DipArbStats:
    """Statistics for DipArb session."""
    start_time: float = field(default_factory=time.time)
    rounds_monitored: int = 0
    rounds_successful: int = 0
    leg1_filled: int = 0
    leg2_filled: int = 0
    total_profit: float = 0.0
    total_spent: float = 0.0
    running_time_ms: float = 0


class DipArbStrategy:
    """
    DipArb Strategy for 15-minute crypto markets.
    
    Monitors UP/DOWN token orderbooks and generates signals when:
    - Leg1: One side dips significantly (opportunity to buy cheap)
    - Leg2: Other side available at price that makes total < $1
    """
    
    def __init__(
        self,
        min_profit_rate: float = 0.02,      # 2% minimum profit
        dip_threshold: float = 0.05,         # 5% dip threshold
        max_total_cost: float = 0.98,        # Max total cost for both legs
        position_size: float = 10.0,         # Position size in USDC
        execution_cooldown: float = 5.0,     # Seconds between executions
        debug: bool = False
    ):
        self.min_profit_rate = min_profit_rate
        self.dip_threshold = dip_threshold
        self.max_total_cost = max_total_cost
        self.position_size = position_size
        self.execution_cooldown = execution_cooldown
        self.debug = debug
        
        # State
        self._current_round: Optional[DipArbRoundState] = None
        self._stats = DipArbStats()
        self._last_execution_time: float = 0
        self._round_counter: int = 0
        
        # Price history for dip detection
        self._price_history: List[Dict] = []
        self._max_history_length = 100
        
        # Market config
        self._market: Optional[DipArbMarketConfig] = None
        
        # Signal handlers
        self._on_signal: Optional[Callable[[DipArbSignal], None]] = None
    
    def on_signal(self, handler: Callable[[DipArbSignal], None]):
        """Register signal handler."""
        self._on_signal = handler
    
    def set_market(self, market: DipArbMarketConfig):
        """Set market configuration."""
        self._market = market
        self._reset_round()
    
    def analyze(
        self,
        up_ask: float,
        down_ask: float,
        up_bid: float = 0,
        down_bid: float = 0,
        timestamp: Optional[float] = None
    ) -> Optional[DipArbSignal]:
        """
        Analyze current prices and generate signal if opportunity exists.
        
        Args:
            up_ask: Best ask price for UP token
            down_ask: Best ask price for DOWN token
            up_bid: Best bid price for UP token (optional)
            down_bid: Best bid price for DOWN token (optional)
            timestamp: Current timestamp (optional)
        
        Returns:
            DipArbSignal if opportunity exists, None otherwise
        """
        ts = timestamp or time.time()
        
        # Update price history
        self._update_price_history(up_ask, down_ask, ts)
        
        # Check execution cooldown
        if (ts - self._last_execution_time) < self.execution_cooldown:
            return None
        
        # Get current round or create new one
        if self._current_round is None:
            self._start_new_round()
        
        # Analyze based on current phase
        if self._current_round.phase == DipArbPhase.WAITING:
            return self._check_leg1_opportunity(up_ask, down_ask, ts)
        
        elif self._current_round.phase == DipArbPhase.LEG1_FILLED:
            return self._check_leg2_opportunity(up_ask, down_ask, ts)
        
        return None
    
    def record_execution(self, signal: DipArbSignal, price: float, shares: float):
        """
        Record successful execution.
        
        Args:
            signal: The signal that was executed
            price: Actual execution price
            shares: Shares filled
        """
        if self._current_round is None:
            return
        
        leg = DipArbLeg(
            side=signal.side,
            price=price,
            shares=shares,
            timestamp=time.time(),
            token_id=signal.token_id
        )
        
        if signal.signal_type == 'leg1':
            self._current_round.leg1 = leg
            self._current_round.phase = DipArbPhase.LEG1_FILLED
            self._stats.leg1_filled += 1
            logger.info(f"Leg1 recorded: {signal.side.value} @ {price:.4f}")
            
        elif signal.signal_type == 'leg2':
            self._current_round.leg2 = leg
            self._current_round.phase = DipArbPhase.COMPLETED
            self._stats.leg2_filled += 1
            
            # Calculate profit
            leg1_price = self._current_round.leg1.price if self._current_round.leg1 else 0
            self._current_round.total_cost = leg1_price + price
            self._current_round.profit = 1.0 - self._current_round.total_cost
            
            self._stats.rounds_successful += 1
            self._stats.total_profit += self._current_round.profit * shares
            self._stats.total_spent += self._current_round.total_cost * shares
            
            logger.info(
                f"Round complete! Cost: {self._current_round.total_cost:.4f}, "
                f"Profit: ${self._current_round.profit * shares:.2f}"
            )
            
            # Start new round
            self._reset_round()
        
        self._last_execution_time = time.time()
    
    def get_stats(self) -> DipArbStats:
        """Get current statistics."""
        self._stats.running_time_ms = (time.time() - self._stats.start_time) * 1000
        return self._stats
    
    def get_current_round(self) -> Optional[DipArbRoundState]:
        """Get current round state."""
        return self._current_round
    
    def get_status(self) -> Dict:
        """Get current strategy status."""
        return {
            'market': self._market.name if self._market else None,
            'round_id': self._current_round.round_id if self._current_round else None,
            'phase': self._current_round.phase.value if self._current_round else 'idle',
            'leg1': {
                'side': self._current_round.leg1.side.value,
                'price': self._current_round.leg1.price
            } if self._current_round and self._current_round.leg1 else None,
            'stats': {
                'rounds_successful': self._stats.rounds_successful,
                'total_profit': self._stats.total_profit,
                'leg1_filled': self._stats.leg1_filled,
                'leg2_filled': self._stats.leg2_filled
            }
        }
    
    # =========================================================================
    # Internal Methods
    # =========================================================================
    
    def _start_new_round(self):
        """Start a new round."""
        self._round_counter += 1
        self._current_round = DipArbRoundState(
            round_id=f"round_{self._round_counter}",
            phase=DipArbPhase.WAITING,
            start_time=time.time()
        )
        self._stats.rounds_monitored += 1
    
    def _reset_round(self):
        """Reset to waiting for new round."""
        self._current_round = None
    
    def _update_price_history(self, up_ask: float, down_ask: float, ts: float):
        """Update price history for dip detection."""
        self._price_history.append({
            'timestamp': ts,
            'up_ask': up_ask,
            'down_ask': down_ask
        })
        
        # Trim history
        if len(self._price_history) > self._max_history_length:
            self._price_history = self._price_history[-self._max_history_length:]
    
    def _detect_dip(self, current_price: float, side: DipArbSide) -> bool:
        """
        Detect if current price represents a significant dip.
        
        Uses sliding window to compare current price with recent average.
        """
        if len(self._price_history) < 5:
            return False
        
        # Get recent prices for this side
        key = 'up_ask' if side == DipArbSide.UP else 'down_ask'
        recent_prices = [p[key] for p in self._price_history[-10:-1]]
        
        if not recent_prices:
            return False
        
        avg_price = sum(recent_prices) / len(recent_prices)
        dip_percent = (avg_price - current_price) / avg_price
        
        return dip_percent >= self.dip_threshold
    
    def _check_leg1_opportunity(
        self,
        up_ask: float,
        down_ask: float,
        ts: float
    ) -> Optional[DipArbSignal]:
        """Check for Leg1 dip opportunity."""
        
        # Check for UP dip
        if self._detect_dip(up_ask, DipArbSide.UP):
            # Calculate potential profit if we can hedge at current DOWN ask
            total_cost = up_ask + down_ask
            if total_cost < self.max_total_cost:
                profit = 1.0 - total_cost
                profit_rate = profit / total_cost
                
                if profit_rate >= self.min_profit_rate:
                    signal = self._create_signal(
                        signal_type='leg1',
                        side=DipArbSide.UP,
                        price=up_ask,
                        expected_profit=profit,
                        reason=f"UP dip detected, potential {profit_rate*100:.1f}% profit"
                    )
                    self._emit_signal(signal)
                    return signal
        
        # Check for DOWN dip
        if self._detect_dip(down_ask, DipArbSide.DOWN):
            total_cost = up_ask + down_ask
            if total_cost < self.max_total_cost:
                profit = 1.0 - total_cost
                profit_rate = profit / total_cost
                
                if profit_rate >= self.min_profit_rate:
                    signal = self._create_signal(
                        signal_type='leg1',
                        side=DipArbSide.DOWN,
                        price=down_ask,
                        expected_profit=profit,
                        reason=f"DOWN dip detected, potential {profit_rate*100:.1f}% profit"
                    )
                    self._emit_signal(signal)
                    return signal
        
        return None
    
    def _check_leg2_opportunity(
        self,
        up_ask: float,
        down_ask: float,
        ts: float
    ) -> Optional[DipArbSignal]:
        """Check for Leg2 hedge opportunity."""
        if not self._current_round or not self._current_round.leg1:
            return None
        
        leg1 = self._current_round.leg1
        
        # Determine hedge side (opposite of leg1)
        if leg1.side == DipArbSide.UP:
            hedge_side = DipArbSide.DOWN
            hedge_price = down_ask
        else:
            hedge_side = DipArbSide.UP
            hedge_price = up_ask
        
        # Calculate total cost and profit
        total_cost = leg1.price + hedge_price
        
        if total_cost < self.max_total_cost:
            profit = 1.0 - total_cost
            profit_rate = profit / total_cost
            
            if profit_rate >= self.min_profit_rate:
                signal = self._create_signal(
                    signal_type='leg2',
                    side=hedge_side,
                    price=hedge_price,
                    expected_profit=profit,
                    reason=f"Hedge available, total cost {total_cost:.4f}, profit {profit_rate*100:.1f}%"
                )
                self._emit_signal(signal)
                return signal
        
        return None
    
    def _create_signal(
        self,
        signal_type: str,
        side: DipArbSide,
        price: float,
        expected_profit: float,
        reason: str
    ) -> DipArbSignal:
        """Create a trading signal."""
        token_id = ""
        if self._market:
            token_id = self._market.up_token_id if side == DipArbSide.UP else self._market.down_token_id
        
        shares = self.position_size / price if price > 0 else 0
        
        return DipArbSignal(
            signal_type=signal_type,
            side=side,
            token_id=token_id,
            target_price=price,
            current_price=price,
            shares=shares,
            reason=reason,
            expected_profit=expected_profit,
            round_id=self._current_round.round_id if self._current_round else "unknown"
        )
    
    def _emit_signal(self, signal: DipArbSignal):
        """Emit signal to handler."""
        if self._on_signal:
            try:
                self._on_signal(signal)
            except Exception as e:
                logger.error(f"Signal handler error: {e}")
        
        logger.info(f"Signal: {signal.signal_type.upper()} {signal.side.value} @ {signal.target_price:.4f}")


# Convenience function
def analyze_dip_arb(
    up_ask: float,
    down_ask: float,
    min_profit_rate: float = 0.02
) -> Dict:
    """
    Quick analysis of DipArb opportunity.
    
    Args:
        up_ask: UP token ask price
        down_ask: DOWN token ask price
        min_profit_rate: Minimum profit rate
    
    Returns:
        Dict with analysis results
    """
    total_cost = up_ask + down_ask
    profit = 1.0 - total_cost
    profit_rate = profit / total_cost if total_cost > 0 else 0
    
    return {
        'up_ask': up_ask,
        'down_ask': down_ask,
        'total_cost': total_cost,
        'profit': profit,
        'profit_rate': profit_rate,
        'profit_pct': f"{profit_rate*100:.2f}%",
        'is_profitable': profit_rate >= min_profit_rate,
        'recommendation': 'BUY BOTH' if profit_rate >= min_profit_rate else 'WAIT'
    }
