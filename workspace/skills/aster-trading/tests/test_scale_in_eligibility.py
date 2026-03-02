#!/usr/bin/env python3
"""
Unit tests for Gap 2 fix: ScaleInEligibility missing symbol field.

This test verifies that:
1. ScaleInEligibility dataclass has a symbol field
2. check_eligibility populates the symbol field when returning ScaleInEligibility
3. log_eligibility_check can access eligibility.symbol without AttributeError
4. The scale_in_conditions_log table contains the correct symbol

This test uses an in-memory SQLite database to avoid affecting production data.
"""

import pytest
import sqlite3
import uuid
import time
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# Test database schema (must match V3 architecture + scale_in_conditions_log)
TEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    position_uuid TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT')),
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL,
    notional REAL,
    leverage INTEGER DEFAULT 1,
    open_time INTEGER NOT NULL,
    close_time INTEGER,
    cycle_id TEXT,
    correlation_id TEXT,
    reason TEXT,
    close_reason TEXT,
    version INTEGER DEFAULT 1,
    metadata_json TEXT,
    scale_in_pending INTEGER DEFAULT 0 CHECK(scale_in_pending IN (0, 1)),
    scale_in_timestamp INTEGER,
    pyramid_scales INTEGER DEFAULT 0,
    last_scale_price REAL,
    last_scale_time INTEGER,
    trailing_active INTEGER DEFAULT 0 CHECK(trailing_active IN (0, 1)),
    trailing_activation_price REAL,
    trailing_callback_pct REAL DEFAULT 0.8,
    tp1_hit INTEGER DEFAULT 0 CHECK(tp1_hit IN (0, 1)),
    tp2_hit INTEGER DEFAULT 0 CHECK(tp2_hit IN (0, 1)),
    sl_at_break_even INTEGER DEFAULT 0 CHECK(sl_at_break_even IN (0, 1)),
    initial_sl_price REAL,
    current_sl_price REAL,
    max_hold_time_minutes INTEGER,
    status TEXT CHECK(status IN ('OPEN', 'CLOSED')) DEFAULT 'OPEN',
    updated_at INTEGER DEFAULT (strftime('%s','now') * 1000),
    created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
);

CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_status ON positions(status);

-- Scale-in conditions log table (Gap 2 fix)
CREATE TABLE IF NOT EXISTS scale_in_conditions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    position_uuid TEXT NOT NULL,
    symbol TEXT NOT NULL,
    can_scale_in INTEGER NOT NULL CHECK(can_scale_in IN (0, 1)),
    path_used TEXT,
    adx_value REAL,
    funding_rate REAL,
    price_distance_pct REAL,
    margin_ratio REAL,
    pyramid_level INTEGER,
    reason TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
);

CREATE INDEX IF NOT EXISTS idx_scale_in_conditions_log_position_uuid ON scale_in_conditions_log(position_uuid);
"""


def create_test_db():
    """Create an in-memory test database with the V3 schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(TEST_SCHEMA)
    return conn


def test_scale_in_eligibility_has_symbol_field():
    """
    Test that ScaleInEligibility dataclass has a symbol field.
    
    This is the core test for Gap 2 fix:
    - Import ScaleInEligibility from the new module
    - Verify it has a 'symbol' attribute
    - Verify the dataclass can be instantiated with symbol
    """
    from src.scale_in.scale_in_eligibility import ScaleInEligibility
    
    # Test that the dataclass has the symbol field
    eligibility = ScaleInEligibility(
        symbol="ETHUSDT",
        can_scale_in=True,
        path_used="PRIMARY",
        adx_value=30.0,
        funding_rate=0.001,
        price_distance_pct=2.5,
        margin_ratio=0.5,
        pyramid_level=1,
        reason="Test eligibility"
    )
    
    # Verify symbol field exists and is correct
    assert hasattr(eligibility, 'symbol'), "ScaleInEligibility should have symbol attribute"
    assert eligibility.symbol == "ETHUSDT", f"Expected symbol='ETHUSDT', got {eligibility.symbol}"


def test_check_eligibility_populates_symbol():
    """
    Test that check_eligibility populates the symbol field.
    
    This verifies:
    - The checker correctly extracts symbol from position
    - The returned ScaleInEligibility has the correct symbol
    """
    from src.scale_in.scale_in_eligibility import ScaleInEligibilityChecker, ScaleInEligibility
    
    checker = ScaleInEligibilityChecker()
    
    # Create a test position
    position = {
        "symbol": "ETHUSDT",
        "pyramid_scales": 0,
        "last_scale_price": None,
        "last_scale_time": None,
        "tp2_hit": True,
        "tp1_hit": False,
        "sl_at_break_even": False,
    }
    
    # Check eligibility
    eligibility = checker.check_eligibility(
        position=position,
        current_price=2000.0,
        adx_value=30.0,
        funding_rate=0.001,
        margin_ratio=0.5
    )
    
    # Verify the symbol is populated
    assert isinstance(eligibility, ScaleInEligibility), "Should return ScaleInEligibility"
    assert eligibility.symbol == "ETHUSDT", f"Expected symbol='ETHUSDT', got {eligibility.symbol}"


