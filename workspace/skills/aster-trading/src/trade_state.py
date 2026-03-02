#!/usr/bin/env python3
"""
trade_state.py - Module for managing trade position state.

Provides functions to get, set, and clear position state for trading symbols.
Supports scaled entries and partial closes with proper weighted average calculations.
"""
import json
import logging
import os
import time
import threading
import warnings
from typing import Dict, Any, Optional, List

# Configure logger
logger = logging.getLogger(__name__)

# DB-first: use state_service for all reads/writes
try:
    from state.state_service import state_service
    from state.models import Position
    _USE_DB = True
except ImportError:
    _USE_DB = False
    from state.models import Position  # Still need for type hints

# Legacy fallback paths - always define for fallback
try:
    from paths import STATE_DIR, TRADE_STATE_FILE
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    STATE_PATH = TRADE_STATE_FILE
except ImportError:
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    STATE_PATH = os.path.join(BASE, "data/state/trade_state.json")

# Thread safety
_state_lock = threading.RLock()

# In-memory cache for performance (TTL: 5 seconds)
_cache: Dict[str, Any] = {}
_cache_time: float = 0
_CACHE_TTL: float = 5.0
_cache_refreshing: bool = False  # Flag to prevent concurrent cache refresh


def _load_raw() -> Dict[str, Any]:
    """Load the raw state dictionary from the legacy JSON file (read-only fallback)."""
    global _cache, _cache_time, _cache_refreshing
    
    current_time = time.time()
    
    # Thread safety: Acquire lock before checking cache to prevent race conditions
    with _state_lock:
        # Return cached data if still valid
        if current_time - _cache_time < _CACHE_TTL and _cache:
            return _cache.copy()
        
        # If another thread is already refreshing, wait briefly and return cached data
        if _cache_refreshing:
            # Wait for other thread to finish and return stale cache
            time.sleep(0.1)
            return _cache.copy() if _cache else {}
        
        # Mark as refreshing to prevent other threads from doing the same
        _cache_refreshing = True
        
        try:
            if not os.path.exists(STATE_PATH):
                return {}
            try:
                warnings.warn(
                    f"Legacy JSON state read fallback in use: {STATE_PATH}. "
                    "SQLite DB is the source of truth and JSON fallback will be removed.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _cache = data
                    _cache_time = current_time
                    return data
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Error loading trade state: {e}")
                return {}
        finally:
            _cache_refreshing = False


def _save_raw(state: Dict[str, Any], invalidate_cache: bool = True) -> None:
    """Deprecated: JSON state writes removed. Keep in-memory cache only."""
    global _cache, _cache_time
    warnings.warn(
        "trade_state JSON writes are deprecated and disabled. "
        "SQLite DB is the single source of truth.",
        DeprecationWarning,
        stacklevel=2,
    )
    with _state_lock:
        if invalidate_cache:
            _cache = dict(state)
            _cache_time = time.time()


def _invalidate_cache() -> None:
    """Invalidate the in-memory cache."""
    global _cache, _cache_time
    _cache = {}
    _cache_time = 0


def set_scale_in_pending(symbol: str, pending: bool) -> None:
    """
    Set the scale_in_pending flag for a symbol.
    This flag prevents bracket manager from interfering during scale-in operations.
    
    Args:
        symbol: The trading symbol
        pending: True if scale-in is in progress, False when complete
    """
    state = get_position_state(symbol)
    if state is None:
        state = {}
    state["scale_in_pending"] = pending
    set_position_state(symbol, state)


def is_scale_in_pending(symbol: str) -> bool:
    """
    Check if a scale-in operation is pending for a symbol.
    
    Args:
        symbol: The trading symbol
        
    Returns:
        True if scale-in is in progress, False otherwise
    """
    state = get_position_state(symbol)
    if state is None:
        return False
    return state.get("scale_in_pending", False)


def get_position_state(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get the position state for a given symbol.
    
    Args:
        symbol: The trading symbol (e.g., "BTCUSDT")
        
    Returns:
        The position state dictionary, or None if not found.
    """
    # DB-first approach
    if _USE_DB:
        try:
            positions = {p.symbol: p for p in state_service.get_positions()}
            pos = positions.get(symbol.upper())
            if pos:
                result = {
                    "side": pos.side,
                    "size": pos.quantity,
                    "entry_price": pos.entry_price,
                    "mark_price": pos.mark_price,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "notional": pos.notional,
                    "leverage": pos.leverage,
                    "open_time": pos.open_time,
                    "cycle_id": pos.cycle_id,
                }
                # Merge metadata into result
                if pos.metadata:
                    result.update(pos.metadata)
                return result
        except Exception as e:
            logger.warning(f"Error reading position from DB: {e}")
        return None
    
    # Legacy file-based fallback
    state = _load_raw()
    return state.get(symbol.upper())


def set_position_state(symbol: str, data: Dict[str, Any]) -> None:
    """
    Set the position state for a given symbol.
    
    Args:
        symbol: The trading symbol (e.g., "BTCUSDT")
        data: The position state data to store
    """
    symbol = symbol.upper()
    
    # DB-first approach
    if _USE_DB:
        try:
            # Extract basic fields for Position model
            # Store detailed info in metadata
            metadata = {
                "entries": data.get("entries", []),
                "closes": data.get("closes", []),
                "tp1_price": data.get("tp1_price", 0.0),
                "tp2_price": data.get("tp2_price", 0.0),
                "sl_price": data.get("sl_price", 0.0),
                "tp1_order_id": data.get("tp1_order_id"),
                "tp2_order_id": data.get("tp2_order_id"),
                "tp1_hit": data.get("tp1_hit", False),
                "tp2_hit": data.get("tp2_hit", False),
                "sl_hit": data.get("sl_hit", False),
                "scale_in_count": data.get("scale_in_count", 0),
                "last_scale_in_time": data.get("last_scale_in_time", 0),
                "realized_pnl": data.get("realized_pnl", 0.0),
                "pyramid_scales": data.get("pyramid_scales", 0),
                # Flag to prevent race condition between scale-in and bracket manager
                "scale_in_pending": data.get("scale_in_pending", False),
            }
            
            position = Position(
                symbol=symbol,
                side=data.get("side", "BUY"),
                quantity=float(data.get("size", 0)),
                entry_price=float(data.get("entry_price", 0)),
                mark_price=data.get("mark_price"),
                unrealized_pnl=data.get("unrealized_pnl"),
                notional=data.get("notional"),
                leverage=data.get("leverage", 1),
                open_time=data.get("open_time"),
                cycle_id=data.get("cycle_id"),
                metadata=metadata
            )
            state_service.upsert_position(position)
            _invalidate_cache()  # Clear local cache
            return
        except Exception as e:
            logger.error(f"Error saving position to DB: {e}")
            return

    # DB module unavailable (breaking change): do not write JSON state
    warnings.warn(
        "set_position_state called without DB support. JSON state writes are disabled.",
        DeprecationWarning,
        stacklevel=2,
    )
    with _state_lock:
        global _cache, _cache_time
        _cache[symbol.upper()] = dict(data)
        _cache_time = time.time()


def clear_position_state(symbol: str) -> None:
    """
    Clear the position state for a given symbol.
    
    Args:
        symbol: The trading symbol to clear
    """
    symbol = symbol.upper()
    
    # DB-first approach
    if _USE_DB:
        try:
            state_service.delete_position(symbol)
            _invalidate_cache()
            return
        except Exception as e:
            logger.error(f"Error deleting position from DB: {e}")
            return

    # DB module unavailable (breaking change): do not write JSON state
    warnings.warn(
        "clear_position_state called without DB support. JSON state writes are disabled.",
        DeprecationWarning,
        stacklevel=2,
    )
    with _state_lock:
        if symbol in _cache:
            del _cache[symbol]
        global _cache_time
        _cache_time = time.time()


# ============================================================================
# ENHANCED POSITION TRACKING - Supports scaled entries and partial closes
# ============================================================================

def init_position(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    order_id: int = None,
    ts: str = None
) -> Dict[str, Any]:
    """
    Initialize a new position with entry tracking.
    
    Args:
        symbol: Trading symbol
        side: BUY or SELL
        entry_price: Entry price
        quantity: Position size
        order_id: Optional order ID from exchange
        ts: Optional timestamp
        
    Returns:
        The initialized position state
    """
    symbol = symbol.upper()
    now_ms = int(time.time() * 1000)
    
    position = {
        "side": side.upper(),
        "entries": [
            {
                "price": float(entry_price),
                "quantity": float(quantity),
                "order_id": order_id,
                "timestamp": ts or now_ms,
                "entry_id": now_ms  # Unique ID for this entry
            }
        ],
        # Calculated fields
        "size": float(quantity),
        "entry_price": float(entry_price),  # Weighted average
        "open_time": now_ms,
        # Bracket tracking
        "sl_price": 0.0,
        "tp1_price": 0.0,
        "tp2_price": 0.0,
        "tp1_order_id": None,
        "tp2_order_id": None,
        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False,
        # Scale-in tracking
        "scale_in_count": 0,
        "last_scale_in_time": 0,
        # PnL tracking
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
    }
    
    set_position_state(symbol, position)
    return position


def add_scale_in(
    symbol: str,
    entry_price: float,
    quantity: float,
    order_id: int = None
) -> Optional[Dict[str, Any]]:
    """
    Add a scaled entry to an existing position.
    Recalculates weighted average entry price.
    
    Args:
        symbol: Trading symbol
        entry_price: New entry price
        quantity: Quantity to add
        order_id: Optional order ID
        
    Returns:
        Updated position state or None if position doesn't exist
    """
    symbol = symbol.upper()
    position = get_position_state(symbol)
    
    if not position:
        return None
    
    now_ms = int(time.time() * 1000)
    
    # Add new entry
    entries = position.get("entries", [])
    entries.append({
        "price": float(entry_price),
        "quantity": float(quantity),
        "order_id": order_id,
        "timestamp": now_ms,
        "entry_id": now_ms
    })
    
    # Recalculate weighted average
    total_qty = sum(e["quantity"] for e in entries)
    if total_qty > 0:
        weighted_sum = sum(e["price"] * e["quantity"] for e in entries)
        new_avg_price = weighted_sum / total_qty
    else:
        new_avg_price = entry_price
    
    # Update position
    position["entries"] = entries
    position["size"] = total_qty
    position["entry_price"] = new_avg_price
    position["scale_in_count"] = position.get("scale_in_count", 0) + 1
    position["last_scale_in_time"] = now_ms
    
    set_position_state(symbol, position)
    return position


def record_partial_close(
    symbol: str,
    close_quantity: float,
    close_price: float,
    close_type: str,  # "TP1", "TP2", "SL", "MANUAL"
    order_id: int = None
) -> Optional[Dict[str, Any]]:
    """
    Record a partial close (TP1, TP2, or SL hit).
    Updates position size and calculates realized PnL.
    
    Args:
        symbol: Trading symbol
        close_quantity: Quantity being closed
        close_price: Price at which close occurred
        close_type: Type of close (TP1, TP2, SL, MANUAL)
        order_id: Optional order ID
        
    Returns:
        Updated position state or None if position doesn't exist
    """
    symbol = symbol.upper()
    position = get_position_state(symbol)
    
    if not position:
        return None
    
    # Calculate realized PnL for this close
    side = position.get("side", "BUY")
    entry_price = position.get("entry_price", 0)
    
    if side == "BUY":
        pnl = (close_price - entry_price) * close_quantity
    else:  # SELL
        pnl = (entry_price - close_price) * close_quantity
    
    # Update realized PnL
    position["realized_pnl"] = position.get("realized_pnl", 0.0) + pnl
    
    # Update size
    position["size"] = position.get("size", 0) - close_quantity
    
    # Track which TP was hit
    if close_type == "TP1":
        position["tp1_hit"] = True
    elif close_type == "TP2":
        position["tp2_hit"] = True
    elif close_type == "SL":
        position["sl_hit"] = True
    
    # Record the close
    closes = position.get("closes", [])
    closes.append({
        "quantity": float(close_quantity),
        "price": float(close_price),
        "pnl": float(pnl),
        "type": close_type,
        "order_id": order_id,
        "timestamp": int(time.time() * 1000)
    })
    position["closes"] = closes
    
    # Clear position if fully closed
    if position["size"] <= 0:
        clear_position_state(symbol)
    else:
        set_position_state(symbol, position)
    
    return position


def get_position_stats(symbol: str) -> Dict[str, Any]:
    """
    Get comprehensive position statistics.
    
    Args:
        symbol: Trading symbol
        
    Returns:
        Dictionary with position stats or empty dict
    """
    position = get_position_state(symbol)
    
    if not position:
        return {}
    
    entries = position.get("entries", [])
    closes = position.get("closes", [])
    
    return {
        "symbol": symbol.upper(),
        "side": position.get("side"),
        "size": position.get("size", 0),
        "entry_price": position.get("entry_price", 0),
        "entries_count": len(entries),
        "closes_count": len(closes),
        "scale_in_count": position.get("scale_in_count", 0),
        "realized_pnl": position.get("realized_pnl", 0),
        "unrealized_pnl": position.get("unrealized_pnl", 0),
        "tp1_hit": position.get("tp1_hit", False),
        "tp2_hit": position.get("tp2_hit", False),
        "sl_hit": position.get("sl_hit", False),
        "open_time": position.get("open_time"),
    }


def sync_all_with_exchange() -> Dict[str, Dict[str, Any]]:
    """
    Sync ALL position states with exchange.
    Clears stale positions that no longer exist on exchange.
    Updates local state to match exchange positions.
    
    This should be called periodically to ensure trade_state stays in sync
    with the exchange, preventing phantom positions and stale data.
    
    Returns:
        Dictionary with sync results
    """
    import sys
    import os
    
    # Add src to path for API import
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    
    try:
        from api.aster_api import get_positions_v3
    except ImportError:
        logger.warning("Cannot import API for sync - skipping")
        return {"error": "API not available"}
    
    # Get all positions from exchange
    exchange_positions = get_positions_v3()
    exchange_symbols = {p["symbol"] for p in exchange_positions if float(p.get("positionAmt", 0)) != 0}
    
    # Get current local state from DB first; legacy JSON read fallback only if DB unavailable
    local_state = get_all_positions()
    
    # Find positions to remove (in local but not on exchange)
    symbols_to_remove = []
    for symbol in local_state:
        if symbol not in exchange_symbols:
            symbols_to_remove.append(symbol)
    
    # Remove stale positions
    for symbol in symbols_to_remove:
        logger.info(f"Clearing stale position from trade_state: {symbol}")
        del local_state[symbol]
    
    # Update/add positions from exchange
    for ex_pos in exchange_positions:
        symbol = ex_pos["symbol"]
        position_amt = float(ex_pos.get("positionAmt", 0))
        
        if position_amt == 0:
            continue  # Skip closed positions
        
        entry_price = float(ex_pos.get("entryPrice", 0))
        if entry_price <= 0:
            continue
        
        side = "BUY" if position_amt > 0 else "SELL"
        
        # Check if we need to create or update
        if symbol not in local_state:
            # New position from exchange - create basic state
            logger.info(f"Creating new position in trade_state: {symbol} {side} @ {entry_price}")
            local_state[symbol] = {
                "side": side,
                "entry_price": entry_price,
                "size": abs(position_amt),
                "open_time": int(ex_pos.get("updateTime", 0)),
                "tp1_hit": False,
                "tp2_hit": False,
                "sl_hit": False,
                "scale_in_count": 0,
                "pyramid_scales": 0
            }
        else:
            # Existing position - verify consistency
            local = local_state[symbol]
            local_side = local.get("side", "")
            local_entry = local.get("entry_price", 0)
            
            # If side changed or entry significantly different, reset
            if local_side != side or (local_entry > 0 and abs(local_entry - entry_price) / entry_price > 0.5):
                logger.warning(f"Position mismatch for {symbol}: local={local_side}@{local_entry}, exchange={side}@{entry_price} - resetting")
                local_state[symbol] = {
                    "side": side,
                    "entry_price": entry_price,
                    "size": abs(position_amt),
                    "open_time": int(ex_pos.get("updateTime", 0)),
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "sl_hit": False,
                    "scale_in_count": 0,
                    "pyramid_scales": 0
                }
    
    # Persist using DB-first setters/deleters (JSON writes are intentionally disabled)
    for symbol in symbols_to_remove:
        clear_position_state(symbol)

    for symbol, data in local_state.items():
        set_position_state(symbol, data)
    
    return {
        "removed": symbols_to_remove,
        "synced": list(exchange_symbols),
        "total_local": len(local_state)
    }


def sync_with_exchange(symbol: str, exchange_position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Sync local position state with exchange position data.
    Handles discrepancies between local and exchange state.
    
    Args:
        symbol: Trading symbol
        exchange_position: Position data from exchange API
        
    Returns:
        Synced position state
    """
    symbol = symbol.upper()
    exchange_amt = float(exchange_position.get("positionAmt", 0))
    
    local_position = get_position_state(symbol)
    
    # No position locally and none on exchange - nothing to do
    if not local_position and exchange_amt == 0:
        return None
    
    # Position on exchange but not locally - create from exchange data
    if not local_position and exchange_amt != 0:
        entry_price = float(exchange_position.get("entryPrice", 0))
        if entry_price > 0 and exchange_amt != 0:
            side = "BUY" if exchange_amt > 0 else "SELL"
            return init_position(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                quantity=abs(exchange_amt)
            )
        return None
    
    # Local position but not on exchange - position was closed
    if local_position and exchange_amt == 0:
        # Check if we have unrecorded closes
        local_size = local_position.get("size", 0)
        if local_size > 0:
            # Position was fully closed externally
            clear_position_state(symbol)
        return None
    
    # Both have positions - verify consistency
    local_size = local_position.get("size", 0)
    if abs(local_size - abs(exchange_amt)) > 0.0001:
        # Significant discrepancy - update from exchange
        entry_price = float(exchange_position.get("entryPrice", local_position.get("entry_price", 0)))
        local_position["size"] = abs(exchange_amt)
        local_position["entry_price"] = entry_price
        set_position_state(symbol, local_position)
    
    return local_position


def get_all_positions() -> Dict[str, Dict[str, Any]]:
    """
    Get all position states.
    
    Returns:
        Dictionary of all positions keyed by symbol
    """
    # DB-first approach
    if _USE_DB:
        try:
            positions = state_service.get_positions()
            result = {}
            for pos in positions:
                if pos.quantity > 0:
                    data = {
                        "side": pos.side,
                        "size": pos.quantity,
                        "entry_price": pos.entry_price,
                        "mark_price": pos.mark_price,
                        "unrealized_pnl": pos.unrealized_pnl,
                        "notional": pos.notional,
                        "leverage": pos.leverage,
                        "open_time": pos.open_time,
                        "cycle_id": pos.cycle_id,
                    }
                    # Merge metadata
                    if pos.metadata:
                        data.update(pos.metadata)
                    result[pos.symbol] = data
            return result
        except Exception as e:
            logger.warning(f"Error reading positions from DB: {e}")
            # Fall through to legacy file-based approach
    
    # Legacy file-based fallback (read-only compatibility)
    warnings.warn(
        f"Legacy JSON state read fallback in use: {STATE_PATH}. "
        "SQLite DB is the source of truth and JSON fallback will be removed.",
        DeprecationWarning,
        stacklevel=2,
    )
    state = _load_raw()
    return {k: v for k, v in state.items() if isinstance(v, dict) and v.get("size", 0) > 0}


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def calculate_unrealized_pnl(symbol: str, current_price: float) -> float:
    """
    Calculate unrealized PnL for a position.
    
    Args:
        symbol: Trading symbol
        current_price: Current market price
        
    Returns:
        Unrealized PnL
    """
    position = get_position_state(symbol)
    if not position:
        return 0.0
    
    size = position.get("size", 0)
    entry = position.get("entry_price", 0)
    side = position.get("side", "BUY")
    
    if size <= 0 or entry <= 0:
        return 0.0
    
    if side == "BUY":
        return (current_price - entry) * size
    else:  # SELL
        return (entry - current_price) * size
