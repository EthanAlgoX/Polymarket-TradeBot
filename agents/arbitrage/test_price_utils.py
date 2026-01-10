"""
Test cases for price_utils.py

Tests the effective price calculation and arbitrage detection.
"""

import unittest
from agents.arbitrage.price_utils import (
    get_effective_prices,
    check_arbitrage,
    round_price,
    round_size,
    EffectivePrices,
    ArbitrageInfo,
    get_arb_summary
)


class TestEffectivePrices(unittest.TestCase):
    """Test effective price calculation."""
    
    def test_basic_effective_prices(self):
        """Test basic effective price calculation."""
        # Typical market: YES.ask=0.55, YES.bid=0.52, NO.ask=0.48, NO.bid=0.45
        eff = get_effective_prices(
            yes_ask=0.55,
            yes_bid=0.52,
            no_ask=0.48,
            no_bid=0.45
        )
        
        # effective_buy_yes = min(0.55, 1 - 0.45) = min(0.55, 0.55) = 0.55
        self.assertAlmostEqual(eff.effective_buy_yes, 0.55, places=4)
        
        # effective_buy_no = min(0.48, 1 - 0.52) = min(0.48, 0.48) = 0.48
        self.assertAlmostEqual(eff.effective_buy_no, 0.48, places=4)
        
        # Long cost = 0.55 + 0.48 = 1.03 (no arbitrage)
        self.assertAlmostEqual(eff.long_cost, 1.03, places=4)
        self.assertLess(eff.long_profit, 0)  # No profit
    
    def test_long_arb_opportunity(self):
        """Test detection of long arbitrage."""
        # Long arb exists when effective long cost < 1
        # YES.ask=0.50, YES.bid=0.48, NO.ask=0.48, NO.bid=0.46
        eff = get_effective_prices(
            yes_ask=0.50,
            yes_bid=0.48,
            no_ask=0.48,
            no_bid=0.46
        )
        
        # effective_buy_yes = min(0.50, 1 - 0.46) = min(0.50, 0.54) = 0.50
        self.assertAlmostEqual(eff.effective_buy_yes, 0.50, places=4)
        
        # effective_buy_no = min(0.48, 1 - 0.48) = min(0.48, 0.52) = 0.48
        self.assertAlmostEqual(eff.effective_buy_no, 0.48, places=4)
        
        # Long cost = 0.50 + 0.48 = 0.98 (arbitrage!)
        self.assertAlmostEqual(eff.long_cost, 0.98, places=4)
        self.assertGreater(eff.long_profit, 0)  # Profit!
        self.assertAlmostEqual(eff.long_profit, 0.02, places=4)  # 2% profit
    
    def test_short_arb_opportunity(self):
        """Test detection of short arbitrage."""
        # Short arb when effective sell revenue > 1
        # YES.ask=0.48, YES.bid=0.52, NO.ask=0.46, NO.bid=0.50
        eff = get_effective_prices(
            yes_ask=0.48,
            yes_bid=0.52,
            no_ask=0.46,
            no_bid=0.50
        )
        
        # effective_sell_yes = max(0.52, 1 - 0.46) = max(0.52, 0.54) = 0.54
        self.assertAlmostEqual(eff.effective_sell_yes, 0.54, places=4)
        
        # effective_sell_no = max(0.50, 1 - 0.48) = max(0.50, 0.52) = 0.52
        self.assertAlmostEqual(eff.effective_sell_no, 0.52, places=4)
        
        # Short revenue = 0.54 + 0.52 = 1.06 (arbitrage!)
        self.assertAlmostEqual(eff.short_revenue, 1.06, places=4)
        self.assertGreater(eff.short_profit, 0)  # Profit!
    
    def test_mirror_property(self):
        """Test that mirror orderbook property is correctly handled."""
        # In Polymarket: Buy YES @ 0.60 = Sell NO @ 0.40
        # So if NO.bid = 0.40, effective buy YES = min(YES.ask, 1 - 0.40) = min(YES.ask, 0.60)
        
        eff = get_effective_prices(
            yes_ask=0.65,  # Direct YES is expensive
            yes_bid=0.55,
            no_ask=0.35,
            no_bid=0.40   # But 1 - NO.bid = 0.60 is cheaper!
        )
        
        # effective_buy_yes should use the mirror: min(0.65, 0.60) = 0.60
        self.assertAlmostEqual(eff.effective_buy_yes, 0.60, places=4)


class TestCheckArbitrage(unittest.TestCase):
    """Test arbitrage detection."""
    
    def test_no_arbitrage(self):
        """Test when no arbitrage exists."""
        arb = check_arbitrage(
            yes_ask=0.55,
            yes_bid=0.52,
            no_ask=0.48,
            no_bid=0.45,
            threshold=0.003
        )
        self.assertIsNone(arb)
    
    def test_long_arbitrage_detected(self):
        """Test long arbitrage detection."""
        arb = check_arbitrage(
            yes_ask=0.50,
            yes_bid=0.48,
            no_ask=0.48,
            no_bid=0.46,
            threshold=0.003
        )
        self.assertIsNotNone(arb)
        self.assertEqual(arb.type, 'long')
        self.assertGreater(arb.profit, 0.01)  # > 1% profit
    
    def test_threshold_filtering(self):
        """Test that threshold filters out small opportunities."""
        # Small arb of ~1%
        arb_low = check_arbitrage(
            yes_ask=0.50,
            yes_bid=0.48,
            no_ask=0.49,
            no_bid=0.47,
            threshold=0.001  # Low threshold
        )
        
        arb_high = check_arbitrage(
            yes_ask=0.50,
            yes_bid=0.48,
            no_ask=0.49,
            no_bid=0.47,
            threshold=0.02  # High threshold
        )
        
        # Same prices, different thresholds
        self.assertIsNotNone(arb_low)  # Should pass low threshold
        self.assertIsNone(arb_high)    # Should fail high threshold


class TestPriceRounding(unittest.TestCase):
    """Test price and size rounding."""
    
    def test_round_price(self):
        """Test price rounding."""
        self.assertEqual(round_price(0.5555, 2), 0.56)
        self.assertEqual(round_price(0.5551, 3), 0.555)  # Python banker's rounding
        self.assertEqual(round_price(0.0001, 4), 0.001)  # Clamped to min
        self.assertEqual(round_price(0.9999, 4), 0.999)  # Clamped to max
    
    def test_round_size(self):
        """Test size rounding."""
        self.assertEqual(round_size(10.567), 10.57)
        self.assertEqual(round_size(0.001), 0.0)


class TestArbSummary(unittest.TestCase):
    """Test arbitrage summary function."""
    
    def test_summary_structure(self):
        """Test that summary contains all expected fields."""
        summary = get_arb_summary(
            yes_ask=0.55,
            yes_bid=0.52,
            no_ask=0.48,
            no_bid=0.45
        )
        
        expected_keys = [
            'long_cost', 'long_profit', 'long_profit_pct',
            'short_revenue', 'short_profit', 'short_profit_pct',
            'effective_buy_yes', 'effective_buy_no',
            'effective_sell_yes', 'effective_sell_no',
            'has_long_arb', 'has_short_arb'
        ]
        
        for key in expected_keys:
            self.assertIn(key, summary)


if __name__ == '__main__':
    unittest.main()
