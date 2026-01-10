"""
Polymarket Trading Interface

Wrapper for Polymarket CLOB client for order execution.
This is a simplified implementation for paper trading compatibility.
"""

import logging
from typing import Optional, Dict, Any

from agents.arbitrage.config import (
    CLOB_API_URL, HOST, CHAIN_ID,
    POLY_API_KEY, POLY_API_SECRET, POLY_PASSPHRASE,
    WALLET_PRIVATE_KEY, PAPER_TRADING
)

logger = logging.getLogger("Polymarket")


class Polymarket:
    """
    Polymarket trading interface.
    
    Wraps the py_clob_client for order execution.
    Supports paper trading mode for simulation.
    """
    
    def __init__(self):
        self.paper_trading = PAPER_TRADING
        self.client = None
        
        if not self.paper_trading and WALLET_PRIVATE_KEY:
            self._init_client()
        else:
            logger.info("Polymarket initialized in paper trading mode")
    
    def _init_client(self):
        """Initialize the CLOB client for live trading."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            
            creds = ApiCreds(
                api_key=POLY_API_KEY,
                api_secret=POLY_API_SECRET,
                api_passphrase=POLY_PASSPHRASE
            )
            
            self.client = ClobClient(
                host=HOST,
                chain_id=CHAIN_ID,
                key=WALLET_PRIVATE_KEY,
                creds=creds
            )
            
            logger.info("CLOB client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            self.paper_trading = True
    
    def execute_order(
        self,
        price: float,
        size: float,
        side: int,  # 0=BUY, 1=SELL
        token_id: str
    ) -> Dict[str, Any]:
        """
        Execute an order on Polymarket.
        
        Args:
            price: Order price
            size: Order size
            side: Order side (0=BUY, 1=SELL)
            token_id: Token ID to trade
            
        Returns:
            Order response dict
        """
        side_str = "BUY" if side == 0 else "SELL"
        
        if self.paper_trading:
            logger.info(f"[PAPER] {side_str} {size} @ {price} - Token: {token_id[:20]}...")
            return {
                "success": True,
                "order_id": "paper_" + str(hash((price, size, token_id)))[:8],
                "price": price,
                "size": size,
                "side": side_str,
                "token_id": token_id,
                "paper_trade": True
            }
        
        if not self.client:
            raise RuntimeError("CLOB client not initialized for live trading")
        
        try:
            from py_clob_client.clob_types import OrderArgs
            
            order_args = OrderArgs(
                price=price,
                size=size,
                side=side,
                token_id=token_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order)
            
            logger.info(f"[LIVE] Order placed: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            raise
    
    def get_balance(self) -> float:
        """Get USDC balance."""
        if self.paper_trading:
            return 1000.0  # Mock balance for paper trading
        
        # Real balance fetching would go here
        return 0.0
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if self.paper_trading:
            logger.info(f"[PAPER] Cancelled order: {order_id}")
            return True
        
        if not self.client:
            return False
        
        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False
