import time
import logging
from datetime import datetime

from agents.arbitrage.types import ArbitrageOpportunity
from agents.polymarket.polymarket import Polymarket
from agents.arbitrage.config import WALLET_PRIVATE_KEY
from agents.arbitrage.logging_config import save_trade
from py_clob_client.clob_types import OrderArgs
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY

logger = logging.getLogger("ExecutionEngine")

class ExecutionEngine:
    def __init__(self):
        # We reuse the existing Polymarket class for its Web3 and ClobClient setup
        # But we need to ensure it's initialized correctly with our key
        self.poly = Polymarket()
        self.can_trade = bool(WALLET_PRIVATE_KEY)
        self.trade_count = 0

    def execute_arbitrage(self, opportunity: ArbitrageOpportunity, size: float) -> bool:
        """
        Executes the arbitrage trade by buying all outcomes.
        Returns True if all orders were placed successfully.
        Saves trade data to logs/trades/ for analysis.
        """
        if not self.can_trade:
            logger.info("Execution skipped: Read-only mode (No private key).")
            return False

        logger.info(f"Executing Arbitrage on {opportunity.market_id} with size {size}")

        success = True
        trade_results = []
        start_time = time.time()

        for i, outcome_id in enumerate(opportunity.outcomes):
            price = opportunity.prices[i]
            order_result = {
                "outcome_id": outcome_id,
                "price": price,
                "size": size,
                "side": "BUY",
                "status": "pending",
                "response": None,
                "error": None,
                "timestamp": datetime.now().isoformat()
            }
            
            try:
                logger.info(f"Buying {outcome_id} @ {price}, size {size}")
                resp = self.poly.execute_order(
                    price=price,
                    size=size,
                    side=BUY,
                    token_id=outcome_id
                )
                order_result["status"] = "success"
                order_result["response"] = str(resp)
                logger.info(f"Order placed: {resp}")
            except Exception as e:
                order_result["status"] = "failed"
                order_result["error"] = str(e)
                logger.error(f"Failed to place order for {outcome_id}: {e}")
                success = False
            
            trade_results.append(order_result)

        # Save complete trade data
        trade_data = {
            "trade_id": f"ARB_{self.trade_count:05d}",
            "type": "ARBITRAGE",
            "market_id": opportunity.market_id,
            "total_cost": opportunity.total_cost,
            "expected_profit": opportunity.potential_profit,
            "expected_profit_pct": opportunity.potential_profit * 100,
            "size": size,
            "execution_time_ms": (time.time() - start_time) * 1000,
            "success": success,
            "orders": trade_results,
            "timestamp": datetime.now().isoformat()
        }
        
        filepath = save_trade(trade_data)
        logger.info(f"交易记录已保存: {filepath}")
        self.trade_count += 1

        return success

    def execute_signal(self, signal, size: float) -> bool:
        """
        Execute a trade signal and save to logs.
        """
        if not self.can_trade:
            logger.info("Execution skipped: Read-only mode")
            return False
        
        start_time = time.time()
        success = False
        response = None
        error = None
        
        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            side = BUY if signal.side == 'BUY' else SELL
            
            resp = self.poly.execute_order(
                price=signal.price,
                size=size,
                side=side,
                token_id=signal.token_id
            )
            success = True
            response = str(resp)
            logger.info(f"Signal executed: {resp}")
        except Exception as e:
            error = str(e)
            logger.error(f"Signal execution failed: {e}")
        
        # Save trade data
        trade_data = {
            "trade_id": f"SIG_{self.trade_count:05d}",
            "type": signal.signal_type.value,
            "market_id": signal.market_id,
            "token_id": signal.token_id,
            "side": signal.side,
            "price": signal.price,
            "size": size,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "execution_time_ms": (time.time() - start_time) * 1000,
            "success": success,
            "response": response,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        filepath = save_trade(trade_data)
        logger.info(f"交易记录已保存: {filepath}")
        self.trade_count += 1
        
        return success
