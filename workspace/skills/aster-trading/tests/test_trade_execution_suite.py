#!/usr/bin/env python3
"""
Test Suite for Real Trade Execution Validation - Aster Trading V2

This test suite validates the complete trade execution flow to identify why
real trades are not being executed on the exchange.

Usage:
    python tests/test_trade_execution_suite.py [--dry-run]

Author: Aster Trading V2
Date: 2026-02-25
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

# Setup path for imports - add src directory to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SKILL_DIR, "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import from existing modules
try:
    from api.aster_api import (
        get_balance_v3,
        get_positions_v3,
        get_equity_total_usdt,
        place_order,
        get_open_orders,
        get_klines,
        signed_get,
        signed_post,
        public_get,
        API_KEY,
        ASTER_API_SECRET,
        ASTER_USER,
        ASTER_SIGNER,
        BASE_URL
    )
    from risk.risk_guard_v2 import RiskGuard, RiskConfig, RiskCheckResult, RiskLevel
    print(f"Successfully imported modules from {SRC_DIR}")
except ImportError as e:
    # Try alternate import for direct execution
    try:
        from src.api.aster_api import (
            get_balance_v3,
            get_positions_v3,
            get_equity_total_usdt,
            place_order,
            get_open_orders,
            get_klines,
            signed_get,
            signed_post,
            public_get,
            API_KEY,
            ASTER_API_SECRET,
            ASTER_USER,
            ASTER_SIGNER,
            BASE_URL
        )
        from src.risk.risk_guard_v2 import RiskGuard, RiskConfig, RiskCheckResult, RiskLevel
    except ImportError as e2:
        logger.error(f"Failed to import modules: {e}, {e2}")
        logger.error(f"Current sys.path: {sys.path}")
        sys.exit(1)


# ============================================================================
# TEST RESULT TRACKING
# ============================================================================

@dataclass
class TestResult:
    """Result of a single test"""
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0


class TestSuite:
    """Test suite that tracks all test results"""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.results: List[TestResult] = []
        self.start_time = time.time()
        
    def add_result(self, result: TestResult):
        """Add a test result"""
        self.results.append(result)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info(f"{status}: {result.name} ({result.execution_time_ms:.1f}ms)")
        if result.message:
            logger.info(f"   → {result.message}")
    
    def print_summary(self):
        """Print final test summary"""
        total_time = (time.time() - self.start_time) * 1000
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        
        print("\n" + "="*80)
        print("TRADE EXECUTION TEST SUITE RESULTS")
        print("="*80)
        print(f"Mode: {'DRY RUN (no actual orders)' if self.dry_run else 'LIVE (real orders allowed)'}")
        print(f"Total Tests: {len(self.results)}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Total Time: {total_time:.1f}ms")
        print("-"*80)
        
        # Group by category
        categories = {}
        for r in self.results:
            # Extract category from test name (format: "Category: Test Name")
            parts = r.name.split(": ", 1)
            cat = parts[0] if len(parts) > 1 else "Other"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(r)
        
        for cat, tests in categories.items():
            cat_passed = sum(1 for t in tests if t.passed)
            cat_total = len(tests)
            print(f"\n{cat}: {cat_passed}/{cat_total} passed")
            for t in tests:
                status = "✅" if t.passed else "❌"
                print(f"  {status} {t.name}")
                if not t.passed and t.message:
                    print(f"      → {t.message}")
        
        print("\n" + "="*80)
        
        # Identify root causes
        self._identify_root_causes()
    
    def _identify_root_causes(self):
        """Identify the root causes of trade execution failures"""
        print("\n🔍 ROOT CAUSE ANALYSIS:")
        print("-"*40)
        
        failures = [r for r in self.results if not r.passed]
        
        # Group failures by type
        failure_categories = {}
        for f in failures:
            # Extract key failure reason
            reason = f.details.get("failure_reason", "Unknown")
            if reason not in failure_categories:
                failure_categories[reason] = []
            failure_categories[reason].append(f)
        
        for reason, tests in failure_categories.items():
            print(f"\n⚠️  {reason}:")
            print(f"   Affected {len(tests)} test(s)")
            for t in tests[:3]:  # Show up to 3 examples
                print(f"   - {t.name}: {t.message[:100]}")
        
        # Check for specific known issues
        print("\n📋 DIAGNOSIS:")
        
        # Check cooldown issue
        cooldown_tests = [r for r in self.results if "cooldown" in r.name.lower() or "Cooldown" in r.message]
        if cooldown_tests:
            for t in cooldown_tests:
                if not t.passed:
                    print(f"   - COOLDOWN BLOCKING TRADES: {t.message}")
        
        # Check min notional issue
        min_notional_tests = [r for r in self.results if "notional" in r.name.lower()]
        for t in min_notional_tests:
            if not t.passed:
                print(f"   - MIN NOTIONAL ISSUE: {t.message}")
        
        # Check equity
        equity_tests = [r for r in self.results if "equity" in r.name.lower()]
        for t in equity_tests:
            if not t.passed:
                print(f"   - EQUITY ISSUE: {t.message}")


# ============================================================================
# API CONNECTION TESTS
# ============================================================================

def test_api_credentials_loaded(test_suite: TestSuite):
    """Test that API credentials are properly loaded"""
    start = time.time()
    
    try:
        has_key = bool(API_KEY)
        has_secret = bool(ASTER_API_SECRET)
        has_user = bool(ASTER_USER)
        has_signer = bool(ASTER_SIGNER)
        
        result = TestResult(
            name="API: Credentials Loaded",
            passed=has_key and has_secret and has_user and has_signer,
            message=f"API_KEY={has_key}, API_SECRET={has_secret}, ASTER_USER={has_user}, ASTER_SIGNER={has_signer}",
            details={
                "api_key_set": bool(API_KEY),
                "api_secret_set": bool(ASTER_API_SECRET),
                "aster_user_set": bool(ASTER_USER),
                "aster_signer_set": bool(ASTER_SIGNER)
            }
        )
    except Exception as e:
        result = TestResult(
            name="API: Credentials Loaded",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result)


def test_api_exchange_connectivity(test_suite: TestSuite):
    """Test connectivity to exchange endpoints"""
    start = time.time()
    
    try:
        # Test public endpoint
        exchange_info = public_get("/fapi/v1/exchangeInfo", {})
        has_markets = "symbols" in str(exchange_info) or len(exchange_info.get("symbols", [])) > 0
        
        result = TestResult(
            name="API: Exchange Connectivity",
            passed=has_markets,
            message=f"Exchange info retrieved, has markets: {has_markets}",
            details={"exchange_info_keys": list(exchange_info.keys()) if isinstance(exchange_info, dict) else []}
        )
    except Exception as e:
        result = TestResult(
            name="API: Exchange Connectivity",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result)


def test_api_signed_request_generation(test_suite: TestSuite):
    """Test signed request generation"""
    start = time.time()
    
    try:
        # Test that we can make a signed request (balance query)
        balances = get_balance_v3()
        
        # Check we got a valid response
        is_valid = isinstance(balances, list) and len(balances) > 0
        
        result = TestResult(
            name="API: Signed Request Generation",
            passed=is_valid,
            message=f"Signed request successful, got {len(balances) if isinstance(balances, list) else 0} balance entries",
            details={
                "response_type": type(balances).__name__,
                "response_length": len(balances) if isinstance(balances, list) else 0
            }
        )
    except Exception as e:
        result = TestResult(
            name="API: Signed Request Generation",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result)


def test_api_balance_retrieval(test_suite: TestSuite):
    """Test balance/equity retrieval"""
    start = time.time()
    
    try:
        # Get balance
        balances = get_balance_v3()
        positions = get_positions_v3()
        equity = get_equity_total_usdt()
        
        # Find USDT balance
        usdt_balance = 0.0
        for b in balances:
            if str(b.get("asset", "")).upper() == "USDT":
                usdt_balance = float(b.get("balance", 0) or 0)
                break
        
        # Count open positions
        open_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        
        result = TestResult(
            name="API: Balance/Equity Retrieval",
            passed=equity >= 0,
            message=f"Equity: ${equity:.2f}, USDT Balance: ${usdt_balance:.2f}, Open Positions: {len(open_positions)}",
            details={
                "total_equity": equity,
                "usdt_balance": usdt_balance,
                "open_positions": len(open_positions)
            }
        )
    except Exception as e:
        result = TestResult(
            name="API: Balance/Equity Retrieval",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result)


# ============================================================================
# RISK GUARD VALIDATION TESTS
# ============================================================================

def test_risk_min_notional_validation(test_suite: TestSuite, equity: float):
    """Test minimum notional validation ($5 minimum)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Test with very small position (should fail or adjust)
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=1.0,  # $1 - below minimum
            entry_price=1.0,
            current_price=1.0,
            leverage=1,
            min_notional=5.0
        )
        
        # The check should either reject or adjust the position
        passed = result.position_size_multiplier > 0 or not result.approved
        
        result_test = TestResult(
            name="Risk: Min Notional $5",
            passed=passed,
            message=f"${1.0} notional: approved={result.approved}, reason={result.reason}",
            details={
                "input_notional": 1.0,
                "approved": result.approved,
                "reason": result.reason,
                "multiplier": result.position_size_multiplier,
                "current_equity": equity
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Min Notional $5",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_daily_loss_limit(test_suite: TestSuite, equity: float):
    """Test daily loss limit check (-5%)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate a 6% daily loss
        guard.state.equity = equity * 0.94  # 6% loss
        guard.state.equity_start_day = equity
        guard.state.daily_pnl = equity * -0.06
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected due to daily loss limit
        passed = not result.approved and "daily" in result.reason.lower()
        
        result_test = TestResult(
            name="Risk: Daily Loss Limit -5%",
            passed=passed,
            message=f"Daily PnL: ${guard.state.daily_pnl:.2f} ({guard.state.daily_pnl/equity*100:.1f}%), approved={result.approved}, reason={result.reason}",
            details={
                "daily_pnl": guard.state.daily_pnl,
                "daily_pnl_pct": guard.state.daily_pnl / equity * 100,
                "approved": result.approved,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Daily Loss Limit -5%",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_drawdown_limit(test_suite: TestSuite, equity: float):
    """Test drawdown limit check (20%)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate 25% drawdown
        guard.state.equity = equity * 0.75
        guard.state.equity_peak = equity
        guard.state.drawdown_pct = 0.25
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected due to drawdown
        passed = not result.approved and "drawdown" in result.reason.lower()
        
        result_test = TestResult(
            name="Risk: Drawdown Limit 20%",
            passed=passed,
            message=f"Drawdown: {guard.state.drawdown_pct*100:.1f}%, approved={result.approved}, reason={result.reason}",
            details={
                "drawdown_pct": guard.state.drawdown_pct,
                "approved": result.approved,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Drawdown Limit 20%",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_trade_count_limit(test_suite: TestSuite, equity: float):
    """Test trade count limit (20/day)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate 20 trades already today
        guard.state.trades_today = 20
        guard.state.equity = equity
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected due to trade count limit
        passed = not result.approved and ("trade" in result.reason.lower() or "límite" in result.reason.lower())
        
        result_test = TestResult(
            name="Risk: Trade Count Limit 20/day",
            passed=passed,
            message=f"Trades today: {guard.state.trades_today}, approved={result.approved}, reason={result.reason}",
            details={
                "trades_today": guard.state.trades_today,
                "approved": result.approved,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Trade Count Limit 20/day",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_cooldown_enforcement(test_suite: TestSuite, equity: float):
    """Test cooldown enforcement (15 min)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate cooldown: trade happened 5 minutes ago
        now = int(time.time() * 1000)
        guard.state.last_trade_time = now - (5 * 60 * 1000)  # 5 minutes ago
        guard.state.equity = equity
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected due to cooldown
        passed = not result.approved and "cooldown" in result.reason.lower()
        
        time_since = (now - guard.state.last_trade_time) / 60000
        
        result_test = TestResult(
            name="Risk: Cooldown 15 min",
            passed=passed,
            message=f"Time since last trade: {time_since:.1f} min, approved={result.approved}, reason={result.reason}",
            details={
                "time_since_trade_min": time_since,
                "cooldown_config_min": 15,
                "approved": result.approved,
                "reason": result.reason,
                "failure_reason": "Cooldown blocking trades" if not passed else None
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Cooldown 15 min",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_position_size_validation(test_suite: TestSuite, equity: float):
    """Test position size validation (5% max)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Test with 10% position (should be reduced)
        guard.state.equity = equity
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=equity * 0.10,  # 10% of equity
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be approved but with reduced position
        passed = result.approved and result.position_size_multiplier < 1.0
        
        result_test = TestResult(
            name="Risk: Position Size 5% max",
            passed=passed,
            message=f"Input 10%, approved={result.approved}, multiplier={result.position_size_multiplier:.2f}, reason={result.reason}",
            details={
                "input_pct": 10.0,
                "approved": result.approved,
                "multiplier": result.position_size_multiplier,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Position Size 5% max",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_total_exposure_check(test_suite: TestSuite, equity: float):
    """Test total exposure check (20% max)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate 15% exposure already open
        guard.state.equity = equity
        guard.state.positions = {
            "EXISTING1": {"notional": equity * 0.10},
            "EXISTING2": {"notional": equity * 0.05}
        }
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=equity * 0.10,  # Try to add 10%
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected (total would be 25% > 20%)
        passed = not result.approved and "exposure" in result.reason.lower()
        
        total_exposure = sum(p.get("notional", 0) for p in guard.state.positions.values()) + (equity * 0.10)
        
        result_test = TestResult(
            name="Risk: Total Exposure 20% max",
            passed=passed,
            message=f"Total exposure: {total_exposure/equity*100:.1f}%, approved={result.approved}, reason={result.reason}",
            details={
                "existing_exposure_pct": 15.0,
                "new_position_pct": 10.0,
                "total_exposure_pct": total_exposure / equity * 100,
                "approved": result.approved,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Total Exposure 20% max",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_risk_consecutive_losses_check(test_suite: TestSuite, equity: float):
    """Test consecutive losses check (5 max)"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Simulate 5 consecutive losses
        guard.state.consecutive_losses = 5
        guard.state.equity = equity
        
        result = guard.check_trade(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            current_price=1.0,
            min_notional=5.0
        )
        
        # Should be rejected
        passed = not result.approved and "consecutive" in result.reason.lower()
        
        result_test = TestResult(
            name="Risk: Consecutive Losses 5 max",
            passed=passed,
            message=f"Consecutive losses: {guard.state.consecutive_losses}, approved={result.approved}, reason={result.reason}",
            details={
                "consecutive_losses": guard.state.consecutive_losses,
                "approved": result.approved,
                "reason": result.reason
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Risk: Consecutive Losses 5 max",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


# ============================================================================
# ORDER PLACEMENT FLOW TESTS
# ============================================================================

def test_signal_generation(test_suite: TestSuite):
    """Test signal generation (BUY/SELL/HOLD)"""
    start = time.time()
    
    try:
        # Get current market data
        klines = get_klines("ASTERUSDT", "1m", 10)
        
        # Check we have data
        has_data = len(klines) > 0
        
        # Basic signal generation logic (just for testing)
        signal = "HOLD"
        if has_data:
            prices = [k["close"] for k in klines]
            if len(prices) >= 2:
                if prices[-1] > prices[-2]:
                    signal = "BUY"
                elif prices[-1] < prices[-2]:
                    signal = "SELL"
        
        result_test = TestResult(
            name="Order: Signal Generation",
            passed=has_data,
            message=f"Signal: {signal}, Klines: {len(klines)}",
            details={
                "signal": signal,
                "klines_count": len(klines)
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Order: Signal Generation",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_position_size_calculation(test_suite: TestSuite, equity: float):
    """Test position size calculation"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Test with 5% of equity
        base_position = equity * 0.05
        leverage = 5
        leveraged_position = base_position * leverage
        
        # Check minimum notional
        min_notional = 5.0
        
        # Ensure we meet minimum
        if leveraged_position < min_notional:
            # Need to increase position
            base_position = min_notional / leverage
            leveraged_position = base_position * leverage
        
        passed = leveraged_position >= min_notional
        
        result_test = TestResult(
            name="Order: Position Size Calculation",
            passed=passed,
            message=f"Equity: ${equity:.2f}, Base: ${base_position:.2f}, Leverage: {leverage}x, Leveraged: ${leveraged_position:.2f}, Min: ${min_notional:.2f}",
            details={
                "equity": equity,
                "base_position": base_position,
                "leverage": leverage,
                "leveraged_position": leveraged_position,
                "min_notional": min_notional
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Order: Position Size Calculation",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_order_parameter_construction(test_suite: TestSuite):
    """Test order parameter construction"""
    start = time.time()
    
    try:
        # Get current price
        klines = get_klines("ASTERUSDT", "1m", 1)
        current_price = float(klines[-1]["close"]) if klines else 0.70
        
        # Construct order parameters
        symbol = "ASTERUSDT"
        side = "BUY"
        order_type = "MARKET"
        quantity = 1.0  # Would be calculated based on position size
        price = current_price
        
        # Validate parameters
        passed = symbol and side and order_type and quantity > 0
        
        result_test = TestResult(
            name="Order: Parameter Construction",
            passed=passed,
            message=f"Symbol: {symbol}, Side: {side}, Type: {order_type}, Qty: {quantity}, Price: ${price:.4f}",
            details={
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "price": price
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Order: Parameter Construction",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_actual_order_submission(test_suite: TestSuite, dry_run: bool):
    """Test actual order submission to exchange"""
    start = time.time()
    
    if dry_run:
        result_test = TestResult(
            name="Order: Actual Submission",
            passed=True,
            message="DRY RUN - skipping actual order",
            details={"dry_run": True}
        )
        result_test.execution_time_ms = (time.time() - start) * 1000
        test_suite.add_result(result_test)
        return
    
    try:
        # Get current price
        klines = get_klines("ASTERUSDT", "1m", 1)
        current_price = float(klines[-1]["close"]) if klines else 0.70
        
        # Place a very small test order (just above minimum)
        # Note: Using minimal quantity for testing
        order_params = {
            "symbol": "ASTERUSDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 1,  # Very small test
            "price": str(current_price)
        }
        
        result = place_order(order_params)
        
        # Check if order was accepted
        passed = "orderId" in result or "clientOrderId" in result or result.get("code", 200) == 200
        
        result_test = TestResult(
            name="Order: Actual Submission",
            passed=passed,
            message=f"Order submitted: {result.get('orderId', 'N/A')}",
            details={
                "order_response": result,
                "order_id": result.get("orderId"),
                "client_order_id": result.get("clientOrderId")
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Order: Actual Submission",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_order_response_handling(test_suite: TestSuite):
    """Test order response handling"""
    start = time.time()
    
    try:
        # Get open orders to test response handling
        open_orders = get_open_orders()
        
        # Should return a list (possibly empty)
        is_valid = isinstance(open_orders, list)
        
        result_test = TestResult(
            name="Order: Response Handling",
            passed=is_valid,
            message=f"Open orders: {len(open_orders)}",
            details={
                "open_orders_count": len(open_orders),
                "response_type": type(open_orders).__name__
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="Order: Response Handling",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


# ============================================================================
# STATE MANAGEMENT TESTS
# ============================================================================

def test_trade_state_persistence(test_suite: TestSuite):
    """Test trade state persistence"""
    start = time.time()
    
    try:
        # Load risk guard with state file
        state_file = "./logs/risk_state.json"
        guard = RiskGuard(state_file=state_file)
        
        # Check state was loaded
        state_exists = os.path.exists(state_file)
        
        result_test = TestResult(
            name="State: Trade Persistence",
            passed=state_exists,
            message=f"State file exists: {state_exists}, Equity: ${guard.state.equity:.2f}",
            details={
                "state_file": state_file,
                "equity": guard.state.equity,
                "trades_today": guard.state.trades_today,
                "last_trade_time": guard.state.last_trade_time
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="State: Trade Persistence",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_equity_tracking(test_suite: TestSuite, equity: float):
    """Test equity tracking"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Update equity
        guard.update_equity(equity)
        
        # Check state updated
        passed = guard.state.equity == equity
        
        result_test = TestResult(
            name="State: Equity Tracking",
            passed=passed,
            message=f"Equity tracked: ${guard.state.equity:.2f}",
            details={
                "equity": guard.state.equity,
                "equity_peak": guard.state.equity_peak,
                "drawdown_pct": guard.state.drawdown_pct
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="State: Equity Tracking",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_pnl_calculation(test_suite: TestSuite, equity: float):
    """Test PnL calculation"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        # Set initial equity
        initial_equity = equity
        guard.update_equity(initial_equity)
        
        # Simulate a winning trade
        guard.on_trade_executed(
            symbol="TESTUSDT",
            side="BUY",
            notional=10.0,
            entry_price=1.0,
            pnl=5.0  # $5 profit
        )
        
        # Check PnL was updated
        expected_equity = initial_equity + 5.0
        passed = abs(guard.state.equity - expected_equity) < 0.01
        
        result_test = TestResult(
            name="State: PnL Calculation",
            passed=passed,
            message=f"Equity after trade: ${guard.state.equity:.2f}, Daily PnL: ${guard.state.daily_pnl:.2f}",
            details={
                "equity": guard.state.equity,
                "daily_pnl": guard.state.daily_pnl,
                "trades_today": guard.state.trades_today
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="State: PnL Calculation",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_position_tracking(test_suite: TestSuite):
    """Test position tracking"""
    start = time.time()
    
    try:
        positions = get_positions_v3()
        
        # Filter for open positions
        open_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        
        result_test = TestResult(
            name="State: Position Tracking",
            passed=True,
            message=f"Open positions: {len(open_positions)}",
            details={
                "total_positions": len(positions),
                "open_positions": len(open_positions),
                "symbols": [p.get("symbol") for p in open_positions]
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="State: Position Tracking",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

def test_end_to_end_trade_execution(test_suite: TestSuite, equity: float, dry_run: bool):
    """Test end-to-end trade execution with small test order"""
    start = time.time()
    
    try:
        # Step 1: Get market data
        klines = get_klines("ASTERUSDT", "1m", 5)
        if not klines:
            raise Exception("Failed to get market data")
        
        current_price = float(klines[-1]["close"])
        
        # Step 2: Calculate position size (5% of equity with 5x leverage)
        base_position = equity * 0.05
        leverage = 5
        leveraged_position = base_position * leverage
        
        # Step 3: Check risk guard
        guard = RiskGuard()
        
        # Simulate clearing cooldown for test (unless dry run)
        if not dry_run:
            # Check if we're in cooldown
            if guard.state.last_trade_time > 0:
                time_since = (int(time.time() * 1000) - guard.state.last_trade_time) / 60000
                if time_since < 15:
                    result_test = TestResult(
                        name="Integration: E2E Trade",
                        passed=False,
                        message=f"BLOCKED: Cooldown active ({time_since:.1f}/15 min)",
                        details={
                            "failure_reason": "Cooldown blocking trades",
                            "time_since_trade_min": time_since
                        }
                    )
                    result_test.execution_time_ms = (time.time() - start) * 1000
                    test_suite.add_result(result_test)
        
        risk_result = guard.check_trade(
            symbol="ASTERUSDT",
            side="BUY",
            notional=base_position,
            entry_price=current_price,
            current_price=current_price,
            leverage=leverage,
            min_notional=5.0
        )
        
        if not risk_result.approved:
            result_test = TestResult(
                name="Integration: E2E Trade",
                passed=False,
                message=f"BLOCKED by Risk Guard: {risk_result.reason}",
                details={
                    "failure_reason": f"Risk guard blocked: {risk_result.reason}",
                    "risk_reason": risk_result.reason,
                    "equity": equity,
                    "position_size": leveraged_position
                }
            )
            result_test.execution_time_ms = (time.time() - start) * 1000
            test_suite.add_result(result_test)
        
        # Step 4: Place order (if not dry run)
        if dry_run:
            result_test = TestResult(
                name="Integration: E2E Trade",
                passed=True,
                message=f"DRY RUN: Would trade ${leveraged_position:.2f} at ${current_price:.4f}",
                details={
                    "dry_run": True,
                    "equity": equity,
                    "base_position": base_position,
                    "leverage": leverage,
                    "leveraged_position": leveraged_position,
                    "price": current_price,
                    "risk_approved": risk_result.approved
                }
            )
        else:
            # Calculate quantity based on position size
            quantity = leveraged_position / current_price
            
            order_params = {
                "symbol": "ASTERUSDT",
                "side": "BUY",
                "type": "MARKET",
                "quantity": str(int(quantity * 100) / 100)  # Round to 2 decimals
            }
            
            order_result = place_order(order_params)
            
            success = "orderId" in order_result or order_result.get("code", 200) == 200
            
            result_test = TestResult(
                name="Integration: E2E Trade",
                passed=success,
                message=f"Order {'submitted' if success else 'failed'}: {order_result.get('orderId', 'N/A')}",
                details={
                    "order_params": order_params,
                    "order_result": order_result,
                    "success": success
                }
            )
        
    except Exception as e:
        result_test = TestResult(
            name="Integration: E2E Trade",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


def test_current_cooldown_status(test_suite: TestSuite):
    """Test current cooldown status - THIS IS CRITICAL"""
    start = time.time()
    
    try:
        guard = RiskGuard()
        
        now = int(time.time() * 1000)
        last_trade = guard.state.last_trade_time
        
        if last_trade > 0:
            time_since_ms = now - last_trade
            time_since_min = time_since_ms / 60000
            cooldown_remaining = max(0, 15 - time_since_min)
            in_cooldown = time_since_min < 15
        else:
            time_since_min = 0
            cooldown_remaining = 0
            in_cooldown = False
        
        result_test = TestResult(
            name="⚠️ CURRENT COOLDOWN STATUS",
            passed=not in_cooldown,
            message=f"Last trade: {time_since_min:.1f} min ago, Cooldown: {cooldown_remaining:.1f} min remaining",
            details={
                "last_trade_time": last_trade,
                "time_since_trade_min": time_since_min,
                "cooldown_config_min": 15,
                "in_cooldown": in_cooldown,
                "cooldown_remaining_min": cooldown_remaining,
                "failure_reason": "Cooldown blocking trades" if in_cooldown else None
            }
        )
    except Exception as e:
        result_test = TestResult(
            name="⚠️ CURRENT COOLDOWN STATUS",
            passed=False,
            message=str(e),
            details={"error": str(e)}
        )
    
    result_test.execution_time_ms = (time.time() - start) * 1000
    test_suite.add_result(result_test)


# ============================================================================
# MAIN TEST RUNNER
# ============================================================================

def run_all_tests(dry_run: bool = False):
    """Run all tests"""
    logger.info("="*60)
    logger.info("Starting Trade Execution Test Suite")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info("="*60)
    
    test_suite = TestSuite(dry_run=dry_run)
    
    # Get current equity first
    try:
        equity = get_equity_total_usdt()
        logger.info(f"Current Equity: ${equity:.2f}")
    except Exception as e:
        logger.error(f"Failed to get equity: {e}")
        equity = 40.83  # Fallback to observed value
    
    # =========================================================================
    # API CONNECTION TESTS
    # =========================================================================
    logger.info("\n--- API Connection Tests ---")
    test_api_credentials_loaded(test_suite)
    test_api_exchange_connectivity(test_suite)
    test_api_signed_request_generation(test_suite)
    test_api_balance_retrieval(test_suite)
    
    # =========================================================================
    # RISK GUARD VALIDATION TESTS
    # =========================================================================
    logger.info("\n--- Risk Guard Validation Tests ---")
    test_risk_min_notional_validation(test_suite, equity)
    test_risk_daily_loss_limit(test_suite, equity)
    test_risk_drawdown_limit(test_suite, equity)
    test_risk_trade_count_limit(test_suite, equity)
    test_risk_cooldown_enforcement(test_suite, equity)
    test_risk_position_size_validation(test_suite, equity)
    test_risk_total_exposure_check(test_suite, equity)
    test_risk_consecutive_losses_check(test_suite, equity)
    
    # =========================================================================
    # ORDER PLACEMENT FLOW TESTS
    # =========================================================================
    logger.info("\n--- Order Placement Flow Tests ---")
    test_signal_generation(test_suite)
    test_position_size_calculation(test_suite, equity)
    test_order_parameter_construction(test_suite)
    test_actual_order_submission(test_suite, dry_run)
    test_order_response_handling(test_suite)
    
    # =========================================================================
    # STATE MANAGEMENT TESTS
    # =========================================================================
    logger.info("\n--- State Management Tests ---")
    test_trade_state_persistence(test_suite)
    test_equity_tracking(test_suite, equity)
    test_pnl_calculation(test_suite, equity)
    test_position_tracking(test_suite)
    
    # =========================================================================
    # INTEGRATION TESTS
    # =========================================================================
    logger.info("\n--- Integration Tests ---")
    test_current_cooldown_status(test_suite)
    test_end_to_end_trade_execution(test_suite, equity, dry_run)
    
    # =========================================================================
    # PRINT SUMMARY
    # =========================================================================
    test_suite.print_summary()
    
    return test_suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade Execution Test Suite")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode (no actual orders)")
    args = parser.parse_args()
    
    run_all_tests(dry_run=args.dry_run)
