# Polymarket Arbitrage Trading Bot

> Advanced arbitrage and copy trading bot for Polymarket with real-time WebSocket monitoring, smart money tracking, and intelligent rebalancing.

[![License: ISC](https://img.shields.io/badge/License-ISC-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-%3E%3D3.10-brightgreen.svg)](https://python.org/)

## Overview

The Polymarket Arbitrage Bot detects and executes arbitrage opportunities on Polymarket prediction markets. It uses **correct effective price calculation** to handle Polymarket's mirror orderbook property and integrates multiple trading strategies.

### Key Features

| Feature | Description |
|---------|-------------|
| 🎯 **Arbitrage Detection** | Effective price calculation for accurate opportunity detection |
| ⚡ **Real-time WebSocket** | Live orderbook updates via Polymarket WebSocket API |
| 🧠 **Smart Money Tracking** | Monitor and copy top performer trades |
| ⚖️ **Auto Rebalancing** | Automatic USDC/token ratio management |
| 📊 **DipArb Strategy** | 15-minute crypto UP/DOWN market arbitrage |
| 📈 **Momentum Strategy** | Technical indicator-based trading |

## Quick Start

### Prerequisites

- Python 3.10+
- Polygon wallet with USDC
- Polymarket API keys

### Installation

```bash
# Clone repository
git clone https://github.com/EthanAlgoX/Polymarket-TradeBot.git
cd Polymarket-TradeBot/agents-main

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run the bot
python -m agents.arbitrage.main
```

**📖 For detailed setup, see [SETUP_CREDENTIALS.md](./SETUP_CREDENTIALS.md)**

## Architecture

```
agents-main/
├── agents/arbitrage/
│   ├── main.py              # Main bot runner
│   ├── strategy.py          # Arbitrage strategy (effective prices)
│   ├── price_utils.py       # Effective price calculation
│   ├── realtime_service.py  # WebSocket real-time service
│   ├── smart_money_service.py  # Smart money tracking
│   ├── rebalancer.py        # USDC/token rebalancing
│   ├── dip_arb.py           # DipArb strategy
│   ├── market_scanner.py    # Market discovery
│   └── strategies/
│       └── momentum_strategy.py
```

## Core Modules

### 1. Effective Price Calculation (`price_utils.py`)

Correctly handles Polymarket's **mirror orderbook property**:

```python
from agents.arbitrage.price_utils import get_effective_prices, check_arbitrage

# Polymarket key property: Buy YES @ P = Sell NO @ (1-P)
eff = get_effective_prices(yes_ask, yes_bid, no_ask, no_bid)

# Correct arbitrage calculation
arb = check_arbitrage(yes_ask, yes_bid, no_ask, no_bid, threshold=0.003)
if arb:
    print(f"Arbitrage: {arb.profit_percent:.2f}%")
```

### 2. WebSocket Real-time Service (`realtime_service.py`)

```python
from agents.arbitrage.realtime_service import RealtimeService

service = RealtimeService()
service.connect()
service.subscribe_market(["token_id_yes", "token_id_no"])
service.on('orderbook', lambda ob: print(f"Bid: {ob.best_bid}"))
```

### 3. Smart Money Service (`smart_money_service.py`)

```python
from agents.arbitrage.smart_money_service import SmartMoneyService

service = SmartMoneyService()
await service.initialize()

# Get top traders
traders = await service.get_smart_money_list(50)
print(f"Top trader: {traders[0].name} PnL=${traders[0].pnl:,.0f}")

# Auto copy trading (dry run)
sub = await service.start_auto_copy_trading(
    top_n=50,
    size_scale=0.1,
    max_size_per_trade=10,
    dry_run=True
)
```

### 4. Rebalancer (`rebalancer.py`)

```python
from agents.arbitrage.rebalancer import Rebalancer

rebalancer = Rebalancer(
    min_usdc_ratio=0.2,
    max_usdc_ratio=0.8,
    target_usdc_ratio=0.5
)

action = rebalancer.calculate_action(usdc=100, yes_tokens=80, no_tokens=50)
if action.is_needed:
    print(f"Action: {action.type.value} ${action.amount:.2f}")
```

### 5. DipArb Strategy (`dip_arb.py`)

For 15-minute crypto UP/DOWN markets:

```python
from agents.arbitrage.dip_arb import DipArbStrategy, analyze_dip_arb

# Quick analysis
result = analyze_dip_arb(up_ask=0.47, down_ask=0.48)
print(f"Profit: {result['profit_pct']}")  # 5.26%
```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `POLY_API_KEY` | Polymarket API Key | ✅ |
| `POLY_API_SECRET` | Polymarket API Secret | ✅ |
| `POLY_PASSPHRASE` | Polymarket Passphrase | ✅ |
| `PRIVATE_KEY` | Wallet private key | ✅ |
| `PAPER_TRADING` | Enable paper trading mode | Optional |

### Bot Configuration (`config.py`)

```python
# Minimum profit threshold for arbitrage
MIN_PROFIT_SPREAD = 0.003  # 0.3%

# Risk management
MAX_DAILY_TRADES = 100
MAX_POSITION_SIZE = 50  # USDC
```

## Monitoring

```bash
# View real-time logs
tail -f agents/arbitrage/logs/bot_$(date +%Y%m%d).log

# Check process
pgrep -f "agents.arbitrage.main"

# Stop bot
pkill -f "agents.arbitrage.main"
```

## License

ISC License - See [LICENSE](LICENSE) file for details.

---

**⚠️ Disclaimer:** This software is for educational purposes only. Trading involves risk of loss. The developers are not responsible for any financial losses incurred while using this bot.
