# V3 Database Schema Documentation

This document describes all database tables and views in the V3 data architecture.

## Overview

The V3 database schema is organized into 4 layers:

1. **Layer 1: Raw Snapshots** - Immutable API responses preserved for forensic analysis
2. **Layer 2: Event Log** - Immutable event store for replay and audit
3. **Layer 3: Operational State** - Canonical runtime state for trading decisions
4. **Layer 4: Risk & Observability** - System health monitoring and risk tracking

---

## Layer 1: Raw Snapshots

### raw_position_snapshots

Immutable record of position data from exchange API.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| sync_batch_id | TEXT | Foreign key to sync_batches |
| symbol | TEXT | Trading symbol (e.g., BTCUSDT) |
| position_amt | REAL | Position size |
| entry_price | REAL | Average entry price |
| mark_price | REAL | Current mark price |
| unrealized_pnl | REAL | Unrealized P&L |
| leverage | INTEGER | Leverage multiplier |
| margin_type | TEXT | Margin type |
| liquidation_price | REAL | Liquidation price |
| update_time | INTEGER | Last update timestamp |
| raw_json | TEXT | Full raw JSON response |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_raw_position_batch` - By sync_batch_id
- `idx_raw_position_symbol_time` - By symbol, created_at DESC

---

### raw_order_snapshots

Immutable record of order data from exchange API.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| sync_batch_id | TEXT | Foreign key to sync_batches |
| order_id | TEXT | Exchange order ID |
| client_order_id | TEXT | Client-defined order ID |
| symbol | TEXT | Trading symbol |
| side | TEXT | BUY or SELL |
| type | TEXT | Order type |
| status | TEXT | Order status |
| price | REAL | Order price |
| quantity | REAL | Order quantity |
| executed_qty | REAL | Executed quantity |
| avg_price | REAL | Average fill price |
| reduce_only | INTEGER | Reduce-only flag (0/1) |
| time_in_force | TEXT | Time in force |
| stop_price | REAL | Stop price |
| update_time | INTEGER | Last update timestamp |
| raw_json | TEXT | Full raw JSON response |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_raw_order_batch` - By sync_batch_id
- `idx_raw_order_symbol_time` - By symbol, created_at DESC
- `idx_raw_order_order_id` - By order_id

---

### raw_account_snapshots

