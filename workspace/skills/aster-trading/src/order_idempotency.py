#!/usr/bin/env python3
"""
Order Idempotency Module for Aster Trading System V2

Provides unique idempotency keys per order to prevent duplicate orders
from being placed due to network timeouts, retries, or re-submissions.

The idempotency key format: {timestamp}_{symbol}_{side}_{type}_{random}
"""

import hashlib
import uuid
import time
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass, field
import threading


@dataclass
class IdempotencyKey:
    """Represents an idempotency key for an order."""
    key: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    used: bool = False
    
    def is_expired(self, ttl_seconds: int = 3600) -> bool:
        """Check if key has expired (default 1 hour)."""
        return (time.time() - self.created_at) > ttl_seconds


class OrderIdempotencyManager:
    """
    Manages idempotency keys for order placement to prevent duplicates.
    
    Thread-safe implementation that:
    - Generates unique keys per order
    - Tracks used keys to detect duplicates
    - Auto-expires old keys to prevent memory leaks
    """
    
    def __init__(self, max_keys: int = 10000, ttl_seconds: int = 3600):
        """
        Initialize the idempotency manager.
        
        Args:
            max_keys: Maximum number of keys to store
            ttl_seconds: Time-to-live for keys (default 1 hour)
        """
        self._keys: Dict[str, IdempotencyKey] = {}
        self._max_keys = max_keys
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # Cleanup every 5 minutes
    
    def generate_key(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None
    ) -> str:
        """
        Generate a unique idempotency key for an order.
        
        Args:
            symbol: Trading symbol (e.g., 'ETHUSDT')
            side: Order side ('BUY' or 'SELL')
            order_type: Order type ('MARKET', 'LIMIT', etc.)
            quantity: Order quantity
            price: Order price (optional)
            
        Returns:
            Unique idempotency key string
        """
        # Create unique identifier components
        timestamp = int(time.time() * 1000)  # millisecond precision
        random_suffix = uuid.uuid4().hex[:8]
        
        # Build the key components
        key_parts = [
            str(timestamp),
            symbol.upper(),
            side.upper(),
            order_type.upper(),
            str(quantity)
        ]
        
        if price is not None:
            key_parts.append(f"{price:.8f}")
        
        key_parts.append(random_suffix)
        
        # Create the key
        key = "_".join(key_parts)
        
        # Store the key
        with self._lock:
            # Periodic cleanup
            self._maybe_cleanup()
            
            # Store the key
            self._keys[key] = IdempotencyKey(
                key=key,
                symbol=symbol.upper(),
                side=side.upper(),
                order_type=order_type.upper(),
                quantity=quantity,
                price=price
            )
        
        return key
    
    def generate_key_from_order(self, order_params: Dict) -> str:
        """
        Generate idempotency key from order parameters dictionary.
        
        Args:
            order_params: Dictionary with order parameters (symbol, side, type, quantity, price)
            
        Returns:
            Unique idempotency key string
        """
        return self.generate_key(
            symbol=order_params.get('symbol', ''),
            side=order_params.get('side', ''),
            order_type=order_params.get('type', 'MARKET'),
            quantity=float(order_params.get('quantity', 0)),
            price=float(order_params['price']) if order_params.get('price') else None
        )
    
    def is_duplicate(self, key: str) -> bool:
        """
        Check if an idempotency key has already been used.
        
        Args:
            key: The idempotency key to check
            
        Returns:
            True if key exists and hasn't been used (duplicate), False otherwise
        """
        with self._lock:
            if key not in self._keys:
                return False
            
            idempotency_key = self._keys[key]
            
            # Check if expired
            if idempotency_key.is_expired(self._ttl_seconds):
                # Remove expired key
                del self._keys[key]
                return False
            
            # Check if already used
            if idempotency_key.used:
                return True
            
            return False
    
    def mark_used(self, key: str) -> bool:
        """
        Mark an idempotency key as used (order successfully placed).
        
        Args:
            key: The idempotency key to mark
            
        Returns:
            True if key was found and marked, False otherwise
        """
        with self._lock:
            if key not in self._keys:
                return False
            
            idempotency_key = self._keys[key]
            
            # Check if expired
            if idempotency_key.is_expired(self._ttl_seconds):
                del self._keys[key]
                return False
            
            # Mark as used
            idempotency_key.used = True
            return True
    
    def get_key_info(self, key: str) -> Optional[Dict]:
        """
        Get information about an idempotency key.
        
        Args:
            key: The idempotency key to look up
            
        Returns:
            Dictionary with key information or None if not found
        """
        with self._lock:
            if key not in self._keys:
                return None
            
            idempotency_key = self._keys[key]
            
            return {
                'key': idempotency_key.key,
                'symbol': idempotency_key.symbol,
                'side': idempotency_key.side,
                'order_type': idempotency_key.order_type,
                'quantity': idempotency_key.quantity,
                'price': idempotency_key.price,
                'created_at': idempotency_key.created_at,
                'used': idempotency_key.used,
                'is_expired': idempotency_key.is_expired(self._ttl_seconds)
            }
    
    def _maybe_cleanup(self) -> None:
        """Periodically clean up expired keys."""
        current_time = time.time()
        
        if current_time - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = current_time
        
        # Remove expired keys
        expired_keys = [
            key for key, ik in self._keys.items()
            if ik.is_expired(self._ttl_seconds)
        ]
        
        for key in expired_keys:
            del self._keys[key]
        
        # If still over max keys, remove oldest used keys
        if len(self._keys) > self._max_keys:
            # Sort by creation time and keep only max_keys
            sorted_keys = sorted(
                self._keys.items(),
                key=lambda x: x[1].created_at
            )
            
            # Remove oldest keys (both used and unused)
            excess = len(self._keys) - self._max_keys
            for key, _ in sorted_keys[:excess]:
                del self._keys[key]
    
    def get_stats(self) -> Dict:
        """Get statistics about the idempotency manager."""
        with self._lock:
            total_keys = len(self._keys)
            used_keys = sum(1 for k in self._keys.values() if k.used)
            expired_keys = sum(1 for k in self._keys.values() if k.is_expired(self._ttl_seconds))
            
            return {
                'total_keys': total_keys,
                'used_keys': used_keys,
                'unused_keys': total_keys - used_keys,
                'expired_keys': expired_keys,
                'max_keys': self._max_keys,
                'ttl_seconds': self._ttl_seconds
            }
    
    def reset(self) -> None:
        """Clear all idempotency keys."""
        with self._lock:
            self._keys.clear()


