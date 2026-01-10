"""
Smart Money Service for Polymarket

Based on poly-sdk-main/src/services/smart-money-service.ts

Features:
- Smart Money wallet identification from leaderboard
- Trade subscription for specific addresses
- Auto copy trading with configurable options

Usage:
    from agents.arbitrage.smart_money_service import SmartMoneyService
    
    service = SmartMoneyService()
    await service.initialize()
    
    # Get top traders
    traders = await service.get_smart_money_list(50)
    
    # Start auto copy trading
    sub = await service.start_auto_copy_trading({
        'top_n': 50,
        'size_scale': 0.1,
        'max_size_per_trade': 10,
        'dry_run': True
    })
"""

import asyncio
import logging
import time
import re
from typing import Optional, Dict, List, Callable, Any, Set
from dataclasses import dataclass, field
from enum import Enum
import httpx

from agents.arbitrage.realtime_service import RealtimeService, ActivityTrade

logger = logging.getLogger("SmartMoneyService")


# ============================================================================
# Market Categorization
# ============================================================================

class MarketCategory(Enum):
    CRYPTO = "crypto"
    POLITICS = "politics"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    ECONOMICS = "economics"
    SCIENCE = "science"
    OTHER = "other"


CATEGORY_PATTERNS = {
    MarketCategory.CRYPTO: re.compile(r'\b(btc|bitcoin|eth|ethereum|sol|solana|xrp|crypto|doge|ada|matic)\b', re.I),
    MarketCategory.POLITICS: re.compile(r'\b(trump|biden|election|president|senate|congress|vote|political|democrat|republican)\b', re.I),
    MarketCategory.SPORTS: re.compile(r'\b(nfl|nba|mlb|nhl|super bowl|world cup|championship|game|match|ufc|soccer|football|basketball)\b', re.I),
    MarketCategory.ECONOMICS: re.compile(r'\b(fed|interest rate|inflation|gdp|recession|economic|unemployment|cpi)\b', re.I),
    MarketCategory.ENTERTAINMENT: re.compile(r'\b(oscar|grammy|movie|twitter|celebrity|entertainment|netflix|spotify)\b', re.I),
    MarketCategory.SCIENCE: re.compile(r'\b(spacex|nasa|ai|openai|google|apple|tesla|tech|technology|science)\b', re.I),
}


def categorize_market(title: str) -> MarketCategory:
    """Categorize a market based on its title."""
    for category, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(title):
            return category
    return MarketCategory.OTHER


# Category colors for charts (from poly-sdk-main)
CATEGORY_COLORS = {
    MarketCategory.CRYPTO: '#f7931a',      # Bitcoin orange
    MarketCategory.POLITICS: '#3b82f6',    # Blue
    MarketCategory.SPORTS: '#22c55e',      # Green
    MarketCategory.ENTERTAINMENT: '#a855f7', # Purple
    MarketCategory.ECONOMICS: '#eab308',   # Yellow
    MarketCategory.SCIENCE: '#06b6d4',     # Cyan
    MarketCategory.OTHER: '#6b7280',       # Gray
}


# ============================================================================
# Types
# ============================================================================

@dataclass
class SmartMoneyLeaderboardEntry:
    """
    Smart Money Leaderboard entry with extended fields.
    
    Ported from poly-sdk-main SmartMoneyLeaderboardEntry.
    """
    address: str
    rank: int
    pnl: float
    volume: float
    trade_count: int = 0
    user_name: Optional[str] = None
    profile_image: Optional[str] = None
    x_username: Optional[str] = None
    verified_badge: bool = False
    # Extended fields from poly-sdk-main
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    maker_volume: float = 0.0
    taker_volume: float = 0.0
    
    @classmethod
    def from_api(cls, data: dict, rank: int = 0) -> 'SmartMoneyLeaderboardEntry':
        """Create from API response."""
        return cls(
            address=data.get('proxyWallet', data.get('address', '')).lower(),
            rank=rank,
            pnl=float(data.get('pnl', 0)),
            volume=float(data.get('volume', 0)),
            trade_count=int(data.get('tradeCount', 0)),
            user_name=data.get('name', data.get('userName')),
            profile_image=data.get('profileImage'),
            x_username=data.get('xUsername'),
            verified_badge=data.get('verifiedBadge', False),
            # Extended fields
            total_pnl=float(data.get('totalPnl', data.get('pnl', 0))),
            realized_pnl=float(data.get('realizedPnl', 0)),
            unrealized_pnl=float(data.get('unrealizedPnl', 0)),
            buy_count=int(data.get('buyCount', 0)),
            sell_count=int(data.get('sellCount', 0)),
            buy_volume=float(data.get('buyVolume', 0)),
            sell_volume=float(data.get('sellVolume', 0)),
            maker_volume=float(data.get('makerVolume', 0)),
            taker_volume=float(data.get('takerVolume', 0)),
        )

