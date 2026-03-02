#!/usr/bin/env python3
"""
SQLite connection factory and schema bootstrap for Aster Trading

This module centralizes DB connection settings (WAL, busy_timeout) and initializes
core tables used as the single source of truth for positions, orders, risk, and metrics.

V3 Schema includes:
- Layer 1 (Raw Snapshots): raw_position_snapshots, raw_order_snapshots, raw_account_snapshots
- Layer 2 (Event Log): events, bracket_state_log, scale_in_conditions_log, event_processing_errors
- Layer 3 (Operational): positions_v3, position_entries, position_closes, bracket_orders
- Layer 4 (Risk): risk_states, risk_events, system_components, sync_metrics
- Additional: sync_batches, funding_rates
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import Iterator

from paths import DATABASE_FILE, LOGS_DIR

logger = logging.getLogger("aster.db")


DEFAULT_DB_PATH = DATABASE_FILE


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cur.fetchall()}
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _ensure_sync_batches_status(cur: sqlite3.Cursor) -> None:
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_batches'")
    row = cur.fetchone()
    if not row:
        return

    columns = [
        "positions_fetched", "orders_fetched", "events_emitted",
        "projections_succeeded", "projections_failed", "error_message"
    ]
    cur.execute(f"PRAGMA table_info(sync_batches)")
    existing = {r[1] for r in cur.fetchall()}
    missing = [col for col in columns if col not in existing]
    if not missing:
        return

    cur.execute("ALTER TABLE sync_batches RENAME TO sync_batches_old")
    cur.execute(
        """
        CREATE TABLE sync_batches (
            batch_id TEXT PRIMARY KEY,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            status TEXT CHECK(status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'PARTIAL')) DEFAULT 'IN_PROGRESS',
            positions_count INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0,
            account_equity REAL,
            error_msg TEXT,
            metadata_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            positions_fetched INTEGER DEFAULT 0,
            orders_fetched INTEGER DEFAULT 0,
            events_emitted INTEGER DEFAULT 0,
            projections_succeeded INTEGER DEFAULT 0,
            projections_failed INTEGER DEFAULT 0,
            error_message TEXT
        )
        """
    )
    cur.execute(
        """
        INSERT INTO sync_batches (
            batch_id, started_at, completed_at, status, positions_count,
            orders_count, account_equity, error_msg, metadata_json, created_at
        )
        SELECT batch_id, started_at, completed_at, status, positions_count,
            orders_count, account_equity, error_msg, metadata_json, created_at
        FROM sync_batches_old
        """
    )
    cur.execute("DROP TABLE sync_batches_old")


def _ensure_sync_batches_stats(cur: sqlite3.Cursor) -> None:
    stats_columns = [
        ("positions_fetched", "positions_fetched INTEGER DEFAULT 0"),
        ("orders_fetched", "orders_fetched INTEGER DEFAULT 0"),
        ("events_emitted", "events_emitted INTEGER DEFAULT 0"),
        ("projections_succeeded", "projections_succeeded INTEGER DEFAULT 0"),
        ("projections_failed", "projections_failed INTEGER DEFAULT 0"),
        ("error_message", "error_message TEXT")
    ]
    for name, definition in stats_columns:
        _ensure_column(cur, "sync_batches", name, definition)


def _init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # Positions snapshot (open positions)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            mark_price REAL,
            unrealized_pnl REAL,
            notional REAL,
            leverage INTEGER,
            open_time INTEGER,
            cycle_id TEXT,
            metadata TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )
    _ensure_column(cur, "positions", "position_uuid", "position_uuid TEXT")
    _ensure_column(cur, "positions", "scale_in_pending", "scale_in_pending INTEGER DEFAULT 0 CHECK(scale_in_pending IN (0, 1))")
    _ensure_column(cur, "positions", "scale_in_timestamp", "scale_in_timestamp INTEGER")

    # Orders (active + historical) — event sourced
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            client_order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            type TEXT,
            status TEXT,
            price REAL,
            quantity REAL,
            executed_qty REAL,
            avg_price REAL,
            reduce_only INTEGER,
            time_in_force TEXT,
            position_side TEXT,
            update_time INTEGER,
            cycle_id TEXT,
            bracket_label TEXT,
            metadata TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Risk state snapshot
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            account_equity REAL,
            daily_pnl REAL,
            drawdown_pct REAL,
            risk_limits TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Healing / execution tracker snapshot
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_tracker (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_signals INTEGER,
            total_orders INTEGER,
            active_orders INTEGER,
            status_distribution TEXT,
            active_order_symbols TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # System runtime state snapshot
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            running INTEGER,
            start_time INTEGER,
            loop_count INTEGER,
            trades_executed INTEGER,
            recent_errors TEXT,
            enabled_symbols TEXT,
            last_signals TEXT,
            market_data TEXT,
            healing_metrics TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Subsystem status tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_components (
            name TEXT PRIMARY KEY,
            status TEXT,
            last_action TEXT,
            last_message TEXT,
            metadata TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Subsystem activity log
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subsystem TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Signals snapshot (last signals per symbol)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS last_signals (
            symbol TEXT PRIMARY KEY,
            action TEXT,
            strength REAL,
            confidence REAL,
            price REAL,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Market snapshot (latest prices per symbol)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_prices (
            symbol TEXT PRIMARY KEY,
            price REAL,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Audit log (append-only events)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            symbol TEXT,
            payload TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Raw signals - ALL signals (not just latest) for historical analysis
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            strength REAL,
            confidence REAL,
            price REAL,
            features_json TEXT,
            correlation_id TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Trades - executed trades history
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            pnl REAL,
            status TEXT DEFAULT 'OPEN',
            open_time INTEGER,
            close_time INTEGER,
            correlation_id TEXT,
            metadata TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Closed positions - historical closed positions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            pnl REAL,
            realized_pnl REAL,
            open_time INTEGER,
            close_time INTEGER,
            duration_minutes INTEGER,
            trade_reason TEXT,
            close_reason TEXT,
            correlation_id TEXT,
            metadata TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Scale-in eligibility checks log (Gap 2 fix: added symbol field)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scale_in_conditions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            position_uuid TEXT NOT NULL,
            symbol TEXT NOT NULL,
            can_scale_in INTEGER NOT NULL CHECK(can_scale_in IN (0, 1)),
            path_used TEXT,
            adx_value REAL,
            funding_rate REAL,
            price_distance_pct REAL,
            margin_ratio REAL,
            pyramid_level INTEGER,
            reason TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )
    
    # Index for querying by position_uuid
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_scale_in_conditions_log_position_uuid ON scale_in_conditions_log(position_uuid)"
    )

    # ============================================================================
    # V3 SCHEMA: LAYER 1 - RAW SNAPSHOTS
    # ============================================================================

    # Raw position snapshots (immutable)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_position_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_batch_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            position_amt REAL,
            entry_price REAL,
            mark_price REAL,
            unrealized_pnl REAL,
            leverage INTEGER,
            margin_type TEXT,
            liquidation_price REAL,
            update_time INTEGER,
            raw_json TEXT NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (sync_batch_id) REFERENCES sync_batches(batch_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_position_batch ON raw_position_snapshots(sync_batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_position_symbol_time ON raw_position_snapshots(symbol, created_at DESC)")

    # Raw order snapshots (immutable)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_order_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_batch_id TEXT NOT NULL,
            order_id TEXT,
            client_order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT,
            type TEXT,
            status TEXT,
            price REAL,
            quantity REAL,
            executed_qty REAL,
            avg_price REAL,
            reduce_only INTEGER,
            time_in_force TEXT,
            stop_price REAL,
            update_time INTEGER,
            raw_json TEXT NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (sync_batch_id) REFERENCES sync_batches(batch_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_order_batch ON raw_order_snapshots(sync_batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_order_symbol_time ON raw_order_snapshots(symbol, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_order_order_id ON raw_order_snapshots(order_id)")

    # Raw account snapshots (immutable)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_batch_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            wallet_balance REAL,
            available_balance REAL,
            cross_wallet_balance REAL,
            cross_initial_margin REAL,
            maintenance_margin REAL,
            raw_json TEXT NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (sync_batch_id) REFERENCES sync_batches(batch_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_account_batch ON raw_account_snapshots(sync_batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_account_asset_time ON raw_account_snapshots(asset, created_at DESC)")

    # ============================================================================
    # V3 SCHEMA: LAYER 2 - EVENT LOG
    # ============================================================================

    # Events table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'POSITION_OPENED', 'POSITION_SCALED_IN', 'POSITION_SCALED_OUT', 'POSITION_CLOSED',
                'ORDER_PLACED', 'ORDER_FILLED', 'ORDER_CANCELLED',
                'SL_TRIGGERED', 'TP1_TRIGGERED', 'TP2_TRIGGERED', 
                'TRAILING_ACTIVATED', 'TRAILING_UPDATED', 'RISK_LIMIT_HIT',
                'SCALE_IN_CHECK', 'SYSTEM_STARTED', 'SYSTEM_STOPPED'
            )),
            event_source TEXT NOT NULL CHECK(event_source IN ('EXCHANGE', 'SYSTEM', 'WEBSOCKET')),
            correlation_id TEXT NOT NULL,
            symbol TEXT,
            payload_json TEXT NOT NULL,
            parent_event_id INTEGER,
            caused_by_event_id INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (parent_event_id) REFERENCES events(id),
            FOREIGN KEY (caused_by_event_id) REFERENCES events(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_parent ON events(parent_event_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_caused_by ON events(caused_by_event_id)")

    # Bracket state transitions (for stop priority audit)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bracket_state_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bracket_uuid TEXT NOT NULL,
            position_uuid TEXT NOT NULL,
            bracket_type TEXT NOT NULL CHECK(bracket_type IN ('STOP_LOSS', 'TAKE_PROFIT_1', 'TAKE_PROFIT_2', 'TRAILING_STOP', 'TIME_STOP')),
            old_state TEXT CHECK(old_state IN ('PENDING', 'ACTIVE', 'FILLED', 'CANCELLED')),
            new_state TEXT CHECK(new_state IN ('PENDING', 'ACTIVE', 'FILLED', 'CANCELLED')),
            trigger_reason TEXT CHECK(trigger_reason IN (
                'TP1_HIT', 'TP2_HIT', 'SCALE_IN_PENDING', 'MANUAL', 'HEALING',
                'PRICE_HIT', 'TRAILING_CALLBACK', 'TIME_EXPIRED', 'SYSTEM_STOP'
            )),
            price_trigger REAL,
            quantity REAL,
            timestamp INTEGER NOT NULL,
            FOREIGN KEY (position_uuid) REFERENCES positions_v3(position_uuid)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bracket_state_position ON bracket_state_log(position_uuid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bracket_state_time ON bracket_state_log(timestamp DESC)")

    # Dead-letter queue for failed projections
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_processing_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            position_uuid TEXT,
            error_message TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            status TEXT CHECK(status IN ('PENDING', 'RESOLVED', 'FAILED')) DEFAULT 'PENDING',
            first_attempt_at INTEGER NOT NULL,
            last_retry_at INTEGER,
            resolved_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
        """
    )
    _ensure_column(cur, "event_processing_errors", "error_message", "error_message TEXT NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_errors_status ON event_processing_errors(status, retry_count)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_errors_event ON event_processing_errors(event_id)")

    # ============================================================================
    # V3 SCHEMA: LAYER 3 - OPERATIONAL STATE
    # ============================================================================

    # Main positions table (V3 with position_uuid as primary key)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS positions_v3 (
            position_uuid TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT')),
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL,
            unrealized_pnl REAL,
            notional REAL,
            leverage INTEGER DEFAULT 1,
            open_time INTEGER NOT NULL,
            close_time INTEGER,
            cycle_id TEXT,
            correlation_id TEXT,
            reason TEXT,
            close_reason TEXT,
            version INTEGER DEFAULT 1,
            metadata_json TEXT,
            scale_in_pending INTEGER DEFAULT 0 CHECK(scale_in_pending IN (0, 1)),
            scale_in_timestamp INTEGER,
            pyramid_scales INTEGER DEFAULT 0,
            last_scale_price REAL,
            last_scale_time INTEGER,
            trailing_active INTEGER DEFAULT 0 CHECK(trailing_active IN (0, 1)),
            trailing_activation_price REAL,
            trailing_callback_pct REAL DEFAULT 0.8,
            tp1_hit INTEGER DEFAULT 0 CHECK(tp1_hit IN (0, 1)),
            tp2_hit INTEGER DEFAULT 0 CHECK(tp2_hit IN (0, 1)),
            sl_at_break_even INTEGER DEFAULT 0 CHECK(sl_at_break_even IN (0, 1)),
            initial_sl_price REAL,
            current_sl_price REAL,
            max_hold_time_minutes INTEGER,
            status TEXT CHECK(status IN ('OPEN', 'CLOSED')) DEFAULT 'OPEN',
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_v3_symbol ON positions_v3(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_v3_status ON positions_v3(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_v3_cycle ON positions_v3(cycle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_v3_correlation ON positions_v3(correlation_id)")

    # Position entries (every time we add to a position)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS position_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_uuid TEXT NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN ('INITIAL', 'SCALE_IN')),
            entry_price REAL NOT NULL,
            quantity REAL NOT NULL,
            notional REAL,
            order_id TEXT,
            correlation_id TEXT,
            timestamp INTEGER NOT NULL,
            metadata_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (position_uuid) REFERENCES positions_v3(position_uuid)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_position_entries_position ON position_entries(position_uuid, timestamp DESC)")

    # Position closes (every time we reduce/close a position)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS position_closes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_uuid TEXT NOT NULL,
            close_type TEXT NOT NULL CHECK(close_type IN ('PARTIAL', 'FULL', 'STOP_LOSS', 'TAKE_PROFIT', 'TIME_STOP', 'LIQUIDATION')),
            close_price REAL NOT NULL,
            quantity REAL NOT NULL,
            pnl REAL,
            pnl_pct REAL,
            order_id TEXT,
            correlation_id TEXT,
            timestamp INTEGER NOT NULL,
            close_reason TEXT,
            metadata_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (position_uuid) REFERENCES positions_v3(position_uuid)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_position_closes_position ON position_closes(position_uuid, timestamp DESC)")

    # Bracket orders (current SL/TP/trailing instructions)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bracket_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bracket_uuid TEXT NOT NULL UNIQUE,
            position_uuid TEXT NOT NULL,
            bracket_type TEXT NOT NULL CHECK(bracket_type IN ('STOP_LOSS', 'TAKE_PROFIT_1', 'TAKE_PROFIT_2', 'TRAILING_STOP', 'TIME_STOP')),
            status TEXT CHECK(status IN ('PENDING', 'ACTIVE', 'FILLED', 'CANCELLED')) DEFAULT 'PENDING',
            order_id TEXT,
            price_trigger REAL,
            quantity REAL,
            reduce_only INTEGER DEFAULT 1 CHECK(reduce_only IN (0, 1)),
            activation_price REAL,
            callback_rate REAL,
            timestamp INTEGER NOT NULL,
            filled_at INTEGER,
            metadata_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (position_uuid) REFERENCES positions_v3(position_uuid)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bracket_orders_position ON bracket_orders(position_uuid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bracket_orders_type ON bracket_orders(bracket_type, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bracket_orders_status ON bracket_orders(status)")

    # ============================================================================
    # V3 SCHEMA: LAYER 4 - RISK & OBSERVABILITY
    # ============================================================================

    # Risk state snapshots (one row per heartbeat)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_states (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            account_equity REAL NOT NULL,
            daily_pnl REAL DEFAULT 0,
            daily_pnl_pct REAL DEFAULT 0,
            drawdown_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            open_positions_count INTEGER DEFAULT 0,
            total_exposure REAL DEFAULT 0,
            can_trade INTEGER DEFAULT 1 CHECK(can_trade IN (0, 1)),
            circuit_breaker_triggered INTEGER DEFAULT 0 CHECK(circuit_breaker_triggered IN (0, 1)),
            risk_limits_json TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )

    # Risk events (discrete breach events)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'CIRCUIT_BREAKER_OPEN', 'DRAWDOWN_LIMIT_HIT', 'DAILY_LOSS_LIMIT_HIT',
                'LIQUIDATION_WARNING', 'MARGIN_RATIO_WARNING', 'POSITION_SIZE_EXCEEDED',
                'EXPOSURE_LIMIT_EXCEEDED', 'RISK_ADJUSTMENT_APPLIED'
            )),
            severity TEXT CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL')) DEFAULT 'INFO',
            symbol TEXT,
            message TEXT NOT NULL,
            details_json TEXT,
            triggered_at INTEGER NOT NULL,
            resolved_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_type_time ON risk_events(event_type, triggered_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_symbol ON risk_events(symbol, triggered_at DESC)")

    # Sync batch metrics (for SLA monitoring)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            duration_ms INTEGER,
            positions_fetched INTEGER,
            orders_fetched INTEGER,
            events_emitted INTEGER,
            projections_succeeded INTEGER,
            projections_failed INTEGER,
            status TEXT,
            error_message TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            FOREIGN KEY (batch_id) REFERENCES sync_batches(batch_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_metrics_time ON sync_metrics(started_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_metrics_status ON sync_metrics(status)")

    # ============================================================================
    # V3 SCHEMA: ADDITIONAL TABLES
    # ============================================================================

    # Sync batches (tracks sync batch metadata)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_batches (
            batch_id TEXT PRIMARY KEY,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            status TEXT CHECK(status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'PARTIAL')) DEFAULT 'IN_PROGRESS',
            positions_count INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0,
            account_equity REAL,
            error_msg TEXT,
            metadata_json TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_batches_status ON sync_batches(status, started_at)")
    _ensure_sync_batches_status(cur)

    # Funding rates (for scale-in eligibility checks)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS funding_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            funding_rate REAL NOT NULL,
            funding_time INTEGER NOT NULL,
            next_funding_time INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            UNIQUE(symbol, funding_time)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol_time ON funding_rates(symbol, funding_time DESC)")

    # Schema migrations tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at INTEGER DEFAULT (strftime('%s','now') * 1000),
            rollback_sql TEXT
        )
        """
    )

    # ============================================================================
    # V3 SCHEMA: DASHBOARD VIEWS
    # ============================================================================

    # Active positions view
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_active_positions AS
        SELECT 
            position_uuid,
            symbol,
            side,
            quantity,
            entry_price,
            current_price,
            unrealized_pnl,
            notional,
            leverage,
            ROUND((julianday('now') - julianday(open_time/1000, 'unixepoch')) * 24 * 60, 2) as hold_time_minutes,
            pyramid_scales,
            scale_in_pending,
            tp1_hit,
            tp2_hit,
            trailing_active,
            status,
            updated_at
        FROM positions_v3
        WHERE status = 'OPEN' AND quantity > 0
        """
    )

    # Position PnL summary
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_position_pnl AS
        SELECT 
            p.position_uuid,
            p.symbol,
            p.side,
            p.quantity as current_qty,
            p.entry_price,
            COALESCE(SUM(pc.pnl), 0) as realized_pnl,
            p.unrealized_pnl,
            COALESCE(SUM(pc.pnl), 0) + COALESCE(p.unrealized_pnl, 0) as total_pnl
        FROM positions_v3 p
        LEFT JOIN position_closes pc ON p.position_uuid = pc.position_uuid
        GROUP BY p.position_uuid
        """
    )

    # Risk dashboard view - FIXED: Query V2 tables (risk_state) instead of empty V3 tables
    # Note: risk_state contains JSON with actual position data including notional values
    # This view extracts equity, pnl, drawdown from risk_state and position counts/notional from JSON
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_risk_dashboard AS
        SELECT 
            (SELECT account_equity FROM risk_state WHERE id = 1) as equity,
            (SELECT daily_pnl FROM risk_state WHERE id = 1) as daily_pnl,
            (SELECT drawdown_pct FROM risk_state WHERE id = 1) as drawdown_pct,
            1 as can_trade,
            (SELECT json_extract(risk_limits, '$.open_positions_count') FROM risk_state WHERE id = 1) as open_positions,
            (SELECT COALESCE(json_extract(risk_limits, '$.positions.SOLUSDT.notional'), 0) + 
                    COALESCE(json_extract(risk_limits, '$.positions.ETHUSDT.notional'), 0) +
                    COALESCE(json_extract(risk_limits, '$.positions.BNBUSDT.notional'), 0) +
                    COALESCE(json_extract(risk_limits, '$.positions.BTCUSDT.notional'), 0) FROM risk_state WHERE id = 1) as total_exposure,
            (SELECT COUNT(*) FROM risk_events WHERE triggered_at > strftime('%s','now')*1000 - 86400000) as events_24h
        """
    )

    # Sync health view
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_sync_health AS
        SELECT 
            batch_id,
            started_at,
            completed_at,
            status,
            positions_count,
            orders_count,
            account_equity,
            error_msg,
            (completed_at - started_at) as duration_ms
        FROM sync_batches
        ORDER BY started_at DESC
        LIMIT 100
        """
    )

    conn.commit()


@contextmanager
def get_connection(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> str:
    """Initialize database and return path."""
    _ensure_dir(db_path)
    with get_connection(db_path) as conn:
        _init_schema(conn)
    logger.info(f"SQLite initialized at {db_path}")
    return db_path


# Initialize eagerly on import to ensure schema exists for writers/readers.
init_db(DEFAULT_DB_PATH)
