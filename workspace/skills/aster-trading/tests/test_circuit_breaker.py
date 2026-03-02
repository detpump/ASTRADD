#!/usr/bin/env python3
"""
Test suite for circuit breaker and retry logic in aster_api.py
"""
import pytest
import time
import threading
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.aster_api import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    RetryConfig,
    _calculate_backoff_delay,
    _should_retry,
    set_retry_config,
    reset_circuit_breaker
)


@pytest.fixture(autouse=True)
def reset_global_circuit_breaker():
    """Reset the global circuit breaker before each test to ensure test isolation"""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class TestCircuitBreaker:
    """Tests for CircuitBreaker class"""
    
    def test_initial_state_closed(self):
        """Test that circuit breaker starts in CLOSED state"""
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
    
    def test_can_execute_when_closed(self):
        """Test can_execute returns True when circuit is CLOSED"""
        cb = CircuitBreaker("test")
        assert cb.can_execute() is True
    
    def test_record_failure_opens_circuit(self):
        """Test that recording enough failures opens the circuit"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        cb.record_failure("test error")
        assert cb.state == CircuitState.CLOSED  # Not yet open
        cb.record_failure("test error")
        assert cb.state == CircuitState.CLOSED  # Not yet open
        cb.record_failure("test error")
        assert cb.state == CircuitState.OPEN
    
    def test_cannot_execute_when_open(self):
        """Test can_execute returns False when circuit is OPEN"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1))
        cb.record_failure("test error")
        assert cb.can_execute() is False
    
    def test_record_success_resets_failure_count(self):
        """Test that recording success resets failure count"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        cb.record_failure("error1")
        cb.record_failure("error2")
        assert cb._failure_count == 2
        cb.record_success()
        assert cb._failure_count == 0
    
    def test_half_open_after_timeout(self):
        """Test circuit transitions to HALF_OPEN after timeout"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1
        ))
        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(0.2)
        assert cb.state == CircuitState.HALF_OPEN
    
    def test_half_open_allows_limited_calls(self):
        """Test HALF_OPEN state allows limited calls"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
            half_open_max_calls=2
        ))
        cb.record_failure("error")
        time.sleep(0.2)  # Force half-open
        
        assert cb.can_execute() is True
        cb._half_open_calls = 1
        assert cb.can_execute() is True
        cb._half_open_calls = 2
        assert cb.can_execute() is False
    
    def test_recovery_closes_circuit(self):
        """Test successful calls in HALF_OPEN close the circuit"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
            success_threshold=2
        ))
        cb.record_failure("error")
        time.sleep(0.2)  # Force half-open
        
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
    
    def test_thread_safety(self):
        """Test circuit breaker is thread-safe"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=100))
        
        def record_failures():
            for _ in range(50):
                cb.record_failure("error")
        
        threads = [threading.Thread(target=record_failures) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have recorded 200 failures
        assert cb._failure_count == 200
        assert cb.state == CircuitState.OPEN
    
    def test_get_status(self):
        """Test get_status returns correct info"""
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        cb.record_failure("error1")
        
        status = cb.get_status()
        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["failure_count"] == 1


class TestRetryLogic:
    """Tests for retry logic"""
    
    def test_calculate_backoff_delay(self, monkeypatch):
        """Test exponential backoff delay calculation with deterministic jitter"""
        set_retry_config(RetryConfig(
            base_delay_seconds=1.0,
            exponential_base=2.0,
            max_delay_seconds=10.0
        ))
        
        # Fix jitter to 1.0 by returning 0.5 + 0.5
        import random
        monkeypatch.setattr(random, "random", lambda: 0.5)
        
        assert _calculate_backoff_delay(0) == 1.0
        assert _calculate_backoff_delay(1) == 2.0
        assert _calculate_backoff_delay(2) == 4.0
        assert _calculate_backoff_delay(3) == 8.0
        assert _calculate_backoff_delay(4) == 10.0  # Capped at max
    
    def test_should_retry_timeout(self):
        """Test should_retry returns True for timeout errors"""
        assert _should_retry("Connection timeout") is True
        assert _should_retry("timeout error") is True
    
    def test_should_retry_connection(self):
        """Test should_retry returns True for connection errors"""
        assert _should_retry("Connection error") is True
        assert _should_retry("connection refused") is True
    
    def test_should_retry_429(self):
        """Test should_retry returns True for HTTP 429"""
        assert _should_retry("HTTP 429") is True
        assert _should_retry("rate limit exceeded") is True
    
    def test_should_retry_5xx(self):
        """Test should_retry returns True for 5xx errors"""
        assert _should_retry("HTTP 500") is True
        assert _should_retry("HTTP 502") is True
        assert _should_retry("HTTP 503") is True
    
    def test_should_not_retry_4xx(self):
        """Test should_retry returns False for client errors"""
        assert _should_retry("HTTP 400") is False
        assert _should_retry("HTTP 401") is False
        assert _should_retry("HTTP 403") is False
    
    def test_should_not_retry_empty(self):
        """Test should_retry returns False for empty error"""
        assert _should_retry("") is False
        assert _should_retry(None) is False


class TestRetryConfig:
    """Tests for RetryConfig"""
    
    def test_default_values(self):
        """Test default retry config values"""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay_seconds == 0.5
        assert config.max_delay_seconds == 10.0
        assert config.exponential_base == 2.0
    
    def test_custom_values(self):
        """Test custom retry config values"""
        config = RetryConfig(
            max_retries=5,
            base_delay_seconds=1.0,
            max_delay_seconds=30.0
        )
        assert config.max_retries == 5
        assert config.base_delay_seconds == 1.0
        assert config.max_delay_seconds == 30.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
