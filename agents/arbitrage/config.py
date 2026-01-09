import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_API_SECRET = os.getenv("POLY_API_SECRET", "")
POLY_PASSPHRASE = os.getenv("POLY_PASSPHRASE", "")
WALLET_PRIVATE_KEY = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")

# URLs
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
HOST = CLOB_API_URL
CHAIN_ID = 137
POLYGON_RPC = "https://polygon-rpc.com"

# =============================================================================
# ARBITRAGE TRADING CONSTANTS (Optimized for more signals)
# =============================================================================
MIN_PROFIT_SPREAD = 0.003  # 0.3% minimum profit (was 0.5%)
MAX_POSITION_SIZE = 50.0   # 50 USDC max per trade
POLL_INTERVAL = 1.0        # 1 second polling interval
MAX_LATENCY_MS = 500       # 500ms max acceptable latency

# =============================================================================
# POSITION MANAGEMENT (Phase 1)
# =============================================================================
PROFIT_TARGET = 0.01       # 1% profit target - exit when reached
STOP_LOSS = 0.02           # 2% stop loss - exit to limit losses
MAX_HOLD_TIME = 300        # 5 minutes max hold time (seconds)
FEE_RATE = 0.001           # 0.1% trading fee estimate
TRAILING_STOP_PERCENT = 0.015  # 1.5% trailing stop

# =============================================================================
# COPY TRADING CONFIGURATION (Phase 2)
# =============================================================================
# Trader addresses to copy (comma-separated in env, parsed to list)
TARGET_TRADERS = [addr.strip() for addr in os.getenv("TARGET_TRADERS", "").split(",") if addr.strip()]

TRADE_MULTIPLIER = float(os.getenv("TRADE_MULTIPLIER", "1.0"))  # Position size multiplier
MIN_ORDER_SIZE = float(os.getenv("MIN_ORDER_SIZE", "1.0"))      # Minimum $1 order
MAX_COPY_TRADES = int(os.getenv("MAX_COPY_TRADES", "2000"))     # Max trades to fetch
COPY_HISTORY_DAYS = int(os.getenv("COPY_HISTORY_DAYS", "30"))   # Days of history to analyze

# =============================================================================
# MOMENTUM STRATEGY (Phase 3 - Optimized for more signals)
# =============================================================================
MOMENTUM_ENABLED = os.getenv("MOMENTUM_ENABLED", "false").lower() == "true"
LOOKBACK_PERIOD = 10           # Reduced from 20 for faster signals
MOMENTUM_THRESHOLD = 0.015     # 1.5% momentum (was 2%)
VOLUME_SPIKE_THRESHOLD = 1.3   # 30% volume increase (was 50%)
BREAKOUT_THRESHOLD = 0.008     # 0.8% breakout (was 1%)
MOMENTUM_MAX_HOLD_TIME = 600   # 10 minutes max for momentum trades

# =============================================================================
# RISK MANAGEMENT (Phase 4)
# =============================================================================
DAILY_PNL_LIMIT = -100.0       # Stop trading if daily loss exceeds $100
MAX_OPEN_POSITIONS = 5         # Maximum concurrent open positions
MAX_DAILY_TRADES = 50          # Maximum trades per day
CIRCUIT_BREAKER_COOLDOWN = 3600  # 1 hour cooldown after circuit breaker triggers
EMERGENCY_STOP_LOSS = 0.10     # 10% emergency stop on any position

# Enhanced Risk Management (Inspired by riskManager.ts)
MARKET_COOLDOWN_DURATION = 300  # 5 minutes cooldown after loss in a market
MIN_TRADE_INTERVAL = 1.0        # Minimum 1 second between trades

# =============================================================================
# MARKET DISCOVERY (Phase 5 - Relaxed filters for more markets)
# =============================================================================
MIN_MARKET_VOLUME = 5000       # Reduced from $10,000 for more markets
MIN_MARKET_LIQUIDITY = 2000    # Reduced from $5,000 for more markets
MAX_TIME_TO_RESOLUTION = 336   # Extended to 14 days (was 7 days)
MARKET_SCAN_INTERVAL = 30      # Faster scanning every 30 seconds (was 60)

# =============================================================================
# PAPER TRADING
# =============================================================================
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

