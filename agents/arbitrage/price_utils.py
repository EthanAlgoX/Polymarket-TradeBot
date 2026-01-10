"""
Price Utilities for Polymarket Trading

Provides helpers for:
- Effective price calculation (accounting for mirror orderbook property)
- Arbitrage detection using effective prices
- Price validation and rounding

Based on: poly-sdk-main/src/utils/price-utils.ts
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class EffectivePrices:
    """Effective prices accounting for Polymarket's mirror orderbook property."""
    effective_buy_yes: float   # Cost to buy YES token
    effective_buy_no: float    # Cost to buy NO token
    effective_sell_yes: float  # Revenue from selling YES token
    effective_sell_no: float   # Revenue from selling NO token
    
    @property
    def long_cost(self) -> float:
        """Total cost to buy complete set (YES + NO)"""
        return self.effective_buy_yes + self.effective_buy_no
    
    @property
    def short_revenue(self) -> float:
        """Total revenue from selling complete set (YES + NO)"""
        return self.effective_sell_yes + self.effective_sell_no
    
    @property
    def long_profit(self) -> float:
        """Profit from long arb (buy both, merge for $1)"""
        return 1.0 - self.long_cost
    
    @property
    def short_profit(self) -> float:
        """Profit from short arb (split $1, sell both)"""
        return self.short_revenue - 1.0


@dataclass
class ArbitrageInfo:
    """Information about a detected arbitrage opportunity."""
    type: str  # 'long' or 'short'
    profit: float  # Profit rate (e.g., 0.01 = 1%)
    profit_percent: float  # Profit percentage
    cost_or_revenue: float  # Long cost or short revenue
    description: str  # Human-readable description
    effective_prices: EffectivePrices


def get_effective_prices(
    yes_ask: float,
    yes_bid: float,
    no_ask: float,
    no_bid: float
) -> EffectivePrices:
    """
    Calculate effective prices accounting for Polymarket's mirror orderbook property.
    
    Polymarket key property: Buy YES @ P = Sell NO @ (1-P)
    This means the same order appears in both orderbooks!
    
    Simple addition causes double-counting:
        ask_YES + ask_NO ≈ 1.998 (NOT ~1.0)
    
    Effective prices find the BEST way to acquire each token:
    - Buy YES: Either buy directly at YES.ask OR sell NO (cost = 1 - NO.bid)
    - Buy NO:  Either buy directly at NO.ask OR sell YES (cost = 1 - YES.bid)
    - Sell YES: Either sell directly at YES.bid OR buy NO (revenue = 1 - NO.ask)
    - Sell NO:  Either sell directly at NO.bid OR buy YES (revenue = 1 - YES.ask)
    
    Args:
        yes_ask: Lowest ask price for YES token
        yes_bid: Highest bid price for YES token
        no_ask: Lowest ask price for NO token
        no_bid: Highest bid price for NO token
    
    Returns:
        EffectivePrices with the optimal prices for each action
    """
    return EffectivePrices(
        # Buy YES: min(直接买 YES, 通过卖 NO 获得)
        effective_buy_yes=min(yes_ask, 1.0 - no_bid) if no_bid > 0 else yes_ask,
        
        # Buy NO: min(直接买 NO, 通过卖 YES 获得)
        effective_buy_no=min(no_ask, 1.0 - yes_bid) if yes_bid > 0 else no_ask,
        
        # Sell YES: max(直接卖 YES, 通过买 NO 获得)
        effective_sell_yes=max(yes_bid, 1.0 - no_ask) if no_ask < 1 else yes_bid,
        
        # Sell NO: max(直接卖 NO, 通过买 YES 获得)
        effective_sell_no=max(no_bid, 1.0 - yes_ask) if yes_ask < 1 else no_bid,
    )


