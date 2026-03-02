#!/usr/bin/env python3
"""
Scale-In Eligibility Module for Trading System V3

Determines if a position is eligible for scale-in based on V2 criteria:
1. Position must be in profit (pnl_pct > 0)
2. DUAL-PATH APPROACH:
   - PRIMARY (Strict): TP2 has been hit (trailing is ACTIVE) + ADX >= 25
   - SECONDARY (Permissive): TP1 hit + SL at break-even + ADX >= 30 + funding favorable
3. Total position doesn't exceed max_notional_usdt

Uses decreasing scale sizes: 3% → 1.5% → 0.75% of available gap.

This module implements the Gap 2 fix: Added symbol field to ScaleInEligibility dataclass.
"""

import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScaleInEligibility:
    """
    Eligibility result for scale-in decision.
    
    Attributes:
        symbol: Trading symbol (e.g., 'ETHUSDT')
        can_scale_in: Whether scale-in is allowed
        path_used: 'PRIMARY' or 'SECONDARY' if eligible, None otherwise
        adx_value: Current ADX momentum indicator
        funding_rate: Current funding rate
        price_distance_pct: Distance from last scale-in price
        margin_ratio: Current margin ratio (0.0-1.0)
        pyramid_level: Current pyramid scale level
        reason: Human-readable reason for decision
    """
    symbol: str
    can_scale_in: bool
    path_used: Optional[str]  # PRIMARY or SECONDARY
    adx_value: float
    funding_rate: float
    price_distance_pct: float
    margin_ratio: float
    pyramid_level: int
    reason: str


