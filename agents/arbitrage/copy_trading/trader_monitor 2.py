"""
Trader Monitor for Copy Trading

Monitors target traders' activities on Polymarket, fetching trades,
positions, and calculating performance metrics.

Ported from polymarket-copy-trading-bot-main TradeMonitor.
"""

import time
import logging
import httpx
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.arbitrage.config import (
    DATA_API_URL, TARGET_TRADERS, COPY_HISTORY_DAYS, MAX_COPY_TRADES
)

logger = logging.getLogger("TraderMonitor")


@dataclass
class Trade:
    """Represents a trade from the Polymarket API."""
    id: str
    timestamp: int
    market: str
    asset: str
    side: str  # 'BUY' or 'SELL'
    price: float
    usdc_size: float
    size: float
    outcome: str
    transaction_hash: Optional[str] = None
    slug: Optional[str] = None
    event_slug: Optional[str] = None


@dataclass
class Position:
    """Represents a position from the Polymarket API."""
    asset: str
    condition_id: str
    size: float
    avg_price: float
    initial_value: float
    current_value: float
    cash_pnl: float
    percent_pnl: float
    title: Optional[str] = None
    outcome: Optional[str] = None


@dataclass
class TraderStats:
    """Statistics for a tracked trader."""
    address: str
    total_trades: int = 0
    total_volume: float = 0.0
    avg_trade_size: float = 0.0
    last_trade_time: int = 0
    unique_markets: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    roi: float = 0.0


