"""
Polymarket Arbitrage Bot - Main Entry Point

Enhanced bot with:
- Multi-strategy support (Arbitrage, Momentum)
- Dynamic market discovery
- Copy trading integration
- Advanced risk management with circuit breaker
- Paper trading mode

Based on architecture from Polymarket-Copy-Trading-Bot-develop.
"""

import time
import logging
import sys
import signal
import asyncio
from typing import List, Optional

# Import our modular components
from agents.arbitrage.config import (
    POLL_INTERVAL, PAPER_TRADING, MOMENTUM_ENABLED,
    TARGET_TRADERS, MARKET_SCAN_INTERVAL
)
from agents.arbitrage.market_data import MarketDataEngine
from agents.arbitrage.market_scanner import MarketScanner
from agents.arbitrage.strategy import ArbitrageStrategy, TradeSignal, SignalType
from agents.arbitrage.strategies.momentum_strategy import MomentumStrategy
from agents.arbitrage.execution import ExecutionEngine
from agents.arbitrage.risk import RiskManager
from agents.arbitrage.types import OrderbookSnapshot

# Import logging configuration
from agents.arbitrage.logging_config import (
    setup_file_logging, save_trade, save_signal, save_daily_summary,
    LOG_ROOT
)

# Configure logging with both console and file output
from datetime import datetime
log_file = LOG_ROOT / f"bot_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8")
    ]
)
logger = logging.getLogger("PolyArbBot")
logger.info(f"日志保存到: {log_file}")