Immutable record of account balance data from exchange API.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| sync_batch_id | TEXT | Foreign key to sync_batches |
| asset | TEXT | Asset symbol (e.g., USDT) |
| wallet_balance | REAL | Wallet balance |
| available_balance | REAL | Available balance |
| cross_wallet_balance | REAL | Cross wallet balance |
| cross_initial_margin | REAL | Cross initial margin |
| maintenance_margin | REAL | Maintenance margin |
| raw_json | TEXT | Full raw JSON response |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_raw_account_batch` - By sync_batch_id
- `idx_raw_account_asset_time` - By asset, created_at DESC

---

## Layer 2: Event Log

### events

Immutable event store for replay and audit. This is the core event sourcing table.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| event_type | TEXT | Event type (see below) |
| event_source | TEXT | Source: EXCHANGE, SYSTEM, WEBSOCKET |
| correlation_id | TEXT | Correlation ID for tracing |
| symbol | TEXT | Associated symbol |
| payload_json | TEXT | Event payload JSON |
| parent_event_id | INTEGER | Parent event ID |
| caused_by_event_id | INTEGER | Causation event ID |
| created_at | INTEGER | Event creation time |

**Event Types:**
- `POSITION_OPENED`, `POSITION_SCALED_IN`, `POSITION_SCALED_OUT`, `POSITION_CLOSED`
- `ORDER_PLACED`, `ORDER_FILLED`, `ORDER_CANCELLED`
- `SL_TRIGGERED`, `TP1_TRIGGERED`, `TP2_TRIGGERED`
- `TRAILING_ACTIVATED`, `TRAILING_UPDATED`
- `RISK_LIMIT_HIT`, `SCALE_IN_CHECK`
- `SYSTEM_STARTED`, `SYSTEM_STOPPED`

**Indexes:**
- `idx_events_correlation` - By correlation_id
- `idx_events_type_time` - By event_type, created_at
- `idx_events_symbol` - By symbol, created_at
- `idx_events_parent` - By parent_event_id
- `idx_events_caused_by` - By caused_by_event_id

---

### bracket_state_log

Audit trail for bracket (stop loss, take profit) state transitions.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| bracket_uuid | TEXT | Unique bracket identifier |
| position_uuid | TEXT | Associated position |
| bracket_type | TEXT | Type: STOP_LOSS, TAKE_PROFIT_1, TAKE_PROFIT_2, TRAILING_STOP, TIME_STOP |
| old_state | TEXT | Previous state |
| new_state | TEXT | New state |
| trigger_reason | TEXT | Trigger reason |
| price_trigger | REAL | Trigger price |
| quantity | REAL | Order quantity |
| timestamp | INTEGER | Transition timestamp |

**Indexes:**
- `idx_bracket_state_position` - By position_uuid
- `idx_bracket_state_time` - By timestamp DESC

---

### scale_in_conditions_log

Audit trail for scale-in eligibility decisions.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| timestamp | INTEGER | Decision timestamp |
| position_uuid | TEXT | Associated position |
| symbol | TEXT | Trading symbol |
| can_scale_in | INTEGER | Decision result (0/1) |
| path_used | TEXT | PRIMARY or SECONDARY |
| adx_value | REAL | ADX indicator value |
| funding_rate | REAL | Current funding rate |
| price_distance_pct | REAL | Price distance percentage |
| margin_ratio | REAL | Current margin ratio |
| pyramid_level | INTEGER | Current pyramid level |
| reason | TEXT | Decision reason |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_scale_in_log_position` - By position_uuid, timestamp DESC
- `idx_scale_in_log_symbol` - By symbol, timestamp DESC

---

### event_processing_errors

Dead-letter queue for failed event projections.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| event_id | INTEGER | Foreign key to events |
| event_type | TEXT | Event type that failed |
| position_uuid | TEXT | Associated position |
| error_message | TEXT | Error description |
| retry_count | INTEGER | Number of retry attempts |
| max_retries | INTEGER | Maximum retry attempts |
| status | TEXT | PENDING, RESOLVED, FAILED |
| first_attempt_at | INTEGER | First attempt timestamp |
| last_retry_at | INTEGER | Last retry timestamp |
| resolved_at | INTEGER | Resolution timestamp |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_event_errors_status` - By status, retry_count
- `idx_event_errors_event` - By event_id

---

## Layer 3: Operational State

### positions_v3

Canonical position table with position_uuid as primary key. Replaces old positions table.

| Column | Type | Description |
|--------|------|-------------|
| position_uuid | TEXT | Primary key (UUID) |
| symbol | TEXT | Trading symbol |
| side | TEXT | LONG or SHORT |
| quantity | REAL | Position quantity |
| entry_price | REAL | Average entry price |
| current_price | REAL | Current market price |
| unrealized_pnl | REAL | Unrealized P&L |
| notional | REAL | Notional value |
| leverage | INTEGER | Leverage multiplier |
| open_time | INTEGER | Position open timestamp |
| close_time | INTEGER | Position close timestamp |
| cycle_id | TEXT | Associated trading cycle |
| correlation_id | TEXT | Correlation ID |
| reason | TEXT | Open reason |
| close_reason | TEXT | Close reason |
| version | INTEGER | Optimistic lock version |
| metadata_json | TEXT | Additional metadata |
| scale_in_pending | INTEGER | Scale-in pending flag (0/1) |
| scale_in_timestamp | INTEGER | Scale-in request timestamp |
| pyramid_scales | INTEGER | Number of scale-ins |
| last_scale_price | REAL | Last scale-in price |
| last_scale_time | INTEGER | Last scale-in timestamp |
| trailing_active | INTEGER | Trailing stop active (0/1) |
| trailing_activation_price | REAL | Trailing activation price |
| trailing_callback_pct | REAL | Trailing callback percentage |
| tp1_hit | INTEGER | Take profit 1 hit (0/1) |
| tp2_hit | INTEGER | Take profit 2 hit (0/1) |
| sl_at_break_even | INTEGER | Stop loss at break even (0/1) |
| initial_sl_price | REAL | Initial stop loss price |
| current_sl_price | REAL | Current stop loss price |
| max_hold_time_minutes | INTEGER | Maximum hold time |
| status | TEXT | OPEN or CLOSED |
| updated_at | INTEGER | Last update timestamp |
| created_at | INTEGER | Creation timestamp |

**Indexes:**
- `idx_positions_v3_symbol` - By symbol
- `idx_positions_v3_status` - By status
- `idx_positions_v3_cycle` - By cycle_id
- `idx_positions_v3_correlation` - By correlation_id
- `idx_positions_v3_scale_pending` - Partial index for scale_in_pending = 1

---

### position_entries

Record of every position entry (initial and scale-ins).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| position_uuid | TEXT | Foreign key to positions_v3 |
| entry_type | TEXT | INITIAL or SCALE_IN |
| entry_price | REAL | Entry price |
| quantity | REAL | Entry quantity |
| notional | REAL | Notional value |
| order_id | TEXT | Associated order ID |
| correlation_id | TEXT | Correlation ID |
| timestamp | INTEGER | Entry timestamp |
| metadata_json | TEXT | Additional metadata |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_position_entries_position` - By position_uuid, timestamp DESC

