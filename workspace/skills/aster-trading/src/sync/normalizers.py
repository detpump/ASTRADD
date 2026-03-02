#!/usr/bin/env python3
"""
Normalizers - API response normalization for V3 data architecture.

Converts raw exchange API responses into normalized internal format.
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def normalize_position_response(positions: List[dict]) -> List[dict]:
    """
    Normalize position response from exchange API.
    
    Args:
        positions: Raw position data from exchange API
        
    Returns:
        List of normalized position dictionaries
    """
    normalized = []
    
    for pos in positions:
        try:
            # Handle different API response formats
            normalized_pos = {
                "position_uuid": _generate_position_uuid(pos),
                "symbol": _normalize_symbol(pos.get("symbol", pos.get("symbolName", ""))),
                "side": _normalize_side(pos.get("positionSide", pos.get("side", ""))),
                "position_amt": _safe_float(pos.get("positionAmt", pos.get("positionAmount", 0))),
                "entry_price": _safe_float(pos.get("entryPrice", pos.get("avgPrice", 0))),
                "mark_price": _safe_float(pos.get("markPrice", pos.get("mark_price", 0))),
                "unrealized_pnl": _safe_float(pos.get("unrealizedPnl", pos.get("unrealized_pnl", 0))),
                "notional": _safe_float(pos.get("notional", 0)),
                "leverage": int(pos.get("leverage", 1)),
                "margin_type": pos.get("marginType", pos.get("margin_type", "cross")),
                "liquidation_price": _safe_float(pos.get("liquidationPrice", pos.get("liquidation_price", 0))),
                "update_time": _normalize_timestamp(pos.get("updateTime", pos.get("update_time", 0))),
                "open_time": _normalize_timestamp(pos.get("openTime", pos.get("open_time", 0))),
                "raw_json": json.dumps(pos) if not isinstance(pos, str) else pos
            }
            normalized.append(normalized_pos)
        except Exception as e:
            logger.warning(f"Failed to normalize position: {e}, data: {pos}")
            continue
    
    return normalized


def normalize_order_response(orders: List[dict]) -> List[dict]:
    """
    Normalize order response from exchange API.
    
    Args:
        orders: Raw order data from exchange API
        
    Returns:
        List of normalized order dictionaries
    """
    normalized = []
    
    for order in orders:
        try:
            normalized_order = {
                "order_id": order.get("orderId", order.get("order_id", "")),
                "client_order_id": order.get("clientOrderId", order.get("client_order_id", "")),
                "symbol": _normalize_symbol(order.get("symbol", "")),
                "side": _normalize_side(order.get("side", "")),
                "type": _normalize_order_type(order.get("type", order.get("orderType", ""))),
                "status": _normalize_order_status(order.get("status", "")),
                "price": _safe_float(order.get("price", 0)),
                "quantity": _safe_float(order.get("quantity", order.get("origQty", 0))),
                "executed_qty": _safe_float(order.get("executedQty", order.get("executed_qty", 0))),
                "avg_price": _safe_float(order.get("avgPrice", order.get("avg_price", 0))),
                "reduce_only": bool(order.get("reduceOnly", order.get("reduce_only", False))),
                "time_in_force": order.get("timeInForce", order.get("time_in_force", "GTC")),
                "stop_price": _safe_float(order.get("stopPrice", order.get("stop_price", 0))),
                "position_side": order.get("positionSide", order.get("position_side", "BOTH")),
                "update_time": _normalize_timestamp(order.get("updateTime", order.get("update_time", 0))),
                "raw_json": json.dumps(order) if not isinstance(order, str) else order
            }
            normalized.append(normalized_order)
        except Exception as e:
            logger.warning(f"Failed to normalize order: {e}, data: {order}")
            continue
    
    return normalized


def normalize_balance_response(balances: List[dict]) -> List[dict]:
    """
    Normalize balance response from exchange API.
    
    Args:
        balances: Raw balance data from exchange API
        
    Returns:
        List of normalized balance dictionaries
    """
    normalized = []
    
    for balance in balances:
        try:
            normalized_balance = {
                "asset": balance.get("asset", "").upper(),
                "wallet_balance": _safe_float(balance.get("walletBalance", balance.get("wallet_balance", 0))),
                "available_balance": _safe_float(balance.get("availableBalance", balance.get("available_balance", 0))),
                "cross_wallet_balance": _safe_float(balance.get("crossWalletBalance", balance.get("cross_wallet_balance", 0))),
                "cross_initial_margin": _safe_float(balance.get("crossInitialMargin", balance.get("cross_initial_margin", 0))),
                "maintenance_margin": _safe_float(balance.get("maintMargin", balance.get("maintenance_margin", 0))),
                "raw_json": json.dumps(balance) if not isinstance(balance, str) else balance
            }
            normalized.append(normalized_balance)
        except Exception as e:
            logger.warning(f"Failed to normalize balance: {e}, data: {balance}")
            continue
    
    return normalized


def _generate_position_uuid(pos: dict) -> str:
    """Generate a unique position UUID from position data."""
    import uuid
    symbol = pos.get("symbol", pos.get("symbolName", ""))
    side = pos.get("positionSide", pos.get("side", ""))
    entry = pos.get("entryPrice", pos.get("avgPrice", ""))
    open_time = pos.get("openTime", pos.get("updateTime", ""))
    
    # Create a deterministic UUID based on position identity
    unique_str = f"{symbol}:{side}:{entry}:{open_time}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_str))


def _normalize_symbol(symbol: str) -> str:
    """Normalize symbol to uppercase format."""
    if not symbol:
        return ""
    return symbol.upper().strip()


def _normalize_side(side: str) -> str:
    """Normalize position side to standard format."""
    if not side:
        return "BOTH"
    side = side.upper().strip()
    if side in ("BUY", "LONG"):
        return "LONG"
    elif side in ("SELL", "SHORT"):
        return "SHORT"
    return side


def _normalize_order_type(order_type: str) -> str:
    """Normalize order type to standard format."""
    if not order_type:
        return "LIMIT"
    order_type = order_type.upper().strip()
    type_map = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "STOP": "STOP",
        "STOP_MARKET": "STOP_MARKET",
        "TAKE_PROFIT": "TAKE_PROFIT",
        "TAKE_PROFIT_MARKET": "TAKE_PROFIT_MARKET",
        "TRAILING_STOP": "TRAILING_STOP",
    }
    return type_map.get(order_type, order_type)


def _normalize_order_status(status: str) -> str:
    """Normalize order status to standard format."""
    if not status:
        return "UNKNOWN"
    status = status.upper().strip()
    status_map = {
        "NEW": "NEW",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELLED",
        "CANCELLED": "CANCELLED",
        "PENDING_CANCEL": "PENDING_CANCEL",
        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }
    return status_map.get(status, status)


def _safe_float(value: Any) -> float:
    """Safely convert value to float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _normalize_timestamp(ts: Any) -> int:
    """Normalize timestamp to milliseconds."""
    if not ts:
        return 0
    try:
        ts = int(ts)
        # Convert seconds to milliseconds if needed
        if ts < 10000000000:  # If less than year 2286 in seconds
            ts *= 1000
        return ts
    except (ValueError, TypeError):
        return 0
