# Data Architecture Detailed – V3 (Production-Ready Implementation Blueprint)

**Version:** 3.0  
**Date:** 2026-03-02  
**Status:** IMPLEMENTATION-READY  
**Supersedes:** [`DATA_ARCHITECTURE_DETAILED_V2.md`](workspace/skills/aster-trading/docs/DATA_ARCHITECTURE_DETAILED_V2.md)  

This document provides a production-ready implementation blueprint that addresses all gaps identified in the V2 architecture analysis. It includes complete DDL statements, migration strategies, implementation pseudo-code, and error recovery procedures designed for autonomous implementation.

---

## Table of Contents

1. [Objectives, Constraints & External Contracts](#1-objectives-constraints--external-contracts)
2. [Migration Strategy & Phase 0](#2-migration-strategy--phase-0)
3. [Complete Database Schema (DDL)](#3-complete-database-schema-ddl)
4. [Exchange Data Sources & Payloads](#4-exchange-data-sources--payloads)
5. [Layered Data Model](#5-layered-data-model)
6. [Sync & Projection Orchestration](#6-sync--projection-orchestration)
7. [Stop Management & Scale-In Control Plane](#7-stop-management--scale-in-control-plane)
8. [Conflict Resolution Algorithms](#8-conflict-resolution-algorithms)
9. [Performance & Scaling](#9-performance--scaling)
10. [Funding Rate Integration](#10-funding-rate-integration)
11. [Testing Strategy](#11-testing-strategy)
12. [Error Recovery Procedures](#12-error-recovery-procedures)
13. [Implementation Checklist](#13-implementation-checklist)
14. [Key Technical Decisions & Rationale](#14-key-technical-decisions--rationale)

---

## 1. Objectives, Constraints & External Contracts

### 1.1 Functional Scope

| Area | Details |
|------|---------|
| Data Acquisition | Acquire exchange state via REST API, persist atomically |
| Event Generation | Generate auditable events with full causal chains |
| State Projection | Project into operational tables deterministically |
| Automation | Drive stop & scale-in automation |
| Telemetry | Surface system health and performance metrics |

### 1.2 Non-Functional Requirements

| Requirement | Target | Implementation |
|-------------|--------|----------------|
| Deterministic Replay | Per `sync_batch_id` | Events stored with correlation_ids, batch atomicity |
| Lossless Forensic Storage | 100% of API responses | `raw_*` tables preserve original JSON |
| Concurrency Safety | No lost updates | Optimistic locking via `version` field |
| Audit Trail | Full causal chain | `parent_event_id` and `correlation_id` linking |
| Recovery Time Objective | < 5 minutes | Dead-letter queue with retry workers |

### 1.3 External Contracts

| Contract | Location | Purpose |
|----------|----------|---------|
| REST/WebSocket Schemas | Aster Futures API | Position, order, balance data |
| Dashboard Views | [`dashboard/server.py`](workspace/skills/aster-trading/dashboard/server.py) | SQLite views for read queries |
| Bracket Semantics | [`risk/manage_brackets.py`](workspace/skills/aster-trading/src/risk/manage_brackets.py) | SL/TP/trailing orchestration |
| Recovery Logic | [`recovery_manager.py`](workspace/skills/aster-trading/src/recovery_manager.py) | State reconstruction |

### 1.4 Invariants (Critical Rules)

| Invariant | Enforcement |
|-----------|-------------|
| Never mutate exchange payloads before `raw_*` tables | Raw layer is append-only |
| Never emit events without `correlation_id` | SyncEngine validates before emission |
| Never scale-in while `scale_in_pending=1` | Database-level constraint + application lock |
| Honor stop priority order | Bracket state machine enforces precedence |
| Optimistic locking prevents lost updates | `version` field checked on every update |

---

## 2. Migration Strategy & Phase 0

### 2.1 Migration Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PHASE 0: FOUNDATION (Weeks 1-2)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Step 0.1: Add Missing Columns to Existing Tables                           │
│  ├── Add position_uuid, version, scale_in_pending to positions              │
│  ├── Add correlation_id to orders table                                     │
│  ├── Add event_type, event_source to audit_log (backward compat)           │
│                                                                             │
│  Step 0.2: Create sync_batches Table (Non-Breaking)                        │
│  ├── Tracks batch metadata without changing existing behavior               │
│  ├── Dual-write: Continue JSON writes + write to sync_batches               │
│                                                                             │
│  Step 0.3: Backfill Existing Data                                           │
│  ├── Generate UUIDs for existing positions                                 │
│  ├── Set initial version=1 for all rows                                    │
│  └── Create correlation_ids for active orders                               │
│                                                                             │
│  Step 0.4: Persist scale_in_pending to Database                            │
│  ├── Move from in-memory cache to DB column                                │
│  ├── Update trade_state.py to read/write from DB                           │
│  └── Test crash recovery: simulate crash mid-scale-in                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Migration SQL (Execute in Order)

```sql
-- ============================================================================
-- MIGRATION STEP 0.1: Add Missing Columns (Non-Breaking)
-- ============================================================================

-- Add UUID support to positions (allows multiple positions per symbol)
ALTER TABLE positions ADD COLUMN position_uuid TEXT DEFAULT (lower(hex(randomblob(16))));
ALTER TABLE positions ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE positions ADD COLUMN scale_in_pending INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN pyramid_scales INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN last_scale_price REAL;
ALTER TABLE positions ADD COLUMN last_scale_time INTEGER;
ALTER TABLE positions ADD COLUMN trailing_active INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN trailing_activation_price REAL;
ALTER TABLE positions ADD COLUMN tp1_hit INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN tp2_hit INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN sl_at_break_even INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN correlation_id TEXT;
ALTER TABLE positions ADD COLUMN open_time INTEGER;
ALTER TABLE positions ADD COLUMN close_time INTEGER;
ALTER TABLE positions ADD COLUMN close_reason TEXT;

-- Add correlation_id to orders for event linking
ALTER TABLE orders ADD COLUMN correlation_id TEXT;

-- Convert existing positions to use UUID (backfill)
UPDATE positions SET position_uuid = lower(hex(randomblob(16))) WHERE position_uuid IS NULL;

-- Create primary key on UUID instead of symbol
-- NOTE: This requires removing old PK constraint first
-- Step 1: Create new table with correct schema
CREATE TABLE positions_new (
    position_uuid TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    mark_price REAL,
    unrealized_pnl REAL,
    notional REAL,
    leverage INTEGER,
    open_time INTEGER,
    close_time INTEGER,
    cycle_id TEXT,
    correlation_id TEXT,
    version INTEGER DEFAULT 1,
    scale_in_pending INTEGER DEFAULT 0,
    pyramid_scales INTEGER DEFAULT 0,
    last_scale_price REAL,
    last_scale_time INTEGER,
    trailing_active INTEGER DEFAULT 0,
    trailing_activation_price REAL,
    tp1_hit INTEGER DEFAULT 0,
    tp2_hit INTEGER DEFAULT 0,
    sl_at_break_even INTEGER DEFAULT 0,
    close_reason TEXT,
    metadata TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
);

-- Step 2: Copy data
INSERT INTO positions_new SELECT 
    position_uuid, symbol, side, quantity, entry_price, mark_price,
    unrealized_pnl, notional, leverage, open_time, close_time,
    cycle_id, correlation_id, version, scale_in_pending, pyramid_scales,
    last_scale_price, last_scale_time, trailing_active, trailing_activation_price,
    tp1_hit, tp2_hit, sl_at_break_even, close_reason, metadata, updated_at
FROM positions;

-- Step 3: Drop old table and rename
DROP TABLE positions;
ALTER TABLE positions_new RENAME TO positions;

-- Create indexes for new schema
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_status ON positions(quantity);
CREATE INDEX idx_positions_correlation ON positions(correlation_id);
CREATE INDEX idx_positions_version ON positions(version);

-- ============================================================================
-- MIGRATION STEP 0.2: Create sync_batches Table (Non-Breaking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS sync_batches (
    batch_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    status TEXT CHECK(status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')) DEFAULT 'IN_PROGRESS',
    positions_count INTEGER DEFAULT 0,
    orders_count INTEGER DEFAULT 0,
    account_equity REAL,
    error_msg TEXT,
    metadata_json TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now') * 1000)
);

CREATE INDEX idx_sync_batches_status ON sync_batches(status, started_at);
CREATE INDEX idx_sync_batches_completed ON sync_batches(completed_at) WHERE completed_at IS NOT NULL;

-- ============================================================================
-- MIGRATION STEP 0.3: Create Funding Rate Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    funding_time INTEGER NOT NULL,
    next_funding_time INTEGER,
    created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
    UNIQUE(symbol, funding_time)
);

CREATE INDEX idx_funding_rates_symbol_time ON funding_rates(symbol, funding_time DESC);
```

### 2.3 Dual-Write Strategy

During migration, the system must write to both old and new storage to ensure zero downtime:

```python
# src/state/migration_helpers.py

def dual_write_position(position_data: dict, db_position: dict) -> None:
    """
    Write to both legacy JSON (for backward compat) and new DB schema.
    Remove JSON writes after full migration.
    """
    # 1. Write to new DB schema (authoritative)
    try:
        state_service.upsert_position(db_position)
    except Exception as e:
        logging.error(f"Failed to write position to DB: {e}")
        raise
    
    # 2. Also write to legacy JSON (temporary - remove after migration)
    try:
        legacy_position = {
            "symbol": position_data["symbol"],
            "side": position_data["side"],
            "size": position_data["quantity"],
            "entry_price": position_data["entry_price"],
        }
        _set_position_state(position_data["symbol"], legacy_position)
        logging.warning("Dual-write: Also wrote to legacy JSON (remove after migration)")
    except Exception as e:
        logging.warning(f"Failed to write legacy JSON (non-critical): {e}")
```

### 2.4 Rollback Procedure

If migration encounters critical errors:

```sql
-- ROLLBACK: Revert to V2 schema
ALTER TABLE positions DROP COLUMN position_uuid;
ALTER TABLE positions DROP COLUMN version;
ALTER TABLE positions DROP COLUMN scale_in_pending;

-- Restore symbol as primary key
CREATE TABLE positions_old AS SELECT symbol, side, quantity, entry_price, 
                                     mark_price, unrealized_pnl, notional, 
                                     leverage, open_time, cycle_id, metadata, updated_at
FROM positions;
DROP TABLE positions;
ALTER TABLE positions_old RENAME TO positions;
```

---

## 3. Complete Database Schema (DDL)

### 3.1 Layer 1 – Raw Snapshots

```sql
-- ============================================================================
-- LAYER 1: RAW SNAPSHOTS
-- Purpose: Preserve original API responses for forensic analysis
-- ============================================================================

-- Raw position snapshots (immutable)
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
);

CREATE INDEX idx_raw_position_batch ON raw_position_snapshots(sync_batch_id);
CREATE INDEX idx_raw_position_symbol_time ON raw_position_snapshots(symbol, created_at DESC);

-- Raw order snapshots (immutable)
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
);

CREATE INDEX idx_raw_order_batch ON raw_order_snapshots(sync_batch_id);
CREATE INDEX idx_raw_order_symbol_time ON raw_order_snapshots(symbol, created_at DESC);
CREATE INDEX idx_raw_order_order_id ON raw_order_snapshots(order_id);

-- Raw account snapshots (immutable)
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
);

CREATE INDEX idx_raw_account_batch ON raw_account_snapshots(sync_batch_id);
CREATE INDEX idx_raw_account_asset_time ON raw_account_snapshots(asset, created_at DESC);
```

### 3.2 Layer 2 – Event-Sourced Log

```sql
-- ============================================================================
-- LAYER 2: EVENT LOG
-- Purpose: Immutable event store for replay and audit
-- ============================================================================

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
);

CREATE INDEX idx_events_correlation ON events(correlation_id);
CREATE INDEX idx_events_type_time ON events(event_type, created_at);
CREATE INDEX idx_events_symbol ON events(symbol, created_at);
CREATE INDEX idx_events_parent ON events(parent_event_id);
CREATE INDEX idx_events_caused_by ON events(caused_by_event_id);

-- Bracket state transitions (for stop priority audit)
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
    FOREIGN KEY (position_uuid) REFERENCES positions(position_uuid)
);

CREATE INDEX idx_bracket_state_position ON bracket_state_log(position_uuid);
CREATE INDEX idx_bracket_state_time ON bracket_state_log(timestamp DESC);

-- Scale-in eligibility audit (for debugging scale-in decisions)
CREATE TABLE IF NOT EXISTS scale_in_conditions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    position_uuid TEXT NOT NULL,
    symbol TEXT NOT NULL,
    can_scale_in INTEGER NOT NULL CHECK(can_scale_in IN (0, 1)),
    path_used TEXT CHECK(path_used IN ('PRIMARY', 'SECONDARY', NULL)),
    adx_value REAL,
    funding_rate REAL,
    price_distance_pct REAL,
    margin_ratio REAL,
    pyramid_level INTEGER,
    reason TEXT NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s','now') * 1000),
    FOREIGN KEY (position_uuid) REFERENCES positions(position_uuid)
);

CREATE INDEX idx_scale_in_log_position ON scale_in_conditions_log(position_uuid, timestamp DESC);
CREATE INDEX idx_scale_in_log_symbol ON scale_in_conditions_log(symbol, timestamp DESC);

-- Dead-letter queue for failed projections
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
);

CREATE INDEX idx_event_errors_status ON event_processing_errors(status, retry_count);
CREATE INDEX idx_event_errors_event ON event_processing_errors(event_id);
```

### 3.3 Layer 3 – Operational State

```sql
-- ============================================================================
-- LAYER 3: OPERATIONAL STATE
-- Purpose: Canonical runtime state for trading decisions
-- ============================================================================

-- Main positions table (revised schema)
CREATE TABLE IF NOT EXISTS positions (
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
);

CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_cycle ON positions(cycle_id);
CREATE INDEX idx_positions_correlation ON positions(correlation_id);
CREATE INDEX idx_positions_scale_pending ON positions(scale_in_pending) WHERE scale_in_pending = 1;

-- Position entries (every time we add to a position)
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
    FOREIGN KEY (position_uuid) REFERENCES positions(position_uuid)
);

CREATE INDEX idx_position_entries_position ON position_entries(position_uuid, timestamp DESC);

-- Position closes (every time we reduce/close a position)
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
    FOREIGN KEY (position_uuid) REFERENCES positions(position_uuid)
);

CREATE INDEX idx_position_closes_position ON position_closes(position_uuid, timestamp DESC);

-- Bracket orders (current SL/TP/trailing instructions)
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
    FOREIGN KEY (position_uuid) REFERENCES positions(position_uuid)
);

CREATE INDEX idx_bracket_orders_position ON bracket_orders(position_uuid);
CREATE INDEX idx_bracket_orders_type ON bracket_orders(bracket_type, status);
CREATE INDEX idx_bracket_orders_status ON bracket_orders(status);
```

### 3.4 Layer 4 – Risk & Observability

```sql
-- ============================================================================
-- LAYER 4: RISK & OBSERVABILITY
-- Purpose: System health monitoring and risk tracking
-- ============================================================================

-- Risk state snapshots (one row per heartbeat)
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
);

-- Risk events (discrete breach events)
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
);

CREATE INDEX idx_risk_events_type_time ON risk_events(event_type, triggered_at DESC);
CREATE INDEX idx_risk_events_symbol ON risk_events(symbol, triggered_at DESC);

-- System components health tracking
CREATE TABLE IF NOT EXISTS system_components (
    name TEXT PRIMARY KEY,
    status TEXT CHECK(status IN ('HEALTHY', 'DEGRADED', 'FAILED', 'UNKNOWN')) DEFAULT 'UNKNOWN',
    last_action TEXT,
    last_message TEXT,
    error_count INTEGER DEFAULT 0,
    last_error TEXT,
    metadata_json TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now') * 1000)
);

-- Sync batch metrics (for SLA monitoring)
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
);

CREATE INDEX idx_sync_metrics_time ON sync_metrics(started_at DESC);
CREATE INDEX idx_sync_metrics_status ON sync_metrics(status);
```

### 3.5 Dashboard Views

```sql
-- ============================================================================
-- DASHBOARD VIEWS (SQLite-compatible)
-- ============================================================================

-- Active positions view
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
FROM positions
WHERE status = 'OPEN' AND quantity > 0;

-- Position PnL summary
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
FROM positions p
LEFT JOIN position_closes pc ON p.position_uuid = pc.position_uuid
GROUP BY p.position_uuid;

-- Risk dashboard view
CREATE VIEW IF NOT EXISTS v_risk_dashboard AS
SELECT 
    (SELECT account_equity FROM risk_states WHERE id = 1) as equity,
    (SELECT daily_pnl FROM risk_states WHERE id = 1) as daily_pnl,
    (SELECT drawdown_pct FROM risk_states WHERE id = 1) as drawdown_pct,
    (SELECT can_trade FROM risk_states WHERE id = 1) as can_trade,
    COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_positions,
    SUM(CASE WHEN status = 'OPEN' THEN notional ELSE 0 END) as total_exposure,
    (SELECT COUNT(*) FROM risk_events WHERE triggered_at > strftime('%s','now')*1000 - 86400000) as events_24h;

-- Sync health view
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
LIMIT 100;
```

---

## 4. Exchange Data Sources & Payloads

### 4.1 Endpoint Matrix

| API Function | Endpoint | Payload Focus | Consumers | Frequency |
|--------------|----------|---------------|-----------|-----------|
| `get_positions_v3()` | `GET /fapi/v2/positionRisk` | Entry price, qty, liquidation, margin data | Sync engine, risk service | Every heartbeat |
| `get_balance_v3()` | `GET /fapi/v1/balance` | Wallet/cross balances, maintenance margin | Equity calc, drawdown guard | Every heartbeat |
| `get_open_orders()` | `GET /fapi/v1/openOrders` | Active bracket + manual orders | Bracket manager, healing loop | Every heartbeat |
| `get_all_orders_v3()` | `GET /fapi/v1/allOrders` | Historical orders for audit/recovery | Recovery manager | Daily/weekly |
| `get_account_trades()` | `GET /fapi/v1/accountTrades` | Executions with price/qty | Fill attribution | Every heartbeat |
| `place_order()` | `POST /fapi/v1/order` | Order submission | Execution adapters | On-demand |
| `cancel_order()` | `DELETE /fapi/v1/order` | Cancel primitives | Bracket manager | On-demand |
| `get_funding_rate()` | `GET /fapi/v1/fundingRate` | Current funding rate | Scale-in eligibility | Every heartbeat |

### 4.2 Field Mappings

```python
# src/sync/field_mappings.py

POSITION_FIELDS = {
    "symbol": "symbol",
    "positionAmt": "position_amt", 
    "entryPrice": "entry_price",
    "markPrice": "mark_price",
    "leverage": "leverage",
    "marginType": "margin_type",
    "liquidationPrice": "liquidation_price",
    "unRealizedProfit": "unrealized_pnl",
    "updateTime": "update_time",
    "positionSide": "position_side"
}

BALANCE_FIELDS = {
    "asset": "asset",
    "walletBalance": "wallet_balance",
    "availableBalance": "available_balance", 
    "crossWalletBalance": "cross_wallet_balance",
    "crossInitialMargin": "cross_initial_margin",
    "maintenanceMargin": "maintenance_margin"
}

FUNDING_FIELDS = {
    "symbol": "symbol",
    "fundingRate": "funding_rate",
    "fundingTime": "funding_time",
    "nextFundingTime": "next_funding_time"
}
```

---

## 5. Layered Data Model

### 5.1 Architecture Diagram

```
EXTERNAL APIS (Binance/Aster Futures REST + WebSocket)
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1: RAW SNAPSHOTS                                             │
│  sync_batches + raw_position_snapshots + raw_order_snapshots       │
│  raw_account_snapshots                                             │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼ (batch contains many snapshots)
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 2: EVENT LOG                                                 │
│  events + bracket_state_log + scale_in_conditions_log              │
│  event_processing_errors (dead-letter queue)                       │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼ (events cause more events)
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 3: OPERATIONAL STATE                                         │
│  positions + position_entries + position_closes + bracket_orders    │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 4: RISK & OBSERVABILITY                                      │
│  risk_states + risk_events + system_components + sync_metrics       │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DASHBOARD VIEWS                                                    │
│  v_active_positions + v_position_pnl + v_risk_dashboard + v_sync_health│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Sync & Projection Orchestration

### 6.1 SyncEngine Class

```python
# src/sync/sync_engine.py

import uuid
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from state.db import get_connection
from state.repositories import (
    upsert_position, get_positions, upsert_order, get_orders,
    upsert_risk_state, get_risk_state
)
from sync.normalizers import (
    normalize_position_response, 
    normalize_order_response,
    normalize_balance_response
)
from sync.change_detector import ChangeDetector
from sync.event_emitter import EventEmitter
from sync.projectors.position_projector import PositionProjector
from sync.projectors.order_projector import OrderProjector
from sync.projectors.bracket_projector import BracketProjector

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
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
    """
    
    def __init__(self):
        self.change_detector = ChangeDetector()
        self.event_emitter = EventEmitter()
        self.position_projector = PositionProjector()
        self.order_projector = OrderProjector()
        self.bracket_projector = BracketProjector()
        self._previous_positions: Dict[str, dict] = {}
        self._previous_orders: Dict[str, dict] = {}
        self._load_cached_state()
    
    def _load_cached_state(self):
        try:
            positions = get_positions()
            # FIXED: Use position_uuid as key instead of symbol to handle multiple positions per symbol
            self._previous_positions = {p.position_uuid: p.__dict__ for p in positions if p.position_uuid}
            orders = get_orders(active_only=True)
            self._previous_orders = {o.order_id: o.__dict__ for o in orders}
        except Exception as e:
            logger.warning(f"Could not load cached state: {e}")
    
    def sync(self) -> SyncResult:
        batch_id = str(uuid.uuid4())
        started_at = int(time.time() * 1000)
        
        result = SyncResult(
            batch_id=batch_id,
            status='FAILED',
            started_at=started_at
        )
        
        try:
            self._start_batch(batch_id, started_at)
            positions_data, orders_data, balances_data = self._fetch_all_data(batch_id)
            result.positions_fetched = len(positions_data)
            result.orders_fetched = len(orders_data)
            
            events = self._detect_changes_and_emit_events(positions_data, orders_data, balances_data)
            result.events_emitted = len(events)
            
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
            self._fail_batch(batch_id, str(e))
        
        result.completed_at = int(time.time() * 1000)
        return result
    
    def _start_batch(self, batch_id: str, started_at: int):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sync_batches (batch_id, started_at, status) VALUES (?, ?, 'IN_PROGRESS')",
                (batch_id, started_at)
            )
            conn.commit()
        logger.debug(f"Started sync batch {batch_id}")
    
    def _fetch_all_data(self, batch_id: str):
        from api.aster_api import get_positions_v3, get_open_orders, get_balance_v3
        
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
    
    def _persist_raw_positions(self, batch_id: str, positions: List[dict]):
        with get_connection() as conn:
            cur = conn.cursor()
            for pos in positions:
                cur.execute(
                    """INSERT INTO raw_position_snapshots 
                       (sync_batch_id, symbol, position_amt, entry_price, mark_price,
                        unrealized_pnl, leverage, liquidation_price, update_time, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, pos["symbol"], pos["position_amt"], pos["entry_price"],
                     pos["mark_price"], pos["unrealized_pnl"], pos["leverage"],
                     pos["liquidation_price"], pos["update_time"], pos["raw_json"])
                )
            conn.commit()
    
    def _persist_raw_orders(self, batch_id: str, orders: List[dict]):
        with get_connection() as conn:
            cur = conn.cursor()
            for order in orders:
                cur.execute(
                    """INSERT INTO raw_order_snapshots 
                       (sync_batch_id, order_id, client_order_id, symbol, side, type,
                        status, price, quantity, executed_qty, avg_price, reduce_only,
                        time_in_force, stop_price, update_time, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, order["order_id"], order.get("client_order_id"),
                     order["symbol"], order["side"], order["type"], order["status"],
                     order["price"], order["quantity"], order["executed_qty"],
                     order["avg_price"], order["reduce_only"], order.get("time_in_force"),
                     order.get("stop_price"), order["update_time"], order["raw_json"])
                )
            conn.commit()
    
    def _persist_raw_accounts(self, batch_id: str, balances: List[dict]):
        with get_connection() as conn:
            cur = conn.cursor()
            for bal in balances:
                cur.execute(
                    """INSERT INTO raw_account_snapshots 
                       (sync_batch_id, asset, wallet_balance, available_balance,
                        cross_wallet_balance, cross_initial_margin, maintenance_margin, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, bal["asset"], bal["wallet_balance"], bal["available_balance"],
                     bal["cross_wallet_balance"], bal["cross_initial_margin"], 
                     bal["maintenance_margin"], bal["raw_json"])
                )
            conn.commit()
    
    def _detect_changes_and_emit_events(self, positions, orders, balances) -> List[dict]:
        events = []
        position_events = self.change_detector.detect_position_changes(
            current=positions, previous=self._previous_positions, correlation_id=str(uuid.uuid4())
        )
        events.extend(position_events)
        
        order_events = self.change_detector.detect_order_changes(
            current=orders, previous=self._previous_orders, correlation_id=str(uuid.uuid4())
        )
        events.extend(order_events)
        
        for event in events:
            self.event_emitter.emit(event)
        
        return events
    
    def _project_events(self, events: List[dict]) -> tuple:
        successes = 0
        failures = 0
        
        for event in events:
            try:
                # FIXED: Use position_uuid instead of symbol to correctly identify position
                # This ensures we check the correct position when multiple positions exist for same symbol
                if event.get("position_uuid"):
                    if self._is_scale_in_pending(event["position_uuid"]):
                        logger.warning(f"Skipping projection for position {event['position_uuid']}: scale_in_pending=1")
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
            cur.execute("SELECT scale_in_pending FROM positions WHERE position_uuid = ?", (position_uuid,))
            row = cur.fetchone()
            return row and row[0] == 1
    
    def _record_projection_error(self, event: dict, error: str):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO event_processing_errors 
                   (event_id, event_type, error_message, first_attempt_at)
                   VALUES (?, ?, ?, ?)""",
                (event.get("id"), event["event_type"], error, int(time.time() * 1000))
            )
            conn.commit()
    
    def _update_risk_state(self, balances: List[dict]):
        usdt_balance = next((b["available_balance"] for b in balances if b["asset"] == "USDT"), 0)
        daily_pnl = 0.0
        
        risk_state = {
            "account_equity": usdt_balance,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": (daily_pnl / usdt_balance * 100) if usdt_balance > 0 else 0,
            "updated_at": int(time.time() * 1000)
        }
        
        upsert_risk_state(risk_state)
    
    def _complete_batch(self, batch_id: str, result: SyncResult):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE sync_batches 
                   SET status = ?, completed_at = ?, positions_count = ?, orders_count = ?
                   WHERE batch_id = ?""",
                (result.status, result.completed_at, result.positions_fetched,
                 result.orders_fetched, batch_id)
            )
            
            cur.execute(
                """INSERT INTO sync_metrics 
                   (batch_id, started_at, completed_at, duration_ms, positions_fetched,
                    orders_fetched, events_emitted, projections_succeeded, 
                    projections_failed, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, result.started_at, result.completed_at,
                 result.completed_at - result.started_at,
                 result.positions_fetched, result.orders_fetched,
                 result.events_emitted, result.projections_succeeded,
                 result.projections_failed, result.status)
            )
            conn.commit()
        
        logger.info(f"Batch {batch_id} completed: {result.status}")
    
    def _fail_batch(self, batch_id: str, error: str):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE sync_batches SET status = 'FAILED', error_msg = ?, completed_at = ? WHERE batch_id = ?",
                (error, int(time.time() * 1000), batch_id)
            )
            conn.commit()
```

### 6.2 Change Detector

```python
# src/sync/change_detector.py

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ChangeDetector:
    """Detects changes between current and previous state to emit events."""
    
    def detect_position_changes(self, current: List[dict], previous: Dict[str, dict], correlation_id: str) -> List[dict]:
        """Detect position changes and emit events.
        
        Events now include position_uuid to enable correct identification of positions
        when multiple positions exist for the same symbol.
        """
        events = []
        # FIXED: Use position_uuid as key instead of symbol to handle multiple positions per symbol
        current_by_uuid = {p["position_uuid"]: p for p in current if "position_uuid" in p}
        previous_by_uuid = {p.get("position_uuid", p.get("symbol", "")): p for p in previous.values()}
        
        for position_uuid, pos in current_by_uuid.items():
            if position_uuid not in previous_by_uuid:
                events.append({
                    "event_type": "POSITION_OPENED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "position_uuid": position_uuid,  # FIXED: Include position_uuid
                    "symbol": pos.get("symbol", ""),
                    "payload_json": self._encode_payload(pos),
                    "position_amt": pos.get("position_amt", 0),
                    "entry_price": pos.get("entry_price", 0),
                    "side": pos.get("side", "")
                })
            else:
                prev = previous_by_uuid[position_uuid]
                if abs(pos.get("position_amt", 0) - prev.get("position_amt", 0)) > 0.0001:
                    if pos.get("position_amt", 0) > prev.get("position_amt", 0):
                        events.append({
                            "event_type": "POSITION_SCALED_IN",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "position_uuid": position_uuid,  # FIXED: Include position_uuid
                            "symbol": pos.get("symbol", ""),
                            "payload_json": self._encode_payload({
                                "old_qty": prev.get("position_amt", 0),
                                "new_qty": pos.get("position_amt", 0),
                                "entry_price": pos.get("entry_price", 0),
                                "side": pos.get("side", "")
                            })
                        })
                    else:
                        events.append({
                            "event_type": "POSITION_SCALED_OUT",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "position_uuid": position_uuid,  # FIXED: Include position_uuid
                            "symbol": pos.get("symbol", ""),
                            "payload_json": self._encode_payload({
                                "old_qty": prev.get("position_amt", 0),
                                "new_qty": pos.get("position_amt", 0)
                            })
                        })
        
        for position_uuid, prev in previous_by_uuid.items():
            if position_uuid not in current_by_uuid and position_uuid in previous_by_uuid:
                events.append({
                    "event_type": "POSITION_CLOSED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "position_uuid": position_uuid,  # FIXED: Include position_uuid
                    "symbol": prev.get("symbol", ""),
                    "payload_json": self._encode_payload({
                        "old_qty": prev.get("position_amt", 0),
                        "close_price": prev.get("mark_price", 0)
                    })
                })
        
        return events
    
    def detect_order_changes(self, current: List[dict], previous: Dict[str, dict], correlation_id: str) -> List[dict]:
        events = []
        current_by_id = {o["order_id"]: o for o in current if o.get("order_id")}
        previous_by_id = previous.copy()
        
        for order_id, order in current_by_id.items():
            if order_id not in previous_by_id:
                events.append({
                    "event_type": "ORDER_PLACED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "symbol": order.get("symbol"),
                    "payload_json": self._encode_payload(order)
                })
            else:
                prev = previous_by_id[order_id]
                old_filled = float(prev.get("executed_qty", 0))
                new_filled = float(order.get("executed_qty", 0))
                
                if new_filled > old_filled:
                    events.append({
                        "event_type": "ORDER_FILLED",
                        "event_source": "EXCHANGE",
                        "correlation_id": correlation_id,
                        "symbol": order.get("symbol"),
                        "payload_json": self._encode_payload({
                            "order_id": order_id,
                            "old_filled": old_filled,
                            "new_filled": new_filled,
                            "avg_price": order.get("avg_price")
                        })
                    })
        
        return events
    
    def _encode_payload(self, data: dict) -> str:
        import json
        return json.dumps(data, default=str)
```

---

## 7. Stop Management & Scale-In Control Plane

### 7.1 Stop Priority Stack (Full Implementation)

```python
# src/risk/stop_priority.py

from enum import IntEnum
from typing import Optional

class StopPriority(IntEnum):
    """Stop priority order (lower number = higher priority)"""
    HARD_STOP = 1          # Daily loss ≥ 5% equity
    INITIAL_SL = 2         # Price-based guard from entry
    BREAK_EVEN_MOVE = 3    # Move SL to entry after TP1
    TRAILING_STOP = 4      # Trailing after TP2 or profit threshold
    TIME_STOP = 5          # Close stale positions


class StopPriorityManager:
    """
    Manages stop priority enforcement to prevent conflicting triggers.
    """
    
    def __init__(self):
        self._active_stops: dict = {}  # position_uuid -> {stop_type: state}
    
    def register_stop(self, position_uuid: str, stop_type: StopPriority, state: str):
        """Register a stop activation."""
        if position_uuid not in self._active_stops:
            self._active_stops[position_uuid] = {}
        
        self._active_stops[position_uuid][stop_type] = state
        self._log_state_transition(position_uuid, stop_type, state)
    
    def can_activate(self, position_uuid: str, requested_stop: StopPriority) -> bool:
        """Check if requested stop can be activated based on priority."""
        if position_uuid not in self._active_stops:
            return True
        
        active = self._active_stops[position_uuid]
        
        # Check if any higher-priority stop is active
        for stop_type, state in active.items():
            if stop_type < requested_stop and state == "ACTIVE":
                return False
        
        return True
    
    def get_active_stop(self, position_uuid: str) -> Optional[StopPriority]:
        """Get the highest-priority active stop."""
        if position_uuid not in self._active_stops:
            return None
        
        active = self._active_stops[position_uuid]
        active_stops = [s for s, state in active.items() if state == "ACTIVE"]
        
        if not active_stops:
            return None
        
        return min(active_stops)
    
    def _log_state_transition(self, position_uuid: str, stop_type: StopPriority, new_state: str):
        """Log bracket state transition for audit."""
        import time
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            old_state = "PENDING"
            
            # Get old state if exists
            if position_uuid in self._active_stops:
                old_state = self._active_stops[position_uuid].get(stop_type, "PENDING")
            
            cur.execute(
                """INSERT INTO bracket_state_log 
                   (bracket_uuid, position_uuid, bracket_type, old_state, new_state, trigger_reason, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (f"{position_uuid}_{stop_type.name}", position_uuid, stop_type.name,
                 old_state, new_state, "PRICE_HIT", int(time.time() * 1000))
            )
            conn.commit()
```

### 7.2 Scale-In Eligibility (Complete Implementation)

```python
# src/scale_in/scale_in_eligibility.py

import time
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScaleInEligibility:
    can_scale_in: bool
    path_used: Optional[str]  # PRIMARY or SECONDARY
    adx_value: float
    funding_rate: float
    price_distance_pct: float
    margin_ratio: float
    pyramid_level: int
    reason: str


class ScaleInEligibilityChecker:
    """
    Determines if a position is eligible for scale-in based on V2 criteria.
    """
    
    # Configuration (should come from config file)
    ADX_THRESHOLD_PRIMARY = 25
    ADX_THRESHOLD_SECONDARY = 30
    MIN_PRICE_DISTANCE_PCT = 1.0
    MARGIN_RATIO_THRESHOLD = 0.70  # 70% of liquidation
    PYRAMID_MAX_LEVELS = 3
    COOLDOWN_MINUTES = 15
    
    # Diminishing scale sizes
    SCALE_SIZES = [0.03, 0.015, 0.0075]  # 3% → 1.5% → 0.75%
    
    def check_eligibility(
        self,
        position: dict,
        current_price: float,
        adx_value: float,
        funding_rate: float,
        margin_ratio: float
    ) -> ScaleInEligibility:
        """
        Check if position is eligible for scale-in.
        
        Args:
            position: Current position data from DB
            current_price: Current market price
            adx_value: ADX momentum indicator
            funding_rate: Current funding rate
            margin_ratio: Current margin ratio (0.0-1.0)
        
        Returns:
            ScaleInEligibility with decision details
        """
        symbol = position["symbol"]
        pyramid_level = position.get("pyramid_scales", 0)
        last_scale_price = position.get("last_scale_price")
        
        # Check pyramid level limit
        if pyramid_level >= self.PYRAMID_MAX_LEVELS:
            return ScaleInEligibility(
                can_scale_in=False,
                path_used=None,
                adx_value=adx_value,
                funding_rate=funding_rate,
                price_distance_pct=0.0,
                margin_ratio=margin_ratio,
                pyramid_level=pyramid_level,
                reason=f"Max pyramid level {self.PYRAMID_MAX_LEVELS} reached"
            )
        
        # Check margin ratio
        if margin_ratio >= self.MARGIN_RATIO_THRESHOLD:
            return ScaleInEligibility(
                can_scale_in=False,
                path_used=None,
                adx_value=adx_value,
                funding_rate=funding_rate,
                price_distance_pct=0.0,
                margin_ratio=margin_ratio,
                pyramid_level=pyramid_level,
                reason=f"Margin ratio {margin_ratio:.1%} >= {self.MARGIN_RATIO_THRESHOLD:.1%}"
            )
        
        # Check price distance from last scale
        if last_scale_price:
            price_distance_pct = abs(current_price - last_scale_price) / last_scale_price * 100
            if price_distance_pct < self.MIN_PRICE_DISTANCE_PCT:
                return ScaleInEligibility(
                    can_scale_in=False,
                    path_used=None,
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"Price distance {price_distance_pct:.2f}% < {self.MIN_PRICE_DISTANCE_PCT}%"
                )
        else:
            price_distance_pct = 100.0  # First scale-in
        
        # Check cooldown
        last_scale_time = position.get("last_scale_time")
        if last_scale_time:
            minutes_since_last = (time.time() * 1000 - last_scale_time) / 60000
            if minutes_since_last < self.COOLDOWN_MINUTES:
                return ScaleInEligibility(
                    can_scale_in=False,
                    path_used=None,
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"Cooldown {minutes_since_last:.0f}m < {self.COOLDOWN_MINUTES}m"
                )
        
        # Determine path
        tp2_hit = position.get("tp2_hit", False)
        tp1_hit = position.get("tp1_hit", False)
        sl_at_break_even = position.get("sl_at_break_even", False)
        
        # PRIMARY path: TP2 hit + ADX >= 25
        if tp2_hit and adx_value >= self.ADX_THRESHOLD_PRIMARY:
            # Also check funding is not negative
            if funding_rate >= 0:
                return ScaleInEligibility(
                    can_scale_in=True,
                    path_used="PRIMARY",
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"PRIMARY: TP2 hit + ADX {adx_value} >= {self.ADX_THRESHOLD_PRIMARY}"
                )
        
        # SECONDARY path: TP1 hit + SL at break-even + ADX >= 30 + funding favorable
        if tp1_hit and sl_at_break_even and adx_value >= self.ADX_THRESHOLD_SECONDARY:
            if funding_rate >= 0:
                return ScaleInEligibility(
                    can_scale_in=True,
                    path_used="SECONDARY",
                    adx_value=adx_value,
                    funding_rate=funding_rate,
                    price_distance_pct=price_distance_pct,
                    margin_ratio=margin_ratio,
                    pyramid_level=pyramid_level,
                    reason=f"SECONDARY: TP1 hit + BE + ADX {adx_value} >= {self.ADX_THRESHOLD_SECONDARY}"
                )
        
        return ScaleInEligibility(
            can_scale_in=False,
            path_used=None,
            adx_value=adx_value,
            funding_rate=funding_rate,
            price_distance_pct=price_distance_pct,
            margin_ratio=margin_ratio,
            pyramid_level=pyramid_level,
            reason="No path met: TP2+ADX25 or TP1+BE+ADX30 required"
        )
    
    def log_eligibility_check(self, position_uuid: str, eligibility: ScaleInEligibility):
        """Log eligibility check to scale_in_conditions_log."""
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO scale_in_conditions_log 
                   (timestamp, position_uuid, symbol, can_scale_in, path_used,
                    adx_value, funding_rate, price_distance_pct, margin_ratio,
                    pyramid_level, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time() * 1000), position_uuid, eligibility.symbol if hasattr(eligibility, 'symbol') else 'UNKNOWN',
                 1 if eligibility.can_scale_in else 0, eligibility.path_used,
                 eligibility.adx_value, eligibility.funding_rate,
                 eligibility.price_distance_pct, eligibility.margin_ratio,
                 eligibility.pyramid_level, eligibility.reason)
            )
            conn.commit()
    
    def get_scale_size(self, pyramid_level: int) -> float:
        """Get scale size for given pyramid level."""
        if pyramid_level < len(self.SCALE_SIZES):
            return self.SCALE_SIZES[pyramid_level]
        return self.SCALE_SIZES[-1]  # Return minimum
```

---

## 8. Conflict Resolution Algorithms

### 8.1 WebSocket vs Batch Reconciliation

```python
# src/sync/conflict_resolver.py

import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class ConflictResolver:
    """
    Resolves conflicts between real-time WebSocket events and batch sync data.
    """
    
    def resolve_position_conflict(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> Tuple[dict, str]:
        """
        Resolve position conflict between WebSocket and batch data.
        
        Returns:
            (resolved_position, resolution_source)
        """
        if not websocket_position:
            return batch_position, "BATCH_ONLY"
        
        if not batch_position:
            return websocket_position, "WEBSOCKET_ONLY"
        
        # WebSocket is more recent - trust it for fills
        ws_time = websocket_position.get("update_time", 0)
        batch_time = batch_position.get("update_time", 0)
        
        if ws_time > batch_time:
            # Check if quantity changed (likely a fill)
            ws_qty = websocket_position.get("position_amt", 0)
            batch_qty = batch_position.get("position_amt", 0)
            
            if abs(ws_qty - batch_qty) > 0.0001:
                logger.info(
                    f"Position conflict resolved: WS qty={ws_qty} vs batch qty={batch_qty}. "
                    f"Trusting WebSocket (more recent)"
                )
                return websocket_position, "WEBSOCKET_QTY_CHANGE"
            
            # Quantity same, trust batch (has more context)
            return batch_position, "BATCH_QTY_MATCH"
        
        # Batch is more recent
        return batch_position, "BATCH_MORE_RECENT"
    
    def resolve_order_conflict(
        self,
        websocket_order: dict,
        batch_order: dict
    ) -> Tuple[dict, str]:
        """Resolve order conflict."""
        if not websocket_order:
            return batch_order, "BATCH_ONLY"
        
        if not batch_order:
            return websocket_order, "WEBSOCKET_ONLY"
        
        # For orders, always trust most recent status
        ws_status = websocket_order.get("status", "")
        batch_status = batch_order.get("status", "")
        
        status_priority = {
            "FILLED": 4,
            "PARTIALLY_FILLED": 3,
            "CANCELLED": 2,
            "NEW": 1,
            "PENDING": 0
        }
        
        ws_priority = status_priority.get(ws_status, 0)
        batch_priority = status_priority.get(batch_status, 0)
        
        if ws_priority > batch_priority:
            logger.info(f"Order conflict: WS status {ws_status} > batch {batch_status}")
            return websocket_order, "WEBSOCKET_STATUS"
        
        return batch_order, "BATCH_STATUS"
    
    def detect_anomaly(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> Optional[str]:
        """Detect anomalies that require manual review."""
        if not websocket_position or not batch_position:
            return None
        
        ws_qty = abs(websocket_position.get("position_amt", 0))
        batch_qty = abs(batch_position.get("position_amt", 0))
        
        # Large quantity discrepancy (>10%)
        if ws_qty > 0 and batch_qty > 0:
            pct_diff = abs(ws_qty - batch_qty) / max(ws_qty, batch_qty)
            if pct_diff > 0.10:
                return f"LARGE_QTY_DIFF: {pct_diff:.1%} difference"
        
        # Opposite sides
        ws_side = "LONG" if websocket_position.get("position_amt", 0) > 0 else "SHORT"
        batch_side = "LONG" if batch_position.get("position_amt", 0) > 0 else "SHORT"
        
        if ws_side != batch_side:
            return f"SIDE_MISMATCH: WS={ws_side} vs batch={batch_side}"
        
        return None
```

---

## 9. Performance & Scaling

### 9.1 Retention Policies

```sql
-- ============================================================================
-- RETENTION POLICIES
-- ============================================================================

-- Raw snapshots: Keep 7 days
DELETE FROM raw_position_snapshots 
WHERE created_at < strftime('%s','now') * 1000 - 7 * 86400000;

-- Events: Keep 30 days
DELETE FROM events 
WHERE created_at < strftime('%s','now') * 1000 - 30 * 86400000;

-- Scale-in logs: Keep 90 days
DELETE FROM scale_in_conditions_log 
WHERE created_at < strftime('%s','now') * 1000 - 90 * 86400000;

-- Sync batches: Keep 90 days
DELETE FROM sync_batches 
WHERE created_at < strftime('%s','now') * 1000 - 90 * 86400000;

-- Optimize database weekly
PRAGMA optimize;
```

### 9.2 Index Maintenance

```sql
-- Analyze tables for query optimization
ANALYZE raw_position_snapshots;
ANALYZE raw_order_snapshots;
ANALYZE events;
ANALYZE positions;

-- Rebuild indexes if fragmentation > 20%
-- (SQLite automatically handles this with PRAGMA optimize)
```

---

## 10. Funding Rate Integration

### 10.1 Funding Rate Fetch & Storage

```python
# src/sync/funding_rate_fetcher.py

import logging
import time
from typing import List, Dict
from state.db import get_connection

logger = logging.getLogger(__name__)


class FundingRateFetcher:
    """Fetches and stores funding rates."""
    
    def fetch_and_store(self, symbols: List[str]):
        """Fetch funding rates for symbols and store in DB."""
        from api.aster_api import public_get
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            for symbol in symbols:
                try:
                    # Fetch funding rate
                    endpoint = "/fapi/v1/fundingRate"
                    params = {"symbol": symbol, "limit": 1}
                    data = public_get(endpoint, params)
                    
                    if data and len(data) > 0:
                        rate_data = data[0]
                        
                        cur.execute(
                            """INSERT OR REPLACE INTO funding_rates 
                               (symbol, funding_rate, funding_time, next_funding_time)
                               VALUES (?, ?, ?, ?)""",
                            (
                                symbol,
                                float(rate_data["fundingRate"]),
                                int(rate_data["fundingTime"]),
                                int(rate_data.get("nextFundingTime", 0))
                            )
                        )
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch funding rate for {symbol}: {e}")
            
            conn.commit()
    
    def get_latest_rate(self, symbol: str) -> float:
        """Get latest funding rate for symbol."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT funding_rate FROM funding_rates WHERE symbol = ? ORDER BY funding_time DESC LIMIT 1",
                (symbol,)
            )
            row = cur.fetchone()
            return row[0] if row else 0.0
```

---

## 11. Testing Strategy

### 11.1 Unit Test Example: Scale-In Eligibility

```python
# tests/unit/test_scale_in_eligibility.py

import pytest
from src.scale_in.scale_in_eligibility import ScaleInEligibilityChecker, ScaleInEligibility


class TestScaleInEligibility:
    
    @pytest.fixture
    def checker(self):
        return ScaleInEligibilityChecker()
    
    def test_primary_path_eligible(self, checker):
        """Test PRIMARY path when TP2 hit and ADX >= 25."""
        position = {
            "symbol": "ETHUSDT",
            "pyramid_scales": 0,
            "last_scale_price": None,
            "last_scale_time": None,
            "tp2_hit": True,
            "tp1_hit": False,
            "sl_at_break_even": False
        }
        
        result = checker.check_eligibility(
            position=position,
            current_price=2000.0,
            adx_value=30.0,
            funding_rate=0.0001,
            margin_ratio=0.3
        )
        
        assert result.can_scale_in is True
        assert result.path_used == "PRIMARY"
    
    def test_secondary_path_eligible(self, checker):
        """Test SECONDARY path when TP1 hit + BE + ADX >= 30."""
        position = {
            "symbol": "ETHUSDT",
            "pyramid_scales": 1,
            "last_scale_price": 1950.0,
            "last_scale_time": 0,  # Long ago
            "tp1_hit": True,
            "tp2_hit": False,
            "sl_at_break_even": True
        }
        
        result = checker.check_eligibility(
            position=position,
            current_price=2000.0,
            adx_value=35.0,
            funding_rate=0.0001,
            margin_ratio=0.4
        )
        
        assert result.can_scale_in is True
        assert result.path_used == "SECONDARY"
    
    def test_margin_ratio_too_high(self, checker):
        """Test rejection when margin ratio >= 70%."""
        position = {
            "symbol": "ETHUSDT",
            "pyramid_scales": 0,
            "last_scale_price": None,
            "last_scale_time": None,
            "tp2_hit": True
        }
        
        result = checker.check_eligibility(
            position=position,
            current_price=2000.0,
            adx_value=30.0,
            funding_rate=0.0001,
            margin_ratio=0.75  # Above threshold
        )
        
        assert result.can_scale_in is False
        assert "margin ratio" in result.reason.lower()
    
    def test_max_pyramid_levels(self, checker):
        """Test rejection when max pyramid levels reached."""
        position = {
            "symbol": "ETHUSDT",
            "pyramid_scales": 3,  # Max reached
            "last_scale_price": 1950.0,
            "last_scale_time": 0,
            "tp2_hit": True
        }
        
        result = checker.check_eligibility(
            position=position,
            current_price=2000.0,
            adx_value=30.0,
            funding_rate=0.0001,
            margin_ratio=0.3
        )
        
        assert result.can_scale_in is False
        assert "pyramid" in result.reason.lower()
```

### 11.2 Integration Test Example: Sync Cycle

```python
# tests/integration/test_sync_cycle.py

import pytest
import time
from src.sync.sync_engine import SyncEngine


class TestSyncCycle:
    
    @pytest.fixture
    def sync_engine(self):
        return SyncEngine()
    
    def test_sync_creates_batch(self, sync_engine):
        """Test that sync creates a batch record."""
        # This would require mock API responses
        # Simplified version
        result = sync_engine.sync()
        
        assert result.batch_id is not None
        assert result.started_at > 0
        assert result.completed_at >= result.started_at
    
    def test_sync_batch_completed(self, sync_engine):
        """Test batch status is COMPLETED or PARTIAL."""
        result = sync_engine.sync()
        
        assert result.status in ['COMPLETED', 'PARTIAL', 'FAILED']
```

---

## 12. Error Recovery Procedures

### 12.1 Dead-Letter Queue Retry Worker

```python
# src/sync/retry_worker.py

import time
import logging
from typing import List
from state.db import get_connection

logger = logging.getLogger(__name__)


class RetryWorker:
    """
    Processes failed event projections from dead-letter queue.
    """
    
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 60
    
    def process_pending(self) -> int:
        """Process all pending failed events."""
        processed = 0
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Get pending errors
            cur.execute(
                """SELECT id, event_id, event_type, position_uuid, error_message
                   FROM event_processing_errors
                   WHERE status = 'PENDING' AND retry_count < ?
                   ORDER BY first_attempt_at ASC""",
                (self.MAX_RETRIES,)
            )
            
            errors = cur.fetchall()
            
            for error in errors:
                error_id, event_id, event_type, position_uuid, error_msg = error
                
                try:
                    # Re-fetch event
                    cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
                    event_row = cur.fetchone()
                    
                    if not event_row:
                        logger.error(f"Event {event_id} not found, marking as FAILED")
                        self._mark_resolved(error_id, "EVENT_MISSING")
                        continue
                    
                    # Re-project
                    self._reproject(dict(event_row))
                    
                    # Mark resolved
                    self._mark_resolved(error_id, "RESOLVED")
                    processed += 1
                    
                except Exception as e:
                    logger.exception(f"Retry failed for error {error_id}: {e}")
                    self._increment_retry(error_id)
        
        return processed
    
    def _reproject(self, event: dict):
        """Re-project an event."""
        # Import projectors
        from sync.projectors.position_projector import PositionProjector
        from sync.projectors.order_projector import OrderProjector
        
        projector = None
        
        if event["event_type"].startswith("POSITION"):
            projector = PositionProjector()
        elif event["event_type"].startswith("ORDER"):
            projector = OrderProjector()
        
        if projector:
            projector.project(event)
    
    def _mark_resolved(self, error_id: int, resolution: str):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE event_processing_errors 
                   SET status = 'RESOLVED', resolved_at = ?
                   WHERE id = ?""",
                (int(time.time() * 1000), error_id)
            )
            conn.commit()
    
    def _increment_retry(self, error_id: int):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE event_processing_errors 
                   SET retry_count = retry_count + 1, 
                       last_retry_at = ?,
                       status = CASE 
                           WHEN retry_count + 1 >= ? THEN 'FAILED' 
                           ELSE 'PENDING' 
                       END
                   WHERE id = ?""",
                (int(time.time() * 1000), self.MAX_RETRIES, error_id)
            )
            conn.commit()
```

### 12.2 Manual Recovery Commands

```sql
-- ============================================================================
-- MANUAL RECOVERY COMMANDS
-- ============================================================================

-- 1. Force complete a stuck IN_PROGRESS batch
UPDATE sync_batches 
SET status = 'COMPLETED', completed_at = strftime('%s','now') * 1000
WHERE status = 'IN_PROGRESS' 
  AND started_at < strftime('%s','now') * 1000 - 300000;  -- 5 minutes ago

-- 2. Clear scale_in_pending flag (if stuck)
UPDATE positions SET scale_in_pending = 0 WHERE scale_in_pending = 1;

-- 3. Reset version conflicts
UPDATE positions SET version = 1 WHERE version = 0;

-- 4. Rebuild position from raw snapshots
-- (Requires application logic, see recovery_manager.py)

-- 5. Clear all event processing errors
DELETE FROM event_processing_errors;
```

---

## 13. Implementation Checklist

### Phase 0: Foundation (Weeks 1-2)

#### Database Migration
- [ ] Backup existing database
- [ ] Execute Migration SQL 0.1 (add columns to positions)
- [ ] Execute Migration SQL 0.2 (create sync_batches table)
- [ ] Execute Migration SQL 0.3 (create funding_rates table)
- [ ] Verify all indexes created successfully
- [ ] Test rollback procedure in staging environment

#### Code Changes
- [ ] Create [`src/state/migration_helpers.py`](workspace/skills/aster-trading/src/state/migration_helpers.py) with dual-write functions
- [ ] Update [`src/trade_state.py`](workspace/skills/aster-trading/src/trade_state.py) to read/write scale_in_pending from DB
- [ ] Update [`src/scale_in_position.py`](workspace/skills/aster-trading/src/scale_in_position.py) to use DB for scale_in_pending
- [ ] Add DB persistence to [`src/risk/manage_brackets.py`](workspace/skills/aster-trading/src/risk/manage_brackets.py)

#### Testing
- [ ] Test crash recovery mid-scale-in
- [ ] Test dual-write strategy (JSON + DB)
- [ ] Verify UUID generation for existing positions
- [ ] Test version field increments on updates

### Phase 1: Raw Layer + Event Foundation (Weeks 3-4)

#### Database Schema
- [ ] Create Layer 1 raw snapshot tables (raw_position_snapshots, raw_order_snapshots, raw_account_snapshots)
- [ ] Create Layer 2 events table with all event types
- [ ] Create bracket_state_log table
- [ ] Create scale_in_conditions_log table
- [ ] Create event_processing_errors table (dead-letter queue)
- [ ] Verify all foreign keys and constraints

#### Sync Engine
- [ ] Create [`src/sync/sync_engine.py`](workspace/skills/aster-trading/src/sync/sync_engine.py) class
- [ ] Implement batch lifecycle management
- [ ] Create [`src/sync/normalizers.py`](workspace/skills/aster-trading/src/sync/normalizers.py) for API response normalization
- [ ] Create [`src/sync/change_detector.py`](workspace/skills/aster-trading/src/sync/change_detector.py) for delta detection
- [ ] Create [`src/sync/event_emitter.py`](workspace/skills/aster-trading/src/sync/event_emitter.py) for event persistence
- [ ] Integrate SyncEngine into [`src/trading_system.py`](workspace/skills/aster-trading/src/trading_system.py) main loop

#### Testing
- [ ] Unit test change detection logic
- [ ] Unit test event emission
- [ ] Integration test: full sync cycle
- [ ] Integration test: raw snapshots preserve original JSON
- [ ] Test batch failure scenarios

### Phase 2: Projectors + State Transitions (Weeks 5-6)

#### Projector Implementation
- [ ] Create [`src/sync/projectors/position_projector.py`](workspace/skills/aster-trading/src/sync/projectors/position_projector.py)
- [ ] Create [`src/sync/projectors/order_projector.py`](workspace/skills/aster-trading/src/sync/projectors/order_projector.py)
- [ ] Create [`src/sync/projectors/bracket_projector.py`](workspace/skills/aster-trading/src/sync/projectors/bracket_projector.py)
- [ ] Implement optimistic locking (version field checking)
- [ ] Implement dead-letter queue integration

#### Database Tables
- [ ] Create position_entries table
- [ ] Create position_closes table
- [ ] Create bracket_orders table
- [ ] Update positions table with all scale-in fields

#### Testing
- [ ] Unit test optimistic locking (concurrent update detection)
- [ ] Unit test projector idempotency
- [ ] Integration test: event replay
- [ ] Integration test: dead-letter queue retry
- [ ] Test version conflict handling

### Phase 3: Stop Priority + Scale-In Integration (Weeks 7-8)

#### Stop Management
- [ ] Create [`src/risk/stop_priority.py`](workspace/skills/aster-trading/src/risk/stop_priority.py) with StopPriorityManager
- [ ] Update [`src/risk/manage_brackets.py`](workspace/skills/aster-trading/src/risk/manage_brackets.py) to enforce priority
- [ ] Implement bracket state logging
- [ ] Add hard stop (daily loss) enforcement
- [ ] Add time stop implementation

#### Scale-In
- [ ] Create [`src/scale_in/scale_in_eligibility.py`](workspace/skills/aster-trading/src/scale_in/scale_in_eligibility.py)
- [ ] Implement PRIMARY path (TP2 + ADX >= 25)
- [ ] Implement SECONDARY path (TP1 + BE + ADX >= 30)
- [ ] Add margin ratio check (≤70%)
- [ ] Add funding rate check
- [ ] Implement scale_in_conditions_log writer

#### Testing
- [ ] Unit test stop priority enforcement
- [ ] Unit test scale-in eligibility (all paths)
- [ ] Integration test: scale-in with trailing update
- [ ] Integration test: stop conflicts resolved by priority
- [ ] Load test: concurrent scale-in requests

### Phase 4: Conflict Resolution + Observability (Weeks 9-10)

#### Conflict Resolution
- [ ] Create [`src/sync/conflict_resolver.py`](workspace/skills/aster-trading/src/sync/conflict_resolver.py)
- [ ] Implement WebSocket vs batch reconciliation
- [ ] Add anomaly detection
- [ ] Integrate with SyncEngine

#### Observability
- [ ] Create Layer 4 risk_states table
- [ ] Create risk_events table
- [ ] Create sync_metrics table
- [ ] Update system_components table
- [ ] Create dashboard views (v_active_positions, v_position_pnl, v_risk_dashboard, v_sync_health)
- [ ] Update [`dashboard/state.py`](workspace/skills/aster-trading/dashboard/state.py) to use new views

#### Monitoring
- [ ] Add sync batch duration alerts
- [ ] Add dead-letter queue depth monitoring
- [ ] Add version conflict rate tracking
- [ ] Add scale-in rejection rate tracking

### Phase 5: Funding Rate + Error Recovery (Weeks 11-12)

#### Funding Rate
- [ ] Create [`src/sync/funding_rate_fetcher.py`](workspace/skills/aster-trading/src/sync/funding_rate_fetcher.py)
- [ ] Integrate funding rate fetch into sync cycle
- [ ] Add funding rate to scale-in eligibility check
- [ ] Test funding rate edge cases (negative, zero, missing)

#### Error Recovery
- [ ] Create [`src/sync/retry_worker.py`](workspace/skills/aster-trading/src/sync/retry_worker.py)
- [ ] Implement dead-letter queue processing
- [ ] Add manual recovery commands
- [ ] Create recovery runbooks
- [ ] Test recovery scenarios (stuck batches, lost events, version conflicts)

#### Performance
- [ ] Implement retention policies (7 days raw, 30 days events, 90 days logs)
- [ ] Add database vacuum schedule
- [ ] Add index maintenance (PRAGMA optimize)
- [ ] Load test full system under production load
- [ ] Profile slow queries and optimize

### Phase 6: Production Readiness (Week 13+)

#### Cleanup
- [ ] Remove JSON fallback writes (dual-write strategy)
- [ ] Remove deprecated trade_state.py JSON functions
- [ ] Clean up backup files (.bak)
- [ ] Remove Phase 1 legacy code

#### Documentation
- [ ] Update README with new architecture
- [ ] Document all new tables and views
- [ ] Create operator runbooks
- [ ] Create disaster recovery procedures
- [ ] Update API documentation

#### Production Deployment
- [ ] Stage migration in production-like environment
- [ ] Performance test with production data volume
- [ ] Execute gradual rollout (canary deployment)
- [ ] Monitor for 48 hours post-deployment
- [ ] Validate all dashboards working
- [ ] Confirm audit trail completeness

---

## 14. Key Technical Decisions & Rationale

### 14.1 Architecture Decisions

| Decision | Rationale | Trade-offs |
|----------|-----------|------------|
| **Event Sourcing** | Enables replay, audit trail, deterministic state reconstruction | More complex than direct state updates; requires projector logic |
| **Batch-oriented Sync** | Atomic cycles prevent partial updates; simplifies recovery | Heartbeat delay vs real-time; 60-second lag acceptable for this use case |
| **SQLite over PostgreSQL** | Simplicity for $40-$2K capital range; single-file portability | Limited to ~100K events/day; migrate to PostgreSQL at scale |
| **Optimistic Locking** | Prevents lost updates without pessimistic locks | Requires retry logic; acceptable for low-concurrency trading bot |
| **Dead-Letter Queue** | Guarantees no lost events; enables async retry | Adds operational overhead; critical for production reliability |

### 14.2 Schema Decisions

| Decision | Rationale | Alternative Considered |
|----------|-----------|------------------------|
| **position_uuid as PK** | Allows multiple positions per symbol over time | Using symbol as PK (rejected - prevents historical tracking) |
| **version field** | Simple optimistic locking implementation | Timestamps (rejected - not atomic) |
| **raw_json preservation** | Future-proof against API schema changes | Normalized columns only (rejected - loses forensic data) |
| **CHECK constraints** | Database-level validation | Application-only (rejected - weaker guarantees) |
| **Integer timestamps (ms)** | Consistent with exchange APIs | datetime columns (rejected - timezone complexity) |

### 14.3 Performance Decisions

| Decision | Rationale | Expected Impact |
|----------|-----------|-----------------|
| **Indexes on correlation_id** | Fast event chain traversal | 10x faster queries |
| **Partial index on scale_in_pending** | Only index active flags (small set) | 50% smaller index size |
| **WAL mode** | Better concurrency for SQLite | Allows read during write |
| **7-day raw retention** | Balance forensics vs storage | ~500MB for 10K positions/day |

### 14.4 Safety Decisions

| Decision | Rationale | Risk Mitigation |
|----------|-----------|-----------------|
| **scale_in_pending in DB** | Survive crashes mid-scale-in | Prevents double-scales |
| **Foreign key constraints** | Referential integrity | Prevents orphaned records |
| **Batch atomicity** | All-or-nothing updates | Prevents partial state |
| **Dual-write during migration** | Zero downtime migration | Fallback to JSON if DB fails |

### 14.5 Operational Decisions

| Decision | Rationale | Monitoring |
|----------|-----------|------------|
| **3 retry max** | Balance recovery vs infinite loops | Alert on persistent failures |
| **70% margin threshold** | Safety margin before 80% liquidation | Track rejection rate |
| **60s heartbeat** | Balance API rate limits vs latency | Monitor batch duration |
| **Manual recovery commands** | Fast resolution of stuck states | Audit all manual interventions |

---

## 15. End-to-End Example: ETHUSDT Scale-In Scenario

### 15.1 Scenario Setup

Initial state:
- Position: 0.50 ETH LONG @ $1850
- SL: $1825 (initial)
- TP1: $1900 (25% size)
- TP2: $1955 (50% size)
- Trailing: Pending at $1880

### 15.2 Event Sequence

```
Sync Batch 1 (T=0):
  ├─ POSITION_OPENED (0.50 ETH @ $1850)
  │  └─ Projector creates: position, position_entries, bracket_orders
  └─ Batch complete: 1 position, 4 brackets

Sync Batch 2 (T=60s):
  ├─ ORDER_FILLED (TP1 filled @ $1900, qty=0.125)
  │  ├─ Projector: position_closes (PARTIAL, pnl=$6.25)
  │  ├─ Projector: Update position qty=0.375
  │  └─ Projector: Move SL to breakeven ($1850)
  ├─ TP1_TRIGGERED event emitted
  └─ bracket_state_log: TAKE_PROFIT_1 PENDING→FILLED

Sync Batch 3 (T=120s):
  ├─ Price now at $1920
  ├─ ADX = 35.5
  ├─ SCALE_IN_CHECK event emitted
  │  ├─ Eligibility: can_scale_in=FALSE (TP2 not hit, TP1 insufficient)
  │  └─ scale_in_conditions_log: reason="No path met"
  └─ Batch complete: No action

Sync Batch 4 (T=180s):
  ├─ ORDER_FILLED (TP2 filled @ $1955, qty=0.1875)
  │  ├─ Projector: position_closes (PARTIAL, pnl=$19.69)
  │  ├─ Projector: Update position qty=0.1875
  │  └─ Projector: Activate trailing stop @ $1940 (callback 0.8%)
  ├─ TP2_TRIGGERED event emitted
  ├─ TRAILING_ACTIVATED event emitted
  └─ bracket_state_log: TRAILING_STOP PENDING→ACTIVE

Sync Batch 5 (T=240s):
  ├─ Price now at $1960
  ├─ ADX = 37.2
  ├─ Funding rate = 0.0001 (positive)
  ├─ SCALE_IN_CHECK event emitted
  │  ├─ Eligibility: can_scale_in=TRUE (PRIMARY path: TP2 hit + ADX >= 25)
  │  ├─ scale_in_conditions_log: path_used=PRIMARY
  │  └─ Set scale_in_pending=1
  ├─ POSITION_SCALED_IN event emitted
  │  ├─ Added qty: 0.15 @ $1960 (3% of account)
  │  ├─ New avg entry: ((0.1875 * $1850) + (0.15 * $1960)) / 0.3375 = $1897
  │  ├─ Projector: position_entries (SCALE_IN, qty=0.15)
  │  ├─ Projector: Update position qty=0.3375, entry_price=$1897
  │  └─ Projector: Update trailing stop for new qty
  ├─ TRAILING_UPDATED event emitted
  │  └─ New trailing: qty=0.3375, callback=0.8%, activation=$1946
  └─ Clear scale_in_pending=0

Sync Batch 6 (T=300s):
  ├─ Price drops to $1931 (trailing callback hit)
  ├─ ORDER_FILLED (Trailing stop @ $1931, qty=0.3375)
  │  ├─ Projector: position_closes (FULL, pnl=$11.47)
  │  ├─ Projector: Update position status=CLOSED, qty=0
  │  └─ Total realized PnL: $6.25 + $19.69 + $11.47 = $37.41
  ├─ POSITION_CLOSED event emitted
  └─ bracket_state_log: TRAILING_STOP ACTIVE→FILLED
```

### 15.3 Database State After Completion

```sql
-- positions table
SELECT * FROM positions WHERE symbol = 'ETHUSDT';
/*
position_uuid | symbol   | side | quantity | entry_price | status | close_reason
a1b2c3d4      | ETHUSDT  | LONG | 0        | 1897        | CLOSED | TRAILING_STOP
pyramid_scales=1, tp1_hit=1, tp2_hit=1, trailing_active=1
*/

-- position_entries table
SELECT * FROM position_entries WHERE position_uuid = 'a1b2c3d4';
/*
id | entry_type | entry_price | quantity | timestamp
1  | INITIAL    | 1850        | 0.5      | T=0
2  | SCALE_IN   | 1960        | 0.15     | T=240s
*/

-- position_closes table
SELECT * FROM position_closes WHERE position_uuid = 'a1b2c3d4';
/*
id | close_type    | close_price | quantity | pnl    | timestamp
1  | PARTIAL       | 1900        | 0.125    | 6.25   | T=60s
2  | PARTIAL       | 1955        | 0.1875   | 19.69  | T=180s
3  | FULL          | 1931        | 0.3375   | 11.47  | T=300s
*/

-- scale_in_conditions_log table
SELECT * FROM scale_in_conditions_log WHERE symbol = 'ETHUSDT' ORDER BY timestamp;
/*
id | can_scale_in | path_used  | adx_value | reason
1  | 0            | NULL       | 35.5      | No path met
2  | 1            | PRIMARY    | 37.2      | PRIMARY: TP2 hit + ADX 37.2 >= 25
*/

-- events table
SELECT event_type, symbol FROM events WHERE symbol = 'ETHUSDT' ORDER BY id;
/*
event_type           | symbol   | correlation_id
POSITION_OPENED      | ETHUSDT  | corr-001
TP1_TRIGGERED        | ETHUSDT  | corr-002
SCALE_IN_CHECK       | ETHUSDT  | corr-003
TP2_TRIGGERED        | ETHUSDT  | corr-004
TRAILING_ACTIVATED   | ETHUSDT  | corr-004
SCALE_IN_CHECK       | ETHUSDT  | corr-005
POSITION_SCALED_IN   | ETHUSDT  | corr-005
TRAILING_UPDATED     | ETHUSDT  | corr-005
POSITION_CLOSED      | ETHUSDT  | corr-006
*/
```

---

## 16. Conclusion & Next Steps

This V3 architecture document provides a complete, production-ready blueprint for implementing a robust, auditable, and scalable trading system data layer. The plan addresses all gaps identified in the V2 analysis:

### Key Improvements Over V2

1. ✅ **Complete Migration Strategy** - Zero-downtime migration with dual-write and rollback procedures
2. ✅ **Full Schema DDL** - All tables, indexes, and constraints defined
3. ✅ **Implementation Code** - Complete pseudo-code for all critical classes
4. ✅ **Conflict Resolution** - WebSocket vs batch reconciliation algorithms
5. ✅ **Performance Specs** - Retention policies, index strategies, load estimates
6. ✅ **Funding Rate Integration** - Complete fetch and storage implementation
7. ✅ **Testing Strategy** - Unit and integration test examples
8. ✅ **Error Recovery** - Dead-letter queue, retry workers, manual commands
9. ✅ **Operational Runbooks** - Step-by-step procedures for common issues

### Implementation Order

Follow the phased approach in Section 13:
1. **Phase 0** (Weeks 1-2): Foundation - Migration and basic schema
2. **Phase 1** (Weeks 3-4): Raw layer + Event sourcing
3. **Phase 2** (Weeks 5-6): Projectors + State transitions
4. **Phase 3** (Weeks 7-8): Stop priority + Scale-in
5. **Phase 4** (Weeks 9-10): Conflict resolution + Observability
6. **Phase 5** (Weeks 11-12): Funding rate + Error recovery
7. **Phase 6** (Week 13+): Production readiness

### Success Criteria

- [ ] All events have correlation_ids and can be replayed
- [ ] scale_in_pending survives system crashes
- [ ] Batch failures don't corrupt state
- [ ] Dead-letter queue processes < 1% of events
- [ ] Sync cycle completes in < 5 seconds
- [ ] Version conflicts occur < 0.1% of time
- [ ] Full audit trail for all trades
- [ ] Zero data loss over 30 days

### Migration to PostgreSQL

If the system scales beyond SQLite's capacity (~100K events/day), migrate to PostgreSQL with minimal code changes:
- Replace `get_connection()` with PostgreSQL connection pool
- Update PRAGMA statements to PostgreSQL equivalents
- Use PostgreSQL's native UUID generation
- Enable pg_stat_statements for query monitoring
- Use materialized views instead of regular views

**This document is ready for autonomous implementation by AI agents or human developers.**