from pydantic import BaseModel
from typing import List, Dict, Optional

class OrderSummary(BaseModel):
    price: float
    size: float

class OrderbookSnapshot(BaseModel):
    market_id: str
    asset_id: str
    bids: List[OrderSummary]
    asks: List[OrderSummary]
    timestamp: float
    spread: float = 0.0
    spread_percent: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth: float = 0.0  # Sum of size of top 5 bids
    ask_depth: float = 0.0  # Sum of size of top 5 asks

class MarketSnapshot(BaseModel):
    id: str
    question: str
    outcomes: List[str]
    outcome_prices: List[float]
    clob_token_ids: List[str]

class ArbitrageOpportunity(BaseModel):
    market_id: str
    timestamp: float
    outcomes: List[str]
    prices: List[float]
    total_cost: float
    potential_profit: float
    max_volume: float

class SpreadOpportunity(BaseModel):
    """Single-side spread opportunity when buying YES or NO is profitable."""
    market_id: str
    timestamp: float
    token_id: str
    side: str  # 'YES' or 'NO'
    entry_price: float
    expected_profit: float  # Expected profit percentage
    confidence: float
    max_volume: float
    opposite_bid: float  # The bid price of the opposite side
