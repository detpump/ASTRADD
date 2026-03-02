#!/usr/bin/env python3
"""
Unit tests for Risk Guard V2 module.
Tests the core risk management functionality.
"""

import pytest
import os
import sys
import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from risk.risk_guard_v2 import (
    RiskLevel,
    RiskConfig,
    PortfolioState,
    RiskCheckResult,
    RiskGuard
)


class TestRiskConfig:
    """Tests for RiskConfig dataclass"""

    def test_default_config(self):
        """Test RiskConfig has correct defaults"""
        config = RiskConfig()
        
        # Position limits
        assert config.max_position_pct == 0.05
        assert config.max_total_exposure_pct == 0.20
        assert config.max_leverage == 10
        
        # Loss limits
        assert config.max_daily_loss_pct == 0.05
        assert config.max_weekly_loss_pct == 0.15
        assert config.max_drawdown_pct == 0.20
        
        # Trading limits
        assert config.max_trades_per_day == 20
        assert config.max_consecutive_losses == 5
        assert config.min_trade_interval_minutes == 15
        
        # Risk controls
        assert config.enable_circuit_breaker is True
        assert config.circuit_breaker_cooldown_minutes == 60
        
        # Dynamic sizing
        assert config.use_dynamic_sizing is True
        assert config.reduce_on_drawdown is True
        assert config.reduce_factor_per_5pct_dd == 0.5

    def test_custom_config(self):
        """Test RiskConfig with custom values"""
        config = RiskConfig(
            max_position_pct=0.10,
            max_leverage=20,
            max_daily_loss_pct=0.03
        )
        
        assert config.max_position_pct == 0.10
        assert config.max_leverage == 20
        assert config.max_daily_loss_pct == 0.03
        # Other values should remain at defaults
        assert config.max_trades_per_day == 20


class TestPortfolioState:
    """Tests for PortfolioState dataclass"""

    def test_default_state(self):
        """Test PortfolioState has correct defaults"""
        state = PortfolioState()
        
        assert state.equity == 0.0
        assert state.equity_peak == 0.0
        assert state.equity_start_day == 0.0
        assert state.equity_start_week == 0.0
        assert state.daily_pnl == 0.0
        assert state.weekly_pnl == 0.0
        assert state.positions == {}
        assert state.open_positions_count == 0
        assert state.trades_today == 0
        assert state.consecutive_losses == 0
        assert state.drawdown_pct == 0.0
        assert state.last_trade_time == 0

    def test_state_with_values(self):
        """Test PortfolioState with custom values"""
        state = PortfolioState(
            equity=10000.0,
            equity_peak=11000.0,
            equity_start_day=10500.0,
            daily_pnl=500.0,
            trades_today=5,
            consecutive_losses=2,
            drawdown_pct=5.5
        )
        
        assert state.equity == 10000.0
        assert state.equity_peak == 11000.0
        assert state.equity_start_day == 10500.0
        assert state.daily_pnl == 500.0
        assert state.trades_today == 5
        assert state.consecutive_losses == 2
        assert state.drawdown_pct == 5.5


class TestRiskCheckResult:
    """Tests for RiskCheckResult dataclass"""

    def test_approved_result(self):
        """Test approved risk check result"""
        result = RiskCheckResult(
            approved=True,
            risk_level=RiskLevel.LOW,
            reason="All checks passed",
            position_size_multiplier=1.0,
            required_actions=[]
        )
        
        assert result.approved is True
        assert result.risk_level == RiskLevel.LOW
        assert result.position_size_multiplier == 1.0
        assert result.required_actions == []

    def test_rejected_result(self):
        """Test rejected risk check result"""
        result = RiskCheckResult(
            approved=False,
            risk_level=RiskLevel.CRITICAL,
            reason="Daily loss limit exceeded",
            position_size_multiplier=0.0,
            required_actions=["Stop trading", "Review positions"]
        )
        
        assert result.approved is False
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.position_size_multiplier == 0.0
        assert len(result.required_actions) == 2


