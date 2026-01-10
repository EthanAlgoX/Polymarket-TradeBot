"""
Binance Service for Real-time Crypto Prices

Provides WebSocket connection to Binance for real-time price data.
Used by DipArb strategy for price comparison and dip detection.

Usage:
    from agents.arbitrage.binance_service import BinanceService
    
    service = BinanceService()
    service.subscribe(['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
    service.on_price(lambda symbol, price: print(f"{symbol}: ${price}"))
    await service.connect()
"""

import asyncio
import logging
import json
import time
from typing import Optional, Dict, List, Callable, Set
from dataclasses import dataclass, field
import websockets

logger = logging.getLogger("BinanceService")


@dataclass
class BinancePrice:
    """Price update from Binance."""
    symbol: str        # e.g., 'BTCUSDT'
    price: float       # Current price
    timestamp: float   # Unix timestamp
    change_24h: float = 0.0  # 24h change percentage


class BinanceService:
    """
    Binance WebSocket service for real-time crypto prices.
    
    Uses Binance mini ticker stream for low-latency updates.
    """
    
    WS_URL = "wss://stream.binance.com:9443/ws"
    STREAM_URL = "wss://stream.binance.com:9443/stream"
    
    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._is_connected: bool = False
        self._is_running: bool = False
        
        # Subscriptions
        self._symbols: Set[str] = set()
        self._prices: Dict[str, BinancePrice] = {}
        
        # Callbacks
        self._on_price_handlers: List[Callable[[str, float], None]] = []
        self._on_connect_handler: Optional[Callable[[], None]] = None
        
        # Reconnection
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 30.0
        
    def subscribe(self, symbols: List[str]):
        """
        Subscribe to price updates for symbols.
        
        Args:
            symbols: List of symbols like ['BTCUSDT', 'ETHUSDT']
        """
        for s in symbols:
            self._symbols.add(s.upper())
        logger.info(f"Subscribed to: {list(self._symbols)}")
    
    def on_price(self, handler: Callable[[str, float], None]):
        """Register price update handler."""
        self._on_price_handlers.append(handler)
    
    def on_connect(self, handler: Callable[[], None]):
        """Register connection handler."""
        self._on_connect_handler = handler
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        bp = self._prices.get(symbol.upper())
        return bp.price if bp else None
    
    def get_all_prices(self) -> Dict[str, float]:
        """Get all current prices."""
        return {s: bp.price for s, bp in self._prices.items()}
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._is_connected
    
    async def connect(self):
        """Connect to Binance WebSocket."""
        if self._is_running:
            return
        
        self._is_running = True
        
        while self._is_running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"Connection error: {e}")
                self._is_connected = False
                
                if self._is_running:
                    logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self._max_reconnect_delay
                    )
    
    async def _connect_and_listen(self):
        """Connect and listen to streams."""
        if not self._symbols:
            logger.warning("No symbols subscribed")
            return
        
        # Build stream URL for all symbols
        streams = [f"{s.lower()}@miniTicker" for s in self._symbols]
        url = f"{self.STREAM_URL}?streams={'/'.join(streams)}"
        
        logger.info(f"Connecting to Binance: {len(streams)} streams")
        
        async with websockets.connect(url) as ws:
            self._ws = ws
            self._is_connected = True
            self._reconnect_delay = 1.0
            
            logger.info("✅ Connected to Binance WebSocket")
            
            if self._on_connect_handler:
                self._on_connect_handler()
            
            async for message in ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    continue
    
    async def _handle_message(self, data: dict):
        """Handle incoming WebSocket message."""
        # Combined stream format: {"stream": "btcusdt@miniTicker", "data": {...}}
        if 'data' in data:
            ticker = data['data']
        else:
            ticker = data
        
        if 's' not in ticker:
            return
        
        symbol = ticker['s']  # e.g., 'BTCUSDT'
        price = float(ticker['c'])  # Current price
        
        # Calculate 24h change
        open_price = float(ticker.get('o', price))
        change_24h = ((price - open_price) / open_price * 100) if open_price > 0 else 0
        
        bp = BinancePrice(
            symbol=symbol,
            price=price,
            timestamp=time.time(),
            change_24h=change_24h
        )
        
        self._prices[symbol] = bp
        
        # Notify handlers
        for handler in self._on_price_handlers:
            try:
                handler(symbol, price)
            except Exception as e:
                logger.error(f"Price handler error: {e}")
    
    async def disconnect(self):
        """Disconnect from Binance."""
        self._is_running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._is_connected = False
        logger.info("Disconnected from Binance")


# Convenience functions
async def get_binance_price(symbol: str) -> Optional[float]:
    """
    Quick fetch of current price from Binance REST API.
    
    For occasional use - prefer WebSocket for continuous updates.
    """
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol.upper()}
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data['price'])
    except Exception as e:
        logger.error(f"Failed to fetch {symbol} price: {e}")
        return None


async def get_binance_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch multiple prices from Binance REST API."""
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.binance.com/api/v3/ticker/price")
            resp.raise_for_status()
            data = resp.json()
            
            symbol_set = {s.upper() for s in symbols}
            return {
                item['symbol']: float(item['price'])
                for item in data
                if item['symbol'] in symbol_set
            }
    except Exception as e:
        logger.error(f"Failed to fetch prices: {e}")
        return {}


# Symbol mapping for DipArb
UNDERLYING_TO_BINANCE = {
    'BTC': 'BTCUSDT',
    'ETH': 'ETHUSDT',
    'SOL': 'SOLUSDT',
    'XRP': 'XRPUSDT',
    'DOGE': 'DOGEUSDT',
}


def get_binance_symbol(underlying: str) -> str:
    """Convert underlying (BTC, ETH) to Binance symbol (BTCUSDT)."""
    return UNDERLYING_TO_BINANCE.get(underlying.upper(), f"{underlying.upper()}USDT")
