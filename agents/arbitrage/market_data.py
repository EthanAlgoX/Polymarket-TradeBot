import time
import httpx
import asyncio
from typing import List, Dict, Optional
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

from agents.arbitrage.config import GAMMA_API_URL, CLOB_API_URL, POLYGON_RPC, WALLET_PRIVATE_KEY
from agents.arbitrage.types import OrderbookSnapshot, OrderSummary

class MarketDataEngine:
    def __init__(self):
        self.gamma_client = httpx.Client(base_url=GAMMA_API_URL)
        # Initialize ClobClient for Orderbook access
        # Note: We use a read-only client if no key is present, but ClobClient usually requires one.
        # For public endpoints like get_order_book, we might not strictly need auth, but the library structure implies it.
        # We'll use the one from our existing polymarket.py or instantiate a new one.

        # Handle empty or invalid keys gracefully by passing None if empty/invalid
        key = WALLET_PRIVATE_KEY
        if not key or key.strip() == "":
            key = None

        try:
            self.clob_client = ClobClient(
                host=CLOB_API_URL,
                key=key,
                chain_id=POLYGON
            )
        except Exception as e:
            # If ClobClient fails to init (e.g. invalid key format), try without key
            print(f"Warning: Failed to init ClobClient with key: {e}. Retrying without key.")
            self.clob_client = ClobClient(
                host=CLOB_API_URL,
                key=None,
                chain_id=POLYGON
            )

        self.orderbooks: Dict[str, OrderbookSnapshot] = {}

    def fetch_orderbook(self, token_id: str) -> Optional[OrderbookSnapshot]:
        """
        Fetches the orderbook for a specific token_id and returns a standardized snapshot.
        """
        try:
            raw_ob = self.clob_client.get_order_book(token_id)

            # Parse bids and asks
            # raw_ob structure depends on the library response, typically has bids/asks as lists of objects
            # or lists of strings. We need to handle the specific format of py-clob-client.

            bids = [OrderSummary(price=float(o.price), size=float(o.size)) for o in raw_ob.bids]
            asks = [OrderSummary(price=float(o.price), size=float(o.size)) for o in raw_ob.asks]

            best_bid = bids[0].price if bids else 0.0
            best_ask = asks[0].price if asks else 0.0

            # Simple spread calc
            spread = best_ask - best_bid if (best_bid and best_ask) else 0.0
            spread_percent = (spread / best_ask) if best_ask > 0 else 0.0

            snapshot = OrderbookSnapshot(
                market_id=raw_ob.market_hash if hasattr(raw_ob, 'market_hash') else "", # Might not be available in simple OB response
                asset_id=token_id,
                bids=bids,
                asks=asks,
                timestamp=time.time(),
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_percent=spread_percent,
                bid_depth=sum(b.size for b in bids[:5]),
                ask_depth=sum(a.size for a in asks[:5])
            )

            self.orderbooks[token_id] = snapshot
            return snapshot

        except Exception as e:
            # Only log non-404 errors (404 is expected for inactive markets)
            error_str = str(e)
            if "404" not in error_str:
                print(f"Error fetching orderbook for {token_id[:30]}...: {e}")
            return None
    
    def has_orderbook(self, token_id: str) -> bool:
        """Check if a token has an active orderbook (quick validation)."""
        try:
            raw_ob = self.clob_client.get_order_book(token_id)
            return len(raw_ob.bids) > 0 or len(raw_ob.asks) > 0
        except Exception:
            return False

    def get_market_price(self, token_id: str) -> float:
        """
        Get the mid-price or last trade price.
        """
        ob = self.fetch_orderbook(token_id)
        if ob and ob.best_bid and ob.best_ask:
            return (ob.best_bid + ob.best_ask) / 2
        return 0.0
