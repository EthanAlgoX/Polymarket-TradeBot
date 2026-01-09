"""
Position Manager for Polymarket Arbitrage Bot

Tracks active positions with entry/exit management, P&L calculation,
and portfolio summary. Ported from Polymarket-Copy-Trading-Bot-develop.
"""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Position:
    """Represents an active trading position."""
    market_id: str
    token_id: str
    outcome: str
    entry_price: float
    entry_time: float
    size: float
    side: PositionSide = PositionSide.LONG
    current_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    stop_price: float = 0.0
    closed: bool = False
    exit_price: Optional[float] = None
    exit_time: Optional[float] = None
    
    @property
    def position_key(self) -> str:
        return f"{self.market_id}:{self.token_id}"
    
    @property
    def current_value(self) -> float:
        """Current value of position based on current price."""
        return self.size * self.current_price if self.current_price > 0 else self.size * self.entry_price
    
    @property
    def invested(self) -> float:
        """Total invested amount."""
        return self.size * self.entry_price
    
    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L for open positions."""
        if self.closed:
            return 0.0
        return self.current_value - self.invested
    
    @property
    def realized_pnl(self) -> float:
        """Realized P&L for closed positions."""
        if not self.closed or self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.size
    
    @property
    def pnl_percent(self) -> float:
        """P&L as percentage."""
        if self.invested == 0:
            return 0.0
        pnl = self.realized_pnl if self.closed else self.unrealized_pnl
        return pnl / self.invested
    
    @property
    def hold_time(self) -> float:
        """Time held in seconds."""
        end_time = self.exit_time if self.closed and self.exit_time else time.time()
        return end_time - self.entry_time
    
    def update_price(self, price: float) -> None:
        """Update current price and track highs/lows."""
        self.current_price = price
        if price > self.highest_price:
            self.highest_price = price
        if self.lowest_price == 0 or price < self.lowest_price:
            self.lowest_price = price


@dataclass
class Trade:
    """Represents a trade execution."""
    timestamp: float
    side: str  # 'BUY' or 'SELL'
    price: float
    size: float
    usdc_size: float
    market_id: str
    token_id: str
    

@dataclass
class PortfolioSummary:
    """Summary of portfolio performance."""
    total_value: float = 0.0
    total_invested: float = 0.0
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_positions: int = 0
    closed_positions: int = 0
    win_rate: float = 0.0
    

class PositionManager:
    """
    Manages trading positions with P&L tracking.
    
    Features:
    - Track active and closed positions
    - Calculate real-time P&L
    - Portfolio summary and statistics
    """
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []
        self.trades: List[Trade] = []
    
    def add_position(
        self,
        market_id: str,
        token_id: str,
        outcome: str,
        entry_price: float,
        size: float,
        side: PositionSide = PositionSide.LONG
    ) -> Position:
        """Add a new position or update existing one."""
        key = f"{market_id}:{token_id}"
        
        if key in self.positions:
            # Add to existing position (average in)
            existing = self.positions[key]
            total_size = existing.size + size
            # Weighted average entry price
            avg_price = (existing.entry_price * existing.size + entry_price * size) / total_size
            existing.entry_price = avg_price
            existing.size = total_size
            return existing
        
        # Create new position
        now = time.time()
        position = Position(
            market_id=market_id,
            token_id=token_id,
            outcome=outcome,
            entry_price=entry_price,
            entry_time=now,
            size=size,
            side=side,
            current_price=entry_price,
            highest_price=entry_price,
            lowest_price=entry_price
        )
        self.positions[key] = position
        
        # Record trade
        self.trades.append(Trade(
            timestamp=now,
            side='BUY',
            price=entry_price,
            size=size,
            usdc_size=entry_price * size,
            market_id=market_id,
            token_id=token_id
        ))
        
        return position
    
    def close_position(
        self,
        market_id: str,
        token_id: str,
        exit_price: float,
        size: Optional[float] = None
    ) -> Optional[Position]:
        """Close a position fully or partially."""
        key = f"{market_id}:{token_id}"
        
        if key not in self.positions:
            return None
        
        position = self.positions[key]
        close_size = size if size else position.size
        
        # Record trade
        now = time.time()
        self.trades.append(Trade(
            timestamp=now,
            side='SELL',
            price=exit_price,
            size=close_size,
            usdc_size=exit_price * close_size,
            market_id=market_id,
            token_id=token_id
        ))
        
        if close_size >= position.size:
            # Full close
            position.closed = True
            position.exit_price = exit_price
            position.exit_time = now
            self.closed_positions.append(position)
            del self.positions[key]
        else:
            # Partial close - create closed portion
            closed_portion = Position(
                market_id=market_id,
                token_id=token_id,
                outcome=position.outcome,
                entry_price=position.entry_price,
                entry_time=position.entry_time,
                size=close_size,
                side=position.side,
                closed=True,
                exit_price=exit_price,
                exit_time=now
            )
            self.closed_positions.append(closed_portion)
            position.size -= close_size
        
        return position
    
    def update_position_prices(self, market_id: str, token_id: str, price: float) -> None:
        """Update current price for a position."""
        key = f"{market_id}:{token_id}"
        if key in self.positions:
            self.positions[key].update_price(price)
    
    def get_position(self, market_id: str, token_id: str) -> Optional[Position]:
        """Get a specific position."""
        key = f"{market_id}:{token_id}"
        return self.positions.get(key)
    
    def get_active_positions(self) -> List[Position]:
        """Get all active (open) positions."""
        return list(self.positions.values())
    
    def get_portfolio_summary(self) -> PortfolioSummary:
        """Calculate portfolio summary statistics."""
        summary = PortfolioSummary()
        
        # Open positions stats
        for pos in self.positions.values():
            summary.total_invested += pos.invested
            summary.total_value += pos.current_value
            summary.unrealized_pnl += pos.unrealized_pnl
            summary.open_positions += 1
        
        # Closed positions stats
        winners = 0
        for pos in self.closed_positions:
            summary.realized_pnl += pos.realized_pnl
            summary.closed_positions += 1
            if pos.realized_pnl > 0:
                winners += 1
        
        summary.total_pnl = summary.realized_pnl + summary.unrealized_pnl
        
        if summary.closed_positions > 0:
            summary.win_rate = winners / summary.closed_positions
        
        return summary
    
    def has_position(self, market_id: str, token_id: str) -> bool:
        """Check if position exists."""
        key = f"{market_id}:{token_id}"
        return key in self.positions
    
    def force_close_all(self, get_price_func) -> List[Position]:
        """Emergency close all positions at current prices."""
        closed = []
        for key in list(self.positions.keys()):
            pos = self.positions[key]
            price = get_price_func(pos.token_id) if get_price_func else pos.current_price
            self.close_position(pos.market_id, pos.token_id, price)
            closed.append(pos)
        return closed
    
    def cleanup(self) -> None:
        """Clear all positions and trades."""
        self.positions.clear()
        self.closed_positions.clear()
        self.trades.clear()
