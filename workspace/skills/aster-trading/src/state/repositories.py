#!/usr/bin/env python3
"""Repository layer encapsulating DB CRUD for state entities."""

import json
import time
from typing import List, Optional
from pathlib import Path

from paths import LOGS_DIR
from .db import get_connection
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
    RawSignal,
    Trade,
    ClosedPosition,
)


def upsert_position(p: Position) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO positions (
                symbol, side, quantity, entry_price, mark_price, unrealized_pnl,
                notional, leverage, open_time, cycle_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                side=excluded.side,
                quantity=excluded.quantity,
                entry_price=excluded.entry_price,
                mark_price=excluded.mark_price,
                unrealized_pnl=excluded.unrealized_pnl,
                notional=excluded.notional,
                leverage=excluded.leverage,
                open_time=excluded.open_time,
                cycle_id=excluded.cycle_id,
                metadata=excluded.metadata,
                updated_at=(strftime('%s','now') * 1000)
            """,
            (
                p.symbol,
                p.side,
                p.quantity,
                p.entry_price,
                p.mark_price,
                p.unrealized_pnl,
                p.notional,
                p.leverage,
                p.open_time,
                p.cycle_id,
                json.dumps(p.metadata) if p.metadata else None,
            ),
        )
        conn.commit()


def get_positions() -> List[Position]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM positions")
        rows = cur.fetchall()
        result = []
        for r in rows:
            data = dict(r)
            # Parse metadata JSON string back to dict
            if data.get('metadata') and isinstance(data['metadata'], str):
                try:
                    data['metadata'] = json.loads(data['metadata'])
                except (json.JSONDecodeError, TypeError):
                    data['metadata'] = {}
            result.append(Position(**data))
        return result


def delete_position(symbol: str) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        conn.commit()


def insert_order(o: Order) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO orders (
                order_id, client_order_id, symbol, side, type, status, price,
                quantity, executed_qty, avg_price, reduce_only, time_in_force,
                position_side, update_time, cycle_id, bracket_label, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                o.order_id,
                o.client_order_id,
                o.symbol,
                o.side,
                o.type,
                o.status,
                o.price,
                o.quantity,
                o.executed_qty,
                o.avg_price,
                int(o.reduce_only) if o.reduce_only is not None else None,
                o.time_in_force,
                o.position_side,
                o.update_time,
                o.cycle_id,
                o.bracket_label,
                json.dumps(o.metadata) if o.metadata else None,
            ),
        )
        conn.commit()
        return cur.lastrowid


def upsert_order(o: Order) -> None:
    """Insert or update an order."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO orders (
                order_id, client_order_id, symbol, side, type, status, price,
                quantity, executed_qty, avg_price, reduce_only, time_in_force,
                position_side, update_time, cycle_id, bracket_label, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status=excluded.status,
                executed_qty=excluded.executed_qty,
                avg_price=excluded.avg_price,
                update_time=excluded.update_time,
                metadata=excluded.metadata
            """,
            (
                o.order_id,
                o.client_order_id,
                o.symbol,
                o.side,
                o.type,
                o.status,
                o.price,
                o.quantity,
                o.executed_qty,
                o.avg_price,
                int(o.reduce_only) if o.reduce_only is not None else None,
                o.time_in_force,
                o.position_side,
                o.update_time,
                o.cycle_id,
                o.bracket_label,
                json.dumps(o.metadata) if o.metadata else None,
            ),
        )
        conn.commit()


def get_orders(active_only: bool = True, limit: int = 200) -> List[Order]:
    """Fetch orders from DB (active_only filters to NEW/PARTIALLY_FILLED/PENDING)."""
    status_filter = ("NEW", "PARTIALLY_FILLED", "PENDING") if active_only else None
    with get_connection() as conn:
        cur = conn.cursor()
        if status_filter:
            cur.execute(
                "SELECT * FROM orders WHERE status IN (?, ?, ?) ORDER BY id DESC LIMIT ?",
                (*status_filter, limit),
            )
        else:
            cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            data = dict(r)
            # Parse metadata JSON string back to dict
            if data.get('metadata') and isinstance(data['metadata'], str):
                try:
                    data['metadata'] = json.loads(data['metadata'])
                except (json.JSONDecodeError, TypeError):
                    data['metadata'] = None
            result.append(Order(**data))
        return result


def upsert_risk_state(r: RiskState) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO risk_state (id, account_equity, daily_pnl, drawdown_pct, risk_limits)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_equity=excluded.account_equity,
                daily_pnl=excluded.daily_pnl,
                drawdown_pct=excluded.drawdown_pct,
                risk_limits=excluded.risk_limits,
                updated_at=(strftime('%s','now') * 1000)
            """,
            (
                r.account_equity,
                r.daily_pnl,
                r.drawdown_pct,
                json.dumps(r.risk_limits) if r.risk_limits else None,
            ),
        )
        conn.commit()


