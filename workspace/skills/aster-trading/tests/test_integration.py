#!/usr/bin/env python3
"""
Integration tests for component interactions.

Tests the interaction between:
- Validation modules
- Security modules
- Monitoring modules
- Circuit breaker
"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from validation.input_validator import (
    InputValidator,
    TradeValidator,
    ValidationResult,
    validate_order,
    validate_risk_params,
    safe_execute
)
from validation.security import (
    SecurityValidator,
    RateLimiter,
    validate_sql,
    validate_script,
    sanitize
)
from api.aster_api import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerConfig
)


class TestValidationSecurityIntegration:
    """Integration tests for validation and security modules working together."""
    
    def test_order_validation_with_security_sanitization(self):
        """Test that order validation uses security sanitization."""
        # Create a potentially malicious order
        order = {
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "price": "50000; DROP TABLE orders; --",  # SQL injection attempt
            "side": "BUY"
        }
        
        # Sanitize the order before validation
        sanitized_order = {
            "symbol": sanitize(order["symbol"]),
            "quantity": order["quantity"],
            "price": sanitize(str(order["price"])),
            "side": sanitize(order["side"])
        }
        
        # Validate the sanitized order
        result = validate_order(sanitized_order)
        assert result.is_valid, f"Order validation failed: {result.errors}"
    
    def test_risk_params_with_input_validation(self):
        """Test risk parameter validation with input validation."""
        risk_params = {
            "max_position_size": 0.1,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.1,
            "max_daily_loss": 0.05
        }
        
        result = validate_risk_params(risk_params)
        assert result.is_valid, f"Risk params validation failed: {result.errors}"
    
    def test_rate_limiter_with_validation(self):
        """Test rate limiter combined with input validation."""
        limiter = RateLimiter(max_calls=5, window_seconds=60)
        
        # Validate inputs first
        validator = InputValidator()
        
        # Valid identifier
        result = validator.validate_string_pattern(
            "test_user_123",
            r"^[a-zA-Z0-9_]+$",
            "identifier"
        )
        assert result.is_valid
        
        # Make valid calls within limit
        for i in range(5):
            allowed = limiter.is_allowed(f"test_user_123")
            assert allowed, f"Call {i+1} should be allowed"
        
        # 6th call should be blocked
        blocked = not limiter.is_allowed("test_user_123")
        assert blocked, "6th call should be blocked by rate limiter"


class TestCircuitBreakerValidationIntegration:
    """Integration tests for circuit breaker with validation."""
    
    def test_circuit_breaker_with_validation_failure_handling(self):
        """Test circuit breaker integration with validation errors."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=0.05,
            success_threshold=2,
            half_open_max_calls=2
        )
        cb = CircuitBreaker('test', config)
        
        # Initial state should be CLOSED
        assert cb.state == CircuitState.CLOSED
        
        # Record failures (simulating validation failures)
        cb.record_failure("Validation error: invalid symbol")
        cb.record_failure("Validation error: invalid quantity")
        cb.record_failure("Validation error: invalid price")
        
        # Circuit should be OPEN now
        assert cb.state == CircuitState.OPEN
        
        # Cannot execute when circuit is open
        assert not cb.can_execute()
        
        # Wait for half-open transition
        time.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN
        
        # Can execute in half-open state
        assert cb.can_execute()
        
        # Record successes to close circuit
        cb.record_success()
        cb.record_success()
        
        # Circuit should be CLOSED again
        assert cb.state == CircuitState.CLOSED
    
    def test_validation_errors_propagate_to_circuit_breaker(self):
        """Test that validation errors properly propagate to circuit breaker."""
        validator = InputValidator()
        config = CircuitBreakerConfig(
            failure_threshold=2,
            timeout_seconds=0.05,
            success_threshold=1,
            half_open_max_calls=1
        )
        cb = CircuitBreaker('validation_test', config)
        security = SecurityValidator()

        # Simulate validation failures
        invalid_orders = [
            {"symbol": "", "quantity": -1},  # Invalid (empty symbol)
            {"symbol": "INVALID", "quantity": 0},  # Invalid quantity
        ]

        for order in invalid_orders:
            symbol_result = validator.validate_type(order.get("symbol", ""), "str", "symbol")
            security_result = security.validate_symbol(order.get("symbol", ""))
            quantity_result = validator.validate_range(order.get("quantity", 0), min_val=0.001, field_name="quantity")
            combined_errors = []
            for result in (symbol_result, security_result, quantity_result):
                if not result.is_valid:
                    combined_errors.extend(result.errors)
            if combined_errors:
                cb.record_failure(f"Validation failed: {combined_errors}")

        # Circuit should be open after failures
        assert cb.state == CircuitState.OPEN


class TestSafeExecutionWithModules:
    """Integration tests for safe execution with various modules."""
    
    def test_safe_execute_with_validator(self):
        """Test safe execution wrapper with validator."""
        def validated_operation(value: int) -> int:
            if value < 0:
                raise ValueError("Value must be positive")
            return value * 2
        
        # Valid execution
        result = safe_execute(validated_operation, 5, default=0)
        assert result == 10
        
        # Invalid execution with default
        result = safe_execute(validated_operation, -5, default=0)
        assert result == 0
    
    def test_safe_execute_with_security_validator(self):
        """Test safe execution with security validation."""
        def secure_operation(user_input: str) -> str:
            # Simulate some processing
            return f"Processed: {user_input}"
        
        # Valid input
        result = safe_execute(secure_operation, "normal input", default="error")
        assert result == "Processed: normal input"
        
        # Potentially dangerous input - should still execute but sanitized
        dangerous_input = "'; DROP TABLE users; --"
        sanitized = sanitize(dangerous_input)
        result = safe_execute(secure_operation, sanitized, default="error")
        assert result != "error"
        assert result.startswith("Processed:")
    
    def test_safe_execute_exception_handling(self):
        """Test that safe_execute properly handles exceptions."""
        def failing_operation():
            raise RuntimeError("Intentional error")
        
        result = safe_execute(failing_operation, default="fallback")
        assert result == "fallback"