def check_arbitrage(
    yes_ask: float,
    yes_bid: float,
    no_ask: float,
    no_bid: float,
    threshold: float = 0.003
) -> Optional[ArbitrageInfo]:
    """
    Check for arbitrage opportunity using effective prices.
    
    Two types of arbitrage:
    - Long Arb: Buy YES + NO for < $1, merge for $1 (profit = 1 - cost)
    - Short Arb: Split $1 into YES + NO, sell for > $1 (profit = revenue - 1)
    
    Args:
        yes_ask: Lowest ask for YES token
        yes_bid: Highest bid for YES token
        no_ask: Lowest ask for NO token
        no_bid: Highest bid for NO token
        threshold: Minimum profit rate to consider (default 0.3%)
    
    Returns:
        ArbitrageInfo if opportunity exists, None otherwise
    """
    # Calculate effective prices
    eff = get_effective_prices(yes_ask, yes_bid, no_ask, no_bid)
    
    # Check Long Arb: buy complete set cheaper than $1
    if eff.long_profit > threshold:
        return ArbitrageInfo(
            type='long',
            profit=eff.long_profit,
            profit_percent=eff.long_profit * 100,
            cost_or_revenue=eff.long_cost,
            description=f"Buy YES @ {eff.effective_buy_yes:.4f} + NO @ {eff.effective_buy_no:.4f}, Merge for $1",
            effective_prices=eff
        )
    
    # Check Short Arb: sell complete set for more than $1
    if eff.short_profit > threshold:
        return ArbitrageInfo(
            type='short',
            profit=eff.short_profit,
            profit_percent=eff.short_profit * 100,
            cost_or_revenue=eff.short_revenue,
            description=f"Split $1, Sell YES @ {eff.effective_sell_yes:.4f} + NO @ {eff.effective_sell_no:.4f}",
            effective_prices=eff
        )
    
    return None


def round_price(price: float, decimals: int = 4) -> float:
    """Round price to specified decimal places, clamped to valid range."""
    rounded = round(price, decimals)
    return max(0.001, min(0.999, rounded))


def round_size(size: float) -> float:
    """Round size to 2 decimal places (Polymarket standard)."""
    return round(size, 2)


def calculate_spread(bid: float, ask: float) -> float:
    """Calculate bid-ask spread."""
    return ask - bid


def calculate_spread_percent(bid: float, ask: float) -> float:
    """Calculate spread as percentage of midpoint."""
    if bid <= 0 or ask <= 0:
        return 0.0
    midpoint = (bid + ask) / 2
    return (ask - bid) / midpoint if midpoint > 0 else 0.0


def calculate_midpoint(bid: float, ask: float) -> float:
    """Calculate midpoint price."""
    return (bid + ask) / 2


def format_price(price: float, decimals: int = 4) -> str:
    """Format price for display."""
    return f"{price:.{decimals}f}"


def format_percent(value: float) -> str:
    """Format percentage for display."""
    return f"{value:.2f}%"


# Convenience functions for quick checks
def has_long_arb(yes_ask: float, yes_bid: float, no_ask: float, no_bid: float, threshold: float = 0.003) -> bool:
    """Quick check if long arbitrage exists."""
    eff = get_effective_prices(yes_ask, yes_bid, no_ask, no_bid)
    return eff.long_profit > threshold


def has_short_arb(yes_ask: float, yes_bid: float, no_ask: float, no_bid: float, threshold: float = 0.003) -> bool:
    """Quick check if short arbitrage exists."""
    eff = get_effective_prices(yes_ask, yes_bid, no_ask, no_bid)
    return eff.short_profit > threshold


def get_arb_summary(yes_ask: float, yes_bid: float, no_ask: float, no_bid: float) -> Dict[str, Any]:
    """Get a summary of arbitrage analysis for the given prices."""
    eff = get_effective_prices(yes_ask, yes_bid, no_ask, no_bid)
    
    return {
        'long_cost': eff.long_cost,
        'long_profit': eff.long_profit,
        'long_profit_pct': eff.long_profit * 100,
        'short_revenue': eff.short_revenue,
        'short_profit': eff.short_profit,
        'short_profit_pct': eff.short_profit * 100,
        'effective_buy_yes': eff.effective_buy_yes,
        'effective_buy_no': eff.effective_buy_no,
        'effective_sell_yes': eff.effective_sell_yes,
        'effective_sell_no': eff.effective_sell_no,
        'has_long_arb': eff.long_profit > 0,
        'has_short_arb': eff.short_profit > 0,
    }