@dataclass
class SmartMoneyWallet:
    """Smart Money wallet information."""
    address: str
    name: Optional[str] = None
    pnl: float = 0.0
    volume: float = 0.0
    score: float = 0.0
    rank: int = 0
    
    @classmethod
    def from_api(cls, data: dict, rank: int = 0) -> 'SmartMoneyWallet':
        """Create from API response."""
        return cls(
            address=data.get('proxyWallet', data.get('address', '')).lower(),
            name=data.get('name', data.get('userName')),
            pnl=float(data.get('pnl', 0)),
            volume=float(data.get('volume', 0)),
            score=min(100, round(float(data.get('pnl', 0)) / 100000 * 50 + float(data.get('volume', 0)) / 1000000 * 50)),
            rank=rank
        )


@dataclass
class SmartMoneyTrade:
    """Smart Money trade event."""
    trader_address: str
    trader_name: Optional[str]
    side: str  # 'BUY' or 'SELL'
    size: float
    price: float
    token_id: str
    outcome: str
    condition_id: str
    market_slug: Optional[str]
    timestamp: float
    is_smart_money: bool = False
    smart_money_info: Optional[SmartMoneyWallet] = None


@dataclass
class AutoCopyTradingOptions:
    """Options for auto copy trading."""
    # Target selection
    target_addresses: Optional[List[str]] = None
    top_n: int = 50
    
    # Order settings
    size_scale: float = 0.1  # 10% of their trade size
    max_size_per_trade: float = 10.0  # Max $10
    max_slippage: float = 0.03  # 3%
    order_type: str = 'FOK'  # 'FOK' or 'FAK'
    delay_ms: int = 0
    
    # Filters
    min_trade_size: float = 1.0
    side_filter: Optional[str] = None  # 'BUY', 'SELL', or None
    category_filter: Optional[List[MarketCategory]] = None
    
    # Mode
    dry_run: bool = True
    
    # Callbacks
    on_trade: Optional[Callable[[SmartMoneyTrade, dict], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None


@dataclass
class AutoCopyTradingStats:
    """Statistics for auto copy trading session."""
    start_time: float = 0.0
    trades_detected: int = 0
    trades_executed: int = 0
    trades_skipped: int = 0
    trades_failed: int = 0
    total_usdc_spent: float = 0.0


@dataclass
class AutoCopyTradingSubscription:
    """Active copy trading subscription."""
    id: str
    target_addresses: List[str]
    start_time: float
    is_active: bool
    stats: AutoCopyTradingStats
    _stop_fn: Callable = None
    
    def stop(self):
        """Stop the subscription."""
        if self._stop_fn:
            self._stop_fn()
    
    def get_stats(self) -> AutoCopyTradingStats:
        """Get current statistics."""
        return self.stats


# ============================================================================
# SmartMoneyService
# ============================================================================

class SmartMoneyService:
    """
    Smart Money monitoring and auto copy trading service.
    
    Core features:
    1. Monitor specific addresses - subscribe_smart_money_trades()
    2. Auto copy trading - start_auto_copy_trading()
    3. Get smart money info - get_smart_money_list(), is_smart_money()
    """
    
    # API endpoints
    DATA_API_URL = "https://data-api.polymarket.com"
    
    def __init__(
        self,
        realtime_service: Optional[RealtimeService] = None,
        min_pnl: float = 1000,
        cache_ttl: int = 300  # 5 minutes
    ):
        self.realtime_service = realtime_service or RealtimeService()
        self.min_pnl = min_pnl
        self.cache_ttl = cache_ttl
        
        # Caches
        self._smart_money_cache: Dict[str, SmartMoneyWallet] = {}
        self._smart_money_set: Set[str] = set()
        self._cache_timestamp: float = 0
        
        # Trade handlers
        self._trade_handlers: List[Callable[[SmartMoneyTrade], None]] = []
        
        # Active subscriptions
        self._active_subscriptions: Dict[str, AutoCopyTradingSubscription] = {}
        self._subscription_counter = 0
        
        # HTTP client
        self._http = httpx.AsyncClient(timeout=30)
    
    async def initialize(self):
        """Initialize the service."""
        # Connect realtime service if not connected
        if not self.realtime_service.is_connected():
            self.realtime_service.connect()
            await asyncio.sleep(2)  # Wait for connection
        
        # Pre-load smart money list
        await self.get_smart_money_list()
        
        logger.info("SmartMoneyService initialized")
    
    # =========================================================================
    # Smart Money Info
    # =========================================================================
    
    async def get_smart_money_list(self, limit: int = 100) -> List[SmartMoneyWallet]:
        """
        Get list of Smart Money wallets from leaderboard.
        
        Args:
            limit: Maximum number of wallets to return
        
        Returns:
            List of SmartMoneyWallet sorted by PnL
        """
        if self._is_cache_valid():
            return list(self._smart_money_cache.values())[:limit]
        
        try:
            # Use correct API endpoint: /v1/leaderboard
            resp = await self._http.get(
                f"{self.DATA_API_URL}/v1/leaderboard",
                params={
                    "timePeriod": "ALL",  # ALL, WEEK, MONTH, DAY
                    "orderBy": "PNL",     # PNL or VOLUME
                    "limit": limit * 2    # Fetch more to filter
                }
            )
            resp.raise_for_status()
            data = resp.json()
            
            smart_money_list = []
            for i, trader in enumerate(data):
                pnl = float(trader.get('pnl', 0))
                if pnl < self.min_pnl:
                    continue
                
                wallet = SmartMoneyWallet.from_api(trader, rank=i + 1)
                smart_money_list.append(wallet)
                self._smart_money_cache[wallet.address] = wallet
                self._smart_money_set.add(wallet.address)
            
            self._cache_timestamp = time.time()
            logger.info(f"Loaded {len(smart_money_list)} smart money wallets")
            
            return smart_money_list[:limit]
            
        except Exception as e:
            logger.error(f"Failed to get smart money list: {e}")
            return []
    
    async def is_smart_money(self, address: str) -> bool:
        """Check if an address is considered Smart Money."""
        normalized = address.lower()
        
        if not self._is_cache_valid():
            await self.get_smart_money_list()
        
        return normalized in self._smart_money_set
    
    async def get_smart_money_info(self, address: str) -> Optional[SmartMoneyWallet]:
        """Get Smart Money info for an address."""
        normalized = address.lower()
        
        if not self._is_cache_valid():
            await self.get_smart_money_list()
        
        return self._smart_money_cache.get(normalized)
    
    # =========================================================================
    # Trade Subscription
    # =========================================================================
    
    def subscribe_smart_money_trades(
        self,
        handler: Callable[[SmartMoneyTrade], None],
        filter_addresses: Optional[List[str]] = None,
        min_size: float = 0
    ):
        """
        Subscribe to trades from specific addresses.
        
        Args:
            handler: Callback for each trade
            filter_addresses: Only notify for these addresses (None = all smart money)
            min_size: Minimum trade size to notify
        
        Returns:
            Subscription with unsubscribe() method
        """
        filter_set = set(addr.lower() for addr in filter_addresses) if filter_addresses else None
        
        def activity_handler(activity: ActivityTrade):
            """Handle activity trade and convert to SmartMoneyTrade."""
            if not activity.trader_address:
                return
            
            trader_addr = activity.trader_address.lower()
            
            # Filter by address if specified
            if filter_set and trader_addr not in filter_set:
                return
            
            # Check if smart money
            is_smart = trader_addr in self._smart_money_set
            
            # Filter by size
            if activity.size < min_size:
                return
            
            trade = SmartMoneyTrade(
                trader_address=trader_addr,
                trader_name=activity.trader_name,
                side=activity.side,
                size=activity.size,
                price=activity.price,
                token_id=activity.asset,
                outcome=activity.outcome,
                condition_id=activity.condition_id,
                market_slug=None,
                timestamp=activity.timestamp,
                is_smart_money=is_smart,
                smart_money_info=self._smart_money_cache.get(trader_addr)
            )
            
            handler(trade)
        
        # Subscribe to activity stream
        self.realtime_service.subscribe_activity({'on_activity': activity_handler})
        self._trade_handlers.append(handler)
        
        class Subscription:
            def __init__(self, service, handler):
                self._service = service
                self._handler = handler
            
            def unsubscribe(self):
                if self._handler in self._service._trade_handlers:
                    self._service._trade_handlers.remove(self._handler)
        
        return Subscription(self, handler)
    
    # =========================================================================
    # Auto Copy Trading
    # =========================================================================
    
    async def start_auto_copy_trading(
        self,
        options: AutoCopyTradingOptions = None,
        **kwargs
    ) -> AutoCopyTradingSubscription:
        """
        Start auto copy trading - when smart money trades, copy immediately.
        
        Args:
            options: Copy trading options (or pass as kwargs)
        
        Returns:
            Subscription with stop() and get_stats() methods
        
        Example:
            sub = await service.start_auto_copy_trading(
                top_n=50,
                size_scale=0.1,
                max_size_per_trade=10,
                dry_run=True
            )
        """
        if options is None:
            options = AutoCopyTradingOptions(**kwargs)
        
        # Get target addresses
        if options.target_addresses:
            target_addresses = [addr.lower() for addr in options.target_addresses]
        else:
            # Get from leaderboard
            smart_money = await self.get_smart_money_list(options.top_n)
            target_addresses = [w.address for w in smart_money]
        
        if not target_addresses:
            raise ValueError("No target addresses found")
        
        # Create subscription
        self._subscription_counter += 1
        sub_id = f"copy_trading_{self._subscription_counter}"
        
        stats = AutoCopyTradingStats(start_time=time.time())
        is_active = [True]  # Use list for mutable reference
        
        def handle_trade(trade: SmartMoneyTrade):
            """Handle a detected trade and potentially copy it."""
            if not is_active[0]:
                return
            
            stats.trades_detected += 1
            
            # Apply filters
            if options.side_filter and trade.side != options.side_filter:
                stats.trades_skipped += 1
                return
            
            trade_value = trade.size * trade.price
            if trade_value < options.min_trade_size:
                stats.trades_skipped += 1
                return
            
            # Calculate copy size
            copy_size = trade.size * options.size_scale
            copy_value = copy_size * trade.price
            
            if copy_value > options.max_size_per_trade:
                copy_size = options.max_size_per_trade / trade.price
                copy_value = options.max_size_per_trade
            
            # Minimum order size check ($1)
            if copy_value < 1.0:
                stats.trades_skipped += 1
                logger.debug(f"Skipping trade: copy value ${copy_value:.2f} below minimum $1")
                return
            
            # Execute or simulate
            result = {
                'success': True,
                'dry_run': options.dry_run,
                'copy_size': copy_size,
                'copy_value': copy_value,
                'original_size': trade.size,
                'original_value': trade_value
            }
            
            if options.dry_run:
                logger.info(
                    f"[DRY RUN] Would copy {trade.trader_name or trade.trader_address[:10]}... "
                    f"{trade.side} {copy_size:.2f} @ {trade.price:.3f} (${copy_value:.2f})"
                )
                stats.trades_executed += 1
                stats.total_usdc_spent += copy_value
            else:
                # TODO: Execute real trade via TradingService
                logger.warning("Real trading not yet implemented - use dry_run=True")
                stats.trades_skipped += 1
                result['success'] = False
            
            if options.on_trade:
                try:
                    options.on_trade(trade, result)
                except Exception as e:
                    logger.error(f"on_trade callback error: {e}")
        
        def stop():
            """Stop copy trading."""
            is_active[0] = False
            if sub_id in self._active_subscriptions:
                del self._active_subscriptions[sub_id]
            logger.info(f"Copy trading stopped. Stats: detected={stats.trades_detected}, executed={stats.trades_executed}")
        
        subscription = AutoCopyTradingSubscription(
            id=sub_id,
            target_addresses=target_addresses,
            start_time=time.time(),
            is_active=True,
            stats=stats,
            _stop_fn=stop
        )
        
        self._active_subscriptions[sub_id] = subscription
        
        # Subscribe to trades from target addresses
        self.subscribe_smart_money_trades(
            handler=handle_trade,
            filter_addresses=target_addresses,
            min_size=options.min_trade_size
        )
        
        logger.info(f"Started copy trading: tracking {len(target_addresses)} wallets")
        
        return subscription
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        return (time.time() - self._cache_timestamp) < self.cache_ttl
    
    async def close(self):
        """Clean up resources."""
        # Stop all subscriptions
        for sub in list(self._active_subscriptions.values()):
            sub.stop()
        
        await self._http.aclose()


# Convenience function
async def create_smart_money_service() -> SmartMoneyService:
    """Create and initialize a SmartMoneyService."""
    service = SmartMoneyService()
    await service.initialize()
    return service