class ScaleInEligibilityChecker:
    """
    Determines if a position is eligible for scale-in based on V2 criteria.
    """
    
    # Configuration (should come from config file)
    ADX_THRESHOLD_PRIMARY = 25
    ADX_THRESHOLD_SECONDARY = 30
    MIN_PRICE_DISTANCE_PCT = 1.0
    MARGIN_RATIO_THRESHOLD = 0.70  # 70% of liquidation
    PYRAMID_MAX_LEVELS = 3
    COOLDOWN_MINUTES = 15
    
    # Diminishing scale sizes
    SCALE_SIZES = [0.03, 0.015, 0.0075]  # 3% → 1.5% → 0.75%
    
    def check_eligibility(
        self,
        position: dict,
        current_price: float,
        adx_value: float,
        funding_rate: float,
        margin_ratio: float
    ) -> ScaleInEligibility:
        """
        Check if position is eligible for scale-in.
        
        Args:
            position: Current position data from DB
            current_price: Current market price
            adx_value: ADX momentum indicator
            funding_rate: Current funding rate
            margin_ratio: Current margin ratio (0.0-1.0)
        
        Returns:
            ScaleInEligibility with decision details
        """
        symbol = position["symbol"]
        pyramid_level = position.get("pyramid_scales", 0)
        last_scale_price = position.get("last_scale_price")
        
        # Check pyramid level limit
        if pyramid_level >= self.PYRAMID_MAX_LEVELS:
            return ScaleInEligibility(
                symbol=symbol,
                can_scale_in=False,
                path_used=None,
                adx_value=adx_value,
                funding_rate=funding_rate,
                price_distance_pct=0.0,
                margin_ratio=margin_ratio,
                pyramid_level=pyramid_level,
                reason=f"Max pyramid level {self.PYRAMID_MAX_LEVELS} reached"
            )
        
        # Check margin ratio
        if margin_ratio >= self.MARGIN_RATIO_THRESHOLD:
            return ScaleInEligibility(
                symbol=symbol,
                can_scale_in=False,
                path_used=None,
                adx_value=adx_value,
                funding_rate=funding_rate,
                price_distance_pct=0.0,
                margin_ratio=margin_ratio,
                pyramid_level=pyramid_level,
                reason=f"Margin ratio {margin_ratio:.1%} >= {self.MARGIN_RATIO_THRESHOLD:.1%}"
            )
        
        # Check price distance from last scale
        if last_scale_price:
            price_distance_pct = abs(current_price - last_scale_price) / last_scale_price * 100
            if price_distance_pct < self.MIN_PRICE_DISTANCE_PCT:
                return ScaleInEligibility(
                    symbol=symbol,
                    can_scale_in=False,
                    path_used=None,
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"Price distance {price_distance_pct:.2f}% < {self.MIN_PRICE_DISTANCE_PCT}%"
                )
        else:
            price_distance_pct = 100.0  # First scale-in
        
        # Check cooldown
        last_scale_time = position.get("last_scale_time")
        if last_scale_time:
            minutes_since_last = (time.time() * 1000 - last_scale_time) / 60000
            if minutes_since_last < self.COOLDOWN_MINUTES:
                return ScaleInEligibility(
                    symbol=symbol,
                    can_scale_in=False,
                    path_used=None,
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"Cooldown {minutes_since_last:.0f}m < {self.COOLDOWN_MINUTES}m"
                )
        
        # Determine path
        tp2_hit = position.get("tp2_hit", False)
        tp1_hit = position.get("tp1_hit", False)
        sl_at_break_even = position.get("sl_at_break_even", False)
        
        # PRIMARY path: TP2 hit + ADX >= 25
        if tp2_hit and adx_value >= self.ADX_THRESHOLD_PRIMARY:
            # Also check funding is not negative
            if funding_rate >= 0:
                return ScaleInEligibility(
                    symbol=symbol,
                    can_scale_in=True,
                    path_used="PRIMARY",
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"PRIMARY: TP2 hit + ADX {adx_value} >= {self.ADX_THRESHOLD_PRIMARY}"
                )
        
        # SECONDARY path: TP1 hit + SL at break-even + ADX >= 30 + funding favorable
        if tp1_hit and sl_at_break_even and adx_value >= self.ADX_THRESHOLD_SECONDARY:
            if funding_rate >= 0:
                return ScaleInEligibility(
                    symbol=symbol,
                    can_scale_in=True,
                    path_used="SECONDARY",
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"SECONDARY: TP1 hit + BE + ADX {adx_value} >= {self.ADX_THRESHOLD_SECONDARY}"
                )
        
        return ScaleInEligibility(
            symbol=symbol,
            can_scale_in=False,
            path_used=None,
            adx_value=adx_value,
            funding_rate=funding_rate,
            price_distance_pct=price_distance_pct,
            margin_ratio=margin_ratio,
            pyramid_level=pyramid_level,
            reason="No path met: TP2+ADX25 or TP1+BE+ADX30 required"
        )
    
    def log_eligibility_check(self, position_uuid: str, eligibility: ScaleInEligibility):
        """
        Log eligibility check to scale_in_conditions_log.
        
        Args:
            position_uuid: Unique identifier for the position
            eligibility: ScaleInEligibility result from check_eligibility
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO scale_in_conditions_log 
                   (timestamp, position_uuid, symbol, can_scale_in, path_used,
                    adx_value, funding_rate, price_distance_pct, margin_ratio,
                    pyramid_level, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time() * 1000), position_uuid, eligibility.symbol,
                 1 if eligibility.can_scale_in else 0, eligibility.path_used,
                 eligibility.adx_value, eligibility.funding_rate,
                 eligibility.price_distance_pct, eligibility.margin_ratio,
                 eligibility.pyramid_level, eligibility.reason)
            )
            conn.commit()
            logger.debug(f"Logged eligibility check for position {position_uuid}, symbol={eligibility.symbol}")
    
    def get_scale_size(self, pyramid_level: int) -> float:
        """Get scale size for given pyramid level."""
        if pyramid_level < len(self.SCALE_SIZES):
            return self.SCALE_SIZES[pyramid_level]
        return self.SCALE_SIZES[-1]  # Return minimum


# Module-level instance for convenience
eligibility_checker = ScaleInEligibilityChecker()
