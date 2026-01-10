"""
Rebalancer Service for Polymarket Arbitrage

Based on poly-sdk-main/src/services/arbitrage-service.ts rebalancer

Features:
- Automatic USDC/Token ratio management
- YES/NO imbalance detection and fixing
- Split USDC → tokens when USDC ratio too high
- Merge tokens → USDC when USDC ratio too low

Usage:
    from agents.arbitrage.rebalancer import Rebalancer
    
    rebalancer = Rebalancer(
        min_usdc_ratio=0.2,
        max_usdc_ratio=0.8,
        target_usdc_ratio=0.5
    )
    
    action = rebalancer.calculate_action(usdc=100, yes_tokens=50, no_tokens=45)
    if action.type != 'none':
        print(f"Recommended: {action.type} {action.amount}")
"""

import logging
from typing import Optional, Dict, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("Rebalancer")


class RebalanceActionType(Enum):
    """Types of rebalance actions."""
    NONE = "none"
    SPLIT = "split"      # USDC → YES + NO tokens
    MERGE = "merge"      # YES + NO tokens → USDC
    SELL_YES = "sell_yes"  # Sell excess YES tokens
    SELL_NO = "sell_no"    # Sell excess NO tokens


@dataclass
class RebalanceAction:
    """Recommended rebalance action."""
    type: RebalanceActionType
    amount: float
    reason: str
    priority: int  # Higher priority = more urgent
    
    @property
    def is_needed(self) -> bool:
        return self.type != RebalanceActionType.NONE


@dataclass
class RebalanceResult:
    """Result of rebalance execution."""
    success: bool
    action: RebalanceAction
    tx_hash: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BalanceState:
    """Current balance state."""
    usdc: float
    yes_tokens: float
    no_tokens: float
    
    @property
    def paired_tokens(self) -> float:
        """Paired YES+NO tokens (can be merged)."""
        return min(self.yes_tokens, self.no_tokens)
    
    @property
    def total_capital(self) -> float:
        """Total capital = USDC + paired tokens."""
        return self.usdc + self.paired_tokens
    
    @property
    def usdc_ratio(self) -> float:
        """USDC ratio (0-1)."""
        if self.total_capital == 0:
            return 0.5
        return self.usdc / self.total_capital
    
    @property
    def token_imbalance(self) -> float:
        """YES - NO token difference."""
        return self.yes_tokens - self.no_tokens


