"""
Real-time WebSocket Service for Polymarket

Based on poly-sdk-main/src/services/realtime-service-v2.ts

Features:
- WebSocket connection to Polymarket real-time data
- Market orderbook subscriptions
- Price and trade updates
- Auto-reconnect on disconnect
- Event-based architecture

Usage:
    from agents.arbitrage.realtime_service import RealtimeService
    
    service = RealtimeService()
    service.connect()
    service.subscribe_market(["token_id_1", "token_id_2"])
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import threading

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None

logger = logging.getLogger("RealtimeService")


# WebSocket endpoints
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
ACTIVITY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/activity"
CHAINLINK_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/prices"  # From poly-sdk-main


class ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class OrderbookLevel:
    """Single orderbook level (price/size)."""
    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    """Orderbook snapshot from WebSocket."""
    asset_id: str
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]
    timestamp: float
    hash: str = ""
    
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def midpoint(self) -> float:
        return (self.best_bid + self.best_ask) / 2


@dataclass
class PriceUpdate:
    """Price update event."""
    asset_id: str
    price: float
    midpoint: float
    spread: float
    timestamp: float


@dataclass
class TradeInfo:
    """Last trade information."""
    asset_id: str
    price: float
    size: float
    side: str  # 'BUY' or 'SELL'
    timestamp: float


@dataclass
class ActivityTrade:
    """Trade activity from activity WebSocket."""
    asset: str
    condition_id: str
    outcome: str
    price: float
    size: float
    side: str
    timestamp: float
    trader_address: Optional[str] = None
    trader_name: Optional[str] = None


@dataclass
class CryptoPrice:
    """
    External crypto price from Chainlink (from poly-sdk-main).
    
    Used for DipArb strategy to track underlying asset prices.
    """
    symbol: str  # e.g., "ETH/USD"
    price: float
    timestamp: float


class RealtimeService:
    """
    Real-time WebSocket service for Polymarket.
    
    Provides:
    - Market orderbook subscriptions
    - Price updates
    - Trade notifications
    - Activity monitoring (for copy trading)
    """
    
    def __init__(
        self,
        auto_reconnect: bool = True,
        ping_interval: int = 30,
        debug: bool = False
    ):
        self.auto_reconnect = auto_reconnect
        self.ping_interval = ping_interval
        self.debug = debug
        
        # Connection state
        self._status = ConnectionStatus.DISCONNECTED
        self._ws: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        
        # Caches
        self._orderbook_cache: Dict[str, OrderbookSnapshot] = {}
        self._price_cache: Dict[str, PriceUpdate] = {}
        self._last_trade_cache: Dict[str, TradeInfo] = {}
        
        # Subscriptions
        self._subscribed_tokens: List[str] = []
        self._subscription_messages: List[dict] = []
        
        # Event handlers
        self._handlers: Dict[str, List[Callable]] = {
            'orderbook': [],
            'price': [],
            'trade': [],
            'activity': [],
            'chainlink': [],  # NEW: Chainlink price updates
            'connected': [],
            'disconnected': [],
            'error': [],
        }
        
        # Chainlink subscriptions (from poly-sdk-main)
        self._chainlink_symbols: List[str] = []
        self._chainlink_cache: Dict[str, CryptoPrice] = {}
        
        # Smart logging (from poly-sdk-main)
        self._last_orderbook_log_time: float = 0
        self.ORDERBOOK_LOG_INTERVAL_MS: int = 10000  # Log every 10 seconds
        self._orderbook_buffer: List[Dict] = []
        self.ORDERBOOK_BUFFER_SIZE: int = 50
        
        # Check websockets availability
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("websockets library not installed. Install with: pip install websockets")
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self) -> 'RealtimeService':
        """Connect to WebSocket server."""
        if not WEBSOCKETS_AVAILABLE:
            logger.error("Cannot connect: websockets library not installed")
            return self
        
        if self._status in [ConnectionStatus.CONNECTED, ConnectionStatus.CONNECTING]:
            logger.debug("Already connected or connecting")
            return self
        
        self._status = ConnectionStatus.CONNECTING
        
        # Start event loop in background thread
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        
        logger.info("WebSocket connecting...")
        return self
    
    def disconnect(self):
        """Disconnect from WebSocket server."""
        self._status = ConnectionStatus.DISCONNECTED
        
        if self._loop and self._ws:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
        
        self._ws = None
        self._subscribed_tokens.clear()
        self._subscription_messages.clear()
        
        logger.info("WebSocket disconnected")
        self._emit('disconnected')
    
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._status == ConnectionStatus.CONNECTED
    
    # =========================================================================
    # Subscriptions
    # =========================================================================
    
    def subscribe_market(self, token_ids: List[str], handlers: Dict[str, Callable] = None):
        """
        Subscribe to market data for given token IDs.
        
        Args:
            token_ids: List of token IDs (YES and NO tokens)
            handlers: Optional handlers for events:
                - on_orderbook: Called with OrderbookSnapshot
                - on_price: Called with PriceUpdate
                - on_trade: Called with TradeInfo
        """
        if handlers:
            if handlers.get('on_orderbook'):
                self.on('orderbook', handlers['on_orderbook'])
            if handlers.get('on_price'):
                self.on('price', handlers['on_price'])
            if handlers.get('on_trade'):
                self.on('trade', handlers['on_trade'])
        
        self._subscribed_tokens.extend(token_ids)
        
        # Build subscription message
        subscriptions = [
            {"topic": "clob_market", "type": "agg_orderbook", "filters": json.dumps(token_ids)},
            {"topic": "clob_market", "type": "price_change", "filters": json.dumps(token_ids)},
            {"topic": "clob_market", "type": "last_trade_price", "filters": json.dumps(token_ids)},
        ]
        
        sub_msg = {"subscriptions": subscriptions}
        self._subscription_messages.append(sub_msg)
        
        # Send if connected
        if self._status == ConnectionStatus.CONNECTED and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_message(sub_msg),
                self._loop
            )
        
        logger.info(f"Subscribed to {len(token_ids)} tokens")
    
    def subscribe_activity(self, handlers: Dict[str, Callable] = None):
        """
        Subscribe to all trading activity (for copy trading).
        
        Args:
            handlers: Optional handlers:
                - on_activity: Called with ActivityTrade
        """
        if handlers and handlers.get('on_activity'):
            self.on('activity', handlers['on_activity'])
        
        subscriptions = [
            {"topic": "activity", "type": "trades"},
            {"topic": "activity", "type": "orders_matched"},
        ]
        
        sub_msg = {"subscriptions": subscriptions}
        self._subscription_messages.append(sub_msg)
        
        if self._status == ConnectionStatus.CONNECTED and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_message(sub_msg),
                self._loop
            )
        
        logger.info("Subscribed to activity stream")
    
    def subscribe_chainlink_prices(
        self,
        symbols: List[str],
        handlers: Dict[str, Callable] = None
    ):
        """
        Subscribe to Chainlink oracle prices for underlying assets.
        
        Ported from poly-sdk-main RealtimeServiceV2.subscribeCryptoChainlinkPrices().
        
        Args:
            symbols: List of symbols like ["ETH/USD", "BTC/USD"]
            handlers: Optional handlers:
                - on_price: Called with CryptoPrice
        """
        if handlers and handlers.get('on_price'):
            self.on('chainlink', handlers['on_price'])
        
        self._chainlink_symbols.extend(symbols)
        
        # Build subscription message
        subscriptions = [
            {
                "topic": "prices",
                "type": "crypto_chainlink",
                "filters": json.dumps(symbols)
            }
        ]
        
        sub_msg = {"subscriptions": subscriptions}
        self._subscription_messages.append(sub_msg)
        
        if self._status == ConnectionStatus.CONNECTED and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_message(sub_msg),
                self._loop
            )
        
        logger.info(f"Subscribed to Chainlink prices: {symbols}")
    
    def get_chainlink_price(self, symbol: str) -> Optional[CryptoPrice]:
        """Get cached Chainlink price for symbol."""
        return self._chainlink_cache.get(symbol)
    
    # =========================================================================
    # Event Handlers
    # =========================================================================
    
    def on(self, event: str, handler: Callable):
        """Register event handler."""
        if event in self._handlers:
            self._handlers[event].append(handler)
    
    def off(self, event: str, handler: Callable):
        """Remove event handler."""
        if event in self._handlers and handler in self._handlers[event]:
            self._handlers[event].remove(handler)
    
    def _emit(self, event: str, data: Any = None):
        """Emit event to handlers."""
        for handler in self._handlers.get(event, []):
            try:
                if data is not None:
                    handler(data)
                else:
                    handler()
            except Exception as e:
                logger.error(f"Handler error for {event}: {e}")
    
    # =========================================================================
    # Cache Access
    # =========================================================================
    
    def get_orderbook(self, token_id: str) -> Optional[OrderbookSnapshot]:
        """Get cached orderbook for token."""
        return self._orderbook_cache.get(token_id)
    
    def get_price(self, token_id: str) -> Optional[PriceUpdate]:
        """Get cached price for token."""
        return self._price_cache.get(token_id)
    
    def get_last_trade(self, token_id: str) -> Optional[TradeInfo]:
        """Get cached last trade for token."""
        return self._last_trade_cache.get(token_id)
    
    # =========================================================================
    # Internal Methods
    # =========================================================================
    
    def _run_event_loop(self):
        """Run asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._loop.run_until_complete(self._connect_websocket())
        except Exception as e:
            logger.error(f"Event loop error: {e}")
            self._status = ConnectionStatus.DISCONNECTED
    
    async def _connect_websocket(self):
        """Connect to WebSocket and handle messages."""
        try:
            async with websockets.connect(WS_URL) as ws:
                self._ws = ws
                self._status = ConnectionStatus.CONNECTED
                logger.info("WebSocket connected")
                self._emit('connected')
                
                # Resend subscriptions
                for sub_msg in self._subscription_messages:
                    await self._send_message(sub_msg)
                
                # Handle messages
                async for message in ws:
                    await self._handle_message(message)
                    
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self._emit('error', e)
            self._status = ConnectionStatus.DISCONNECTED
            
            # Auto-reconnect
            if self.auto_reconnect:
                logger.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
                await self._connect_websocket()
    
    async def _send_message(self, msg: dict):
        """Send message to WebSocket."""
        if self._ws:
            await self._ws.send(json.dumps(msg))
            if self.debug:
                logger.debug(f"Sent: {msg}")
    
    async def _handle_message(self, raw_message: str):
        """Handle incoming WebSocket message."""
        try:
            messages = json.loads(raw_message)
            
            # Handle array of messages
            if isinstance(messages, list):
                for msg in messages:
                    self._process_message(msg)
            else:
                self._process_message(messages)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
    
    def _process_message(self, msg: dict):
        """Process a single message."""
        msg_type = msg.get("type", "")
        topic = msg.get("topic", "")
        
        if self.debug:
            logger.debug(f"Received: {topic}/{msg_type}")
        
        if topic == "clob_market":
            if msg_type == "agg_orderbook":
                self._handle_orderbook(msg)
            elif msg_type == "price_change":
                self._handle_price_change(msg)
            elif msg_type == "last_trade_price":
                self._handle_last_trade(msg)
                
        elif topic == "activity":
            if msg_type in ["trades", "orders_matched"]:
                self._handle_activity(msg)
        
        elif topic == "prices":
            if msg_type == "crypto_chainlink":
                self._handle_chainlink_price(msg)
    
    def _handle_orderbook(self, msg: dict):
        """Handle orderbook update."""
        data = msg.get("data", {})
        asset_id = data.get("asset_id", "")
        
        if not asset_id:
            return
        
        # Parse bids and asks
        bids = [
            OrderbookLevel(float(b.get("price", 0)), float(b.get("size", 0)))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderbookLevel(float(a.get("price", 0)), float(a.get("size", 0)))
            for a in data.get("asks", [])
        ]
        
        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        
        snapshot = OrderbookSnapshot(
            asset_id=asset_id,
            bids=bids,
            asks=asks,
            timestamp=time.time(),
            hash=data.get("hash", "")
        )
        
        self._orderbook_cache[asset_id] = snapshot
        self._emit('orderbook', snapshot)
        
        # Also emit price update
        if bids and asks:
            price_update = PriceUpdate(
                asset_id=asset_id,
                price=snapshot.midpoint,
                midpoint=snapshot.midpoint,
                spread=snapshot.spread,
                timestamp=snapshot.timestamp
            )
            self._price_cache[asset_id] = price_update
            self._emit('price', price_update)
    
    def _handle_price_change(self, msg: dict):
        """Handle price change update."""
        data = msg.get("data", {})
        asset_id = data.get("asset_id", "")
        
        if asset_id and asset_id in self._orderbook_cache:
            ob = self._orderbook_cache[asset_id]
            price_update = PriceUpdate(
                asset_id=asset_id,
                price=ob.midpoint,
                midpoint=ob.midpoint,
                spread=ob.spread,
                timestamp=time.time()
            )
            self._price_cache[asset_id] = price_update
            self._emit('price', price_update)
    
    def _handle_last_trade(self, msg: dict):
        """Handle last trade update."""
        data = msg.get("data", {})
        asset_id = data.get("asset_id", "")
        
        if not asset_id:
            return
        
        trade = TradeInfo(
            asset_id=asset_id,
            price=float(data.get("price", 0)),
            size=float(data.get("size", 0)),
            side=data.get("side", "BUY"),
            timestamp=time.time()
        )
        
        self._last_trade_cache[asset_id] = trade
        self._emit('trade', trade)
    
    def _handle_activity(self, msg: dict):
        """Handle activity trade update (for copy trading)."""
        data = msg.get("data", {})
        
        # Extract trader info
        trader = data.get("trader", {})
        
        activity = ActivityTrade(
            asset=data.get("asset", ""),
            condition_id=data.get("conditionId", ""),
            outcome=data.get("outcome", ""),
            price=float(data.get("price", 0)),
            size=float(data.get("size", 0)),
            side=data.get("side", ""),
            timestamp=float(data.get("timestamp", time.time())),
            trader_address=trader.get("address"),
            trader_name=trader.get("name")
        )
        
        self._emit('activity', activity)
    
    def _handle_chainlink_price(self, msg: dict):
        """Handle Chainlink price update (from poly-sdk-main)."""
        data = msg.get("data", {})
        symbol = data.get("symbol", "")
        
        if not symbol:
            return
        
        crypto_price = CryptoPrice(
            symbol=symbol,
            price=float(data.get("price", 0)),
            timestamp=float(data.get("timestamp", time.time()))
        )
        
        self._chainlink_cache[symbol] = crypto_price
        self._emit('chainlink', crypto_price)
        
        if self.debug:
            logger.debug(f"Chainlink: {symbol} = ${crypto_price.price:.2f}")
    
    def _update_orderbook_buffer(self, asset_id: str, best_bid: float, best_ask: float):
        """Update smart logging buffer (from poly-sdk-main)."""
        self._orderbook_buffer.append({
            'timestamp': time.time(),
            'asset_id': asset_id,
            'best_bid': best_bid,
            'best_ask': best_ask
        })
        if len(self._orderbook_buffer) > self.ORDERBOOK_BUFFER_SIZE:
            self._orderbook_buffer.pop(0)
    
    def _maybe_log_orderbook_summary(self):
        """Log aggregated orderbook stats every 10 seconds (from poly-sdk-main)."""
        now = time.time() * 1000
        if now - self._last_orderbook_log_time < self.ORDERBOOK_LOG_INTERVAL_MS:
            return
        
        if not self._orderbook_buffer:
            return
        
        self._last_orderbook_log_time = now
        
        # Log summary
        if self.debug:
            logger.debug(f"📊 Orderbook buffer: {len(self._orderbook_buffer)} entries in last 10s")


# Convenience function for synchronous usage
def create_realtime_service(auto_reconnect: bool = True) -> RealtimeService:
    """Create and connect a realtime service."""
    service = RealtimeService(auto_reconnect=auto_reconnect)
    service.connect()
    return service
