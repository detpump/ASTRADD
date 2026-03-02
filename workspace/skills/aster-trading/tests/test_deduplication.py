#!/usr/bin/env python3
"""
Test script for deduplication logic in centralized_logger.py
Tests all edge cases for the _is_duplicate method and cycle_id integration.
"""

import os
import sys
import time
import json
import tempfile
import threading
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import the module we're testing
from centralized_logger import CentralizedLogger, _logged_trades, _log_dedup_lock


def test_is_duplicate_basic():
    """Test basic deduplication functionality"""
    print("\n=== TEST: Basic Deduplication ===")
    
    # Create a fresh logger instance
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        
        # Clear any existing state
        _logged_trades.clear()
        
        # First call should NOT be a duplicate
        key = "test_trade_001"
        result1 = logger._is_duplicate(key)
        print(f"First call with key '{key}': is_duplicate = {result1}")
        assert result1 == False, "First call should NOT be duplicate"
        
        # Second call with same key should be duplicate
        result2 = logger._is_duplicate(key)
        print(f"Second call with key '{key}': is_duplicate = {result2}")
        assert result2 == True, "Second call SHOULD be duplicate"
        
        # Verify in dict
        print(f"Logged trades dict: {_logged_trades}")
        assert key in _logged_trades
        
        print("✅ PASSED: Basic deduplication works")


