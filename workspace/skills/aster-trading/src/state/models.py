#!/usr/bin/env python3
"""Pydantic models for DB entities and state snapshots."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, ConfigDict


class Position(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    side: str
    quantity: float
    entry_price: float
    mark_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    notional: Optional[float] = None
    leverage: int = 1
    open_time: Optional[int] = None
    cycle_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class Order(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str
    side: str
    type: Optional[str] = None
    status: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[float] = None
    executed_qty: Optional[float] = None
    avg_price: Optional[float] = None
    reduce_only: Optional[bool] = None
    time_in_force: Optional[str] = None
    position_side: Optional[str] = None
    update_time: Optional[int] = None
    cycle_id: Optional[str] = None
    bracket_label: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class RiskState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    account_equity: Optional[float] = None
    daily_pnl: Optional[float] = None
    drawdown_pct: Optional[float] = None
    risk_limits: Optional[Dict[str, Any]] = None


class ExecutionTracker(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    total_signals: Optional[int] = None
    total_orders: Optional[int] = None
    active_orders: Optional[int] = None
    status_distribution: Optional[Dict[str, int]] = None
    active_order_symbols: Optional[List[str]] = None


class LastSignal(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    action: Optional[str] = None
    strength: Optional[float] = None
    confidence: Optional[float] = None
    price: Optional[float] = None


class MarketPrice(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    price: float


class SystemState(BaseModel):
    """System state for persistence across restarts"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    running: bool = False
    start_time: Optional[int] = None
    loop_count: int = 0
    trades_executed: int = 0
    recent_errors: Optional[List[str]] = None
    enabled_symbols: Optional[List[str]] = None
    last_signals: Optional[Dict[str, Any]] = None
    market_data: Optional[Dict[str, Any]] = None
    healing_metrics: Optional[Dict[str, Any]] = None


class AuditEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: str
    symbol: Optional[str] = None
    payload: Dict[str, Any]


class SystemComponent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    status: Optional[str] = None
    last_action: Optional[str] = None
    last_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    updated_at: Optional[int] = None


class SystemActivity(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = None
    subsystem: str
    action: str
    status: str
    message: Optional[str] = None
    created_at: Optional[int] = None


class RawSignal(BaseModel):
    """All signals for historical tracking (not just latest)"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = None
    symbol: str
    action: str
    strength: Optional[float] = None
    confidence: Optional[float] = None
    price: Optional[float] = None
    features_json: Optional[str] = None
    correlation_id: Optional[str] = None
    status: Optional[str] = "PENDING"
    created_at: Optional[int] = None


class Trade(BaseModel):
    """Executed trades history"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = None
    symbol: str
    side: str
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    quantity: Optional[float] = None
    pnl: Optional[float] = None
    status: Optional[str] = "OPEN"
    open_time: Optional[int] = None
    close_time: Optional[int] = None
    correlation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[int] = None


class ClosedPosition(BaseModel):
    """Historical closed positions"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[int] = None
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    open_time: Optional[int] = None
    close_time: Optional[int] = None
    duration_minutes: Optional[int] = None
    trade_reason: Optional[str] = None
    close_reason: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[int] = None