---

### position_closes

Record of every position close (partial, full, stop loss, take profit).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| position_uuid | TEXT | Foreign key to positions_v3 |
| close_type | TEXT | PARTIAL, FULL, STOP_LOSS, TAKE_PROFIT, TIME_STOP, LIQUIDATION |
| close_price | REAL | Close price |
| quantity | REAL | Closed quantity |
| pnl | REAL | Realized P&L |
| pnl_pct | REAL | P&L percentage |
| order_id | TEXT | Associated order ID |
| correlation_id | TEXT | Correlation ID |
| timestamp | INTEGER | Close timestamp |
| close_reason | TEXT | Close reason |
| metadata_json | TEXT | Additional metadata |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_position_closes_position` - By position_uuid, timestamp DESC

---

### bracket_orders

Current bracket orders (stop loss, take profit, trailing stop) for positions.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| bracket_uuid | TEXT | Unique bracket ID |
| position_uuid | TEXT | Foreign key to positions_v3 |
| bracket_type | TEXT | STOP_LOSS, TAKE_PROFIT_1, TAKE_PROFIT_2, TRAILING_STOP, TIME_STOP |
| status | TEXT | PENDING, ACTIVE, FILLED, CANCELLED |
| order_id | TEXT | Associated exchange order ID |
| price_trigger | REAL | Trigger price |
| quantity | REAL | Order quantity |
| reduce_only | INTEGER | Reduce-only flag (0/1) |
| activation_price | REAL | Activation price (for trailing) |
| callback_rate | REAL | Callback rate |
| timestamp | INTEGER | Creation timestamp |
| filled_at | INTEGER | Fill timestamp |
| metadata_json | TEXT | Additional metadata |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_bracket_orders_position` - By position_uuid
- `idx_bracket_orders_type` - By bracket_type, status
- `idx_bracket_orders_status` - By status

---

## Layer 4: Risk & Observability

### risk_states

Current risk state snapshot (one row).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key (always 1) |
| account_equity | REAL | Total account equity |
| daily_pnl | REAL | Daily P&L |
| daily_pnl_pct | REAL | Daily P&L percentage |
| drawdown_pct | REAL | Current drawdown |
| max_drawdown_pct | REAL | Maximum drawdown |
| open_positions_count | INTEGER | Number of open positions |
| total_exposure | REAL | Total exposure |
| can_trade | INTEGER | Trading allowed flag (0/1) |
| circuit_breaker_triggered | INTEGER | Circuit breaker status (0/1) |
| risk_limits_json | TEXT | Current risk limits JSON |
| updated_at | INTEGER | Last update timestamp |

---

### risk_events