class TraderMonitor:
    """
    Monitors target traders' activities on Polymarket.
    
    Features:
    - Fetch trader activity history
    - Track trader positions
    - Calculate trader performance
    - Estimate trader capital
    """
    
    def __init__(
        self,
        traders: Optional[List[str]] = None,
        history_days: int = COPY_HISTORY_DAYS,
        max_trades: int = MAX_COPY_TRADES
    ):
        self.traders = traders or TARGET_TRADERS
        self.history_days = history_days
        self.max_trades = max_trades
        self.client = httpx.Client(
            base_url=DATA_API_URL,
            timeout=10.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        # Cache for trader data
        self._trade_cache: Dict[str, List[Trade]] = {}
        self._position_cache: Dict[str, List[Position]] = {}
        self._stats_cache: Dict[str, TraderStats] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 60.0  # Cache for 60 seconds
    
    def _is_cache_valid(self, trader: str) -> bool:
        """Check if cache is still valid."""
        if trader not in self._cache_time:
            return False
        return time.time() - self._cache_time[trader] < self._cache_ttl
    
    def fetch_trader_activity(
        self,
        trader_address: str,
        force_refresh: bool = False
    ) -> List[Trade]:
        """
        Fetch trade activity for a trader.
        
        Returns list of trades within the history window.
        """
        if not force_refresh and self._is_cache_valid(trader_address):
            cached = self._trade_cache.get(trader_address)
            if cached:
                return cached
        
        try:
            since_timestamp = int(
                (time.time() - self.history_days * 24 * 60 * 60)
            )
            
            all_trades: List[Trade] = []
            offset = 0
            batch_size = 100
            
            while len(all_trades) < self.max_trades:
                response = self.client.get(
                    f"/activity",
                    params={
                        "user": trader_address,
                        "type": "TRADE",
                        "limit": batch_size,
                        "offset": offset
                    }
                )
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    break
                
                for item in data:
                    # Filter by timestamp
                    if item.get("timestamp", 0) < since_timestamp:
                        continue
                    
                    trade = Trade(
                        id=item.get("id", ""),
                        timestamp=item.get("timestamp", 0),
                        market=item.get("slug") or item.get("market", ""),
                        asset=item.get("asset", ""),
                        side=item.get("side", "BUY"),
                        price=float(item.get("price", 0)),
                        usdc_size=float(item.get("usdcSize", 0)),
                        size=float(item.get("size", 0)),
                        outcome=item.get("outcome", "Unknown"),
                        transaction_hash=item.get("transactionHash"),
                        slug=item.get("slug"),
                        event_slug=item.get("eventSlug")
                    )
                    all_trades.append(trade)
                
                if len(data) < batch_size:
                    break
                
                offset += batch_size
                time.sleep(0.2)  # Rate limiting
            
            # Sort by timestamp
            all_trades.sort(key=lambda t: t.timestamp)
            
            # Update cache
            self._trade_cache[trader_address] = all_trades
            self._cache_time[trader_address] = time.time()
            
            logger.info(f"Fetched {len(all_trades)} trades for {trader_address[:10]}...")
            return all_trades
            
        except Exception as e:
            logger.error(f"Error fetching activity for {trader_address[:10]}...: {e}")
            return self._trade_cache.get(trader_address, [])
    
    def fetch_trader_positions(
        self,
        trader_address: str,
        force_refresh: bool = False
    ) -> List[Position]:
        """
        Fetch current positions for a trader.
        """
        cache_key = f"{trader_address}_positions"
        
        if not force_refresh and self._is_cache_valid(cache_key):
            cached = self._position_cache.get(trader_address)
            if cached:
                return cached
        
        try:
            response = self.client.get(
                f"/positions",
                params={"user": trader_address}
            )
            response.raise_for_status()
            data = response.json()
            
            positions = []
            for item in data:
                positions.append(Position(
                    asset=item.get("asset", ""),
                    condition_id=item.get("conditionId", ""),
                    size=float(item.get("size", 0)),
                    avg_price=float(item.get("avgPrice", 0)),
                    initial_value=float(item.get("initialValue", 0)),
                    current_value=float(item.get("currentValue", 0)),
                    cash_pnl=float(item.get("cashPnl", 0)),
                    percent_pnl=float(item.get("percentPnl", 0)),
                    title=item.get("title"),
                    outcome=item.get("outcome")
                ))
            
            # Update cache
            self._position_cache[trader_address] = positions
            self._cache_time[cache_key] = time.time()
            
            logger.debug(f"Fetched {len(positions)} positions for {trader_address[:10]}...")
            return positions
            
        except Exception as e:
            logger.error(f"Error fetching positions for {trader_address[:10]}...: {e}")
            return self._position_cache.get(trader_address, [])
    
    def get_trader_capital_estimate(
        self,
        trader_address: str,
        trades: Optional[List[Trade]] = None
    ) -> float:
        """
        Estimate trader's capital based on position values and trade history.
        
        This is a rough estimate used for proportional position sizing.
        """
        try:
            positions = self.fetch_trader_positions(trader_address)
            
            # Sum of current position values
            position_value = sum(pos.current_value for pos in positions)
            
            # If we have trade history, use it to estimate max capital deployed
            if trades:
                # Look at max cumulative BUY - SELL to estimate peak capital
                cumulative = 0.0
                max_capital = 0.0
                for trade in trades:
                    if trade.side == "BUY":
                        cumulative += trade.usdc_size
                    else:
                        cumulative -= trade.usdc_size
                    max_capital = max(max_capital, cumulative)
                
                # Use the larger of position value or max deployed
                return max(position_value, max_capital, 50000.0)  # Min $50k assumption
            
            # Default to position value with minimum
            return max(position_value, 50000.0)
            
        except Exception as e:
            logger.error(f"Error estimating capital for {trader_address[:10]}...: {e}")
            return 100000.0  # Default estimate
    
    def calculate_trader_stats(self, trader_address: str) -> TraderStats:
        """Calculate comprehensive stats for a trader."""
        trades = self.fetch_trader_activity(trader_address)
        positions = self.fetch_trader_positions(trader_address)
        
        stats = TraderStats(address=trader_address)
        
        if not trades:
            return stats
        
        stats.total_trades = len(trades)
        stats.total_volume = sum(t.usdc_size for t in trades)
        stats.avg_trade_size = stats.total_volume / len(trades) if trades else 0
        stats.last_trade_time = trades[-1].timestamp if trades else 0
        stats.unique_markets = len(set(t.market for t in trades))
        
        # Calculate P&L from positions
        stats.total_pnl = sum(pos.cash_pnl for pos in positions)
        total_invested = sum(pos.initial_value for pos in positions)
        stats.roi = (stats.total_pnl / total_invested * 100) if total_invested > 0 else 0
        
        # Approximate win rate from position P&L
        winners = sum(1 for pos in positions if pos.percent_pnl > 0)
        stats.win_rate = (winners / len(positions) * 100) if positions else 0
        
        self._stats_cache[trader_address] = stats
        return stats
    
    def get_new_trades(
        self,
        trader_address: str,
        since_timestamp: int
    ) -> List[Trade]:
        """Get trades newer than the given timestamp."""
        trades = self.fetch_trader_activity(trader_address)
        return [t for t in trades if t.timestamp > since_timestamp]
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self.client.close()
        self._trade_cache.clear()
        self._position_cache.clear()
        self._stats_cache.clear()