def get_risk_state() -> Optional[RiskState]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM risk_state WHERE id = 1")
        row = cur.fetchone()
        if row:
            data = dict(row)
            # Parse risk_limits JSON string back to dict
            if data.get('risk_limits') and isinstance(data['risk_limits'], str):
                try:
                    data['risk_limits'] = json.loads(data['risk_limits'])
                except (json.JSONDecodeError, TypeError):
                    data['risk_limits'] = {}
            return RiskState(**data)
        return None


def upsert_execution_tracker(e: ExecutionTracker) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO execution_tracker (
                id, total_signals, total_orders, active_orders,
                status_distribution, active_order_symbols
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                total_signals=excluded.total_signals,
                total_orders=excluded.total_orders,
                active_orders=excluded.active_orders,
                status_distribution=excluded.status_distribution,
                active_order_symbols=excluded.active_order_symbols,
                updated_at=(strftime('%s','now') * 1000)
            """,
            (
                e.total_signals,
                e.total_orders,
                e.active_orders,
                json.dumps(e.status_distribution) if e.status_distribution else None,
                json.dumps(e.active_order_symbols) if e.active_order_symbols else None,
            ),
        )
        conn.commit()


def get_execution_tracker() -> Optional[ExecutionTracker]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM execution_tracker WHERE id = 1")
        row = cur.fetchone()
        if row:
            data = dict(row)
            # Parse status_distribution JSON string back to dict
            if data.get('status_distribution') and isinstance(data['status_distribution'], str):
                try:
                    data['status_distribution'] = json.loads(data['status_distribution'])
                except (json.JSONDecodeError, TypeError):
                    data['status_distribution'] = None

            # Parse active_order_symbols JSON string back to list
            if data.get('active_order_symbols') and isinstance(data['active_order_symbols'], str):
                try:
                    data['active_order_symbols'] = json.loads(data['active_order_symbols'])
                except (json.JSONDecodeError, TypeError):
                    data['active_order_symbols'] = None

            return ExecutionTracker(**data)
        return None


def upsert_last_signal(s: LastSignal) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO last_signals (symbol, action, strength, confidence, price)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                action=excluded.action,
                strength=excluded.strength,
                confidence=excluded.confidence,
                price=excluded.price,
                updated_at=(strftime('%s','now') * 1000)
            """,
            (s.symbol, s.action, s.strength, s.confidence, s.price),
        )
        conn.commit()


def get_last_signals() -> dict:
    """Return last signals keyed by symbol."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT symbol, action, strength, confidence, price, updated_at FROM last_signals")
        rows = cur.fetchall()
        out = {}
        for r in rows:
            data = dict(r)
            symbol = str(data.get("symbol", "")).upper()
            if not symbol:
                continue
            out[symbol] = {
                "action": data.get("action"),
                "strength": data.get("strength"),
                "confidence": data.get("confidence"),
                "price": data.get("price"),
                "updated_at": data.get("updated_at"),
            }
        return out


def upsert_market_price(m: MarketPrice) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_prices (symbol, price)
            VALUES (?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                price=excluded.price,
                updated_at=(strftime('%s','now') * 1000)
            """,
            (m.symbol, m.price),
        )
        conn.commit()


