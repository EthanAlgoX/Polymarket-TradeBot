"""
Enhanced DipArb Strategy Service for Polymarket

15-Minute Crypto Market Arbitrage Strategy

Strategy Logic:
1. Leg1: Detect 30% dip within 3s sliding window → buy dipping side
2. Leg2: Wait for sum_target (0.95u) → buy other side for guaranteed profit
3. Stop Loss: If leg2 not executed within timeout, sell leg1
4. Auto Merge: After leg2, merge UP+DOWN → USDC.e
5. Market Rotation: Auto switch to next 15-min market after resolution

Configuration:
    sliding_window_ms: 3000   # 3s window for dip detection
    dip_threshold: 0.30       # 30% crash threshold
    sum_target: 0.95          # Total cost target for both legs
    leg2_timeout_seconds: 100 # Sell leg1 if no leg2 within timeout
    auto_merge: True          # Merge after leg2 completion
    split_orders: 1           # Split large orders
"""

import asyncio
import logging
import time
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

logger = logging.getLogger("DipArb")


class DipArbPhase(Enum):
    """DipArb round phases."""
    WAITING = "waiting"           # Waiting for dip opportunity
    LEG1_PENDING = "leg1_pending" # Leg1 signal emitted, awaiting fill
    LEG1_FILLED = "leg1_filled"   # Leg1 executed, waiting for hedge
    LEG2_PENDING = "leg2_pending" # Leg2 signal emitted, awaiting fill
    COMPLETED = "completed"       # Both legs filled
    STOP_LOSS = "stop_loss"       # Leg1 sold due to timeout


class DipArbSide(Enum):
    """Market side."""
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class DipArbConfig:
    """Enhanced DipArb configuration."""
    # Leg1: Sliding window dip detection
    sliding_window_ms: int = 3000     # 3s window
    dip_threshold: float = 0.30       # 30% crash threshold
    
    # Leg2: Sum target
    sum_target: float = 0.95          # Buy both for 0.95u total
    
    # Stop loss
    leg2_timeout_seconds: int = 100   # Sell leg1 if no leg2 within timeout
    enable_stop_loss: bool = True
    
    # Auto merge
    auto_merge: bool = True           # Merge after leg2
    
    # Order splitting
    split_orders: int = 1             # Split into N orders
    order_interval_ms: int = 100      # Delay between split orders
    
    # Position sizing
    position_size: float = 10.0       # USDC per trade
    min_order_size: float = 1.0       # Minimum order size
    
    # Other
    execution_cooldown: float = 1.0   # Seconds between executions
    debug: bool = False


@dataclass
class PricePoint:
    """Single price observation."""
    timestamp: float  # Unix timestamp in seconds
    up_ask: float
    down_ask: float
    up_bid: float = 0.0
    down_bid: float = 0.0


@dataclass
class DipArbSignal:
    """Trading signal for DipArb strategy."""
    signal_type: str  # 'leg1', 'leg2', 'stop_loss'
    side: DipArbSide
    token_id: str
    target_price: float
    current_price: float
    shares: float
    reason: str
    expected_profit: float
    round_id: str
    is_sell: bool = False  # True for stop loss sell
    timestamp: float = field(default_factory=time.time)


@dataclass
class DipArbLeg:
    """Executed leg information."""
    side: DipArbSide
    price: float
    shares: float
    timestamp: float
    token_id: str
    order_ids: List[str] = field(default_factory=list)


@dataclass
class DipArbRoundState:
    """State of a DipArb round."""
    round_id: str
    phase: DipArbPhase
    start_time: float
    leg1: Optional[DipArbLeg] = None
    leg2: Optional[DipArbLeg] = None
    leg1_fill_time: Optional[float] = None
    total_cost: float = 0.0
    profit: float = 0.0
    merged: bool = False
    stop_loss_triggered: bool = False


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
    price_to_beat: Optional[float] = None