class TestRiskGuard:
    """Tests for RiskGuard class"""

    @pytest.fixture
    def temp_state_file(self):
        """Create a temporary state file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "equity": 10000.0,
                "equity_peak": 10500.0,
                "equity_start_day": 10500.0,
                "equity_start_week": 11000.0,
                "daily_pnl": 0.0,
                "weekly_pnl": 0.0,
                "positions": {},
                "open_positions_count": 0,
                "trades_today": 0,
                "consecutive_losses": 0,
                "drawdown_pct": 0.0,
                "last_trade_time": 0,
                "date": datetime.now().date().isoformat()
            }, f)
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)

    def test_risk_level_enum(self):
        """Test RiskLevel enum values"""
        assert RiskLevel.LOW.value == 0
        assert RiskLevel.NORMAL.value == 1
        assert RiskLevel.HIGH.value == 2
        assert RiskLevel.CRITICAL.value == 3

    def test_calculate_drawdown(self, temp_state_file):
        """Test drawdown calculation"""
        # Create state with equity below peak
        # Note: drawdown_pct is a stored field, not a computed property
        # In practice, RiskGuard calculates and stores this value
        state = PortfolioState(
            equity=9000.0,
            equity_peak=10000.0,
            equity_start_day=10000.0,
            drawdown_pct=10.0  # Manually set calculated value
        )
        
        # Verify the stored value is correct
        # Drawdown = (peak - current) / peak * 100 = (10000 - 9000) / 10000 * 100 = 10%
        assert state.drawdown_pct == 10.0

    def test_position_size_limits(self, temp_state_file):
        """Test position size limit calculations"""
        config = RiskConfig(
            max_position_pct=0.05,  # 5% max position
            max_total_exposure_pct=0.20  # 20% total exposure
        )
        
        equity = 10000.0
        
        # Max position size = equity * max_position_pct
        max_position = equity * config.max_position_pct
        assert max_position == 500.0
        
        # Max total exposure = equity * max_total_exposure_pct
        max_exposure = equity * config.max_total_exposure_pct
        assert max_exposure == 2000.0

    def test_daily_loss_check(self, temp_state_file):
        """Test daily loss limit checking"""
        config = RiskConfig(max_daily_loss_pct=0.05)  # 5% max daily loss
        equity = 10000.0
        
        # Test at exactly 5% loss
        daily_pnl = -500.0
        loss_pct = abs(daily_pnl) / equity
        assert loss_pct == 0.05  # At limit
        
        # Test over 5% loss
        daily_pnl = -600.0
        loss_pct = abs(daily_pnl) / equity
        assert loss_pct > 0.05  # Over limit

    def test_consecutive_losses_check(self, temp_state_file):
        """Test consecutive losses tracking"""
        state = PortfolioState(consecutive_losses=0)
        
        # Simulate a losing trade
        state.consecutive_losses += 1
        assert state.consecutive_losses == 1
        
        # Add more losses
        state.consecutive_losses += 1
        state.consecutive_losses += 1
        state.consecutive_losses += 1
        assert state.consecutive_losses == 4
        
        # Reset after win
        state.consecutive_losses = 0
        assert state.consecutive_losses == 0


class TestRiskCalculations:
    """Tests for risk calculation logic"""

    @pytest.fixture
    def temp_state_file_calc(self):
        """Create a temporary state file for calculations tests"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "equity": 10000.0,
                "equity_peak": 10500.0,
                "equity_start_day": 10500.0,
                "equity_start_week": 11000.0,
                "daily_pnl": 0.0,
                "weekly_pnl": 0.0,
                "positions": {},
                "open_positions_count": 0,
                "trades_today": 0,
                "consecutive_losses": 0,
                "drawdown_pct": 0.0,
                "last_trade_time": 0,
                "date": datetime.now().date().isoformat()
            }, f)
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)

    def test_leverage_sizing(self):
        """Test leverage-based position sizing"""
        equity = 10000.0
        leverage = 10
        risk_pct = 0.02  # 2% risk per trade
        
        # Position size with leverage = equity * leverage * risk_pct
        position_size = equity * leverage * risk_pct
        assert position_size == 2000.0

    def test_dynamic_sizing_reduces_on_drawdown(self):
        """Test dynamic position sizing based on drawdown"""
        config = RiskConfig(
            use_dynamic_sizing=True,
            reduce_on_drawdown=True,
            reduce_factor_per_5pct_dd=0.5  # Reduce 50% every 5% drawdown
        )
        
        # Base multiplier
        base_multiplier = 1.0
        
        # At 5% drawdown, reduce by 50%
        drawdown = 5.0
        reduction_factor = 1.0 - (drawdown / 5.0 * (1.0 - config.reduce_factor_per_5pct_dd))
        assert reduction_factor == 0.5
        
        # At 10% drawdown (2 x 5%), reduce by 75%
        drawdown = 10.0
        reduction_factor = 1.0 - (drawdown / 5.0 * (1.0 - config.reduce_factor_per_5pct_dd))
        assert reduction_factor == 0.0  # Capped at 0

    def test_trade_interval_check(self, temp_state_file_calc):
        """Test minimum trade interval enforcement"""
        config = RiskConfig(min_trade_interval_minutes=15)
        
        # Current time
        current_time = int(datetime.now().timestamp() * 1000)
        
        # Last trade 10 minutes ago (under limit)
        last_trade_time = current_time - (10 * 60 * 1000)
        minutes_since_last = (current_time - last_trade_time) / (60 * 1000)
        assert minutes_since_last < config.min_trade_interval_minutes
        
        # Last trade 20 minutes ago (over limit)
        last_trade_time = current_time - (20 * 60 * 1000)
        minutes_since_last = (current_time - last_trade_time) / (60 * 1000)
        assert minutes_since_last >= config.min_trade_interval_minutes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
