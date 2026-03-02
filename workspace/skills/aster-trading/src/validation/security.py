#!/usr/bin/env python3
"""
Security Validation Module for Aster Trading System

Provides security validation functions, injection prevention, and secure coding practices.

Author: Aster Trading V2
Date: 2026-02-26
"""

import re
import logging
import hashlib
import hmac
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Exception for security validation failures"""
    def __init__(self, message: str, error_code: str = "SECURITY_ERROR"):
        self.error_code = error_code
        super().__init__(message)


@dataclass
class SecurityValidationResult:
    """Result of security validation"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    
    def __bool__(self):
        return self.is_valid


class SecurityValidator:
    """
    Security validation for trading system inputs and operations.
    """
    
    # Dangerous patterns for injection attacks
    SQL_INJECTION_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE|UNION)\b)",
        r"(--|#|/\*|\*/)",
        r"(\bOR\b.*\b=\b|\bAND\b.*\b=\b)",
    ]
    
    SCRIPT_INJECTION_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe[^>]*>",
        r"eval\s*\(",
        r"expression\s*\(",
    ]
    
    PATH_INJECTION_PATTERNS = [
        r"\.\.\/",
        r"^\/",
        r"^\\[a-zA-Z]:\\",
        r"%2e%2e",
        r"\betc\/passwd\b",
    ]
    
    def __init__(self):
        self._compiled_sql_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.SQL_INJECTION_PATTERNS
        ]
        self._compiled_script_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.SCRIPT_INJECTION_PATTERNS
        ]
        self._compiled_path_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.PATH_INJECTION_PATTERNS
        ]
    
    def validate_sql_safety(self, value: str, field_name: str = "input") -> SecurityValidationResult:
        """
        Validate string is safe from SQL injection attacks.
        
        Args:
            value: String to validate
            field_name: Name of field being validated
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not isinstance(value, str):
            result.errors.append(f"Field '{field_name}' must be a string")
            result.is_valid = False
            return result
        
        for pattern in self._compiled_sql_patterns:
            if pattern.search(value):
                result.errors.append(
                    f"Potential SQL injection detected in '{field_name}'"
                )
                result.is_valid = False
                logger.warning(f"SQL injection attempt detected in field: {field_name}")
        
        return result
    
    def validate_script_safety(self, value: str, field_name: str = "input") -> SecurityValidationResult:
        """
        Validate string is safe from script injection attacks.
        
        Args:
            value: String to validate
            field_name: Name of field being validated
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not isinstance(value, str):
            result.errors.append(f"Field '{field_name}' must be a string")
            result.is_valid = False
            return result
        
        for pattern in self._compiled_script_patterns:
            if pattern.search(value):
                result.errors.append(
                    f"Potential script injection detected in '{field_name}'"
                )
                result.is_valid = False
                logger.warning(f"Script injection attempt detected in field: {field_name}")
        
        return result
    
    def validate_path_safety(self, value: str, field_name: str = "path") -> SecurityValidationResult:
        """
        Validate string is safe from path injection attacks.
        
        Args:
            value: String to validate
            field_name: Name of field being validated
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not isinstance(value, str):
            result.errors.append(f"Field '{field_name}' must be a string")
            result.is_valid = False
            return result
        
        for pattern in self._compiled_path_patterns:
            if pattern.search(value):
                result.errors.append(
                    f"Potential path injection detected in '{field_name}'"
                )
                result.is_valid = False
                logger.warning(f"Path injection attempt detected in field: {field_name}")
        
        return result
    
    def validate_api_key_format(self, api_key: str) -> SecurityValidationResult:
        """
        Validate API key format is correct.
        
        Args:
            api_key: API key to validate
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not api_key:
            result.errors.append("API key cannot be empty")
            result.is_valid = False
            return result
        
        # Check minimum length
        if len(api_key) < 16:
            result.errors.append("API key too short (minimum 16 characters)")
            result.is_valid = False
        
        # Check for common weak patterns
        if api_key.lower() in ['test', 'demo', 'password', '123456', 'api_key']:
            result.errors.append("API key appears to be a default or weak value")
            result.is_valid = False
        
        # Check character composition
        has_letter = any(c.isalpha() for c in api_key)
        has_digit = any(c.isdigit() for c in api_key)
        
        if not (has_letter and has_digit):
            result.warnings.append(
                "API key should contain both letters and numbers"
            )
        
        return result
    
    def validate_private_key_format(self, private_key: str) -> SecurityValidationResult:
        """
        Validate private key format (hex or base64).
        
        Args:
            private_key: Private key to validate
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not private_key:
            result.errors.append("Private key cannot be empty")
            result.is_valid = False
            return result
        
        # Check length for hex (64 chars for 32 bytes) or base64
        is_hex = all(c in '0123456789abcdefABCDEF' for c in private_key)
        is_base64 = all(c in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/=' for c in private_key)
        
        if not (is_hex or is_base64):
            result.errors.append(
                "Private key must be in hex or base64 format"
            )
            result.is_valid = False
        
        if is_hex and len(private_key) != 64:
            result.errors.append(
                f"Hex private key must be 64 characters, got {len(private_key)}"
            )
            result.is_valid = False
        
        return result
    
    def validate_symbol(self, symbol: str) -> SecurityValidationResult:
        """
        Validate trading symbol format.
        
        Args:
            symbol: Trading symbol to validate
        
        Returns:
            SecurityValidationResult with validation status
        """
        result = SecurityValidationResult(is_valid=True, errors=[], warnings=[])
        
        if not symbol:
            result.errors.append("Symbol cannot be empty")
            result.is_valid = False
            return result
        
        # Check format: uppercase letters and numbers, 2-10 chars
        if not re.match(r'^[A-Z0-9]{2,10}$', symbol):
            result.errors.append(
                f"Invalid symbol format: {symbol}. Expected 2-10 uppercase alphanumeric characters"
            )
            result.is_valid = False
        
        return result
    
    def sanitize_input(self, value: str) -> str:
        """
        Sanitize input by removing potentially dangerous characters.
        
        Args:
            value: String to sanitize
        
        Returns:
            Sanitized string
        """
        if not isinstance(value, str):
            return str(value)
        
        # Remove null bytes
        sanitized = value.replace('\x00', '')
        
        # Remove control characters
        sanitized = ''.join(
            c for c in sanitized if ord(c) >= 32 or c in '\n\r\t'
        )
        
        return sanitized.strip()


class RateLimiter:
    """Rate limiter for API requests and operations."""
    
    def __init__(
        self,
        max_requests: int = 100,
        time_window_seconds: float = 60.0,
        *,
        max_calls: Optional[int] = None,
        window_seconds: Optional[float] = None,
        allow_burst: bool = False,
        burst_limit: Optional[int] = None
    ):
        # Allow legacy parameter names used by tests (max_calls/window_seconds)
        if max_calls is not None:
            max_requests = max_calls
        if window_seconds is not None:
            time_window_seconds = window_seconds
        self.max_requests = max_requests
        self.time_window_seconds = time_window_seconds
        self.allow_burst = allow_burst
        self.burst_limit = burst_limit if burst_limit is not None else max_requests
        self._requests: Dict[str, List[float]] = {}
    
    def is_allowed(self, identifier: str) -> bool:
        """
        Check if request is allowed for given identifier.
        
        Args:
            identifier: Client identifier (IP, API key, etc.)
        
        Returns:
            True if request is allowed, False if rate limited
        """
        import time
        
        current_time = time.time()
        
        if identifier not in self._requests:
            self._requests[identifier] = []
        
        # Remove old requests outside time window
        self._requests[identifier] = [
            t for t in self._requests[identifier]
            if current_time - t < self.time_window_seconds
        ]
        
        # Check if under limit
        if len(self._requests[identifier]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for: {identifier}")
            return False
        
        # Add current request
        self._requests[identifier].append(current_time)
        return True
    
    def get_remaining(self, identifier: str) -> int:
        """Get remaining requests for identifier"""
        import time
        
        current_time = time.time()
        
        if identifier not in self._requests:
            return self.max_requests
        
        # Count requests in window
        in_window = sum(
            1 for t in self._requests[identifier]
            if current_time - t < self.time_window_seconds
        )
        
        return max(0, self.max_requests - in_window)
    
    def reset(self, identifier: str = None):
        """Reset rate limit for identifier or all"""
        if identifier:
            self._requests.pop(identifier, None)
        else:
            self._requests.clear()


def verify_hmac_signature(
    message: str,
    signature: str,
    secret: str,
    algorithm: str = "sha256"
) -> bool:
    """
    Verify HMAC signature of a message.
    
    Args:
        message: Original message
        signature: Provided signature to verify
        secret: Secret key
        algorithm: Hash algorithm (sha256, sha512, etc.)
    
    Returns:
        True if signature is valid
    """
    try:
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            algorithm
        ).hexdigest()
        
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        logger.error(f"Error verifying HMAC: {e}")
        return False


# Singleton instances
_security_validator = SecurityValidator()
_rate_limiter = RateLimiter()


def validate_sql(*args, **kwargs) -> SecurityValidationResult:
    """Convenience function for SQL injection validation"""
    return _security_validator.validate_sql_safety(*args, **kwargs)


def validate_script(*args, **kwargs) -> SecurityValidationResult:
    """Convenience function for script injection validation"""
    return _security_validator.validate_script_safety(*args, **kwargs)


def sanitize(*args, **kwargs) -> str:
    """Convenience function for input sanitization"""
    return _security_validator.sanitize_input(*args, **kwargs)