@dataclass
class DipArbStats:
    """Statistics for DipArb session."""
    start_time: float = field(default_factory=time.time)
    rounds_monitored: int = 0
    rounds_successful: int = 0
    rounds_stop_loss: int = 0
    leg1_filled: int = 0
    leg2_filled: int = 0
    merges_completed: int = 0
    total_profit: float = 0.0
    total_spent: float = 0.0
    total_stop_loss: float = 0.0
    markets_rotated: int = 0
    running_time_ms: float = 0
    pairs_merged_at_startup: float = 0.0  # NEW: Pairs auto-merged at startup
    emergency_exits: int = 0  # NEW: Emergency exit count


@dataclass
class DipArbPendingRedemption:
    """Pending redemption after market resolution (from poly-sdk-main)."""
    condition_id: str
    up_token_id: str
    down_token_id: str
    shares: float
    market_end_time: float
    created_at: float = field(default_factory=time.time)


class DipArbService:
    """
    Enhanced DipArb Service for 15-minute crypto markets.
    
    Features:
    - Sliding window dip detection (3s, 30%)
    - Stop loss mechanism
    - Auto merge after leg2
    - Market rotation
    - Order splitting
    """
    
    VERSION = "v20260111_1"
    
    def __init__(self, config: Optional[DipArbConfig] = None):
        self.config = config or DipArbConfig()
        
        # State
        self._current_round: Optional[DipArbRoundState] = None
        self._market: Optional[DipArbMarketConfig] = None
        self._stats = DipArbStats()
        self._round_counter: int = 0
        self._last_execution_time: float = 0
        self._is_running: bool = False
        
        # Price history with sliding window (increased buffer from poly-sdk-main)
        self._price_history: deque = deque(maxlen=1000)
        self.MAX_HISTORY_LENGTH = 100  # Keep last 100 price points for analysis
        
        # Callbacks
        self._on_signal: Optional[Callable[[DipArbSignal], None]] = None
        self._on_merge: Optional[Callable[[str, float], None]] = None
        self._on_market_rotate: Optional[Callable[[DipArbMarketConfig], None]] = None
        self._on_redeem: Optional[Callable[[str, float], None]] = None  # NEW: redemption callback
        
        # Background tasks
        self._stop_loss_task: Optional[asyncio.Task] = None
        
        # Pending redemptions for resolved markets (from poly-sdk-main)
        self._pending_redemptions: List = []
        self._redeem_check_interval: Optional[asyncio.Task] = None
        
        # Smart logging state (from poly-sdk-main)
        self._last_orderbook_log_time: float = 0
        self.ORDERBOOK_LOG_INTERVAL_MS: int = 10000  # Log orderbook every 10 seconds
        self._orderbook_buffer: List[Dict] = []
        self.ORDERBOOK_BUFFER_SIZE: int = 50  # Keep 5 seconds of data
        
        logger.info(f"DipArbService {self.VERSION} initialized")
        logger.info(f"Config: window={self.config.sliding_window_ms}ms, dip={self.config.dip_threshold*100:.0f}%, target={self.config.sum_target}")
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def update_config(self, **kwargs):
        """Update configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                logger.info(f"Config updated: {key}={value}")
    
    # =========================================================================
    # Event Handlers
    # =========================================================================
    
    def on_signal(self, handler: Callable[[DipArbSignal], None]):
        """Register signal handler."""
        self._on_signal = handler
    
    def on_merge(self, handler: Callable[[str, float], None]):
        """Register merge completion handler."""
        self._on_merge = handler
    
    def on_market_rotate(self, handler: Callable[[DipArbMarketConfig], None]):
        """Register market rotation handler."""
        self._on_market_rotate = handler
    
    # =========================================================================
    # Market Management
    # =========================================================================
    
    def set_market(self, market: DipArbMarketConfig):
        """Set current market configuration."""
        self._market = market
        self._reset_round()
        self._price_history.clear()
        self._orderbook_buffer.clear()
        logger.info(f"Market set: {market.name} ({market.underlying} {market.duration_minutes}m)")
    
    async def scan_and_merge_existing_pairs(
        self,
        get_balance_fn: Callable[[str, str, str], tuple],
        merge_fn: Callable[[str, str, str, float], bool]
    ) -> Dict:
        """
        Scan and merge existing UP/DOWN pairs at startup.
        
        Ported from poly-sdk-main DipArbService.scanAndMergeExistingPairs().
        When the service starts or rotates to a new market, check if there are
        existing UP + DOWN token pairs from previous sessions and merge them.
        
        Args:
            get_balance_fn: async (condition_id, up_token_id, down_token_id) -> (up_balance, down_balance)
            merge_fn: async (condition_id, up_token_id, down_token_id, amount) -> bool
        
        Returns:
            Dict with merge results
        """
        if not self._market:
            return {'success': False, 'error': 'No market configured'}
        
        try:
            # Get current balances
            up_balance, down_balance = await get_balance_fn(
                self._market.condition_id,
                self._market.up_token_id,
                self._market.down_token_id
            )
            
            # Calculate pairs to merge
            pairs_to_merge = min(up_balance, down_balance)
            
            if pairs_to_merge > 0.01:  # Minimum 0.01 to avoid dust
                logger.info(f"🔍 Found existing pairs: UP={up_balance:.2f}, DOWN={down_balance:.2f}")
                logger.info(f"🔄 Auto-merging {pairs_to_merge:.2f} pairs at startup...")
                
                success = await merge_fn(
                    self._market.condition_id,
                    self._market.up_token_id,
                    self._market.down_token_id,
                    pairs_to_merge
                )
                
                if success:
                    self._stats.pairs_merged_at_startup += pairs_to_merge
                    logger.info(f"✅ Startup merge successful: {pairs_to_merge:.2f} pairs → ${pairs_to_merge:.2f} USDC.e")
                    return {
                        'success': True,
                        'pairs_merged': pairs_to_merge,
                        'usdc_recovered': pairs_to_merge
                    }
                else:
                    logger.error("❌ Startup merge failed")
                    return {'success': False, 'error': 'Merge transaction failed'}
            
            elif up_balance > 0 or down_balance > 0:
                logger.info(f"📊 Existing positions: UP={up_balance:.2f}, DOWN={down_balance:.2f} (no pairs to merge)")
                return {'success': True, 'pairs_merged': 0, 'note': 'No complete pairs'}
            
            return {'success': True, 'pairs_merged': 0}
            
        except Exception as e:
            logger.warning(f"Warning: Failed to scan existing pairs: {e}")
            return {'success': False, 'error': str(e)}
    
    async def rotate_to_next_market(self, next_market: DipArbMarketConfig):
        """
        Rotate to next 15-minute market.
        
        Called when current market ends or resolves.
        """
        # Handle any pending positions
        if self._current_round and self._current_round.phase == DipArbPhase.LEG1_FILLED:
            logger.warning("Market ending with open leg1 position - will be redeemed after resolution")
        
        self._stats.markets_rotated += 1
        self.set_market(next_market)
        
        if self._on_market_rotate:
            self._on_market_rotate(next_market)
        
        logger.info(f"Rotated to market: {next_market.name}")
    
    # =========================================================================
    # Price Analysis
    # =========================================================================
    
    def update_prices(
        self,
        up_ask: float,
        down_ask: float,
        up_bid: float = 0.0,
        down_bid: float = 0.0,
        timestamp: Optional[float] = None
    ) -> Optional[DipArbSignal]:
        """
        Update price data and check for signals.
        
        This is the main entry point - call this with each orderbook update.
        """
        ts = timestamp or time.time()
        
        # Record price point
        price_point = PricePoint(
            timestamp=ts,
            up_ask=up_ask,
            down_ask=down_ask,
            up_bid=up_bid,
            down_bid=down_bid
        )
        self._price_history.append(price_point)
        
        # Check execution cooldown
        if (ts - self._last_execution_time) < self.config.execution_cooldown:
            return None
        
        # Ensure we have a round
        if self._current_round is None:
            self._start_new_round()
        
        # Analyze based on phase
        signal = None
        
        if self._current_round.phase == DipArbPhase.WAITING:
            signal = self._check_leg1_opportunity(up_ask, down_ask, up_bid, down_bid, ts)
        
        elif self._current_round.phase == DipArbPhase.LEG1_FILLED:
            signal = self._check_leg2_opportunity(up_ask, down_ask, ts)
        
        return signal
    
    def get_price_from_history(self, side: str, ms_ago: int) -> Optional[float]:
        """
        Get price from N milliseconds ago for sliding window detection.
        
        Ported from poly-sdk-main DipArbService.getPriceFromHistory().
        
        Args:
            side: 'UP' or 'DOWN'
            ms_ago: Milliseconds ago (e.g., 3000 for 3 seconds)
        
        Returns:
            Price from that time, or None if not available
        """
        if not self._price_history:
            return None
        
        target_ts = time.time() - (ms_ago / 1000.0)
        key = 'up_ask' if side == 'UP' else 'down_ask'
        
        # Find closest price point to target timestamp
        closest_price = None
        closest_delta = float('inf')
        
        for p in self._price_history:
            delta = abs(p.timestamp - target_ts)
            if delta < closest_delta:
                closest_delta = delta
                closest_price = getattr(p, key)
        
        # Only return if within reasonable range (100ms tolerance)
        if closest_delta <= 0.1:
            return closest_price
        
        return None
    
    def _detect_sliding_window_dip(self, side: DipArbSide, current_ts: float) -> Optional[float]:
        """
        Detect dip within sliding window.
        
        Returns dip percentage if detected, None otherwise.
        """
        window_start = current_ts - (self.config.sliding_window_ms / 1000.0)
        
        # Get prices within window
        key = 'up_ask' if side == DipArbSide.UP else 'down_ask'
        
        window_prices = []
        for p in self._price_history:
            if p.timestamp >= window_start:
                price = getattr(p, key)
                if price > 0:
                    window_prices.append((p.timestamp, price))
        
        if len(window_prices) < 2:
            return None
        
        # Find max price in window (before current)
        max_price = 0
        current_price = window_prices[-1][1]
        
        for ts, price in window_prices[:-1]:
            if price > max_price:
                max_price = price
        
        if max_price == 0:
            return None
        
        # Calculate dip percentage
        dip_pct = (max_price - current_price) / max_price
        
        if dip_pct >= self.config.dip_threshold:
            logger.info(
                f"🔥 DIP DETECTED: {side.value} dropped {dip_pct*100:.1f}% "
                f"({max_price:.4f} → {current_price:.4f}) in {self.config.sliding_window_ms}ms"
            )
            return dip_pct
        
        return None
    
    def _update_orderbook_buffer(self, up_ask: float, down_ask: float, up_depth: float = 0, down_depth: float = 0):
        """Update smart logging buffer (from poly-sdk-main)."""
        self._orderbook_buffer.append({
            'timestamp': time.time(),
            'up_ask': up_ask,
            'down_ask': down_ask,
            'up_depth': up_depth,
            'down_depth': down_depth
        })
        if len(self._orderbook_buffer) > self.ORDERBOOK_BUFFER_SIZE:
            self._orderbook_buffer.pop(0)
    
    def _maybe_log_orderbook_summary(self):
        """Log aggregated orderbook stats every 10 seconds (from poly-sdk-main)."""
        now = time.time() * 1000
        if now - self._last_orderbook_log_time < self.ORDERBOOK_LOG_INTERVAL_MS:
            return
        
        if not self._orderbook_buffer:
            return
        
        self._last_orderbook_log_time = now
        
        # Calculate averages from buffer
        up_asks = [b['up_ask'] for b in self._orderbook_buffer if b['up_ask'] > 0]
        down_asks = [b['down_ask'] for b in self._orderbook_buffer if b['down_ask'] > 0]
        
        if up_asks and down_asks:
            avg_up = sum(up_asks) / len(up_asks)
            avg_down = sum(down_asks) / len(down_asks)
            avg_sum = avg_up + avg_down
            
            if self.config.debug:
                logger.debug(
                    f"📊 Orderbook (10s avg): UP={avg_up:.4f}, DOWN={avg_down:.4f}, "
                    f"Sum={avg_sum:.4f}, Gap={1-avg_sum:.4f}"
                )
    
    def _check_leg1_opportunity(
        self,
        up_ask: float,
        down_ask: float,
        up_bid: float,
        down_bid: float,
        ts: float
    ) -> Optional[DipArbSignal]:
        """Check for Leg1 dip opportunity using sliding window."""
        
        # Check UP dip
        up_dip = self._detect_sliding_window_dip(DipArbSide.UP, ts)
        if up_dip is not None:
            total_cost = up_ask + down_ask
            if total_cost <= self.config.sum_target:
                return self._create_leg1_signal(DipArbSide.UP, up_ask, total_cost, up_dip)
            else:
                # Still buy on dip even if sum not yet at target
                # Leg2 will wait for price to come down
                return self._create_leg1_signal(DipArbSide.UP, up_ask, total_cost, up_dip)
        
        # Check DOWN dip
        down_dip = self._detect_sliding_window_dip(DipArbSide.DOWN, ts)
        if down_dip is not None:
            total_cost = up_ask + down_ask
            if total_cost <= self.config.sum_target:
                return self._create_leg1_signal(DipArbSide.DOWN, down_ask, total_cost, down_dip)
            else:
                return self._create_leg1_signal(DipArbSide.DOWN, down_ask, total_cost, down_dip)
        
        return None
    
    def _create_leg1_signal(
        self,
        side: DipArbSide,
        price: float,
        total_cost: float,
        dip_pct: float
    ) -> DipArbSignal:
        """Create Leg1 buy signal."""
        token_id = ""
        if self._market:
            token_id = self._market.up_token_id if side == DipArbSide.UP else self._market.down_token_id
        
        shares = self.config.position_size / price if price > 0 else 0
        expected_profit = (1.0 - self.config.sum_target) * shares
        
        signal = DipArbSignal(
            signal_type='leg1',
            side=side,
            token_id=token_id,
            target_price=price,
            current_price=price,
            shares=shares,
            reason=f"{side.value} dip {dip_pct*100:.1f}% in {self.config.sliding_window_ms}ms",
            expected_profit=expected_profit,
            round_id=self._current_round.round_id if self._current_round else "unknown"
        )
        
        self._emit_signal(signal)
        return signal
    
    def _check_leg2_opportunity(
        self,
        up_ask: float,
        down_ask: float,
        ts: float
    ) -> Optional[DipArbSignal]:
        """Check for Leg2 hedge opportunity based on sum_target."""
        if not self._current_round or not self._current_round.leg1:
            return None
        
        leg1 = self._current_round.leg1
        
        # Determine hedge side
        if leg1.side == DipArbSide.UP:
            hedge_side = DipArbSide.DOWN
            hedge_price = down_ask
        else:
            hedge_side = DipArbSide.UP
            hedge_price = up_ask
        
        # Calculate total cost
        total_cost = leg1.price + hedge_price
        
        # Check if sum_target met
        if total_cost <= self.config.sum_target:
            profit = 1.0 - total_cost
            
            token_id = ""
            if self._market:
                token_id = self._market.up_token_id if hedge_side == DipArbSide.UP else self._market.down_token_id
            
            signal = DipArbSignal(
                signal_type='leg2',
                side=hedge_side,
                token_id=token_id,
                target_price=hedge_price,
                current_price=hedge_price,
                shares=leg1.shares,  # Match leg1 shares
                reason=f"Sum target met: {total_cost:.4f} <= {self.config.sum_target}",
                expected_profit=profit * leg1.shares,
                round_id=self._current_round.round_id
            )
            
            self._emit_signal(signal)
            return signal
        
        return None
    
    # =========================================================================
    # Execution Recording
    # =========================================================================
    
    def record_leg1_fill(self, signal: DipArbSignal, price: float, shares: float, order_ids: List[str] = None):
        """Record Leg1 fill and start stop loss timer."""
        if self._current_round is None:
            return
        
        self._current_round.leg1 = DipArbLeg(
            side=signal.side,
            price=price,
            shares=shares,
            timestamp=time.time(),
            token_id=signal.token_id,
            order_ids=order_ids or []
        )
        self._current_round.phase = DipArbPhase.LEG1_FILLED
        self._current_round.leg1_fill_time = time.time()
        self._stats.leg1_filled += 1
        self._last_execution_time = time.time()
        
        logger.info(f"✅ Leg1 FILLED: {signal.side.value} x{shares:.2f} @ {price:.4f}")
        
        # Start stop loss timer
        if self.config.enable_stop_loss:
            self._start_stop_loss_timer()
    
    def record_leg2_fill(self, signal: DipArbSignal, price: float, shares: float, order_ids: List[str] = None):
        """Record Leg2 fill and trigger auto merge."""
        if self._current_round is None or self._current_round.leg1 is None:
            return
        
        # Cancel stop loss timer
        self._cancel_stop_loss_timer()
        
        self._current_round.leg2 = DipArbLeg(
            side=signal.side,
            price=price,
            shares=shares,
            timestamp=time.time(),
            token_id=signal.token_id,
            order_ids=order_ids or []
        )
        self._current_round.phase = DipArbPhase.COMPLETED
        
        # Calculate profit
        leg1_price = self._current_round.leg1.price
        self._current_round.total_cost = leg1_price + price
        self._current_round.profit = 1.0 - self._current_round.total_cost
        
        self._stats.leg2_filled += 1
        self._stats.rounds_successful += 1
        self._stats.total_profit += self._current_round.profit * shares
        self._stats.total_spent += self._current_round.total_cost * shares
        
        self._last_execution_time = time.time()
        
        logger.info(
            f"✅ Leg2 FILLED: {signal.side.value} x{shares:.2f} @ {price:.4f}\n"
            f"   💰 Round Complete! Cost: {self._current_round.total_cost:.4f}, "
            f"Profit: ${self._current_round.profit * shares:.2f}"
        )
        
        # Trigger auto merge
        if self.config.auto_merge:
            asyncio.create_task(self._execute_merge())
        else:
            self._reset_round()
    
    async def _execute_merge(self):
        """Execute auto merge after leg2."""
        if not self._current_round or not self._market:
            return
        
        shares = self._current_round.leg1.shares if self._current_round.leg1 else 0
        
        logger.info(f"🔄 Auto merging {shares:.2f} pairs → USDC.e...")
        
        # Call merge handler if registered
        if self._on_merge:
            try:
                self._on_merge(self._market.condition_id, shares)
                self._current_round.merged = True
                self._stats.merges_completed += 1
                logger.info(f"✅ Merge completed: {shares:.2f} → ${shares:.2f} USDC.e")
            except Exception as e:
                logger.error(f"Merge failed: {e}")
        
        self._reset_round()
    
    async def emergency_exit_leg1(self, sell_fn: Callable[[str, float], bool] = None) -> Dict:
        """
        Emergency exit Leg1 position.
        
        Ported from poly-sdk-main DipArbService.emergencyExitLeg1().
        Sells the Leg1 tokens at market price to avoid unhedged exposure.
        
        Args:
            sell_fn: async (token_id, shares) -> bool
        
        Returns:
            Dict with exit result
        """
        if not self._current_round or not self._current_round.leg1:
            return {'success': False, 'error': 'No open Leg1 position'}
        
        leg1 = self._current_round.leg1
        
        logger.warning(
            f"🚨 EMERGENCY EXIT: Selling {leg1.shares:.2f} {leg1.side.value} tokens"
        )
        
        # Cancel stop loss timer
        self._cancel_stop_loss_timer()
        
        try:
            if sell_fn:
                success = await sell_fn(leg1.token_id, leg1.shares)
            else:
                # Create signal for external execution
                signal = DipArbSignal(
                    signal_type='emergency_exit',
                    side=leg1.side,
                    token_id=leg1.token_id,
                    target_price=0,  # Market sell
                    current_price=0,
                    shares=leg1.shares,
                    reason="Emergency exit requested",
                    expected_profit=-(leg1.price * leg1.shares),
                    round_id=self._current_round.round_id,
                    is_sell=True
                )
                self._emit_signal(signal)
                success = True
            
            if success:
                self._stats.emergency_exits += 1
                self._current_round.phase = DipArbPhase.STOP_LOSS
                self._reset_round()
                logger.info(f"✅ Emergency exit executed")
                return {'success': True, 'shares_sold': leg1.shares}
            else:
                return {'success': False, 'error': 'Sell failed'}
                
        except Exception as e:
            logger.error(f"Emergency exit error: {e}")
            return {'success': False, 'error': str(e)}
    
    # =========================================================================
    # Stop Loss
    # =========================================================================
    
    def _start_stop_loss_timer(self):
        """Start background timer for stop loss."""
        if self._stop_loss_task:
            self._stop_loss_task.cancel()
        
        self._stop_loss_task = asyncio.create_task(self._stop_loss_countdown())
    
    def _cancel_stop_loss_timer(self):
        """Cancel stop loss timer."""
        if self._stop_loss_task:
            self._stop_loss_task.cancel()
            self._stop_loss_task = None
    
    async def _stop_loss_countdown(self):
        """Background task for stop loss timeout."""
        try:
            await asyncio.sleep(self.config.leg2_timeout_seconds)
            
            # Check if still in leg1_filled state
            if self._current_round and self._current_round.phase == DipArbPhase.LEG1_FILLED:
                await self._trigger_stop_loss()
        except asyncio.CancelledError:
            pass  # Timer was cancelled (leg2 filled)
    
    async def _trigger_stop_loss(self):
        """Trigger stop loss - sell leg1 position."""
        if not self._current_round or not self._current_round.leg1:
            return
        
        leg1 = self._current_round.leg1
        
        logger.warning(
            f"⚠️ STOP LOSS TRIGGERED: {self.config.leg2_timeout_seconds}s timeout\n"
            f"   Selling {leg1.shares:.2f} {leg1.side.value} tokens"
        )
        
        # Create stop loss sell signal
        signal = DipArbSignal(
            signal_type='stop_loss',
            side=leg1.side,
            token_id=leg1.token_id,
            target_price=0,  # Market sell
            current_price=0,
            shares=leg1.shares,
            reason=f"Leg2 timeout after {self.config.leg2_timeout_seconds}s",
            expected_profit=-leg1.price * leg1.shares,  # Expect loss
            round_id=self._current_round.round_id,
            is_sell=True
        )
        
        self._current_round.phase = DipArbPhase.STOP_LOSS
        self._current_round.stop_loss_triggered = True
        self._stats.rounds_stop_loss += 1
        self._stats.total_stop_loss += leg1.price * leg1.shares
        
        self._emit_signal(signal)
        self._reset_round()
    
    # =========================================================================
    # Order Splitting
    # =========================================================================
    
    def calculate_split_orders(self, total_shares: float, price: float) -> List[Dict]:
        """
        Calculate how to split a large order.
        
        Returns list of order specs with shares and size.
        """
        if self.config.split_orders <= 1:
            return [{'shares': total_shares, 'size': total_shares * price}]
        
        shares_per_order = total_shares / self.config.split_orders
        
        orders = []
        for i in range(self.config.split_orders):
            order_shares = shares_per_order
            order_size = order_shares * price
            
            # Ensure minimum order size
            if order_size < self.config.min_order_size:
                order_size = self.config.min_order_size
                order_shares = order_size / price
            
            orders.append({
                'index': i + 1,
                'shares': order_shares,
                'size': order_size,
                'delay_ms': i * self.config.order_interval_ms
            })
        
        return orders
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _start_new_round(self):
        """Start a new trading round."""
        self._round_counter += 1
        self._current_round = DipArbRoundState(
            round_id=f"round_{self._round_counter}",
            phase=DipArbPhase.WAITING,
            start_time=time.time()
        )
        self._stats.rounds_monitored += 1
    
    def _reset_round(self):
        """Reset to waiting for new round."""
        self._cancel_stop_loss_timer()
        self._current_round = None
    
    def _emit_signal(self, signal: DipArbSignal):
        """Emit signal to handler."""
        if self._on_signal:
            try:
                self._on_signal(signal)
            except Exception as e:
                logger.error(f"Signal handler error: {e}")
        
        emoji = "🔴" if signal.is_sell else "🟢"
        action = "SELL" if signal.is_sell else "BUY"
        logger.info(f"{emoji} Signal: {signal.signal_type.upper()} {action} {signal.side.value} @ {signal.target_price:.4f}")
    
    # =========================================================================
    # Status & Stats
    # =========================================================================
    
    def get_stats(self) -> DipArbStats:
        """Get current statistics."""
        self._stats.running_time_ms = (time.time() - self._stats.start_time) * 1000
        return self._stats
    
    def get_status(self) -> Dict:
        """Get current status."""
        return {
            'version': self.VERSION,
            'market': self._market.name if self._market else None,
            'underlying': self._market.underlying if self._market else None,
            'round_id': self._current_round.round_id if self._current_round else None,
            'phase': self._current_round.phase.value if self._current_round else 'idle',
            'leg1': {
                'side': self._current_round.leg1.side.value,
                'price': self._current_round.leg1.price,
                'shares': self._current_round.leg1.shares,
                'age_seconds': time.time() - self._current_round.leg1_fill_time if self._current_round.leg1_fill_time else 0
            } if self._current_round and self._current_round.leg1 else None,
            'config': {
                'sliding_window_ms': self.config.sliding_window_ms,
                'dip_threshold': self.config.dip_threshold,
                'sum_target': self.config.sum_target,
                'leg2_timeout_seconds': self.config.leg2_timeout_seconds,
                'auto_merge': self.config.auto_merge,
                'split_orders': self.config.split_orders
            },
            'stats': {
                'rounds_monitored': self._stats.rounds_monitored,
                'rounds_successful': self._stats.rounds_successful,
                'rounds_stop_loss': self._stats.rounds_stop_loss,
                'total_profit': self._stats.total_profit,
                'merges_completed': self._stats.merges_completed,
                'markets_rotated': self._stats.markets_rotated
            }
        }
    
    def format_status(self) -> str:
        """Format status as readable string."""
        status = self.get_status()
        
        lines = [
            f"📊 DipArb Status ({self.VERSION})",
            f"{'='*40}",
            f"Market: {status['market'] or 'None'}",
            f"Phase: {status['phase']}",
        ]
        
        if status['leg1']:
            leg1 = status['leg1']
            lines.append(f"Leg1: {leg1['side']} x{leg1['shares']:.2f} @ {leg1['price']:.4f} ({leg1['age_seconds']:.0f}s ago)")
        
        lines.extend([
            f"\n📈 Stats:",
            f"  Rounds: {status['stats']['rounds_successful']}/{status['stats']['rounds_monitored']}",
            f"  Profit: ${status['stats']['total_profit']:.2f}",
            f"  Stop Losses: {status['stats']['rounds_stop_loss']}",
            f"  Merges: {status['stats']['merges_completed']}",
        ])
        
        return "\n".join(lines)


# Convenience functions
def create_dip_arb_service(
    sliding_window_ms: int = 3000,
    dip_threshold: float = 0.30,
    sum_target: float = 0.95,
    leg2_timeout_seconds: int = 100,
    **kwargs
) -> DipArbService:
    """Create DipArb service with common parameters."""
    config = DipArbConfig(
        sliding_window_ms=sliding_window_ms,
        dip_threshold=dip_threshold,
        sum_target=sum_target,
        leg2_timeout_seconds=leg2_timeout_seconds,
        **kwargs
    )
    return DipArbService(config)


def analyze_dip_arb(up_ask: float, down_ask: float, sum_target: float = 0.95) -> Dict:
    """Quick analysis of current opportunity."""
    total_cost = up_ask + down_ask
    profit = 1.0 - total_cost
    profit_rate = profit / total_cost if total_cost > 0 else 0
    is_profitable = total_cost <= sum_target
    
    return {
        'up_ask': up_ask,
        'down_ask': down_ask,
        'total_cost': total_cost,
        'sum_target': sum_target,
        'profit': profit,
        'profit_rate': profit_rate,
        'profit_pct': f"{profit_rate*100:.2f}%",
        'is_profitable': is_profitable,
        'recommendation': 'BUY BOTH' if is_profitable else 'WAIT'
    }