class TestValidationChaining:
    """Integration tests for chaining validation operations."""
    
    def test_multi_stage_validation(self):
        """Test multi-stage validation pipeline."""
        validator = InputValidator()
        security = SecurityValidator()
        
        # Stage 1: Type validation
        result = validator.validate_type(123, "str", "field")
        assert not result.is_valid
        
        # Stage 2: Security validation on valid input
        result = security.validate_symbol("BTCUSDT")
        assert result.is_valid
        
        # Stage 3: Pattern validation
        result = validator.validate_string_pattern(
            "BTCUSDT",
            r"^[A-Z]{5,12}$",
            "symbol"
        )
        assert result.is_valid
    
    def test_validation_pipeline_with_risk_check(self):
        """Test validation pipeline with risk checking."""
        # Valid trade parameters
        trade = {
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "price": 50000.0,
            "leverage": 10
        }
        
        # Validate order
        order_result = validate_order(trade)
        assert order_result.is_valid
        
        # Validate risk params
        risk_params = {
            "max_position_size": trade["quantity"] * trade["price"] / 1000,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.1
        }
        risk_result = validate_risk_params(risk_params)
        assert risk_result.is_valid
        
        # Both validations passed
        assert order_result.is_valid and risk_result.is_valid


class TestErrorRecoveryIntegration:
    """Integration tests for error recovery scenarios."""
    
    def test_circuit_breaker_recovery_with_validation(self):
        """Test circuit breaker recovery combined with validation recovery."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            timeout_seconds=0.05,
            success_threshold=1,
            half_open_max_calls=1
        )
        cb = CircuitBreaker('recovery_test', config)
        
        # Simulate failures
        cb.record_failure("Error 1")
        cb.record_failure("Error 2")
        
        # Circuit is open
        assert cb.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(0.1)
        
        # Should be half-open
        assert cb.state == CircuitState.HALF_OPEN
        
        # Try with valid operation
        validator = InputValidator()
        valid_input = "VALID"
        result = validator.validate_string_pattern(valid_input, r"^[A-Z]+$", "test")
        
        if result.is_valid:
            cb.record_success()
        
        # Circuit should be closed after success
        assert cb.state == CircuitState.CLOSED
    
    def test_graceful_degradation_with_fallbacks(self):
        """Test graceful degradation when components fail."""
        limiter = RateLimiter(max_calls=1, window_seconds=60)
        
        # First call succeeds
        assert limiter.is_allowed("user1")
        
        # Second call would be blocked, use fallback
        def fallback_operation():
            return "fallback_result"
        
        # If rate limited, use fallback
        if not limiter.is_allowed("user1"):
            result = fallback_operation()
        else:
            result = "primary_result"
        
        assert result == "fallback_result"


class TestMonitoringIntegration:
    """Integration tests for monitoring with other components."""
    
    def test_validation_metrics_collection(self):
        """Test that validation results can be collected for metrics."""
        validator = InputValidator()
        
        valid_count = 0
        invalid_count = 0
        
        # Run multiple validations
        test_cases = [
            (True, "BTCUSDT"),
            (True, "ETHUSDT"),
            (False, ""),
            (True, "BNBUSDT"),
            (False, "invalid"),
        ]
        
        for expected_valid, value in test_cases:
            result = validator.validate_string_pattern(
                value,
                r"^[A-Z]{2,10}$",
                "symbol"
            )
            if result.is_valid:
                valid_count += 1
            else:
                invalid_count += 1
        
        # Check metrics
        assert valid_count == 3
        assert invalid_count == 2


class TestEndToEndScenarios:
    """End-to-end integration test scenarios."""
    
    def test_complete_order_processing_flow(self):
        """Test complete order processing with all validations."""
        # 1. Input validation
        order = {
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "price": 50000.0,
            "side": "BUY"
        }
        
        # 2. Sanitize inputs
        sanitized = {k: sanitize(str(v)) for k, v in order.items()}
        
        # 3. Validate
        result = validate_order(sanitized)
        assert result.is_valid, f"Order validation failed: {result.errors}"
        
        # 4. Check rate limit
        limiter = RateLimiter(max_calls=100, window_seconds=60)
        assert limiter.is_allowed("trading_user")
        
        # 5. All checks passed
        assert result.is_valid
    
    def test_rejected_order_flow(self):
        """Test order rejection flow through all validations."""
        # 1. Malicious input
        order = {
            "symbol": "<script>alert('xss')</script>",
            "quantity": -100,  # Invalid negative
            "price": "50000' OR '1'='1",  # SQL injection
            "side": "INVALID_SIDE"
        }
        
        # 2. Sanitize first
        sanitized = {k: sanitize(str(v)) for k, v in order.items()}
        
        # 3. Validate - should fail
        result = validate_order(sanitized)
        assert not result.is_valid or len(result.errors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