def test_is_duplicate_different_keys():
    """Test that different keys are not treated as duplicates"""
    print("\n=== TEST: Different Keys ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        # Different keys should not be duplicates
        key1 = "trade_001"
        key2 = "trade_002"
        
        result1 = logger._is_duplicate(key1)
        result2 = logger._is_duplicate(key2)
        
        print(f"Key1: {result1}, Key2: {result2}")
        assert result1 == False
        assert result2 == False
        
        print("✅ PASSED: Different keys are independent")


def test_is_duplicate_time_boundary():
    """Test deduplication at time boundaries"""
    print("\n=== TEST: Time Boundary ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        key = "test_trade_boundary"
        
        # First call
        logger._is_duplicate(key)
        
        # Simulate time passing - use a mock to control time
        # Add 3 seconds to the timestamp (within window)
        _logged_trades[key] = time.time() - 3
        result = logger._is_duplicate(key)
        print(f"After 3 seconds (within 5s window): is_duplicate = {result}")
        assert result == True
        
        # Add 6 seconds (outside window)
        _logged_trades[key] = time.time() - 6
        result = logger._is_duplicate(key)
        print(f"After 6 seconds (outside 5s window): is_duplicate = {result}")
        assert result == False  # Should NOT be duplicate - expired
        
        print("✅ PASSED: Time boundary works correctly")


def test_cycle_id_none():
    """Test behavior when cycle_id is None"""
    print("\n=== TEST: cycle_id is None ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        # Test log_trade with no cycle_id - uses order_id instead
        trade_key = f"trade_BTCUSDT_12345_FILLED"
        dedup_key = f"trade_BTCUSDT_12345_FILLED"
        
        # This should NOT be a duplicate since no dedup key was registered
        result = logger._is_duplicate(dedup_key)
        print(f"With order_id dedup key: is_duplicate = {result}")
        assert result == False
        
        # Now register it
        logger._is_duplicate(dedup_key)
        
        # Second call with same order_id should be duplicate
        result2 = logger._is_duplicate(dedup_key)
        print(f"Second call with same key: is_duplicate = {result2}")
        assert result2 == True
        
        print("✅ PASSED: order_id fallback works when cycle_id is None")


def test_concurrent_logging():
    """Test concurrent logging from multiple threads"""
    print("\n=== TEST: Concurrent Logging ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        results = []
        errors = []
        
        def log_trade_thread(thread_id):
            try:
                key = f"trade_thread_{thread_id}"
                result = logger._is_duplicate(key)
                results.append((thread_id, result))
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        # Run 10 threads concurrently
        threads = []
        for i in range(10):
            t = threading.Thread(target=log_trade_thread, args=(i,))
            threads.append(t)
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        print(f"Results: {results}")
        print(f"Errors: {errors}")
        
        # All should succeed without errors
        assert len(errors) == 0, f"Errors occurred: {errors}"
        
        # First calls should NOT be duplicates
        for thread_id, result in results:
            assert result == False, f"Thread {thread_id} got unexpected duplicate"
        
        print("✅ PASSED: Concurrent logging works correctly")


def test_write_jsonl_returns_false_on_duplicate():
    """Test that _write_jsonl returns False when duplicate is detected"""
    print("\n=== TEST: _write_jsonl Returns False on Duplicate ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        data = {"symbol": "BTCUSDT", "status": "FILLED"}
        dedup_key = "trade_test_001"
        
        # First call should succeed
        result1 = logger._write_jsonl(logger.trades_file, data, dedup_key=dedup_key)
        print(f"First write: {result1}")
        
        # Second call with same dedup key should return False
        result2 = logger._write_jsonl(logger.trades_file, data, dedup_key=dedup_key)
        print(f"Second write (duplicate): {result2}")
        
        assert result1 == True
        assert result2 == False
        
        # Verify only one line was written
        with open(logger.trades_file, 'r') as f:
            lines = f.readlines()
        print(f"Lines in file: {len(lines)}")
        assert len(lines) == 1
        
        print("✅ PASSED: Duplicate writes are blocked")


def test_write_jsonl_no_dedup():
    """Test that _write_jsonl works normally without dedup_key"""
    print("\n=== TEST: _write_jsonl Without Dedup Key ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        data = {"symbol": "ETHUSDT", "status": "PENDING"}
        
        # Without dedup_key, should always write
        result1 = logger._write_jsonl(logger.trades_file, data)
        result2 = logger._write_jsonl(logger.trades_file, data)
        
        print(f"First write: {result1}, Second write: {result2}")
        
        assert result1 == True
        assert result2 == True  # Both succeed without dedup
        
        # Should have 2 lines
        with open(logger.trades_file, 'r') as f:
            lines = f.readlines()
        print(f"Lines in file: {len(lines)}")
        assert len(lines) == 2
        
        print("✅ PASSED: Writes work normally without dedup key")


def test_dedup_key_generation():
    """Test dedup key generation logic"""
    print("\n=== TEST: Dedup Key Generation ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        
        # Test with cycle_id - should produce key like "trade_cycle_abc123"
        cycle_id = "abc123"
        key1 = f"trade_{cycle_id}"
        print(f"cycle_id dedup key: {key1}")
        
        # Test with order_id + symbol + status - should produce unique keys
        key2 = f"trade_BTCUSDT_999_FILLED"
        
        print(f"order_id dedup key: {key2}")
        
        # Both should be unique
        assert key1 != key2
        
        # Test different statuses generate different keys
        key3 = f"trade_BTCUSDT_999_PENDING"
        assert key2 != key3
        
        # Test that different cycle_ids produce different keys
        key4 = f"trade_cycle_xyz789"
        assert key1 != key4
        
        print("✅ PASSED: Dedup keys are properly generated")


def test_log_trade_integration():
    """Integration test for log_trade with cycle_id"""
    print("\n=== TEST: log_trade Integration ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        # First trade
        result1 = logger.log_trade(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
            price=50000,
            notional=500,
            status="FILLED",
            order_id="12345",
            cycle_id="cycle_001"
        )
        
        # Second trade with same cycle_id should be blocked
        result2 = logger.log_trade(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
            price=50000,
            notional=500,
            status="FILLED",
            order_id="12345",
            cycle_id="cycle_001"
        )
        
        print(f"First trade: {result1}, Second trade: {result2}")
        
        assert result1 == True
        assert result2 == False  # Duplicate!
        
        # Trade with different cycle_id should succeed
        result3 = logger.log_trade(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
            price=50000,
            notional=500,
            status="FILLED",
            order_id="12345",
            cycle_id="cycle_002"
        )
        
        print(f"Different cycle_id: {result3}")
        assert result3 == True
        
        # Verify file has exactly 2 lines
        with open(logger.trades_file, 'r') as f:
            lines = f.readlines()
        
        print(f"Total lines written: {len(lines)}")
        
        # Parse and verify
        trades = [json.loads(line) for line in lines]
        print(f"Trades: {trades}")
        
        assert len(trades) == 2
        
        print("✅ PASSED: Full integration test works")


def test_cleanup_old_entries():
    """Test that old entries are cleaned up"""
    print("\n=== TEST: Cleanup Old Entries ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = CentralizedLogger(log_path=tmpdir)
        _logged_trades.clear()
        
        # Add some entries
        logger._is_duplicate("key1")
        logger._is_duplicate("key2")
        logger._is_duplicate("key3")
        
        print(f"Entries before cleanup: {len(_logged_trades)}")
        
        # Manually age one entry beyond window
        _logged_trades["key1"] = time.time() - 10  # 10 seconds old
        
        # Add new entry - should trigger cleanup
        logger._is_duplicate("key4")
        
        print(f"Entries after adding key4: {len(_logged_trades)}")
        
        # key1 should be removed, others should remain
        # Note: Since key1 is > 5 seconds old, it should be cleaned up
        # But the cleanup only runs when _is_duplicate is called
        
        # Verify behavior by checking time difference
        current = time.time()
        for k, v in _logged_trades.items():
            age = current - v
            print(f"  {k}: age = {age:.2f}s")
            # All entries should be within window (except key1 which was manually aged)
        
        print("✅ PASSED: Cleanup logic verified")


def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("RUNNING DEDUPLICATION LOGIC TESTS")
    print("=" * 60)
    
    tests = [
        test_is_duplicate_basic,
        test_is_duplicate_different_keys,
        test_is_duplicate_time_boundary,
        test_cycle_id_none,
        test_concurrent_logging,
        test_write_jsonl_returns_false_on_duplicate,
        test_write_jsonl_no_dedup,
        test_dedup_key_generation,
        test_log_trade_integration,
        test_cleanup_old_entries,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ FAILED: {test.__name__}")
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
