#!/usr/bin/env python3
"""
Input Validation Module for Aster Trading System

Provides comprehensive input validation, type checking, and defensive programming utilities
for all trading system components.

Author: Aster Trading V2
Date: 2026-02-26
"""

import re
import logging
from typing import Any, Dict, List, Optional, Union, Callable
from dataclasses import dataclass
from enum import Enum
import traceback

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom exception for validation errors"""
    def __init__(self, field: str, message: str, value: Any = None):
        self.field = field
        self.message = message
        self.value = value
        super().__init__(f"Validation error for '{field}': {message}")


class ValidationResult:
    """Result of a validation operation"""
    def __init__(self, is_valid: bool = True, errors: List[str] = None, warnings: List[str] = None):
        self.is_valid = is_valid
        self.errors = errors or []
        self.warnings = warnings or []
    
    def add_error(self, error: str):
        self.is_valid = False
        self.errors.append(error)
    
    def add_warning(self, warning: str):
        """Add a warning that doesn't invalidate the result"""
        self.warnings.append(warning)
    
    def __bool__(self):
        return self.is_valid


class InputValidator:
    """
    Comprehensive input validation with type checking and defensive programming.
    
    Supports:
    - Type validation
    - Range validation
    - String pattern matching
    - Collection validation
    - Custom validators
    """
    
    # Type validators
    TYPE_VALIDATORS = {
        'str': lambda x: isinstance(x, str),
        'int': lambda x: isinstance(x, int) and not isinstance(x, bool),
        'float': lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        'bool': lambda x: isinstance(x, bool),
        'list': lambda x: isinstance(x, list),
        'dict': lambda x: isinstance(x, dict),
    }
    
    def __init__(self):
        self._custom_validators: Dict[str, Callable] = {}
    
    def register_validator(self, name: str, validator: Callable[[Any], bool]):
        """Register a custom validator function"""
        self._custom_validators[name] = validator
    
    def validate_type(self, value: Any, expected_type: str, field_name: str) -> ValidationResult:
        """Validate value is of expected type"""
        result = ValidationResult()
        
        if expected_type not in self.TYPE_VALIDATORS:
            result.add_error(f"Unknown type: {expected_type}")
            return result
        
        if not self.TYPE_VALIDATORS[expected_type](value):
            result.add_error(
                f"Field '{field_name}' expected {expected_type}, got {type(value).__name__}"
            )
        return result
    
    def validate_range(
        self, 
        value: Union[int, float], 
        min_val: Optional[Union[int, float]] = None,
        max_val: Optional[Union[int, float]] = None,
        field_name: str = "value"
    ) -> ValidationResult:
        """Validate numeric value is within range"""
        result = ValidationResult()
        
        if min_val is not None and value < min_val:
            result.add_error(f"Field '{field_name}' value {value} is below minimum {min_val}")
        
        if max_val is not None and value > max_val:
            result.add_error(f"Field '{field_name}' value {value} exceeds maximum {max_val}")
        
        return result
    
    def validate_string_pattern(
        self, 
        value: str, 
        pattern: str, 
        field_name: str = "string"
    ) -> ValidationResult:
        """Validate string matches regex pattern"""
        result = ValidationResult()
        
        if not isinstance(value, str):
            result.add_error(f"Field '{field_name}' must be a string")
            return result
        
        if not re.match(pattern, value):
            result.add_error(f"Field '{field_name}' does not match required pattern")
        
        return result
    
    def validate_string_length(
        self,
        value: str,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        field_name: str = "string"
    ) -> ValidationResult:
        """Validate string length is within bounds"""
        result = ValidationResult()
        
        if not isinstance(value, str):
            result.add_error(f"Field '{field_name}' must be a string")
            return result
        
        length = len(value)
        
        if min_length is not None and length < min_length:
            result.add_error(
                f"Field '{field_name}' length {length} is below minimum {min_length}"
            )
        
        if max_length is not None and length > max_length:
            result.add_error(
                f"Field '{field_name}' length {length} exceeds maximum {max_length}"
            )
        
        return result
    
    def validate_collection_size(
        self,
        value: List,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        field_name: str = "collection"
    ) -> ValidationResult:
        """Validate collection size is within bounds"""
        result = ValidationResult()
        
        if not isinstance(value, (list, tuple, set, dict)):
            result.add_error(f"Field '{field_name}' must be a collection")
            return result
        
        size = len(value)
        
        if min_size is not None and size < min_size:
            result.add_error(
                f"Field '{field_name}' size {size} is below minimum {min_size}"
            )
        
        if max_size is not None and size > max_size:
            result.add_error(
                f"Field '{field_name}' size {size} exceeds maximum {max_size}"
            )
        
        return result
    
    def validate_enum(
        self,
        value: Any,
        allowed_values: List[Any],
        field_name: str = "value"
    ) -> ValidationResult:
        """Validate value is in allowed set"""
        result = ValidationResult()
        
        if value not in allowed_values:
            result.add_error(
                f"Field '{field_name}' value '{value}' not in allowed values: {allowed_values}"
            )
        
        return result
    
    def validate_not_none(self, value: Any, field_name: str) -> ValidationResult:
        """Validate value is not None"""
        result = ValidationResult()
        
        if value is None:
            result.add_error(f"Field '{field_name}' cannot be None")
        
        return result
    
    def validate_not_empty(self, value: Any, field_name: str) -> ValidationResult:
        """Validate value is not empty"""
        result = ValidationResult()
        
        if value is None:
            result.add_error(f"Field '{field_name}' cannot be None")
        elif isinstance(value, (str, list, dict, tuple, set)) and len(value) == 0:
            result.add_error(f"Field '{field_name}' cannot be empty")
        
        return result
    
    def validate_custom(
        self,
        value: Any,
        validator_name: str,
        field_name: str = "value"
    ) -> ValidationResult:
        """Validate using custom registered validator"""
        result = ValidationResult()
        
        if validator_name not in self._custom_validators:
            result.add_error(f"Unknown validator: {validator_name}")
            return result
        
        try:
            if not self._custom_validators[validator_name](value):
                result.add_error(f"Field '{field_name}' failed custom validation: {validator_name}")
        except Exception as e:
            result.add_error(f"Custom validator '{validator_name}' raised error: {e}")
        
        return result


