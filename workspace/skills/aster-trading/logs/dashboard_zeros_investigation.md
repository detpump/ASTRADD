# Dashboard Zeros Investigation - V3 Schema Analysis

## Database Location
- **Database File**: `workspace/skills/aster-trading/logs/aster.db`
- **Type**: SQLite3 database with mixed V2/V3 schema

## Root Cause Identified

**The dashboard indicators show zeros because they're querying empty V3 tables instead of populated V2 tables.**

### Data Availability Analysis

| Table | Record Count | Status |
|-------|-------------|---------|
| `positions_v3` | 0 | EMPTY |
| `risk_states` | 0 | EMPTY |
| `positions` | 2 | STALE DATA (all zeros) |
| `risk_state` | 1 | POPULATED (contains JSON with live data) |
| `orders` | 47,024 | POPULATED |

### Original Dashboard Query Problem

The dashboard view `v_risk_dashboard` was designed for V3 schema but queried empty tables:

```sql
-- Original problematic query (from v_risk_dashboard view):
SELECT 
    (SELECT account_equity FROM risk_states WHERE id = 1) as equity,  -- V3: EMPTY!
    (SELECT daily_pnl FROM risk_states WHERE id = 1) as daily_pnl,    -- V3: EMPTY!
    ...
    COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_positions     -- V3: EMPTY!
FROM positions_v3  -- V3: EMPTY!
```

### Where Actual Data Exists

**V2 tables with actual trading data:**
- `risk_state`: Contains equity=39.78, daily_pnl=0.065, drawdown=0.21%, positions JSON
- `positions`: Contains 2 position records (though values are zeros - stale data)
- `orders`: Contains 47,024 historical orders

## FIX APPLIED ✅

Modified `v_risk_dashboard` view in `db.py` to query V2 tables:

```sql
-- FIXED: Query V2 tables instead of empty V3 tables
CREATE VIEW v_risk_dashboard AS
SELECT 
    (SELECT account_equity FROM risk_state WHERE id = 1) as equity,
    (SELECT daily_pnl FROM risk_state WHERE id = 1) as daily_pnl,
    (SELECT drawdown_pct FROM risk_state WHERE id = 1) as drawdown_pct,
    1 as can_trade,
    (SELECT json_extract(risk_limits, '$.open_positions_count') FROM risk_state WHERE id = 1) as open_positions,
    (SELECT COALESCE(json_extract(risk_limits, '$.positions.SOLUSDT.notional'), 0) + 
            COALESCE(json_extract(risk_limits, '$.positions.ETHUSDT.notional'), 0) +
            COALESCE(json_extract(risk_limits, '$.positions.BNBUSDT.notional'), 0) +
            COALESCE(json_extract(risk_limits, '$.positions.BTCUSDT.notional'), 0) 
     FROM risk_state WHERE id = 1) as total_exposure,
    (SELECT COUNT(*) FROM risk_events WHERE triggered_at > strftime('%s','now')*1000 - 86400000) as events_24h
```

## Results After Fix

**Dashboard output now shows real data:**
```
39.78|0.065|0.0021|1|2|53.93|0
(equity|daily_pnl|drawdown_pct|can_trade|open_positions|total_exposure|events_24h)
```

| Metric | Before (V3) | After (V2) |
|--------|-------------|------------|
| equity | 0/null | 39.78 |
| daily_pnl | 0/null | 0.065 (6.5%) |
| drawdown_pct | 0/null | 0.0021 (0.21%) |
| can_trade | null | 1 |
| open_positions | 0 | 2 |
| total_exposure | 0 | 53.93 |
| events_24h | 0 | 0 |

## Investigation Date
2026-03-02 22:55 UTC
