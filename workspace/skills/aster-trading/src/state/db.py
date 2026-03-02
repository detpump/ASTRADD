#!/usr/bin/env python3
"""
SQLite connection factory and schema bootstrap for Aster Trading

This module centralizes DB connection settings (WAL, busy_timeout) and initializes
core tables used as the single source of truth for positions, orders, risk, and metrics.
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