def get_market_prices() -> dict:
    """Return latest market prices keyed by symbol."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT symbol, price, updated_at FROM market_prices")
        rows = cur.fetchall()
        out = {}
        for r in rows:
            data = dict(r)
            symbol = str(data.get("symbol", "")).upper()
            if not symbol:
                continue
            out[symbol] = {
                "price": data.get("price"),
                "updated_at": data.get("updated_at"),
            }
        return out


def log_audit(event: AuditEvent) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO audit_log (event_type, symbol, payload)
            VALUES (?, ?, ?)
            """,
            (event.event_type, event.symbol, json.dumps(event.payload)),
        )
        conn.commit()
        rowid = cur.lastrowid

        # Mirror to JSONL for external audit trail
        try:
            history_dir = Path(LOGS_DIR) / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            jsonl_path = history_dir / "audit.jsonl"
            payload = {
                "id": rowid,
                "event_type": event.event_type,
                "symbol": event.symbol,
                "payload": event.payload,
                "created_at": int(time.time() * 1000),
            }
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return rowid


# System State
def upsert_system_state(s: SystemState) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_state (id, running, start_time, loop_count, trades_executed, 
                recent_errors, enabled_symbols, last_signals, market_data, healing_metrics, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                running=excluded.running,
                start_time=excluded.start_time,
                loop_count=excluded.loop_count,
                trades_executed=excluded.trades_executed,
                recent_errors=excluded.recent_errors,
                enabled_symbols=excluded.enabled_symbols,
                last_signals=excluded.last_signals,
                market_data=excluded.market_data,
                healing_metrics=excluded.healing_metrics,
                updated_at=excluded.updated_at
            """,
            (
                1 if s.running else 0,
                s.start_time,
                s.loop_count,
                s.trades_executed,
                json.dumps(s.recent_errors) if s.recent_errors else None,
                json.dumps(s.enabled_symbols) if s.enabled_symbols else None,
                json.dumps(s.last_signals) if s.last_signals else None,
                json.dumps(s.market_data) if s.market_data else None,
                json.dumps(s.healing_metrics) if s.healing_metrics else None,
                int(time.time() * 1000),
            ),
        )
        conn.commit()


def get_system_state() -> Optional[SystemState]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM system_state WHERE id = 1")
        row = cur.fetchone()
        if row:
            data = dict(row)
            # Parse JSON fields
            for field in ['recent_errors', 'enabled_symbols', 'last_signals', 'market_data', 'healing_metrics']:
                if data.get(field) and isinstance(data[field], str):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        data[field] = None
            data['running'] = bool(data.get('running', 0))
            return SystemState(**data)
        return None


# System components / subsystem tracking
def upsert_system_component(component: SystemComponent) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_components (name, status, last_action, last_message, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                status=excluded.status,
                last_action=excluded.last_action,
                last_message=excluded.last_message,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (
                component.name,
                component.status,
                component.last_action,
                component.last_message,
                json.dumps(component.metadata) if component.metadata else None,
                component.updated_at or int(time.time() * 1000),
            ),
        )
        conn.commit()


def get_system_components(names: Optional[List[str]] = None) -> List[SystemComponent]:
    with get_connection() as conn:
        cur = conn.cursor()
        if names:
            placeholders = ",".join(["?"] * len(names))
            cur.execute(
                f"SELECT * FROM system_components WHERE name IN ({placeholders})",
                tuple(names),
            )
        else:
            cur.execute("SELECT * FROM system_components")
        rows = cur.fetchall()
        components: List[SystemComponent] = []
        for row in rows:
            data = dict(row)
            if data.get("metadata") and isinstance(data["metadata"], str):
                try:
                    data["metadata"] = json.loads(data["metadata"])
                except (json.JSONDecodeError, TypeError):
                    data["metadata"] = None
            components.append(SystemComponent(**data))
        return components


def get_system_component(name: str) -> Optional[SystemComponent]:
    comps = get_system_components([name])
    return comps[0] if comps else None


def log_system_activity(activity: SystemActivity) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_activity (subsystem, action, status, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                activity.subsystem,
                activity.action,
                activity.status,
                activity.message,
                activity.created_at or int(time.time() * 1000),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_system_activity(subsystem: Optional[str] = None, limit: int = 200) -> List[SystemActivity]:
    with get_connection() as conn:
        cur = conn.cursor()
        if subsystem:
            cur.execute(
                """
                SELECT * FROM system_activity
                WHERE subsystem = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (subsystem, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM system_activity
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        activities: List[SystemActivity] = []
        for row in rows:
            activities.append(SystemActivity(**dict(row)))
        return activities


# =============================================================================
# Raw Signals Repository - All signals for historical tracking
# =============================================================================

def insert_raw_signal(s: RawSignal) -> int:
    """Insert a new raw signal for historical tracking."""
    import json
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO raw_signals (
                symbol, action, strength, confidence, price,
                features_json, correlation_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s.symbol,
                s.action,
                s.strength,
                s.confidence,
                s.price,
                s.features_json,
                s.correlation_id,
                s.status or "PENDING",
                s.created_at or (int(time.time() * 1000)),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_raw_signals(symbol: Optional[str] = None, limit: int = 100) -> List[RawSignal]:
    """Get raw signals, optionally filtered by symbol."""
    with get_connection() as conn:
        cur = conn.cursor()
        if symbol:
            cur.execute(
                """
                SELECT * FROM raw_signals
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM raw_signals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        signals: List[RawSignal] = []
        for row in rows:
            signals.append(RawSignal(**dict(row)))
        return signals


# =============================================================================
# Trades Repository - Executed trades history
# =============================================================================

def insert_trade(t: Trade) -> int:
    """Insert a new trade."""
    import json
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trades (
                symbol, side, entry_price, exit_price, quantity, pnl,
                status, open_time, close_time, correlation_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.symbol,
                t.side,
                t.entry_price,
                t.exit_price,
                t.quantity,
                t.pnl,
                t.status or "OPEN",
                t.open_time,
                t.close_time,
                t.correlation_id,
                json.dumps(t.metadata) if t.metadata else None,
                t.created_at or (int(time.time() * 1000)),
            ),
        )
        conn.commit()
        return cur.lastrowid


def update_trade(trade_id: int, exit_price: float, pnl: float, close_time: int, status: str = "CLOSED") -> None:
    """Update trade with exit info when position is closed."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE trades SET exit_price = ?, pnl = ?, close_time = ?, status = ?
            WHERE id = ?
            """,
            (exit_price, pnl, close_time, status, trade_id),
        )
        conn.commit()


def get_trades(status: Optional[str] = None, limit: int = 100) -> List[Trade]:
    """Get trades, optionally filtered by status."""
    import json
    with get_connection() as conn:
        cur = conn.cursor()
        if status:
            cur.execute(
                """
                SELECT * FROM trades
                WHERE status = ?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM trades
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        trades: List[Trade] = []
        for row in rows:
            data = dict(row)
            if data.get("metadata"):
                data["metadata"] = json.loads(data["metadata"])
            trades.append(Trade(**data))
        return trades


# =============================================================================
# Closed Positions Repository - Historical closed positions
# =============================================================================

def insert_closed_position(cp: ClosedPosition) -> int:
    """Insert a closed position for historical tracking."""
    import json
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO closed_positions (
                symbol, side, entry_price, exit_price, quantity, pnl,
                realized_pnl, open_time, close_time, duration_minutes,
                trade_reason, close_reason, correlation_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cp.symbol,
                cp.side,
                cp.entry_price,
                cp.exit_price,
                cp.quantity,
                cp.pnl,
                cp.realized_pnl,
                cp.open_time,
                cp.close_time,
                cp.duration_minutes,
                cp.trade_reason,
                cp.close_reason,
                cp.correlation_id,
                json.dumps(cp.metadata) if cp.metadata else None,
                cp.created_at or (int(time.time() * 1000)),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_closed_positions(symbol: Optional[str] = None, limit: int = 100) -> List[ClosedPosition]:
    """Get closed positions history."""
    import json
    with get_connection() as conn:
        cur = conn.cursor()
        if symbol:
            cur.execute(
                """
                SELECT * FROM closed_positions
                WHERE symbol = ?
                ORDER BY close_time DESC
                LIMIT ?
                """,
                (symbol, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM closed_positions
                ORDER BY close_time DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        positions: List[ClosedPosition] = []
        for row in rows:
            data = dict(row)
            if data.get("metadata"):
                data["metadata"] = json.loads(data["metadata"])
            positions.append(ClosedPosition(**data))
        return positions