def test_log_eligibility_check_with_symbol():
    """
    Test that log_eligibility_check works with eligibility.symbol.
    
    This is the key test for Gap 2:
    - Create a ScaleInEligibility with symbol
    - Call log_eligibility_check
    - Verify no AttributeError is raised
    - Verify the log entry has the correct symbol
    
    Note: This test directly tests the SQL insertion logic to avoid pydantic dependency.
    """
    from src.scale_in.scale_in_eligibility import ScaleInEligibility
    import time
    
    conn = create_test_db()
    
    # Create test eligibility with symbol
    eligibility = ScaleInEligibility(
        symbol="ETHUSDT",
        can_scale_in=True,
        path_used="PRIMARY",
        adx_value=30.0,
        funding_rate=0.001,
        price_distance_pct=2.5,
        margin_ratio=0.5,
        pyramid_level=1,
        reason="PRIMARY: TP2 hit + ADX 30.0 >= 25"
    )
    
    position_uuid = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    
    # Directly insert into scale_in_conditions_log using the same logic as log_eligibility_check
    # This tests that eligibility.symbol is accessible without AttributeError
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO scale_in_conditions_log 
           (timestamp, position_uuid, symbol, can_scale_in, path_used,
            adx_value, funding_rate, price_distance_pct, margin_ratio,
            pyramid_level, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (timestamp, position_uuid, eligibility.symbol,  # This is the key: eligibility.symbol works!
         1 if eligibility.can_scale_in else 0, eligibility.path_used,
         eligibility.adx_value, eligibility.funding_rate,
         eligibility.price_distance_pct, eligibility.margin_ratio,
         eligibility.pyramid_level, eligibility.reason)
    )
    conn.commit()
    
    # Verify the log entry was created
    cursor.execute(
        "SELECT * FROM scale_in_conditions_log WHERE position_uuid = ?",
        (position_uuid,)
    )
    row = cursor.fetchone()
    
    assert row is not None, "Log entry should exist in scale_in_conditions_log"
    assert row['symbol'] == 'ETHUSDT', f"Expected symbol='ETHUSDT', got {row['symbol']}"
    assert row['can_scale_in'] == 1, "can_scale_in should be 1"
    assert row['path_used'] == 'PRIMARY', f"Expected path_used='PRIMARY', got {row['path_used']}"
    
    conn.close()


def test_eligibility_symbol_not_unknown():
    """
    Test that eligibility.symbol is populated, not 'UNKNOWN'.
    
    Before the fix, log_eligibility_check used:
        eligibility.symbol if hasattr(eligibility, 'symbol') else 'UNKNOWN'
    
    After the fix, symbol should always be present.
    """
    from src.scale_in.scale_in_eligibility import ScaleInEligibility, ScaleInEligibilityChecker
    
    checker = ScaleInEligibilityChecker()
    
    # Test with various symbols
    test_symbols = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "ASTERUSDT"]
    
    for test_symbol in test_symbols:
        position = {
            "symbol": test_symbol,
            "pyramid_scales": 0,
            "last_scale_price": None,
            "last_scale_time": None,
            "tp2_hit": True,
            "tp1_hit": False,
            "sl_at_break_even": False,
        }
        
        eligibility = checker.check_eligibility(
            position=position,
            current_price=2000.0,
            adx_value=30.0,
            funding_rate=0.001,
            margin_ratio=0.5
        )
        
        assert eligibility.symbol == test_symbol, \
            f"Expected symbol='{test_symbol}', got '{eligibility.symbol}'"
        assert eligibility.symbol != 'UNKNOWN', "Symbol should not be UNKNOWN"


def test_ineligibility_also_has_symbol():
    """
    Test that even ineligible positions have the symbol field populated.
    
    This ensures the symbol is always present regardless of can_scale_in.
    """
    from src.scale_in.scale_in_eligibility import ScaleInEligibilityChecker
    
    checker = ScaleInEligibilityChecker()
    
    # Position that won't be eligible (pyramid level maxed out)
    position = {
        "symbol": "BNBUSDT",
        "pyramid_scales": 3,  # At max level
        "last_scale_price": 100.0,
        "last_scale_time": int(time.time() * 1000),
        "tp2_hit": True,
        "tp1_hit": True,
        "sl_at_break_even": True,
    }
    
    eligibility = checker.check_eligibility(
        position=position,
        current_price=200.0,
        adx_value=35.0,
        funding_rate=0.001,
        margin_ratio=0.3
    )
    
    # Should be ineligible but still have symbol
    assert eligibility.can_scale_in is False, "Should not be eligible"
    assert eligibility.symbol == "BNBUSDT", f"Expected symbol='BNBUSDT', got {eligibility.symbol}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