class PolymarketArbBot:
    """
    Enhanced Polymarket Arbitrage Bot.
    
    Features:
    - Multiple trading strategies
    - Dynamic market discovery
    - Copy trading capabilities
    - Risk management with circuit breaker
    - Paper trading mode
    """
    
    def __init__(self):
        self.running = False
        
        # Initialize components
        self.market_engine = MarketDataEngine()
        self.market_scanner = MarketScanner()
        self.arb_strategy = ArbitrageStrategy()
        self.momentum_strategy = MomentumStrategy() if MOMENTUM_ENABLED else None
        self.risk_manager = RiskManager()
        self.executor = ExecutionEngine()
        
        # Track state
        self._last_scan_time = 0
        self._target_markets: List[dict] = []
        
        # Paper trading mode
        self.paper_trading = PAPER_TRADING
        
        # Copy trading (optional)
        self.copy_trading_enabled = bool(TARGET_TRADERS)
        self.trader_monitor = None
        self.copy_executor = None
        
        if self.copy_trading_enabled:
            self._init_copy_trading()
        
        # Register circuit breaker callback
        self.risk_manager.on_circuit_breaker(self._on_circuit_breaker)
        
        logger.info("PolyArbBot initialized")
        logger.info(f"Paper Trading: {self.paper_trading}")
        logger.info(f"Momentum Strategy: {'Enabled' if self.momentum_strategy else 'Disabled'}")
        logger.info(f"Copy Trading: {'Enabled' if self.copy_trading_enabled else 'Disabled'}")
    
    def _init_copy_trading(self):
        """Initialize copy trading components."""
        try:
            from agents.arbitrage.copy_trading.trader_monitor import TraderMonitor
            from agents.arbitrage.copy_trading.trade_executor import CopyTradeExecutor
            
            self.trader_monitor = TraderMonitor(traders=TARGET_TRADERS)
            self.copy_executor = CopyTradeExecutor(
                trader_monitor=self.trader_monitor,
                execution_engine=self.executor if not self.paper_trading else None
            )
            logger.info(f"Copy trading initialized for {len(TARGET_TRADERS)} traders")
        except Exception as e:
            logger.error(f"Failed to initialize copy trading: {e}")
            self.copy_trading_enabled = False
    
    def _on_circuit_breaker(self, triggered: bool, reason: str):
        """Handle circuit breaker events."""
        if triggered:
            logger.error(f"CIRCUIT BREAKER ACTIVATED: {reason}")
            self._emergency_stop()
        else:
            logger.info("Circuit breaker reset, trading resumed")
    
    def _emergency_stop(self):
        """Emergency stop - close all positions."""
        logger.error("Executing emergency stop...")
        
        # Close arbitrage positions
        positions = self.arb_strategy.get_active_positions()
        for pos in positions:
            logger.warning(f"Emergency close: {pos.market_id}")
            # In paper mode, just clear tracking
            if self.paper_trading:
                self.arb_strategy.position_manager.close_position(
                    pos.market_id, pos.token_id, pos.current_price
                )
        
        # Close momentum positions
        if self.momentum_strategy:
            positions = self.momentum_strategy.get_active_positions()
            for pos in positions:
                logger.warning(f"Emergency close momentum: {pos.market_id}")
                if self.paper_trading:
                    self.momentum_strategy.position_manager.close_position(
                        pos.market_id, pos.token_id, pos.current_price
                    )
    
    def scan_markets(self) -> List[dict]:
        """Scan for tradable markets."""
        now = time.time()
        
        if now - self._last_scan_time < MARKET_SCAN_INTERVAL:
            return self._target_markets
        
        logger.info("Scanning for tradable markets...")
        
        tradable = self.market_scanner.scan(force=True)
        
        # Convert to target market format
        self._target_markets = []
        for market in tradable[:10]:  # Top 10 markets
            if market.tokens:
                self._target_markets.append({
                    "market_id": market.id,
                    "question": market.question,
                    "outcomes": [t.token_id for t in market.tokens],
                    "volume": market.volume_24h,
                    "liquidity": market.liquidity
                })
        
        self._last_scan_time = now
        logger.info(f"Found {len(self._target_markets)} tradable markets")
        
        return self._target_markets
    
    def process_market(self, market: dict) -> None:
        """Process a single market for trading opportunities."""
        market_id = market["market_id"]
        token_ids = market["outcomes"]
        
        # Fetch orderbooks
        snapshots: List[OrderbookSnapshot] = []
        valid_data = True
        
        for token_id in token_ids:
            ob = self.market_engine.fetch_orderbook(token_id)
            if ob:
                snapshots.append(ob)
            else:
                valid_data = False
                break
        
        if not valid_data or not snapshots:
            return
        
        # Update risk manager with position count
        arb_positions = len(self.arb_strategy.get_active_positions())
        mom_positions = len(self.momentum_strategy.get_active_positions()) if self.momentum_strategy else 0
        self.risk_manager.update_open_positions(arb_positions + mom_positions)
        
        # Evaluate arbitrage strategy
        signals = self.arb_strategy.evaluate(market_id, snapshots)
        self._process_signals(signals, "Arbitrage")
        
        # Evaluate momentum strategy
        if self.momentum_strategy and snapshots:
            for ob in snapshots:
                mom_signals = self.momentum_strategy.evaluate(market_id, ob.asset_id, ob)
                self._process_signals(mom_signals, "Momentum")
    
    def _process_signals(self, signals: List, strategy_name: str) -> None:
        """Process and execute trading signals."""
        for signal in signals:
            if hasattr(signal, 'signal_type'):
                # TradeSignal from ArbitrageStrategy
                self._execute_arb_signal(signal, strategy_name)
            else:
                # TradeSignal from MomentumStrategy
                self._execute_momentum_signal(signal, strategy_name)
    
    def _execute_arb_signal(self, signal: TradeSignal, strategy_name: str) -> None:
        """Execute arbitrage strategy signal."""
        # Check with risk manager
        if signal.signal_type == SignalType.ENTRY:
            # Create a mock opportunity for risk check
            from agents.arbitrage.types import ArbitrageOpportunity
            opp = ArbitrageOpportunity(
                market_id=signal.market_id,
                timestamp=signal.timestamp,
                outcomes=[signal.token_id],
                prices=[signal.price],
                total_cost=signal.price,
                potential_profit=0.01,  # Minimum check
                max_volume=signal.size
            )
            
            current_balance = 100.0  # Would fetch real balance
            if not self.risk_manager.check_opportunity(opp, current_balance):
                logger.debug(f"Signal rejected by risk manager: {signal.reason}")
                return
        
        if self.paper_trading:
            logger.info(f"[PAPER] {strategy_name} {signal.side} {signal.market_id[:20]}... @ {signal.price:.4f}")
            # Update position tracking
            self.arb_strategy.on_order_fill(signal, signal.price, signal.size)
        else:
            logger.info(f"[LIVE] Executing {signal.side} @ {signal.price}")
            # Real execution would go here
            success = True
            if success:
                self.arb_strategy.on_order_fill(signal, signal.price, signal.size)
                # Record trade with risk manager
                self.risk_manager.record_trade(0.0, True)  # P&L calculated later
    
    def _execute_momentum_signal(self, signal, strategy_name: str) -> None:
        """Execute momentum strategy signal."""
        if self.paper_trading:
            logger.info(f"[PAPER] {strategy_name} {signal.side} {signal.market_id[:20]}... @ {signal.price:.4f}")
            self.momentum_strategy.on_order_fill(signal, signal.price, signal.size)
        else:
            logger.info(f"[LIVE] Executing {signal.side} @ {signal.price}")
            # Real execution
            self.momentum_strategy.on_order_fill(signal, signal.price, signal.size)
            self.risk_manager.record_trade(0.0, True)
    
    def run(self):
        """Main bot loop."""
        logger.info("Starting Polymarket Arbitrage Bot...")
        self.running = True
        
        try:
            # Initial market scan
            self.scan_markets()
            
            while self.running:
                # Check circuit breaker
                metrics = self.risk_manager.get_risk_metrics()
                if not metrics.can_trade:
                    logger.warning("Trading paused by risk manager")
                    time.sleep(POLL_INTERVAL * 10)
                    continue
                
                # Periodic market scan
                markets = self.scan_markets()
                
                if not markets:
                    logger.debug("No tradable markets found")
                    time.sleep(POLL_INTERVAL)
                    continue
                
                # Process each market
                for market in markets:
                    try:
                        self.process_market(market)
                    except Exception as e:
                        logger.error(f"Error processing market {market['market_id'][:20]}: {e}")
                
                # Log status periodically
                self._log_status()
                
                time.sleep(POLL_INTERVAL)
        
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}")
        finally:
            self.stop()
    
    def _log_status(self):
        """Log periodic status update."""
        arb_summary = self.arb_strategy.get_portfolio_summary()
        
        logger.debug(
            f"Status - Arb Positions: {arb_summary.open_positions}, "
            f"P&L: ${arb_summary.total_pnl:.2f}"
        )
    
    def stop(self):
        """Stop the bot gracefully."""
        logger.info("Stopping bot...")
        self.running = False
        
        # Cleanup
        self.arb_strategy.cleanup()
        if self.momentum_strategy:
            self.momentum_strategy.cleanup()
        self.market_scanner.cleanup()
        if self.trader_monitor:
            self.trader_monitor.cleanup()
        
        # Print final summary
        self._print_summary()
        
        logger.info("Bot stopped successfully.")
    
    def _print_summary(self):
        """Print final trading summary."""
        logger.info("=" * 50)
        logger.info("TRADING SESSION SUMMARY")
        logger.info("=" * 50)
        
        arb_summary = self.arb_strategy.get_portfolio_summary()
        logger.info(f"Arbitrage Strategy:")
        logger.info(f"  Open Positions: {arb_summary.open_positions}")
        logger.info(f"  Closed Positions: {arb_summary.closed_positions}")
        logger.info(f"  Total P&L: ${arb_summary.total_pnl:.2f}")
        logger.info(f"  Win Rate: {arb_summary.win_rate*100:.1f}%")
        
        if self.momentum_strategy:
            mom_summary = self.momentum_strategy.get_portfolio_summary()
            logger.info(f"Momentum Strategy:")
            logger.info(f"  Open Positions: {mom_summary.open_positions}")
            logger.info(f"  Closed Positions: {mom_summary.closed_positions}")
            logger.info(f"  Total P&L: ${mom_summary.total_pnl:.2f}")
            logger.info(f"  Win Rate: {mom_summary.win_rate*100:.1f}%")
        
        risk_metrics = self.risk_manager.get_risk_metrics()
        logger.info(f"Risk Metrics:")
        logger.info(f"  Daily Trades: {risk_metrics.daily_trades}")
        logger.info(f"  Daily P&L: ${risk_metrics.daily_pnl:.2f}")
        
        logger.info("=" * 50)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info("Received shutdown signal")
    global bot
    if bot:
        bot.stop()
    sys.exit(0)


# Global bot instance for signal handler
bot: Optional[PolymarketArbBot] = None


def main():
    global bot
    
    logger.info("=" * 50)
    logger.info("POLYMARKET ARBITRAGE BOT")
    logger.info("=" * 50)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        bot = PolymarketArbBot()
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise


if __name__ == "__main__":
    main()

