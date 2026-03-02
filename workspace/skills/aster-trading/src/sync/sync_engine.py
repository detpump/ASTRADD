#!/usr/bin/env python3
"""
SyncEngine - Main synchronization engine for V3 data architecture.

Coordinates data fetching, change detection, event emission, and state projection.
"""

import uuid
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from src.state.db import get_connection
from src.state.repositories import (
    upsert_position, get_positions, upsert_order, get_orders,
    upsert_risk_state, get_risk_state
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    batch_id: str
    status: str
    started_at: int
    completed_at: int
    positions_fetched: int = 0
    orders_fetched: int = 0
    events_emitted: int = 0
    projections_succeeded: int = 0
    projections_failed: int = 0
    errors: List[str] = field(default_factory=list)


class SyncEngine:
    """
    Main synchronization engine that coordinates data fetching, 
    change detection, event emission, and state projection.
    
    FIXED: Uses position_uuid instead of symbol to correctly identify positions
    when multiple positions exist for the same symbol.
    """
    
    def __init__(self):
        self.change_detector = None  # Will be initialized lazily to avoid circular imports
        self.event_emitter = None  # Will be initialized lazily
        self.position_projector = None  # Will be initialized lazily
        self.order_projector = None  # Will be initialized lazily
        self.bracket_projector = None  # Will be initialized lazily
        self._previous_positions: Dict[str, dict] = {}
        self._previous_orders: Dict[str, dict] = {}
        self._load_cached_state()
    
    def _init_projectors(self):
        """Lazy initialization to avoid circular imports."""
        if self.change_detector is None:
            from src.sync.change_detector import ChangeDetector
            self.change_detector = ChangeDetector()
        if self.event_emitter is None:
            from src.sync.event_emitter import EventEmitter
            self.event_emitter = EventEmitter()
        if self.position_projector is None:
            from src.sync.projectors.position_projector import PositionProjector
            self.position_projector = PositionProjector()
        if self.order_projector is None:
            from src.sync.projectors.order_projector import OrderProjector
            self.order_projector = OrderProjector()
        if self.bracket_projector is None:
            from src.sync.projectors.bracket_projector import BracketProjector
            self.bracket_projector = BracketProjector()
    
    def _load_cached_state(self):
        """Load cached state from database."""
        try:
            positions = get_positions()
            # FIXED: Use position_uuid as key instead of symbol to handle multiple positions per symbol
            self._previous_positions = {p.position_uuid: p.__dict__ for p in positions if p.position_uuid}
            orders = get_orders(active_only=True)
            self._previous_orders = {o.order_id: o.__dict__ for o in orders}
        except Exception as e:
            logger.warning(f"Could not load cached state: {e}")
    
    def sync(self) -> SyncResult:
        """Execute a full sync cycle."""
        batch_id = str(uuid.uuid4())
        started_at = int(time.time() * 1000)
        completed_at = int(time.time() * 1000)
        
        result = SyncResult(
            batch_id=batch_id,
            status='FAILED',
            started_at=started_at,
            completed_at=completed_at
        )
        
        try:
            self._start_batch(batch_id, started_at)
            positions_data, orders_data, balances_data = self._fetch_all_data(batch_id)
            result.positions_fetched = len(positions_data)
            result.orders_fetched = len(orders_data)
            
            events = self._detect_changes_and_emit_events(positions_data, orders_data, balances_data)
            result.events_emitted = len(events)
            
            self._init_projectors()
            projections_ok, projections_fail = self._project_events(events)
            result.projections_succeeded = projections_ok
            result.projections_failed = projections_fail
            
            self._update_risk_state(balances_data)
            
            result.status = 'COMPLETED' if projections_fail == 0 else 'PARTIAL'
            self._complete_batch(batch_id, result)
            self._load_cached_state()
            
        except Exception as e:
            logger.exception(f"Sync failed: {e}")
            result.errors.append(str(e))
            result.status = 'FAILED'
            self._fail_batch(batch_id, str(e))
        
        result.completed_at = int(time.time() * 1000)
        return result
    
    def _start_batch(self, batch_id: str, started_at: int):
        """Mark the start of a sync batch."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sync_batches (batch_id, started_at, status) VALUES (?, ?, 'IN_PROGRESS')",
                (batch_id, started_at)
            )
            conn.commit()
        logger.debug(f"Started sync batch {batch_id}")
    
    def _fetch_all_data(self, batch_id: str):
        """Fetch all data from exchange APIs."""
        # Import here to avoid issues if API is not available
        try:
            from src.api.aster_api import get_positions_v3, get_open_orders, get_balance_v3
            from src.sync.normalizers import (
                normalize_position_response, 
                normalize_order_response,
                normalize_balance_response
            )
            
            positions_raw = get_positions_v3() or []
            positions_data = normalize_position_response(positions_raw)
            self._persist_raw_positions(batch_id, positions_data)
            
            orders_raw = get_open_orders() or []
            orders_data = normalize_order_response(orders_raw)
            self._persist_raw_orders(batch_id, orders_data)
            
            balances_raw = get_balance_v3() or []
            balances_data = normalize_balance_response(balances_raw)
            self._persist_raw_accounts(batch_id, balances_data)
            
            return positions_data, orders_data, balances_data
        except ImportError as e:
            logger.warning(f"API not available for sync: {e}")
            return [], [], []
    
    def _persist_raw_positions(self, batch_id: str, positions: List[dict]):
        """Persist raw position snapshots to database."""
        with get_connection() as conn:
            cur = conn.cursor()
            for pos in positions:
                cur.execute(
                    """INSERT INTO raw_position_snapshots 
                       (sync_batch_id, symbol, position_amt, entry_price, mark_price,
                        unrealized_pnl, leverage, liquidation_price, update_time, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, pos["symbol"], pos.get("position_amt", 0), pos.get("entry_price", 0),
                     pos.get("mark_price", 0), pos.get("unrealized_pnl", 0), pos.get("leverage", 1),
                     pos.get("liquidation_price", 0), pos.get("update_time", 0), pos.get("raw_json", "{}"))
                )
            conn.commit()
    
    def _persist_raw_orders(self, batch_id: str, orders: List[dict]):
        """Persist raw order snapshots to database."""
        with get_connection() as conn:
            cur = conn.cursor()
            for order in orders:
                cur.execute(
                    """INSERT INTO raw_order_snapshots 
                       (sync_batch_id, order_id, client_order_id, symbol, side, type,
                        status, price, quantity, executed_qty, avg_price, reduce_only,
                        time_in_force, stop_price, update_time, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, order.get("order_id", ""), order.get("client_order_id", ""),
                     order.get("symbol", ""), order.get("side", ""), order.get("type", ""),
                     order.get("status", ""), order.get("price", 0), order.get("quantity", 0),
                     order.get("executed_qty", 0), order.get("avg_price", 0), 
                     order.get("reduce_only", False), order.get("time_in_force", ""),
                     order.get("stop_price", 0), order.get("update_time", 0), 
                     order.get("raw_json", "{}"))
                )
            conn.commit()
    
    def _persist_raw_accounts(self, batch_id: str, balances: List[dict]):
        """Persist raw account balance snapshots to database."""
        with get_connection() as conn:
            cur = conn.cursor()
            for bal in balances:
                cur.execute(
                    """INSERT INTO raw_account_snapshots 
                       (sync_batch_id, asset, wallet_balance, available_balance,
                        cross_wallet_balance, cross_initial_margin, maintenance_margin, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, bal.get("asset", ""), bal.get("wallet_balance", 0),
                     bal.get("available_balance", 0), bal.get("cross_wallet_balance", 0),
                     bal.get("cross_initial_margin", 0), bal.get("maintenance_margin", 0),
                     bal.get("raw_json", "{}"))
                )
            conn.commit()
    
    def _detect_changes_and_emit_events(self, positions, orders, balances) -> List[dict]:
        """Detect changes and emit events."""
        self._init_projectors()
        
        events = []
        position_events = self.change_detector.detect_position_changes(
            current=positions, previous=self._previous_positions, correlation_id=str(uuid.uuid4())
        )
        events.extend(position_events)
        
        order_events = self.change_detector.detect_order_changes(
            current=orders, previous=self._previous_orders, correlation_id=str(uuid.uuid4())
        )
        events.extend(order_events)
        
        # Persist all events to the events table
        for event in events:
            self._persist_event(event)
        
        for event in events:
            self.event_emitter.emit(event)
        
        return events
    
    def _persist_event(self, event: dict) -> Optional[int]:
        """Persist event to the events table.
        
        Args:
            event: Event dictionary to persist
            
        Returns:
            Event ID if successful, None otherwise
        """
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO events (
                        event_type, event_source, correlation_id, symbol,
                        payload_json, parent_event_id, caused_by_event_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.get("event_type", "UNKNOWN"),
                        event.get("event_source", "EXCHANGE"),
                        event.get("correlation_id", str(uuid.uuid4())),
                        event.get("symbol", ""),
                        event.get("payload_json", "{}"),
                        event.get("parent_event_id"),
                        event.get("caused_by_event_id"),
                        int(time.time() * 1000)
                    )
                )
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            logger.warning(f"Failed to persist event: {e}")
            return None
    
    def _project_events(self, events: List[dict]) -> tuple:
        """Project events to state tables."""
        successes = 0
        failures = 0
        
        for event in events:
            try:
                # FIXED: Use position_uuid instead of symbol to correctly identify position
                # This ensures we check the correct position when multiple positions exist for same symbol
                if event.get("position_uuid"):
                    if self._is_scale_in_pending(event["position_uuid"]):
                        logger.warning(
                            f"Skipping projection for position {event['position_uuid']}: scale_in_pending=1"
                        )
                        continue
                
                if event["event_type"].startswith("POSITION"):
                    self.position_projector.project(event)
                elif event["event_type"].startswith("ORDER"):
                    self.order_projector.project(event)
                elif "BRACKET" in event["event_type"] or "TP" in event["event_type"] or "SL" in event["event_type"]:
                    self.bracket_projector.project(event)
                
                successes += 1
                
            except Exception as e:
                logger.exception(f"Projection failed for event {event}: {e}")
                self._record_projection_error(event, str(e))
                failures += 1
        
        return successes, failures
    
    def _is_scale_in_pending(self, position_uuid: str) -> bool:
        """Check if a scale-in is pending for a specific position.
        
        Args:
            position_uuid: The unique identifier of the position (NOT symbol).
            
        Returns:
            True if scale_in_pending=1 for this specific position, False otherwise.
            
        Note: Uses position_uuid as the primary key to correctly identify the position,
              since multiple positions can exist for the same symbol.
        """
        with get_connection() as conn:
            cur = conn.cursor()
            # FIXED: Query by position_uuid (primary key) instead of symbol
            # This ensures we check the correct position when multiple positions exist for same symbol
            cur.execute(
                "SELECT scale_in_pending FROM positions WHERE position_uuid = ?", 
                (position_uuid,)
            )
            row = cur.fetchone()
            return row and row[0] == 1
    
    def _record_projection_error(self, event: dict, error: str):
        """Record projection error to dead-letter queue (event_processing_errors).
        
        This implements the dead-letter queue for failed projections,
        ensuring failed events are logged for retry.
        
        Args:
            event: The event that failed to process
            error: The error message
        """
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                # Get event_id from events table if available
                event_id = self._get_event_id(event.get("correlation_id", ""))
                
                cur.execute(
                    """INSERT INTO event_processing_errors 
                       (event_id, event_type, position_uuid, error_message, 
                        retry_count, max_retries, status, first_attempt_at, created_at)
                       VALUES (?, ?, ?, ?, 0, 3, 'PENDING', ?, ?)""",
                    (
                        event_id if event_id else 0,
                        event.get("event_type", "UNKNOWN"),
                        event.get("position_uuid", ""),
                        error,
                        int(time.time() * 1000),
                        int(time.time() * 1000)
                    )
                )
                conn.commit()
                logger.warning(f"Recorded projection error for {event.get('event_type')}: {error}")
        except Exception as e:
            logger.error(f"Failed to record projection error: {e}")
    
    def _get_event_id(self, correlation_id: str) -> Optional[int]:
        """Get event ID from correlation ID.
        
        Args:
            correlation_id: Correlation ID to search for
            
        Returns:
            Event ID if found, None otherwise
        """
        if not correlation_id:
            return None
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id FROM events WHERE correlation_id = ? LIMIT 1",
                    (correlation_id,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None
    
    def retry_failed_projections(self, max_to_process: int = 10) -> Dict[str, int]:
        """Retry failed projections from the dead-letter queue.
        
        Args:
            max_to_process: Maximum number of failed events to retry
            
        Returns:
            Dictionary with retry results
        """
        results = {"retried": 0, "resolved": 0, "failed": 0}
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                # Get pending failures that haven't exceeded max retries
                cur.execute(
                    """SELECT id, event_id, event_type, error_message 
                       FROM event_processing_errors 
                       WHERE status = 'PENDING' AND retry_count < max_retries
                       ORDER BY first_attempt_at ASC LIMIT ?""",
                    (max_to_process,)
                )
                failures = cur.fetchall()
                
                for failure in failures:
                    error_id, event_id, event_type, error_message = failure
                    
                    try:
                        # Increment retry count
                        cur.execute(
                            """UPDATE event_processing_errors 
                               SET retry_count = retry_count + 1, last_retry_at = ?
                               WHERE id = ?""",
                            (int(time.time() * 1000), error_id)
                        )
                        conn.commit()
                        
                        # TODO: Actually retry the projection here
                        # For now, mark as resolved if retries exhausted
                        results["retried"] += 1
                        
                    except Exception as e:
                        logger.warning(f"Retry failed for error {error_id}: {e}")
                        results["failed"] += 1
                
                # Mark resolved if max retries reached
                cur.execute(
                    """UPDATE event_processing_errors 
                       SET status = 'RESOLVED', resolved_at = ?
                       WHERE status = 'PENDING' AND retry_count >= max_retries""",
                    (int(time.time() * 1000),)
                )
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error in retry_failed_projections: {e}")
        
        return results
    
    def _update_risk_state(self, balances: List[dict]):
        """Update risk state from balance data."""
        if not balances:
            return
            
        total_equity = sum(b.get("wallet_balance", 0) for b in balances)
        risk_state = RiskState(
            account_equity=total_equity,
            daily_pnl=0,  # Would be calculated from trades
            drawdown_pct=0,  # Would be calculated from peak equity
            risk_limits={}
        )
        upsert_risk_state(risk_state)
    
    def _complete_batch(self, batch_id: str, result: SyncResult):
        """Mark batch as completed and record metrics."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE sync_batches 
                   SET status = ?, completed_at = ?, 
                       positions_fetched = ?, orders_fetched = ?,
                       events_emitted = ?, projections_succeeded = ?, projections_failed = ?
                    WHERE batch_id = ?""",
                (result.status, result.completed_at, result.positions_fetched,
                 result.orders_fetched, result.events_emitted, result.projections_succeeded,
                 result.projections_failed, batch_id)
            )
            
            # Record sync metrics
            duration_ms = result.completed_at - result.started_at if result.completed_at and result.started_at else 0
            cur.execute(
                """INSERT INTO sync_metrics (
                    batch_id, started_at, completed_at, duration_ms,
                    positions_fetched, orders_fetched, events_emitted,
                    projections_succeeded, projections_failed, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, result.started_at, result.completed_at, duration_ms,
                 result.positions_fetched, result.orders_fetched, result.events_emitted,
                 result.projections_succeeded, result.projections_failed, result.status)
            )
            
            conn.commit()
        logger.debug(f"Completed sync batch {batch_id}: {result.status}")
    
    def _fail_batch(self, batch_id: str, error: str):
        """Mark batch as failed and record metrics."""
        completed_at = int(time.time() * 1000)
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE sync_batches 
                   SET status = 'FAILED', completed_at = ?, error_message = ?
                    WHERE batch_id = ?""",
                (completed_at, error, batch_id)
            )
            
            # Record sync metrics for failed batch
            # We need to get the started_at from the batch
            cur.execute(
                "SELECT started_at FROM sync_batches WHERE batch_id = ?",
                (batch_id,)
            )
            row = cur.fetchone()
            started_at = row[0] if row else completed_at
            duration_ms = completed_at - started_at if started_at else 0
            
            cur.execute(
                """INSERT INTO sync_metrics (
                    batch_id, started_at, completed_at, duration_ms,
                    positions_fetched, orders_fetched, events_emitted,
                    projections_succeeded, projections_failed, status, error_message
                ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 'FAILED', ?)""",
                (batch_id, started_at, completed_at, duration_ms, error)
            )
            
            conn.commit()
        logger.error(f"Failed sync batch {batch_id}: {error}")


# Import dataclass for type hint
from dataclasses import dataclass

@dataclass
class RiskState:
    """Risk state data class (placeholder - should be imported from models)."""
    account_equity: Optional[float] = None
    daily_pnl: Optional[float] = None
    drawdown_pct: Optional[float] = None
    risk_limits: Optional[Dict] = None
