#!/usr/bin/env python3
"""State service orchestrating repositories for higher-level operations."""

from typing import List, Optional

from .models import (
    Position,
    Order,
    RiskState,
    ExecutionTracker,
    LastSignal,
    MarketPrice,
    AuditEvent,
    SystemState,
    SystemComponent,
    SystemActivity,
)
from . import repositories as repo


class StateService:
    """Facade to simplify state operations across repositories."""

    # Positions
    def upsert_position(self, position: Position) -> None:
        repo.upsert_position(position)

    def get_positions(self) -> List[Position]:
        return repo.get_positions()

    def delete_position(self, symbol: str) -> None:
        repo.delete_position(symbol)

    # Orders
    def insert_order(self, order: Order) -> int:
        return repo.insert_order(order)

    def get_orders(self, active_only: bool = True, limit: int = 200):
        return repo.get_orders(active_only=active_only, limit=limit)

    # Risk state
    def upsert_risk_state(self, risk: RiskState) -> None:
        repo.upsert_risk_state(risk)

    def get_risk_state(self) -> Optional[RiskState]:
        return repo.get_risk_state()

    # Execution tracker
    def upsert_execution_tracker(self, tracker: ExecutionTracker) -> None:
        repo.upsert_execution_tracker(tracker)

    def get_execution_tracker(self) -> Optional[ExecutionTracker]:
        return repo.get_execution_tracker()

    # Last signals
    def upsert_last_signal(self, sig: LastSignal) -> None:
        repo.upsert_last_signal(sig)

    def get_last_signals(self) -> dict:
        return repo.get_last_signals()

    # Market prices
    def upsert_market_price(self, price: MarketPrice) -> None:
        repo.upsert_market_price(price)

    def get_market_prices(self) -> dict:
        return repo.get_market_prices()

    # Audit log
    def log_audit(self, event: AuditEvent) -> int:
        return repo.log_audit(event)

    # System state
    def upsert_system_state(self, state: SystemState) -> None:
        repo.upsert_system_state(state)

    def get_system_state(self) -> Optional[SystemState]:
        return repo.get_system_state()

    # System components
    def upsert_system_component(self, component: SystemComponent) -> None:
        repo.upsert_system_component(component)

    def get_system_components(self, names: Optional[list[str]] = None):
        return repo.get_system_components(names)

    def get_system_component(self, name: str) -> Optional[SystemComponent]:
        return repo.get_system_component(name)

    # System activity
    def log_system_activity(self, activity: SystemActivity) -> int:
        return repo.log_system_activity(activity)

    def get_system_activity(self, subsystem: Optional[str] = None, limit: int = 200):
        return repo.get_system_activity(subsystem=subsystem, limit=limit)


state_service = StateService()
