#!/usr/bin/env python3
"""
Edge case tests for deduplication logic in centralized_logger.py
Tests for: concurrent writes, TTL boundary, empty cycle_id, memory cleanup
"""

import os
import sys
import time
import threading
import tempfile
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import the module we're testing
from centralized_logger import CentralizedLogger, _logged_trades, _log_dedup_lock, _DEDUP_WINDOW_SECONDS


class TestConcurrentWrites:
    """Test concurrent writes to the same dedup key"""
    
    def test_concurrent_same_key_race_condition(self):
        """Test multiple threads trying to write to the same dedup key simultaneously"""
        print("\n=== TEST: Concurrent Writes to Same Key ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            results = []
            errors = []
            key = "concurrent_trade_key"
            
            def check_duplicate():
                try:
                    result = logger._is_duplicate(key)
                    results.append(result)
                except Exception as e:
                    errors.append(str(e))
            
            # Run 20 threads all trying to check the same key simultaneously
            threads = []
            for _ in range(20):
                t = threading.Thread(target=check_duplicate)
                threads.append(t)
            
            # Start all threads at once
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            print(f"Results: {results}")
            print(f"Errors: {errors}")
            
            # Should have no errors
            assert len(errors) == 0, f"Errors occurred: {errors}"
            
            # First call should return False, others should return True
            # Due to threading, we might have some race conditions
            false_count = results.count(False)
            true_count = results.count(True)
            
            print(f"False (new): {false_count}, True (duplicate): {true_count}")
            
            # Exactly one should be False (first one)
            assert false_count == 1, f"Expected exactly 1 False, got {false_count}"
            assert true_count == 19, f"Expected 19 True, got {true_count}"
            
            print("✅ PASSED: Concurrent writes handled correctly")
    
    def test_concurrent_different_keys(self):
        """Test multiple threads writing to different keys"""
        print("\n=== TEST: Concurrent Writes Different Keys ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            results = []
            errors = []
            
            def check_key(thread_id):
                try:
                    key = f"trade_thread_{thread_id}"
                    result = logger._is_duplicate(key)
                    results.append((thread_id, result))
                except Exception as e:
                    errors.append((thread_id, str(e)))
            
            # Run 10 threads with different keys
            threads = []
            for i in range(10):
                t = threading.Thread(target=check_key, args=(i,))
                threads.append(t)
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            print(f"Results: {results}")
            print(f"Errors: {errors}")
            
            assert len(errors) == 0, f"Errors: {errors}"
            
            # All should return False (first call for each unique key)
            for thread_id, result in results:
                assert result == False, f"Thread {thread_id} got duplicate unexpectedly"
            
            print("✅ PASSED: Different keys work concurrently")


class TestTTLBoundary:
    """Test TTL expiration at exact boundary"""
    
    def test_ttl_exactly_at_5_seconds(self):
        """Test that entry is NOT duplicate exactly at 5 second mark"""
        print("\n=== TEST: TTL Boundary at 5 Seconds ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            key = "ttl_test_key"
            
            # First call
            result1 = logger._is_duplicate(key)
            print(f"First call: is_duplicate = {result1}")
            assert result1 == False
            
            # Get the timestamp that was stored
            stored_time = _logged_trades[key]
            current_time = time.time()
            
            # Simulate time advancing to exactly 5 seconds later
            # Use mock to control time
            future_time = stored_time + _DEDUP_WINDOW_SECONDS
            
            with patch('centralized_logger.time.time', return_value=future_time):
                result2 = logger._is_duplicate(key)
                print(f"At exactly 5 seconds (boundary): is_duplicate = {result2}")
                # At exactly the boundary, should NOT be duplicate (current - stored >= window)
            
            # After 5 seconds, it should NOT be a duplicate (expires)
            assert result2 == False, "At exactly 5 seconds, entry should be expired"
            
            print("✅ PASSED: TTL boundary at 5 seconds works correctly")
    
    def test_ttl_just_before_5_seconds(self):
        """Test that entry IS still duplicate just before 5 second mark"""
        print("\n=== TEST: TTL Just Before 5 Seconds ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            key = "ttl_test_key_2"
            
            # First call
            result1 = logger._is_duplicate(key)
            assert result1 == False
            
            # Get the stored timestamp
            stored_time = _logged_trades[key]
            
            # Simulate time advancing to just before 5 seconds (4.999 seconds)
            almost_expired_time = stored_time + _DEDUP_WINDOW_SECONDS - 0.001
            
            with patch('centralized_logger.time.time', return_value=almost_expired_time):
                result2 = logger._is_duplicate(key)
                print(f"Just before 5 seconds (4.999s): is_duplicate = {result2}")
            
            # Just before 5 seconds, should still be duplicate
            assert result2 == True, "Just before 5 seconds, entry should still be duplicate"
            
            print("✅ PASSED: Entry is duplicate just before TTL expires")
    
    def test_ttl_after_5_seconds(self):
        """Test that entry is NOT duplicate after 5 seconds"""
        print("\n=== TEST: TTL After 5 Seconds ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            key = "ttl_test_key_3"
            
            # First call
            result1 = logger._is_duplicate(key)
            assert result1 == False
            
            # Get the stored timestamp
            stored_time = _logged_trades[key]
            
            # Simulate time advancing past 5 seconds
            expired_time = stored_time + _DEDUP_WINDOW_SECONDS + 1
            
            with patch('centralized_logger.time.time', return_value=expired_time):
                result2 = logger._is_duplicate(key)
                print(f"After 6 seconds: is_duplicate = {result2}")
            
            # After 5 seconds, should NOT be duplicate
            assert result2 == False, "After 5 seconds, entry should be expired"
            
            print("✅ PASSED: Entry expired after TTL")


class TestEmptyCycleId:
    """Test handling of empty/None cycle_id"""
    
    def test_empty_cycle_id_string(self):
        """Test behavior with empty string cycle_id"""
        print("\n=== TEST: Empty Cycle ID String ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # Empty string should fall back to order_id based key
            result = logger._is_duplicate("")
            print(f"Empty string key: is_duplicate = {result}")
            
            # Should NOT be duplicate (empty key is treated as new)
            assert result == False
            
            print("✅ PASSED: Empty cycle_id string handled")
    
    def test_none_cycle_id(self):
        """Test behavior when cycle_id is None (falls back to order_id)"""
        print("\n=== TEST: None Cycle ID (Fallback to Order ID) ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # When cycle_id is None, log_trade falls back to order_id + symbol + status
            # Test the dedup key generation logic
            symbol = "BTCUSDT"
            order_id = "12345"
            status = "FILLED"
            
            # Simulate the fallback key generation
            dedup_key = f"trade_{symbol}_{order_id}_{status}"
            print(f"Fallback dedup key: {dedup_key}")
            
            # First call should NOT be duplicate
            result1 = logger._is_duplicate(dedup_key)
            assert result1 == False
            
            # Second call SHOULD be duplicate
            result2 = logger._is_duplicate(dedup_key)
            assert result2 == True
            
            print("✅ PASSED: None cycle_id falls back to order_id correctly")
    
    def test_only_none_values(self):
        """Test log_trade with both cycle_id and order_id as None"""
        print("\n=== TEST: Both Cycle ID and Order ID None ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # When both are None, dedup_key will be None
            # and _write_jsonl should still work (writes without dedup)
            data = {"symbol": "ETHUSDT", "status": "PENDING"}
            
            result1 = logger._write_jsonl(logger.trades_file, data, dedup_key=None)
            result2 = logger._write_jsonl(logger.trades_file, data, dedup_key=None)
            
            print(f"Without dedup_key - First: {result1}, Second: {result2}")
            
            # Both should succeed since there's no dedup_key
            assert result1 == True
            assert result2 == True
            
            # Should have written 2 lines
            with open(logger.trades_file, 'r') as f:
                lines = f.readlines()
            assert len(lines) == 2
            
            print("✅ PASSED: Works correctly when both cycle_id and order_id are None")


class TestMemoryCleanup:
    """Test memory cleanup of old entries"""
    
    def test_cleanup_removes_old_entries(self):
        """Test that old entries are cleaned up from memory"""
        print("\n=== TEST: Memory Cleanup of Old Entries ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # Add entries with different ages
            current_time = time.time()
            
            # Add some entries
            _logged_trades["fresh_1"] = current_time
            _logged_trades["fresh_2"] = current_time - 2  # 2 seconds old
            _logged_trades["old_1"] = current_time - 10  # 10 seconds old (expired)
            _logged_trades["old_2"] = current_time - 15  # 15 seconds old (expired)
            _logged_trades["boundary"] = current_time - 5.001  # Just expired
            
            initial_count = len(_logged_trades)
            print(f"Initial entries: {initial_count}")
            print(f"Entries: {dict(_logged_trades)}")
            
            # Call _is_duplicate which triggers cleanup
            logger._is_duplicate("new_entry")
            
            final_count = len(_logged_trades)
            print(f"After cleanup: {final_count}")
            print(f"Remaining: {dict(_logged_trades)}")
            
            # Old entries should be removed
            assert "old_1" not in _logged_trades
            assert "old_2" not in _logged_trades
            assert "boundary" not in _logged_trades
            
            # Fresh entries should remain
            assert "fresh_1" in _logged_trades
            assert "fresh_2" in _logged_trades
            
            # New entry should be added
            assert "new_entry" in _logged_trades
            
            print("✅ PASSED: Old entries are cleaned up properly")
    
    def test_no_memory_leak_after_many_operations(self):
        """Test that memory doesn't grow unbounded"""
        print("\n=== TEST: No Memory Leak ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # Add many entries with staggered timestamps
            current_time = time.time()
            
            # Add 100 entries
            for i in range(100):
                _logged_trades[f"key_{i}"] = current_time - (i % 10)  # Ages 0-9 seconds
            
            print(f"Entries before cleanup: {len(_logged_trades)}")
            
            # Simulate time passing - set current time to 20 seconds later
            future_time = current_time + 20
            
            with patch('centralized_logger.time.time', return_value=future_time):
                # Add new entry - triggers cleanup
                logger._is_duplicate("new_key")
            
            final_count = len(_logged_trades)
            print(f"Entries after cleanup: {final_count}")
            
            # Should only have the new entry (all 100 old ones expired)
            # Because 20 seconds > 5 second window
            assert final_count <= 10, f"Expected <= 10 entries, got {final_count}"
            
            print("✅ PASSED: No memory leak after many operations")
    
    def test_concurrent_cleanup(self):
        """Test cleanup works correctly with concurrent access"""
        print("\n=== TEST: Concurrent Cleanup ===")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = CentralizedLogger(log_path=tmpdir)
            _logged_trades.clear()
            
            # Add old entries
            current_time = time.time()
            for i in range(50):
                _logged_trades[f"old_{i}"] = current_time - 10  # All expired
            
            errors = []
            
            def add_and_check():
                try:
                    # This should trigger cleanup
                    key = f"thread_{threading.current_thread().ident}"
                    logger._is_duplicate(key)
                except Exception as e:
                    errors.append(str(e))
            
            threads = []
            for _ in range(10):
                t = threading.Thread(target=add_and_check)
                threads.append(t)
            
            for t in threads:
                t.start()
            
            for t in threads:
                t.join()
            
            print(f"Errors: {errors}")
            assert len(errors) == 0
            
            # Old entries should be cleaned up
            for i in range(50):
                assert f"old_{i}" not in _logged_trades
            
            print("✅ PASSED: Concurrent cleanup works correctly")


def run_all_tests():
    """Run all edge case tests"""
    print("=" * 60)
    print("RUNNING DEDUPLICATION EDGE CASE TESTS")
    print("=" * 60)
    
    test_classes = [
        TestConcurrentWrites,
        TestTTLBoundary,
        TestEmptyCycleId,
        TestMemoryCleanup,
    ]
    
    passed = 0
    failed = 0
    
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith('test_'):
                try:
                    print(f"\n--- Running {test_class.__name__}.{method_name} ---")
                    getattr(instance, method_name)()
                    passed += 1
                except Exception as e:
                    print(f"❌ FAILED: {test_class.__name__}.{method_name}")
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
