"""
Market Scanner for Polymarket Arbitrage Bot

Discovers and filters tradable markets based on configurable criteria
including volume, liquidity, and time to resolution.

Ported from Polymarket-Copy-Trading-Bot-develop MarketScanner.
"""

import time
import logging
import httpx
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field

from agents.arbitrage.config import (
    GAMMA_API_URL, DATA_API_URL,
    MIN_MARKET_VOLUME, MIN_MARKET_LIQUIDITY,
    MAX_TIME_TO_RESOLUTION, MARKET_SCAN_INTERVAL
)

logger = logging.getLogger("MarketScanner")


@dataclass
class MarketToken:
    """Token within a market."""
    token_id: str
    outcome: str
    price: float = 0.0


@dataclass
class ScannedMarket:
    """A scanned market with its properties."""
    id: str
    condition_id: str
    question: str
    slug: Optional[str] = None
    tokens: List[MarketToken] = field(default_factory=list)
    volume_24h: float = 0.0
    liquidity: float = 0.0
    end_date: Optional[str] = None
    time_to_resolution: float = 0.0  # Hours
    active: bool = True
    meets_criteria: bool = False


class MarketScanner:
    """
    Scans and filters Polymarket markets.
    
    Features:
    - Fetch active markets from Polymarket APIs
    - Filter by volume, liquidity, time to resolution
    - Track market states and changes
    - Periodic scanning
    """
    
    def __init__(
        self,
        min_volume: float = MIN_MARKET_VOLUME,
        min_liquidity: float = MIN_MARKET_LIQUIDITY,
        max_time_to_resolution: float = MAX_TIME_TO_RESOLUTION,
        scan_interval: float = MARKET_SCAN_INTERVAL
    ):
        self.min_volume = min_volume
        self.min_liquidity = min_liquidity
        self.max_time_to_resolution = max_time_to_resolution
        self.scan_interval = scan_interval
        
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        # Market cache
        self._markets: Dict[str, ScannedMarket] = {}
        self._tradable_markets: List[ScannedMarket] = []
        self._last_scan_time: float = 0
        
        # Scanning state
        self._scanning = False
    
    def fetch_markets(self, limit: int = 100) -> List[ScannedMarket]:
        """
        Fetch markets from Polymarket API.
        
        Uses Gamma API for market data.
        """
        markets: List[ScannedMarket] = []
        
        try:
            # Try Gamma API first
            response = self.client.get(
                f"{GAMMA_API_URL}/markets",
                params={"limit": limit, "active": "true"}
            )
            response.raise_for_status()
            data = response.json()
            
            for item in data:
                tokens = []
                # Parse tokens/outcomes
                if "tokens" in item:
                    for token in item["tokens"]:
                        tokens.append(MarketToken(
                            token_id=token.get("token_id", ""),
                            outcome=token.get("outcome", ""),
                            price=float(token.get("price", 0))
                        ))
                elif "outcomes" in item:
                    # Alternative format from Gamma API
                    # All fields may be JSON strings that need parsing
                    import json as json_parser
                    
                    # Parse outcomes (may be JSON string like '["Yes", "No"]')
                    outcomes_raw = item.get("outcomes", "[]")
                    if isinstance(outcomes_raw, str):
                        try:
                            outcomes = json_parser.loads(outcomes_raw) if outcomes_raw else []
                        except:
                            outcomes = []
                    else:
                        outcomes = outcomes_raw if outcomes_raw else []
                    
                    # Parse clobTokenIds (JSON string like '["token1", "token2"]')
                    clob_ids_raw = item.get("clobTokenIds", item.get("clob_token_ids", "[]"))
                    if isinstance(clob_ids_raw, str):
                        try:
                            clob_ids = json_parser.loads(clob_ids_raw) if clob_ids_raw else []
                        except:
                            clob_ids = []
                    else:
                        clob_ids = clob_ids_raw if clob_ids_raw else []
                    
                    # Parse outcome prices (JSON string or list)
                    outcome_prices_raw = item.get("outcomePrices", item.get("outcome_prices", "[]"))
                    if isinstance(outcome_prices_raw, str):
                        try:
                            outcome_prices = json_parser.loads(outcome_prices_raw) if outcome_prices_raw else []
                            outcome_prices = [float(p) for p in outcome_prices]
                        except:
                            outcome_prices = []
                    else:
                        outcome_prices = [float(p) for p in outcome_prices_raw] if outcome_prices_raw else []
                    
                    for i, outcome in enumerate(outcomes):
                        token_id = clob_ids[i] if i < len(clob_ids) else ""
                        price = outcome_prices[i] if i < len(outcome_prices) else 0
                        tokens.append(MarketToken(
                            token_id=token_id,
                            outcome=outcome,
                            price=price
                        ))
                
                # Calculate time to resolution
                end_date = item.get("end_date_iso") or item.get("end_date")
                time_to_resolution = self._calculate_time_to_resolution(end_date)
                
                market = ScannedMarket(
                    id=item.get("condition_id", item.get("id", "")),
                    condition_id=item.get("condition_id", ""),
                    question=item.get("question", ""),
                    slug=item.get("slug"),
                    tokens=tokens,
                    volume_24h=float(item.get("volume_24h", item.get("volume", 0)) or 0),
                    liquidity=float(item.get("liquidity", 0) or 0),
                    end_date=end_date,
                    time_to_resolution=time_to_resolution,
                    active=item.get("active", True)
                )
                
                # Check if meets criteria
                market.meets_criteria = self._check_criteria(market)
                markets.append(market)
            
            logger.info(f"Fetched {len(markets)} markets from Gamma API")
            
        except Exception as e:
            logger.error(f"Error fetching markets from Gamma API: {e}")
            
            # Fallback to Data API
            try:
                response = self.client.get(
                    f"{DATA_API_URL}/markets",
                    params={"limit": limit}
                )
                response.raise_for_status()
                data = response.json()
                
                for item in data:
                    market = ScannedMarket(
                        id=item.get("conditionId", item.get("id", "")),
                        condition_id=item.get("conditionId", ""),
                        question=item.get("question", ""),
                        slug=item.get("slug"),
                        tokens=[],
                        volume_24h=float(item.get("volume", 0) or 0),
                        liquidity=float(item.get("liquidity", 0) or 0),
                        active=True
                    )
                    market.meets_criteria = self._check_criteria(market)
                    markets.append(market)
                
                logger.info(f"Fetched {len(markets)} markets from Data API (fallback)")
                
            except Exception as e2:
                logger.error(f"Error fetching markets from Data API: {e2}")
        
        return markets
    
    def _calculate_time_to_resolution(self, end_date: Optional[str]) -> float:
        """Calculate hours until market resolution."""
        if not end_date:
            return 999999.0  # Very large number for unknown
        
        try:
            from datetime import datetime
            import dateutil.parser
            
            end_dt = dateutil.parser.parse(end_date)
            now = datetime.now(end_dt.tzinfo)
            
            delta = end_dt - now
            hours = delta.total_seconds() / 3600
            return max(0, hours)
            
        except Exception:
            return 999999.0
    
    def _check_criteria(self, market: ScannedMarket) -> bool:
        """Check if market meets all criteria."""
        # Must have tokens with valid IDs
        valid_tokens = [t for t in market.tokens if t.token_id and len(t.token_id) > 10]
        if not valid_tokens:
            return False
        
        # Check volume (use total volume if 24h not available)
        if market.volume_24h < self.min_volume:
            return False
        
        # Check liquidity (skip if 0, some markets don't report this)
        # if market.liquidity < self.min_liquidity:
        #     return False
        
        # Check time to resolution (skip if unknown)
        if 0 < market.time_to_resolution < 1:  # Less than 1 hour - too close
            return False
        
        if market.time_to_resolution > self.max_time_to_resolution and market.time_to_resolution < 999999:
            return False
        
        # Must be active
        if not market.active:
            return False
        
        return True
    
    def scan(self, force: bool = False) -> List[ScannedMarket]:
        """
        Scan markets and update cache.
        
        Args:
            force: Force scan even if interval hasn't passed
            
        Returns list of tradable markets.
        """
        now = time.time()
        
        # Check if we should scan
        if not force and (now - self._last_scan_time) < self.scan_interval:
            return self._tradable_markets
        
        self._scanning = True
        
        try:
            markets = self.fetch_markets()
            
            # Update cache
            self._markets.clear()
            for market in markets:
                self._markets[market.id] = market
            
            # Filter tradable markets
            self._tradable_markets = [m for m in markets if m.meets_criteria]
            
            # Sort by volume
            self._tradable_markets.sort(key=lambda m: m.volume_24h, reverse=True)
            
            self._last_scan_time = now
            
            logger.info(
                f"Scan complete: {len(self._tradable_markets)} tradable markets "
                f"out of {len(markets)} total"
            )
            
            return self._tradable_markets
            
        finally:
            self._scanning = False
    
    def get_tradable_markets(self) -> List[ScannedMarket]:
        """Get cached list of tradable markets."""
        return self._tradable_markets.copy()
    
    def get_market(self, market_id: str) -> Optional[ScannedMarket]:
        """Get a specific market by ID."""
        return self._markets.get(market_id)
    
    def get_top_markets(self, limit: int = 10) -> List[ScannedMarket]:
        """Get top N tradable markets by volume."""
        return self._tradable_markets[:limit]
    
    def is_scanning(self) -> bool:
        """Check if currently scanning."""
        return self._scanning
    
    def get_market_token_ids(self, market_id: str) -> List[str]:
        """Get token IDs for a market."""
        market = self._markets.get(market_id)
        if not market:
            return []
        return [t.token_id for t in market.tokens]
    
    def cleanup(self) -> None:
        """Cleanup resources."""
        self.client.close()
        self._markets.clear()
        self._tradable_markets.clear()
