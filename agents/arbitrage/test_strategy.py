from agents.arbitrage.strategy import ArbitrageStrategy
from agents.arbitrage.types import OrderbookSnapshot, OrderSummary
import time

def test_strategy():
    print("Initializing Strategy...")
    strategy = ArbitrageStrategy(min_profit=0.005) # 0.5%

    # Mock Data for a NegRisk opportunity
    # Market with 2 outcomes: Yes and No.
    # If Ask(Yes) = 0.40 and Ask(No) = 0.55 -> Sum = 0.95 -> Profit = 0.05 (5%)

    ts = time.time()

    # Orderbook for Outcome A (Yes)
    ob_a = OrderbookSnapshot(
        market_id="market_1",
        asset_id="token_a",
        bids=[],
        asks=[OrderSummary(price=0.40, size=100.0)],
        timestamp=ts,
        best_ask=0.40
    )

    # Orderbook for Outcome B (No)
    ob_b = OrderbookSnapshot(
        market_id="market_1",
        asset_id="token_b",
        bids=[],
        asks=[OrderSummary(price=0.55, size=50.0)], # Less liquidity here
        timestamp=ts,
        best_ask=0.55
    )

    print("Testing Detection Logic...")
    opportunity = strategy.detect_arbitrage("market_1", [ob_a, ob_b])

    if opportunity:
        print("Arbitrage Detected!")
        print(f"Market: {opportunity.market_id}")
        print(f"Total Cost: {opportunity.total_cost}")
        print(f"Potential Profit: {opportunity.potential_profit}")
        print(f"Max Volume: {opportunity.max_volume}")

        # Validation
        assert abs(opportunity.total_cost - 0.95) < 1e-9, f"Expected 0.95, got {opportunity.total_cost}"
        assert abs(opportunity.potential_profit - 0.05) < 1e-9, f"Expected 0.05, got {opportunity.potential_profit}"
        assert opportunity.max_volume == 50.0
        print("Validation Passed!")
    else:
        print("No opportunity detected (Failed).")

if __name__ == "__main__":
    test_strategy()