class Rebalancer:
    """
    Automatic portfolio rebalancer for Polymarket arbitrage.
    
    Maintains USDC/token ratio within configured bounds:
    - If USDC ratio < min_usdc_ratio: Merge tokens to recover USDC
    - If USDC ratio > max_usdc_ratio: Split USDC to create tokens
    - If YES/NO imbalance too high: Sell excess tokens
    """
    
    def __init__(
        self,
        min_usdc_ratio: float = 0.2,
        max_usdc_ratio: float = 0.8,
        target_usdc_ratio: float = 0.5,
        imbalance_threshold: float = 5.0,
        min_trade_size: float = 5.0,
        cooldown_seconds: float = 30.0
    ):
        """
        Initialize rebalancer.
        
        Args:
            min_usdc_ratio: Minimum USDC ratio (default 20%)
            max_usdc_ratio: Maximum USDC ratio (default 80%)
            target_usdc_ratio: Target when rebalancing (default 50%)
            imbalance_threshold: Max YES/NO difference before fixing
            min_trade_size: Minimum trade size in USDC
            cooldown_seconds: Cooldown between rebalance actions
        """
        self.min_usdc_ratio = min_usdc_ratio
        self.max_usdc_ratio = max_usdc_ratio
        self.target_usdc_ratio = target_usdc_ratio
        self.imbalance_threshold = imbalance_threshold
        self.min_trade_size = min_trade_size
        self.cooldown_seconds = cooldown_seconds
        
        # State tracking
        self._last_rebalance_time: float = 0
        self._total_capital: float = 0
        
        # Event handlers
        self._on_rebalance: Optional[Callable[[RebalanceResult], None]] = None
    
    def on_rebalance(self, handler: Callable[[RebalanceResult], None]):
        """Register rebalance event handler."""
        self._on_rebalance = handler
    
    def calculate_action(
        self,
        usdc: float,
        yes_tokens: float,
        no_tokens: float
    ) -> RebalanceAction:
        """
        Calculate recommended rebalance action.
        
        Priority order:
        1. Fix YES/NO imbalance (highest - risk control)
        2. Split if USDC ratio too high
        3. Merge if USDC ratio too low
        
        Args:
            usdc: Current USDC balance
            yes_tokens: Current YES token balance
            no_tokens: Current NO token balance
        
        Returns:
            RebalanceAction with type, amount, and reason
        """
        state = BalanceState(usdc=usdc, yes_tokens=yes_tokens, no_tokens=no_tokens)
        
        if state.total_capital == 0:
            return RebalanceAction(
                type=RebalanceActionType.NONE,
                amount=0,
                reason="No capital",
                priority=0
            )
        
        self._total_capital = state.total_capital
        
        # Priority 1: Fix YES/NO imbalance (risk control)
        imbalance = state.token_imbalance
        if abs(imbalance) > self.imbalance_threshold:
            if imbalance > 0:
                # Too many YES tokens, sell some
                sell_amount = min(imbalance, yes_tokens * 0.5)
                if sell_amount >= self.min_trade_size:
                    return RebalanceAction(
                        type=RebalanceActionType.SELL_YES,
                        amount=round(sell_amount, 2),
                        reason=f"Risk: YES > NO by {imbalance:.2f}",
                        priority=100
                    )
            else:
                # Too many NO tokens, sell some
                sell_amount = min(-imbalance, no_tokens * 0.5)
                if sell_amount >= self.min_trade_size:
                    return RebalanceAction(
                        type=RebalanceActionType.SELL_NO,
                        amount=round(sell_amount, 2),
                        reason=f"Risk: NO > YES by {-imbalance:.2f}",
                        priority=100
                    )
        
        # Priority 2: USDC ratio too high → Split to create tokens
        if state.usdc_ratio > self.max_usdc_ratio:
            target_usdc = self._total_capital * self.target_usdc_ratio
            excess_usdc = usdc - target_usdc
            split_amount = min(excess_usdc * 0.5, usdc * 0.3)
            
            if split_amount >= self.min_trade_size:
                return RebalanceAction(
                    type=RebalanceActionType.SPLIT,
                    amount=round(split_amount, 2),
                    reason=f"USDC {state.usdc_ratio*100:.0f}% > {self.max_usdc_ratio*100:.0f}% max",
                    priority=50
                )
        
        # Priority 3: USDC ratio too low → Merge tokens to recover USDC
        if state.usdc_ratio < self.min_usdc_ratio and state.paired_tokens >= self.min_trade_size:
            target_usdc = self._total_capital * self.target_usdc_ratio
            needed_usdc = target_usdc - usdc
            merge_amount = min(needed_usdc * 0.5, state.paired_tokens * 0.5)
            
            if merge_amount >= self.min_trade_size:
                return RebalanceAction(
                    type=RebalanceActionType.MERGE,
                    amount=round(merge_amount, 2),
                    reason=f"USDC {state.usdc_ratio*100:.0f}% < {self.min_usdc_ratio*100:.0f}% min",
                    priority=50
                )
        
        return RebalanceAction(
            type=RebalanceActionType.NONE,
            amount=0,
            reason="Balanced",
            priority=0
        )
    
    def get_status(
        self,
        usdc: float,
        yes_tokens: float,
        no_tokens: float
    ) -> Dict:
        """
        Get current balance status and recommendations.
        
        Returns:
            Dict with status information
        """
        state = BalanceState(usdc=usdc, yes_tokens=yes_tokens, no_tokens=no_tokens)
        action = self.calculate_action(usdc, yes_tokens, no_tokens)
        
        return {
            'usdc': usdc,
            'yes_tokens': yes_tokens,
            'no_tokens': no_tokens,
            'paired_tokens': state.paired_tokens,
            'total_capital': state.total_capital,
            'usdc_ratio': state.usdc_ratio,
            'usdc_ratio_pct': f"{state.usdc_ratio*100:.1f}%",
            'token_imbalance': state.token_imbalance,
            'is_balanced': not action.is_needed,
            'recommended_action': {
                'type': action.type.value,
                'amount': action.amount,
                'reason': action.reason,
                'priority': action.priority
            },
            'config': {
                'min_usdc_ratio': self.min_usdc_ratio,
                'max_usdc_ratio': self.max_usdc_ratio,
                'target_usdc_ratio': self.target_usdc_ratio,
                'imbalance_threshold': self.imbalance_threshold
            }
        }
    
    def format_status(
        self,
        usdc: float,
        yes_tokens: float,
        no_tokens: float
    ) -> str:
        """
        Format balance status as readable string.
        """
        status = self.get_status(usdc, yes_tokens, no_tokens)
        
        lines = [
            "📊 Balance Status:",
            f"  USDC: ${status['usdc']:.2f}",
            f"  YES:  {status['yes_tokens']:.2f}",
            f"  NO:   {status['no_tokens']:.2f}",
            f"  Paired: {status['paired_tokens']:.2f}",
            f"  Total: ${status['total_capital']:.2f}",
            f"  USDC Ratio: {status['usdc_ratio_pct']}",
        ]
        
        if status['token_imbalance'] != 0:
            lines.append(f"  Imbalance: {status['token_imbalance']:+.2f}")
        
        action = status['recommended_action']
        if action['type'] != 'none':
            lines.append(f"\n🔄 Recommended: {action['type'].upper()} ${action['amount']:.2f}")
            lines.append(f"   Reason: {action['reason']}")
        else:
            lines.append("\n✅ Portfolio is balanced")
        
        return "\n".join(lines)


# Convenience function
def analyze_balance(
    usdc: float,
    yes_tokens: float,
    no_tokens: float,
    min_usdc_ratio: float = 0.2,
    max_usdc_ratio: float = 0.8
) -> Dict:
    """
    Quick analysis of balance state.
    
    Args:
        usdc: USDC balance
        yes_tokens: YES token balance
        no_tokens: NO token balance
        min_usdc_ratio: Min acceptable USDC ratio
        max_usdc_ratio: Max acceptable USDC ratio
    
    Returns:
        Dict with analysis results
    """
    rebalancer = Rebalancer(
        min_usdc_ratio=min_usdc_ratio,
        max_usdc_ratio=max_usdc_ratio
    )
    return rebalancer.get_status(usdc, yes_tokens, no_tokens)
