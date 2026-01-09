"""
Trader Discovery System for Polymarket

Discovers and ranks profitable traders for copy trading.
Ported from polymarket-copy-trading-bot-main findBestTraders.ts.

Features:
- Fetch trader leaderboard from Polymarket API
- Simulate historical trades for profitability analysis
- Calculate ROI, win rate, and other metrics
- Rank traders for copy trading selection
"""

import time
import logging
import asyncio
import aiohttp
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.arbitrage.config import (
    COPY_HISTORY_DAYS, MIN_ORDER_SIZE, TRADE_MULTIPLIER
)

logger = logging.getLogger("TraderDiscovery")

# Configuration
DATA_API_URL = "https://data-api.polymarket.com"
DEFAULT_STARTING_CAPITAL = 1000.0
MAX_TRADES_LIMIT = 2000
MIN_TRADER_TRADES = 100


@dataclass
class Trade:
    """Represents a single trade from Polymarket."""
    id: str
    timestamp: float
    market: str
    asset: str
    side: str  # 'BUY' or 'SELL'
    price: float
    usdc_size: float
    size: float
    outcome: str


@dataclass
class SimulatedPosition:
    """Tracks a simulated position during backtesting."""
    market: str
    outcome: str
    entry_price: float
    invested: float
    current_value: float
    pnl: float
    closed: bool
    trades: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TraderResult:
    """Result of analyzing a trader's performance."""
    address: str
    name: Optional[str] = None
    starting_capital: float = 0.0
    current_capital: float = 0.0
    total_trades: int = 0
    copied_trades: int = 0
    skipped_trades: int = 0
    total_pnl: float = 0.0
    roi: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    win_rate: float = 0.0
    avg_trade_size: float = 0.0
    open_positions: int = 0
    closed_positions: int = 0
    simulation_time_ms: float = 0.0
    error: Optional[str] = None
    
    @property
    def is_profitable(self) -> bool:
        return self.roi > 0