class TradeValidator:
    """
    Specialized validator for trading-related data structures.
    """
    
    def __init__(self):
        self.validator = InputValidator()
    
    def validate_order_params(
        self,
        symbol: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "market"
    ) -> ValidationResult:
        """Validate order parameters"""
        result = ValidationResult()
        
        # Validate symbol
        result.errors.extend(
            self.validator.validate_string_pattern(
                symbol, r'^[A-Z0-9]{2,10}$', "symbol"
            ).errors
        )
        
        # Validate quantity
        result.errors.extend(
            self.validator.validate_range(quantity, min_val=0.0001, field_name="quantity").errors
        )
        
        # Validate price for limit orders
        if order_type == "limit":
            if price is None:
                result.add_error("Price required for limit orders")
            else:
                result.errors.extend(
                    self.validator.validate_range(price, min_val=0.0001, field_name="price").errors
                )
        
        # Validate order type
        result.errors.extend(
            self.validator.validate_enum(
                order_type, ["market", "limit", "stop_loss", "take_profit"], "order_type"
            ).errors
        )
        
        result.is_valid = len(result.errors) == 0
        return result
    
    def validate_risk_params(
        self,
        max_position_size: float,
        max_daily_loss: float,
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> ValidationResult:
        """Validate risk management parameters"""
        result = ValidationResult()
        
        # Position size (percentage of equity) - allow realistic ranges
        result.errors.extend(
            self.validator.validate_range(
                max_position_size, min_val=0.0, max_val=1.0, field_name="max_position_size"
            ).errors
        )

        # Daily loss (percentage of equity)
        result.errors.extend(
            self.validator.validate_range(
                max_daily_loss, min_val=0.0, max_val=0.5, field_name="max_daily_loss"
            ).errors
        )

        # Stop loss percentage (expressed as positive fraction)
        result.errors.extend(
            self.validator.validate_range(
                stop_loss_pct, min_val=0.0, max_val=1.0, field_name="stop_loss_pct"
            ).errors
        )

        # Take profit percentage
        result.errors.extend(
            self.validator.validate_range(
                take_profit_pct, min_val=0.0, max_val=1.0, field_name="take_profit_pct"
            ).errors
        )
        
        result.is_valid = len(result.errors) == 0
        return result


# Singleton instance
_validator = InputValidator()
_trade_validator = TradeValidator()


def validate_order(order: Optional[Dict[str, Any]] = None, *args, **kwargs) -> ValidationResult:
    """Convenience function for order validation accepting dict or explicit kwargs."""
    positional: tuple[Any, ...]
    if order is not None and not isinstance(order, dict):
        positional = (order, *args)
        order = None
    else:
        positional = args
    merged: Dict[str, Any]
    if order is not None:
        if not isinstance(order, dict):
            raise TypeError("order must be a dict")
        merged = {**order, **kwargs}
    elif positional:
        if len(positional) > 4:
            raise TypeError("validate_order accepts up to four positional arguments")
        symbol = positional[0]
        quantity = positional[1] if len(positional) > 1 else kwargs.get("quantity")
        price = positional[2] if len(positional) > 2 else kwargs.get("price")
        order_type = positional[3] if len(positional) > 3 else kwargs.get("order_type")
        merged = {**kwargs}
        if "symbol" not in merged:
            merged["symbol"] = symbol
        if "quantity" not in merged:
            merged["quantity"] = quantity
        if price is not None and "price" not in merged:
            merged["price"] = price
        if order_type is not None and "order_type" not in merged:
            merged["order_type"] = order_type
    else:
        merged = kwargs
    if "symbol" not in merged or "quantity" not in merged:
        raise ValueError("Order validation requires at least 'symbol' and 'quantity'")
    raw_price = merged.get("price")
    parsed_price = None
    if raw_price is not None:
        try:
            parsed_price = float(raw_price)
        except (TypeError, ValueError):
            parsed_price = None
    return _trade_validator.validate_order_params(
        symbol=str(merged.get("symbol", "")),
        quantity=float(merged.get("quantity", 0)),
        price=parsed_price,
        order_type=str(merged.get("order_type") or merged.get("type") or "market")
    )


def validate_risk_params(params: Optional[Dict[str, Any]] = None, *args, **kwargs) -> ValidationResult:
    """Convenience function for risk parameter validation accepting dict or positional inputs."""
    if params is not None and not isinstance(params, dict):
        args = (params, *args)
        params = None
    if params is not None:
        if not isinstance(params, dict):
            raise TypeError("params must be a dict")
        merged = {**params, **kwargs}
    elif args:
        if len(args) > 4:
            raise TypeError("validate_risk_params accepts up to four positional arguments")
        max_position_size = args[0]
        max_daily_loss_arg = args[1] if len(args) > 1 else kwargs.get("max_daily_loss")
        stop_loss_pct = args[2] if len(args) > 2 else kwargs.get("stop_loss_pct")
        take_profit_pct = args[3] if len(args) > 3 else kwargs.get("take_profit_pct")
        merged = {**kwargs}
        if "max_position_size" not in merged:
            merged["max_position_size"] = max_position_size
        if max_daily_loss_arg is not None and "max_daily_loss" not in merged:
            merged["max_daily_loss"] = max_daily_loss_arg
        if "stop_loss_pct" not in merged:
            merged["stop_loss_pct"] = stop_loss_pct
        if "take_profit_pct" not in merged:
            merged["take_profit_pct"] = take_profit_pct
    else:
        merged = kwargs
    required = ["max_position_size", "stop_loss_pct", "take_profit_pct"]
    missing = [field for field in required if field not in merged]
    if missing:
        raise ValueError(f"Missing risk parameter fields: {', '.join(missing)}")
    max_daily_loss_value = merged.get("max_daily_loss", 0.05)
    try:
        max_daily_loss_value = float(max_daily_loss_value)
    except (TypeError, ValueError):
        raise ValueError("max_daily_loss must be numeric if provided")
    return _trade_validator.validate_risk_params(
        max_position_size=float(merged["max_position_size"]),
        max_daily_loss=max_daily_loss_value,
        stop_loss_pct=float(merged["stop_loss_pct"]),
        take_profit_pct=float(merged["take_profit_pct"])
    )


def safe_execute(func: Callable, *args, default=None, log_errors: bool = True, **kwargs) -> Any:
    """
    Safely execute a function with error handling.
    
    Args:
        func: Function to execute
        *args: Positional arguments
        default: Default value to return on error
        log_errors: Whether to log errors
        **kwargs: Keyword arguments
    
    Returns:
        Function result or default value on error
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_errors:
            logger.error(
                f"Error executing {func.__name__}: {e}\n{traceback.format_exc()}"
            )
        return default


def validate_required_fields(data: Dict, required_fields: List[str]) -> ValidationResult:
    """
    Validate that all required fields are present in data.
    
    Args:
        data: Dictionary to validate
        required_fields: List of required field names
    
    Returns:
        ValidationResult with any errors
    """
    result = ValidationResult()
    
    for field in required_fields:
        if field not in data:
            result.add_error(f"Required field missing: {field}")
        elif data[field] is None:
            result.add_error(f"Required field cannot be None: {field}")
    
    return result


# =============================================================================
# Order Validation Functions - SL/TP validation at order placement time
# =============================================================================

def validate_stop_loss(
    stop_loss_price: float,
    entry_price: float,
    side: str,
    max_loss_pct: float = 10.0
) -> ValidationResult:
    """
    Validate stop loss price before placing an order.
    
    Args:
        stop_loss_price: The stop loss price to validate
        entry_price: The entry price of the position
        side: Position side ('BUY' or 'SELL')
        max_loss_pct: Maximum allowed loss percentage (default 10%)
        
    Returns:
        ValidationResult with any errors
    """
    result = ValidationResult()
    
    # Check if SL is provided
    if stop_loss_price is None or (isinstance(stop_loss_price, (int, float)) and stop_loss_price <= 0):
        result.add_error(f"Stop loss must be positive: {stop_loss_price}")
        return result
    
    # Validate SL is a valid number
    if not isinstance(stop_loss_price, (int, float)) or stop_loss_price != stop_loss_price:  # Check for NaN
        result.add_error(f"Invalid stop loss value: {stop_loss_price}")
        return result
    
    # Validate entry price
    if entry_price <= 0:
        result.add_error(f"Invalid entry price: {entry_price}")
        return result
    
    # For BUY positions, SL must be below entry
    if side.upper() == "BUY":
        if stop_loss_price >= entry_price:
            result.add_error(
                f"Stop loss ({stop_loss_price}) must be below entry ({entry_price}) for BUY positions"
            )
        else:
            loss_pct = (entry_price - stop_loss_price) / entry_price * 100
            if loss_pct > max_loss_pct:
                result.add_error(
                    f"Stop loss too far from entry: {loss_pct:.2f}% loss (max allowed: {max_loss_pct}%)"
                )
    
    # For SELL positions, SL must be above entry
    elif side.upper() == "SELL":
        if stop_loss_price <= entry_price:
            result.add_error(
                f"Stop loss ({stop_loss_price}) must be above entry ({entry_price}) for SELL positions"
            )
        else:
            loss_pct = (stop_loss_price - entry_price) / entry_price * 100
            if loss_pct > max_loss_pct:
                result.add_error(
                    f"Stop loss too far from entry: {loss_pct:.2f}% loss (max allowed: {max_loss_pct}%)"
                )
    
    return result


def validate_take_profit(
    take_profit_price: float,
    entry_price: float,
    side: str,
    min_profit_pct: float = 0.5,
    max_profit_pct: float = 50.0
) -> ValidationResult:
    """
    Validate take profit price before placing an order.
    
    Args:
        take_profit_price: The take profit price to validate
        entry_price: The entry price of the position
        side: Position side ('BUY' or 'SELL')
        min_profit_pct: Minimum required profit percentage (default 0.5%)
        max_profit_pct: Maximum allowed profit percentage (default 50%)
        
    Returns:
        ValidationResult with any errors
    """
    result = ValidationResult()
    
    # Check if TP is provided
    if take_profit_price is None or (isinstance(take_profit_price, (int, float)) and take_profit_price <= 0):
        result.add_error(f"Take profit must be positive: {take_profit_price}")
        return result
    
    # Validate TP is a valid number
    if not isinstance(take_profit_price, (int, float)) or take_profit_price != take_profit_price:  # Check for NaN
        result.add_error(f"Invalid take profit value: {take_profit_price}")
        return result
    
    # Validate entry price
    if entry_price <= 0:
        result.add_error(f"Invalid entry price: {entry_price}")
        return result
    
    # For BUY positions, TP must be above entry
    if side.upper() == "BUY":
        if take_profit_price <= entry_price:
            result.add_error(
                f"Take profit ({take_profit_price}) must be above entry ({entry_price}) for BUY positions"
            )
        else:
            profit_pct = (take_profit_price - entry_price) / entry_price * 100
            if profit_pct < min_profit_pct:
                result.add_error(
                    f"Take profit too close to entry: {profit_pct:.2f}% profit (min required: {min_profit_pct}%)"
                )
            elif profit_pct > max_profit_pct:
                result.add_warning(
                    f"Take profit unusually high: {profit_pct:.2f}% profit (max typical: {max_profit_pct}%)"
                )
    
    # For SELL positions, TP must be below entry
    elif side.upper() == "SELL":
        if take_profit_price >= entry_price:
            result.add_error(
                f"Take profit ({take_profit_price}) must be below entry ({entry_price}) for SELL positions"
            )
        else:
            profit_pct = (entry_price - take_profit_price) / entry_price * 100
            if profit_pct < min_profit_pct:
                result.add_error(
                    f"Take profit too close to entry: {profit_pct:.2f}% profit (min required: {min_profit_pct}%)"
                )
            elif profit_pct > max_profit_pct:
                result.add_warning(
                    f"Take profit unusually high: {profit_pct:.2f}% profit (max typical: {max_profit_pct}%)"
                )
    
    return result


def validate_order_prices(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    max_loss_pct: float = 10.0,
    min_profit_pct: float = 0.5,
    max_profit_pct: float = 50.0
) -> ValidationResult:
    """
    Validate all order prices (entry, SL, TP) together.
    
    Args:
        symbol: Trading symbol
        side: Position side ('BUY' or 'SELL')
        entry_price: The entry price
        stop_loss: Optional stop loss price
        take_profit: Optional take profit price
        max_loss_pct: Maximum allowed loss percentage
        min_profit_pct: Minimum required profit percentage
        max_profit_pct: Maximum allowed profit percentage
        
    Returns:
        ValidationResult with any errors
    """
    result = ValidationResult()
    
    # Validate entry price
    if entry_price <= 0:
        result.add_error(f"Invalid entry price for {symbol}: {entry_price}")
        return result
    
    # Validate side
    if side.upper() not in ("BUY", "SELL"):
        result.add_error(f"Invalid side: {side} (must be BUY or SELL)")
        return result
    
    # Validate stop loss
    if stop_loss is not None and stop_loss > 0:
        sl_result = validate_stop_loss(stop_loss, entry_price, side, max_loss_pct)
        if not sl_result.is_valid:
            for error in sl_result.errors:
                result.add_error(f"Stop loss validation failed: {error}")
    
    # Validate take profit
    if take_profit is not None and take_profit > 0:
        tp_result = validate_take_profit(take_profit, entry_price, side, min_profit_pct, max_profit_pct)
        if not tp_result.is_valid:
            for error in tp_result.errors:
                result.add_error(f"Take profit validation failed: {error}")
    
    # Additional check: SL and TP should be on opposite sides of entry
    if stop_loss and take_profit and stop_loss > 0 and take_profit > 0:
        if side.upper() == "BUY":
            if not (stop_loss < entry_price < take_profit):
                result.add_error(
                    f"For BUY: SL ({stop_loss}) < Entry ({entry_price}) < TP ({take_profit})"
                )
        else:  # SELL
            if not (take_profit < entry_price < stop_loss):
                result.add_error(
                    f"For SELL: TP ({take_profit}) < Entry ({entry_price}) < SL ({stop_loss})"
                )
    
    return result