Risk event audit trail.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| event_type | TEXT | Event type |
| severity | TEXT | INFO, WARNING, CRITICAL |
| symbol | TEXT | Associated symbol |
| message | TEXT | Event message |
| details_json | TEXT | Additional details |
| triggered_at | INTEGER | Trigger timestamp |
| resolved_at | INTEGER | Resolution timestamp |
| created_at | INTEGER | Record creation time |

**Event Types:**
- `CIRCUIT_BREAKER_OPEN`, `DRAWDOWN_LIMIT_HIT`, `DAILY_LOSS_LIMIT_HIT`
- `LIQUIDATION_WARNING`, `MARGIN_RATIO_WARNING`, `POSITION_SIZE_EXCEEDED`
- `EXPOSURE_LIMIT_EXCEEDED`, `RISK_ADJUSTMENT_APPLIED`

**Indexes:**
- `idx_risk_events_type_time` - By event_type, triggered_at DESC
- `idx_risk_events_symbol` - By symbol, triggered_at DESC

---

### system_components

System component health tracking.

| Column | Type | Description |
|--------|------|-------------|
| name | TEXT | Primary key |
| status | TEXT | HEALTHY, DEGRADED, FAILED, UNKNOWN |
| last_action | TEXT | Last action performed |
| last_message | TEXT | Last status message |
| last_timestamp | INTEGER | Last update timestamp |

---

### sync_batches

Sync batch tracking.

| Column | Type | Description |
|--------|------|-------------|
| batch_id | TEXT | Primary key (UUID) |
| started_at | INTEGER | Batch start timestamp |
| completed_at | INTEGER | Batch completion timestamp |
| status | TEXT | IN_PROGRESS, COMPLETED, FAILED |
| positions_fetched | INTEGER | Positions count |
| orders_fetched | INTEGER | Orders count |
| events_emitted | INTEGER | Events emitted count |
| projections_succeeded | INTEGER | Successful projections |
| projections_failed | INTEGER | Failed projections |

---

### sync_metrics

Sync performance metrics.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| batch_id | TEXT | Foreign key to sync_batches |
| started_at | INTEGER | Start timestamp |
| completed_at | INTEGER | Completion timestamp |
| duration_ms | INTEGER | Duration in milliseconds |
| positions_fetched | INTEGER | Positions fetched |
| orders_fetched | INTEGER | Orders fetched |
| events_emitted | INTEGER | Events emitted |
| projections_succeeded | INTEGER | Successful projections |
| projections_failed | INTEGER | Failed projections |
| status | TEXT | Batch status |

---

### funding_rates

Cached funding rates from exchange.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| symbol | TEXT | Trading symbol |
| funding_rate | REAL | Funding rate |
| funding_time | INTEGER | Funding time timestamp |
| next_funding_time | INTEGER | Next funding timestamp |
| created_at | INTEGER | Record creation time |

**Indexes:**
- `idx_funding_rates_symbol` - By symbol

---

## Database Migrations

Migrations are stored in `src/state/migrations/`:

- `001_add_v3_schema.sql` - Initial V3 schema creation
- `001_add_v3_schema_rollback.sql` - Rollback script

Run migrations using:
```bash
python -m src.state.migration_runner
```

---

## Entity Relationship Diagram

```
sync_batches (1) ──┬── (N) raw_position_snapshots
                   ├── (N) raw_order_snapshots
                   └── (N) raw_account_snapshots

events (1) ──┬── (N) event_processing_errors (dead-letter queue)
             ├── (N) bracket_state_log
             └── (N) scale_in_conditions_log

positions_v3 (1) ──┬── (N) position_entries
                   ├── (N) position_closes
                   └── (N) bracket_orders

risk_states (1) ── (N) risk_events
```

---

## Notes

1. All tables use `created_at` timestamp (milliseconds since epoch)
2. Primary keys are either INTEGER AUTOINCREMENT or TEXT (UUID)
3. Foreign keys are not enforced in SQLite but should be maintained logically
4. JSON columns store structured data as text
5. All "flag" columns use INTEGER (0/1) for boolean values