class TraderDiscovery:
    """
    Discovers and ranks profitable traders from Polymarket.
    
    Usage:
        discovery = TraderDiscovery()
        best_traders = await discovery.find_best_traders(count=10)
        for trader in best_traders:
            print(f"{trader.address}: ROI={trader.roi:.2f}%")
    """
    
    def __init__(
        self,
        starting_capital: float = DEFAULT_STARTING_CAPITAL,
        history_days: int = COPY_HISTORY_DAYS,
        multiplier: float = TRADE_MULTIPLIER,
        min_order_size: float = MIN_ORDER_SIZE,
        min_trader_trades: int = MIN_TRADER_TRADES
    ):
        self.starting_capital = starting_capital
        self.history_days = history_days
        self.multiplier = multiplier
        self.min_order_size = min_order_size
        self.min_trader_trades = min_trader_trades
        
        # Known successful traders (fallback)
        self.known_traders = [
            "0x7c3db723f1d4d8cb9c550095203b686cb11e5c6b",
            "0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292",
            "0xa4b366ad22fc0d06f1e934ff468e8922431a87b8",
        ]
    
    async def fetch_trader_leaderboard(self, session: aiohttp.ClientSession) -> List[str]:
        """
        Fetch trader addresses from Polymarket leaderboard.
        
        Attempts to extract active traders from recent market activity.
        Falls back to known traders list if API fails.
        """
        try:
            logger.info("Fetching trader leaderboard from Polymarket...")
            
            async with session.get(
                f"{DATA_API_URL}/markets",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status != 200:
                    raise Exception(f"API returned status {response.status}")
                
                markets = await response.json()
            
            traders = set()
            
            # Extract traders from recent market activity
            for market in markets[:5]:
                try:
                    condition_id = market.get("conditionId", "")
                    if not condition_id:
                        continue
                    
                    async with session.get(
                        f"{DATA_API_URL}/trades?market={condition_id}&limit=100",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as trades_response:
                        if trades_response.status == 200:
                            trades = await trades_response.json()
                            for trade in trades:
                                owner = trade.get("owner", "")
                                if owner:
                                    traders.add(owner.lower())
                except Exception:
                    continue
            
            trader_list = list(traders)[:20]  # Top 20 most active
            logger.info(f"Found {len(trader_list)} traders from recent activity")
            return trader_list
            
        except Exception as e:
            logger.warning(f"Could not fetch leaderboard: {e}, using known traders")
            return self.known_traders
    
    async def fetch_trader_activity(
        self,
        session: aiohttp.ClientSession,
        address: str
    ) -> List[Trade]:
        """
        Fetch trading activity for a specific trader.
        
        Returns list of trades sorted by timestamp (oldest first).
        """
        try:
            since_timestamp = time.time() - (self.history_days * 24 * 60 * 60)
            all_trades: List[Trade] = []
            offset = 0
            batch_size = 100
            
            while len(all_trades) < MAX_TRADES_LIMIT:
                async with session.get(
                    f"{DATA_API_URL}/activity",
                    params={
                        "user": address,
                        "type": "TRADE",
                        "limit": batch_size,
                        "offset": offset
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        break
                    
                    data = await response.json()
                    if not data:
                        break
                    
                    for item in data:
                        ts = item.get("timestamp", 0)
                        if ts < since_timestamp:
                            continue
                        
                        trade = Trade(
                            id=item.get("id", ""),
                            timestamp=ts,
                            market=item.get("slug", item.get("market", "")),
                            asset=item.get("asset", ""),
                            side=item.get("side", ""),
                            price=float(item.get("price", 0)),
                            usdc_size=float(item.get("usdcSize", 0)),
                            size=float(item.get("size", 0)),
                            outcome=item.get("outcome", "Unknown")
                        )
                        all_trades.append(trade)
                    
                    if len(data) < batch_size:
                        break
                    
                    offset += batch_size
            
            # Sort by timestamp (oldest first for simulation)
            all_trades.sort(key=lambda t: t.timestamp)
            return all_trades
            
        except Exception as e:
            logger.error(f"Error fetching activity for {address[:10]}...: {e}")
            return []
    
    async def simulate_trader(
        self,
        session: aiohttp.ClientSession,
        address: str
    ) -> TraderResult:
        """
        Simulate copy trading a specific trader.
        
        Returns profitability metrics including ROI and win rate.
        """
        start_time = time.time()
        
        try:
            # Fetch trades
            trades = await self.fetch_trader_activity(session, address)
            
            if len(trades) < self.min_trader_trades:
                return TraderResult(
                    address=address,
                    starting_capital=self.starting_capital,
                    current_capital=self.starting_capital,
                    total_trades=len(trades),
                    error=f"Not enough trades ({len(trades)} < {self.min_trader_trades})"
                )
            
            # Run simulation
            your_capital = self.starting_capital
            total_invested = 0.0
            copied_trades = 0
            skipped_trades = 0
            
            positions: Dict[str, SimulatedPosition] = {}
            
            for trade in trades:
                # Estimate trader's capital at time of trade
                trader_capital = 100000  # Assume large capital
                trader_percent = trade.usdc_size / trader_capital
                base_order_size = your_capital * trader_percent
                order_size = base_order_size * self.multiplier
                
                if order_size < self.min_order_size:
                    skipped_trades += 1
                    continue
                
                # Cap at 95% of available capital
                if order_size > your_capital * 0.95:
                    order_size = your_capital * 0.95
                    if order_size < self.min_order_size:
                        skipped_trades += 1
                        continue
                
                position_key = f"{trade.asset}:{trade.outcome}"
                
                if trade.side == "BUY":
                    shares_received = order_size / trade.price if trade.price > 0 else 0
                    
                    if position_key not in positions:
                        positions[position_key] = SimulatedPosition(
                            market=trade.market,
                            outcome=trade.outcome,
                            entry_price=trade.price,
                            invested=0.0,
                            current_value=0.0,
                            pnl=0.0,
                            closed=False
                        )
                    
                    pos = positions[position_key]
                    pos.trades.append({
                        "timestamp": trade.timestamp,
                        "side": "BUY",
                        "price": trade.price,
                        "size": shares_received,
                        "usdc_size": order_size
                    })
                    pos.invested += order_size
                    pos.current_value += order_size
                    your_capital -= order_size
                    total_invested += order_size
                    copied_trades += 1
                    
                elif trade.side == "SELL":
                    if position_key in positions:
                        pos = positions[position_key]
                        sell_amount = min(order_size, pos.current_value)
                        
                        pos.trades.append({
                            "timestamp": trade.timestamp,
                            "side": "SELL",
                            "price": trade.price,
                            "size": sell_amount / trade.price if trade.price > 0 else 0,
                            "usdc_size": sell_amount
                        })
                        
                        pos.current_value -= sell_amount
                        your_capital += sell_amount
                        
                        if pos.current_value < 0.01:
                            pos.closed = True
                        
                        copied_trades += 1
                    else:
                        skipped_trades += 1
            
            # Calculate final results
            open_value = sum(p.current_value for p in positions.values() if not p.closed)
            current_capital = your_capital + open_value
            
            total_pnl = current_capital - self.starting_capital
            roi = (total_pnl / self.starting_capital) * 100 if self.starting_capital > 0 else 0
            
            # Calculate PnL for each position
            realized_pnl = 0.0
            unrealized_pnl = 0.0
            
            for pos in positions.values():
                total_bought = sum(t["usdc_size"] for t in pos.trades if t["side"] == "BUY")
                total_sold = sum(t["usdc_size"] for t in pos.trades if t["side"] == "SELL")
                
                if pos.closed:
                    pos.pnl = total_sold - total_bought
                    realized_pnl += pos.pnl
                else:
                    pos.pnl = pos.current_value - total_bought + total_sold
                    unrealized_pnl += pos.pnl
            
            # Calculate win rate
            closed_positions = [p for p in positions.values() if p.closed]
            winning_positions = [p for p in closed_positions if p.pnl > 0]
            win_rate = len(winning_positions) / len(closed_positions) * 100 if closed_positions else 0
            
            avg_trade_size = total_invested / copied_trades if copied_trades > 0 else 0
            
            return TraderResult(
                address=address,
                starting_capital=self.starting_capital,
                current_capital=current_capital,
                total_trades=len(trades),
                copied_trades=copied_trades,
                skipped_trades=skipped_trades,
                total_pnl=total_pnl,
                roi=roi,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                win_rate=win_rate,
                avg_trade_size=avg_trade_size,
                open_positions=sum(1 for p in positions.values() if not p.closed),
                closed_positions=len(closed_positions),
                simulation_time_ms=(time.time() - start_time) * 1000
            )
            
        except Exception as e:
            logger.error(f"Error simulating trader {address[:10]}...: {e}")
            return TraderResult(
                address=address,
                starting_capital=self.starting_capital,
                current_capital=self.starting_capital,
                error=str(e),
                simulation_time_ms=(time.time() - start_time) * 1000
            )
    
    async def find_best_traders(self, count: int = 10) -> List[TraderResult]:
        """
        Find and rank the best traders by ROI.
        
        Args:
            count: Number of top traders to return
            
        Returns:
            List of TraderResult sorted by ROI (descending)
        """
        logger.info(f"Finding best traders (history: {self.history_days} days)...")
        
        async with aiohttp.ClientSession() as session:
            # Fetch trader list
            traders = await self.fetch_trader_leaderboard(session)
            
            if not traders:
                logger.warning("No traders found")
                return []
            
            # Simulate all traders concurrently
            tasks = [self.simulate_trader(session, addr) for addr in traders]
            results = await asyncio.gather(*tasks)
            
            # Filter and sort by ROI
            valid_results = [r for r in results if not r.error and r.copied_trades > 0]
            sorted_results = sorted(valid_results, key=lambda r: r.roi, reverse=True)
            
            # Log summary
            profitable = [r for r in sorted_results if r.is_profitable]
            logger.info(
                f"Analyzed {len(traders)} traders: "
                f"{len(valid_results)} valid, {len(profitable)} profitable"
            )
            
            return sorted_results[:count]
    
    async def find_traders_by_win_rate(self, count: int = 10, min_closed: int = 5) -> List[TraderResult]:
        """
        Find traders sorted by win rate.
        
        Args:
            count: Number of top traders to return
            min_closed: Minimum closed positions required
            
        Returns:
            List of TraderResult sorted by win rate (descending)
        """
        all_traders = await self.find_best_traders(count=100)
        
        # Filter by minimum closed positions
        filtered = [r for r in all_traders if r.closed_positions >= min_closed]
        
        # Sort by win rate
        sorted_results = sorted(filtered, key=lambda r: r.win_rate, reverse=True)
        
        return sorted_results[:count]


# Utility function for direct usage
async def discover_best_traders(count: int = 10, history_days: int = 30) -> List[TraderResult]:
    """
    Discover the best Polymarket traders.
    
    Example:
        traders = await discover_best_traders(count=5)
        for t in traders:
            print(f"{t.address}: ROI={t.roi:.2f}%, Win Rate={t.win_rate:.1f}%")
    """
    discovery = TraderDiscovery(history_days=history_days)
    return await discovery.find_best_traders(count=count)
