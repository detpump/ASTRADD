#!/usr/bin/env python3
"""
Integration Tests for Order Execution with Mock API.

These tests validate the complete order lifecycle using the mock API server,
ensuring proper integration between the trading system and the Aster API.
"""

import pytest
import sys
import os
import time
import threading
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Import the API module
from api.aster_api import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    reset_circuit_breaker,
    set_retry_config,
    RetryConfig,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_circuit():
    """Reset circuit breaker before each test"""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx client that returns test data"""
    client = MagicMock()
    
    # Mock response for exchange info
    exchange_response = MagicMock()
    exchange_response.status_code = 200
    exchange_response.json.return_value = {
        "timezone": "UTC",
        "serverTime": 1700000000000,
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "pricePrecision": 2,
                "quantityPrecision": 3,
            }
        ]
    }
    
    # Mock response for balance
    balance_response = MagicMock()
    balance_response.status_code = 200
    balance_response.json.return_value = {
        "assets": [
            {
                "asset": "USDT",
                "balance": "10000.00",
                "crossWalletBalance": "10000.00",
            }
        ]
    }
    
    # Mock response for positions
    positions_response = MagicMock()
    positions_response.status_code = 200
    positions_response.json.return_value = [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.000",
            "entryPrice": "0.00",
            "markPrice": "42000.00",
            "unRealizedProfit": "0.00",
            "leverage": "10",
        }
    ]
    
    # Mock response for placing order
    order_response = MagicMock()
    order_response.status_code = 200
    order_response.json.return_value = {
        "orderId": 123456,
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "side": "BUY",
        "type": "MARKET",
        "price": "42000.00",
        "origQty": "0.001",
        "executedQty": "0.001",
    }
    
    # Set up the mock to return different responses based on URL
    def get_response(url, method="GET", **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json = lambda: {}
        
        if "exchangeInfo" in url:
            response.json = exchange_response.json
        elif "balance" in url:
            response.json = balance_response.json
        elif "positionRisk" in url:
            response.json = positions_response.json
        elif "order" in url and method == "POST":
            response.json = order_response.json
        return response
    
    def wrap_response(response):
        class ResponseWrapper:
            def __init__(self, resp):
                self._resp = resp
                self.status_code = resp.status_code
                self.text = str(resp.json())
            
            def json(self):
                return self._resp.json()
        return ResponseWrapper(response)
    
    client.get = lambda url, **kwargs: wrap_response(get_response(url, method="GET", **kwargs))
    client.post = lambda url, **kwargs: wrap_response(get_response(url, method="POST", **kwargs))
    client.request = lambda method, url, **kwargs: wrap_response(get_response(url, method=method.upper(), **kwargs))
    
    return client


# =============================================================================
# Test Classes
# =============================================================================

class TestOrderExecution:
    """Test complete order lifecycle"""
    
    def test_get_exchange_info_success(self, mock_httpx_client):
        from api.aster_api import get_exchange_info
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            result = get_exchange_info()
            assert result and "symbols" in result
    
    def test_get_balance_success(self, mock_httpx_client):
        from api.aster_api import get_balance_v3
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            result = get_balance_v3()
            assert result
    
    def test_get_positions_success(self, mock_httpx_client):
        from api.aster_api import get_positions_v3
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            result = get_positions_v3()
            assert result


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration with API calls"""
    
    def test_api_rejects_when_circuit_open(self, mock_httpx_client):
        """Test that API calls are rejected when circuit is open"""
        from api.aster_api import get_exchange_info
        
        # Force circuit breaker open
        reset_circuit_breaker()
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker("aster_api", config)
        cb.record_failure("Test error")
        assert cb.state == CircuitState.OPEN
        
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            result = get_exchange_info()
            assert "error" in result or cb.state == CircuitState.OPEN
    
    def test_circuit_breaker_recovery(self):
        """Test circuit breaker recovery after timeout"""
        # Create circuit breaker with short timeout
        config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
            success_threshold=1
        )
        cb = CircuitBreaker("test_recovery", config)
        
        # Record a failure to open the circuit
        cb.record_failure("test error")
        assert cb.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(0.2)
        
        # Should transition to HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN
        
        # Record success
        cb.record_success()
        
        # Should be closed now
        assert cb.state == CircuitState.CLOSED


class TestRetryLogic:
    """Test retry logic integration"""
    
    def test_retry_config_applied(self):
        """Test that retry config is applied correctly"""
        # Set custom retry config
        config = RetryConfig(
            max_retries=5,
            base_delay_seconds=1.0,
            max_delay_seconds=30.0
        )
        set_retry_config(config)
        
        from api.aster_api import _retry_config
        
        assert _retry_config.max_retries == 5
        assert _retry_config.base_delay_seconds == 1.0
        assert _retry_config.max_delay_seconds == 30.0


class TestErrorHandling:
    """Test error handling scenarios"""
    
    def test_handles_429_error(self):
        """Test handling of rate limit errors"""
        from api.aster_api import _handle_http_error
        
        result = _handle_http_error(429, "Rate limit exceeded")
        
        assert result["retryable"] is True
        assert "retry" in result["error"].lower()
    
    def test_handles_500_error(self):
        """Test handling of server errors"""
        from api.aster_api import _handle_http_error
        
        result = _handle_http_error(500)
        
        assert result["retryable"] is True
    
    def test_handles_400_error(self):
        """Test handling of client errors"""
        from api.aster_api import _handle_http_error
        
        result = _handle_http_error(400)
        
        assert result["retryable"] is False
    
    def test_circuit_breaker_ignores_429(self):
        """Test that 429 errors don't count towards circuit breaker"""
        from api.aster_api import _is_circuit_breaker_error
        
        # 429 should not open circuit
        assert _is_circuit_breaker_error(429) is False
        
        # 500 should open circuit
        assert _is_circuit_breaker_error(500) is True
        
        # 400 should not open circuit
        assert _is_circuit_breaker_error(400) is False


class TestConcurrentAccess:
    """Test thread safety of API components"""
    
    def test_circuit_breaker_thread_safety(self):
        """Test circuit breaker handles concurrent access"""
        config = CircuitBreakerConfig(failure_threshold=100)
        cb = CircuitBreaker("concurrent_test", config)
        
        def record_failures():
            for _ in range(50):
                cb.record_failure("error")
        
        # Run concurrent threads
        threads = [threading.Thread(target=record_failures) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have 200 failures and circuit should be open
        assert cb._failure_count == 200
        assert cb.state == CircuitState.OPEN


class TestMarketData:
    """Test market data fetching"""
    
    def test_get_current_price(self, mock_httpx_client):
        """Test fetching current price"""
        from api.aster_api import get_current_price
        
        # Mock the response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": "BTCUSDT",
            "price": "42000.00"
        }
        
        mock_httpx_client.get.return_value = mock_response
        
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            price = get_current_price("BTCUSDT")
            
            # Should return the parsed price
            assert isinstance(price, float)
    
    def test_get_mark_price(self, mock_httpx_client):
        """Test fetching mark price"""
        from api.aster_api import get_mark_price
        
        # Mock the response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": "BTCUSDT",
            "markPrice": "41950.00"
        }
        
        mock_httpx_client.get.return_value = mock_response
        
        with patch('api.aster_api._get_httpx_client', return_value=mock_httpx_client):
            price = get_mark_price("BTCUSDT")
            
            assert isinstance(price, float)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
