#!/usr/bin/env python3
"""
Comprehensive tests for validation and security modules.

Tests:
- Input validation
- Security validation
- Trade validation
- Edge cases
- Boundary conditions
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from validation.input_validator import (
    InputValidator,
    TradeValidator,
    ValidationResult,
    validate_order,
    validate_risk_params,
    safe_execute,
    validate_required_fields
)

from validation.security import (
    SecurityValidator,
    RateLimiter,
    SecurityValidationResult,
    verify_hmac_signature,
    validate_sql,
    validate_script,
    sanitize
)


class TestInputValidator:
    """Tests for InputValidator class"""
    
    def test_validate_type_valid(self):
        """Test type validation with valid input"""
        validator = InputValidator()
        result = validator.validate_type("test", "str", "field")
        assert result.is_valid
    
    def test_validate_type_invalid(self):
        """Test type validation with invalid input"""
        validator = InputValidator()
        result = validator.validate_type(123, "str", "field")
        assert not result.is_valid
        assert "expected str" in str(result.errors)
    
    def test_validate_range_valid(self):
        """Test range validation with valid input"""
        validator = InputValidator()
        result = validator.validate_range(5, min_val=0, max_val=10, field_name="value")
        assert result.is_valid
    
    def test_validate_range_below_min(self):
        """Test range validation below minimum"""
        validator = InputValidator()
        result = validator.validate_range(-1, min_val=0, max_val=10, field_name="value")
        assert not result.is_valid
    
    def test_validate_range_above_max(self):
        """Test range validation above maximum"""
        validator = InputValidator()
        result = validator.validate_range(100, min_val=0, max_val=10, field_name="value")
        assert not result.is_valid
    
    def test_validate_string_pattern_valid(self):
        """Test string pattern validation"""
        validator = InputValidator()
        result = validator.validate_string_pattern("BTCUSDT", r"^[A-Z]+$", "symbol")
        assert result.is_valid
    
    def test_validate_string_pattern_invalid(self):
        """Test string pattern validation with invalid input"""
        validator = InputValidator()
        result = validator.validate_string_pattern("btcusdt", r"^[A-Z]+$", "symbol")
        assert not result.is_valid
    
    def test_validate_string_length(self):
        """Test string length validation"""
        validator = InputValidator()
        
        # Too short
        result = validator.validate_string_length("ab", min_length=3, max_length=10, field_name="value")
        assert not result.is_valid
        
        # Valid
        result = validator.validate_string_length("abc", min_length=3, max_length=10, field_name="value")
        assert result.is_valid
        
        # Too long
        result = validator.validate_string_length("abcdefghijk", min_length=3, max_length=10, field_name="value")
        assert not result.is_valid
    
    def test_validate_collection_size(self):
        """Test collection size validation"""
        validator = InputValidator()
        
        # Empty list, minimum 1
        result = validator.validate_collection_size([], min_size=1, max_size=10, field_name="items")
        assert not result.is_valid
        
        # Valid
        result = validator.validate_collection_size([1, 2, 3], min_size=1, max_size=10, field_name="items")
        assert result.is_valid
        
        # Too large
        result = validator.validate_collection_size([1]*20, min_size=1, max_size=10, field_name="items")
        assert not result.is_valid
    
    def test_validate_enum(self):
        """Test enum validation"""
        validator = InputValidator()
        
        # Valid
        result = validator.validate_enum("market", ["market", "limit"], "order_type")
        assert result.is_valid
        
        # Invalid
        result = validator.validate_enum("invalid", ["market", "limit"], "order_type")
        assert not result.is_valid
    
    def test_validate_not_none(self):
        """Test not None validation"""
        validator = InputValidator()
        
        # Valid
        result = validator.validate_not_none("value", "field")
        assert result.is_valid
        
        # Invalid
        result = validator.validate_not_none(None, "field")
        assert not result.is_valid
    
    def test_validate_not_empty(self):
        """Test not empty validation"""
        validator = InputValidator()
        
        # Valid
        result = validator.validate_not_empty("value", "field")
        assert result.is_valid
        
        # Invalid - empty string
        result = validator.validate_not_empty("", "field")
        assert not result.is_valid
        
        # Invalid - empty list
        result = validator.validate_not_empty([], "field")
        assert not result.is_valid
        
        # Invalid - None
        result = validator.validate_not_empty(None, "field")
        assert not result.is_valid


class TestTradeValidator:
    """Tests for TradeValidator class"""
    
    def test_validate_order_valid_market(self):
        """Test order validation with valid market order"""
        validator = TradeValidator()
        result = validator.validate_order_params("BTCUSDT", 0.001, order_type="market")
        assert result.is_valid
    
    def test_validate_order_valid_limit(self):
        """Test order validation with valid limit order"""
        validator = TradeValidator()
        result = validator.validate_order_params("BTCUSDT", 0.001, price=50000.0, order_type="limit")
        assert result.is_valid
    
    def test_validate_order_invalid_symbol(self):
        """Test order validation with invalid symbol"""
        validator = TradeValidator()
        result = validator.validate_order_params("btcusdt", 0.001)
        assert not result.is_valid
    
    def test_validate_order_zero_quantity(self):
        """Test order validation with zero quantity"""
        validator = TradeValidator()
        result = validator.validate_order_params("BTCUSDT", 0.0)
        assert not result.is_valid
    
    def test_validate_order_limit_without_price(self):
        """Test limit order validation without price"""
        validator = TradeValidator()
        result = validator.validate_order_params("BTCUSDT", 0.001, order_type="limit")
        assert not result.is_valid
    
    def test_validate_risk_params_valid(self):
        """Test risk parameter validation with valid inputs"""
        validator = TradeValidator()
        result = validator.validate_risk_params(
            max_position_size=0.1,
            max_daily_loss=0.05,
            stop_loss_pct=0.02,
            take_profit_pct=0.04
        )
        assert result.is_valid
    
    def test_validate_risk_params_invalid(self):
        """Test risk parameter validation with invalid inputs"""
        validator = TradeValidator()
        result = validator.validate_risk_params(
            max_position_size=1.5,  # > 100%
            max_daily_loss=0.05,
            stop_loss_pct=0.02,
            take_profit_pct=0.04
        )
        assert not result.is_valid


class TestSecurityValidator:
    """Tests for SecurityValidator class"""
    
    def test_validate_sql_safety_valid(self):
        """Test SQL injection validation with safe input"""
        validator = SecurityValidator()
        result = validator.validate_sql_safety("BTCUSDT", "symbol")
        assert result.is_valid
    
    def test_validate_sql_safety_injection(self):
        """Test SQL injection detection"""
        validator = SecurityValidator()
        result = validator.validate_sql_safety("'; DROP TABLE users;--", "input")
        assert not result.is_valid
    
    def test_validate_script_safety_valid(self):
        """Test script injection validation with safe input"""
        validator = SecurityValidator()
        result = validator.validate_script_safety("Normal text", "input")
        assert result.is_valid
    
    def test_validate_script_safety_injection(self):
        """Test script injection detection"""
        validator = SecurityValidator()
        result = validator.validate_script_safety("<script>alert('xss')</script>", "input")
        assert not result.is_valid
    
    def test_validate_path_safety_valid(self):
        """Test path injection validation with safe input"""
        validator = SecurityValidator()
        result = validator.validate_path_safety("normal/path", "input")
        assert result.is_valid
    
    def test_validate_path_safety_injection(self):
        """Test path injection detection"""
        validator = SecurityValidator()
        result = validator.validate_path_safety("../../../etc/passwd", "input")
        assert not result.is_valid
    
    def test_validate_api_key_format_valid(self):
        """Test API key format validation with valid key"""
        validator = SecurityValidator()
        result = validator.validate_api_key_format("abcd1234efgh5678")
        assert result.is_valid
    
    def test_validate_api_key_format_too_short(self):
        """Test API key format validation with short key"""
        validator = SecurityValidator()
        result = validator.validate_api_key_format("abc")
        assert not result.is_valid
    
    def test_validate_api_key_format_weak(self):
        """Test API key format validation with weak key"""
        validator = SecurityValidator()
        result = validator.validate_api_key_format("test")
        assert not result.is_valid
    
    def test_validate_private_key_format_valid_hex(self):
        """Test private key format validation with valid hex"""
        validator = SecurityValidator()
        # 64 char hex string
        key = "a" * 64
        result = validator.validate_private_key_format(key)
        assert result.is_valid
    
    def test_validate_private_key_format_invalid(self):
        """Test private key format validation with invalid key"""
        validator = SecurityValidator()
        result = validator.validate_private_key_format("invalid_key")
        assert not result.is_valid
    
    def test_validate_symbol_valid(self):
        """Test symbol validation with valid symbol"""
        validator = SecurityValidator()
        result = validator.validate_symbol("BTCUSDT")
        assert result.is_valid
    
    def test_validate_symbol_invalid(self):
        """Test symbol validation with invalid symbol"""
        validator = SecurityValidator()
        result = validator.validate_symbol("btc")  # too short
        assert not result.is_valid
    
    def test_sanitize_input(self):
        """Test input sanitization"""
        validator = SecurityValidator()
        
        # Null byte removal
        result = validator.sanitize_input("test\x00value")
        assert "\x00" not in result
        
        # Control character removal
        result = validator.sanitize_input("test\x01value")
        assert "\x01" not in result


class TestRateLimiter:
    """Tests for RateLimiter class"""
    
    def test_rate_limiter_allows_within_limit(self):
        """Test rate limiter allows requests within limit"""
        limiter = RateLimiter(max_requests=5, time_window_seconds=60.0)
        
        for _ in range(5):
            assert limiter.is_allowed("test_client")
    
    def test_rate_limiter_blocks_over_limit(self):
        """Test rate limiter blocks requests over limit"""
        limiter = RateLimiter(max_requests=3, time_window_seconds=60.0)
        
        # First 3 should pass
        assert limiter.is_allowed("test_client")
        assert limiter.is_allowed("test_client")
        assert limiter.is_allowed("test_client")
        
        # 4th should be blocked
        assert not limiter.is_allowed("test_client")
    
    def test_rate_limiter_separate_identifiers(self):
        """Test rate limiter maintains separate limits per identifier"""
        limiter = RateLimiter(max_requests=2, time_window_seconds=60.0)
        
        assert limiter.is_allowed("client1")
        assert limiter.is_allowed("client1")
        assert not limiter.is_allowed("client1")
        
        # client2 should still be allowed
        assert limiter.is_allowed("client2")
    
    def test_rate_limiter_get_remaining(self):
        """Test getting remaining requests"""
        limiter = RateLimiter(max_requests=5, time_window_seconds=60.0)
        
        limiter.is_allowed("client")
        limiter.is_allowed("client")
        
        assert limiter.get_remaining("client") == 3


class TestConvenienceFunctions:
    """Tests for convenience validation functions"""
    
    def test_validate_order_convenience(self):
        """Test validate_order convenience function"""
        result = validate_order("BTCUSDT", 0.001, order_type="market")
        assert isinstance(result, ValidationResult)
    
    def test_validate_risk_params_convenience(self):
        """Test validate_risk_params convenience function"""
        result = validate_risk_params(0.1, 0.05, 0.02, 0.04)
        assert isinstance(result, ValidationResult)
    
    def test_validate_sql_convenience(self):
        """Test validate_sql convenience function"""
        result = validate_sql("test input", "field")
        assert isinstance(result, SecurityValidationResult)
    
    def test_validate_script_convenience(self):
        """Test validate_script convenience function"""
        result = validate_script("test input", "field")
        assert isinstance(result, SecurityValidationResult)
    
    def test_sanitize_convenience(self):
        """Test sanitize convenience function"""
        result = sanitize("test\x00value")
        assert isinstance(result, str)


class TestSafeExecute:
    """Tests for safe_execute function"""
    
    def test_safe_execute_success(self):
        """Test safe_execute with successful function"""
        def add(a, b):
            return a + b
        
        result = safe_execute(add, 1, 2)
        assert result == 3
    
    def test_safe_execute_with_default(self):
        """Test safe_execute returns default on error"""
        def raise_error():
            raise ValueError("test error")
        
        result = safe_execute(raise_error, default="error occurred")
        assert result == "error occurred"
    
    def test_safe_execute_with_kwargs(self):
        """Test safe_execute with keyword arguments"""
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"
        
        result = safe_execute(greet, "World", greeting="Hi")
        assert result == "Hi, World!"


class TestValidateRequiredFields:
    """Tests for validate_required_fields function"""
    
    def test_validate_required_fields_all_present(self):
        """Test validation when all required fields present"""
        data = {"field1": "value1", "field2": "value2"}
        result = validate_required_fields(data, ["field1", "field2"])
        assert result.is_valid
    
    def test_validate_required_fields_missing(self):
        """Test validation when field is missing"""
        data = {"field1": "value1"}
        result = validate_required_fields(data, ["field1", "field2"])
        assert not result.is_valid
        assert "field2" in str(result.errors)
    
    def test_validate_required_fields_none_value(self):
        """Test validation when field is None"""
        data = {"field1": None}
        result = validate_required_fields(data, ["field1"])
        assert not result.is_valid


class TestHmacVerification:
    """Tests for HMAC signature verification"""
    
    def test_hmac_valid(self):
        """Test HMAC verification with valid signature"""
        import hmac
        message = "test message"
        secret = "test_secret"
        
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            "sha256"
        ).hexdigest()
        
        assert verify_hmac_signature(message, signature, secret)
    
    def test_hmac_invalid(self):
        """Test HMAC verification with invalid signature"""
        assert not verify_hmac_signature("message", "invalid_signature", "secret")


class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""
    
    def test_type_validator_boolean(self):
        """Test type validator handles booleans correctly"""
        validator = InputValidator()
        
        # bool is not int
        result = validator.validate_type(True, "int", "value")
        assert not result.is_valid
        
        # bool is bool
        result = validator.validate_type(True, "bool", "value")
        assert result.is_valid
    
    def test_float_with_int(self):
        """Test type validator accepts int for float"""
        validator = InputValidator()
        result = validator.validate_type(5, "float", "value")
        assert result.is_valid
    
    def test_empty_string_validation(self):
        """Test validation of empty strings"""
        validator = InputValidator()
        
        result = validator.validate_not_empty("", "test")
        assert not result.is_valid
        
        result = validator.validate_string_length("", min_length=0, max_length=10, field_name="test")
        assert result.is_valid
    
    def test_unicode_handling(self):
        """Test validation handles unicode"""
        validator = InputValidator()
        
        result = validator.validate_string_pattern("₿₮ℂ", r"^[A-Z]+$", "symbol")
        assert not result.is_valid
        
        result = validator.validate_string_pattern("BTC", r"^[A-Z]+$", "symbol")
        assert result.is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