# Global instance for use throughout the application
_idempotency_manager: Optional[OrderIdempotencyManager] = None
_manager_lock = threading.Lock()


def get_idempotency_manager() -> OrderIdempotencyManager:
    """Get the global idempotency manager instance."""
    global _idempotency_manager
    
    with _manager_lock:
        if _idempotency_manager is None:
            _idempotency_manager = OrderIdempotencyManager()
        
        return _idempotency_manager


def generate_order_idempotency_key(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None
) -> str:
    """
    Convenience function to generate an idempotency key.
    
    Args:
        symbol: Trading symbol
        side: Order side ('BUY' or 'SELL')
        order_type: Order type
        quantity: Order quantity
        price: Order price (optional)
        
    Returns:
        Unique idempotency key
    """
    return get_idempotency_manager().generate_key(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price
    )


def check_duplicate_order(key: str) -> bool:
    """
    Check if an order is a duplicate.
    
    Args:
        key: Idempotency key
        
    Returns:
        True if duplicate, False otherwise
    """
    return get_idempotency_manager().is_duplicate(key)


def mark_order_placed(key: str) -> bool:
    """
    Mark an order as successfully placed.
    
    Args:
        key: Idempotency key
        
    Returns:
        True if successful, False otherwise
    """
    return get_idempotency_manager().mark_used(key)
