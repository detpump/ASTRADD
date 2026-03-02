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
| `risk_state` | 1 | POPULATED |
| `orders` | 43,024 | POPULATED |

### Current Dashboard Query Problem

The dashboard view `v_risk_dashboard` is designed for V3 schema but queries empty tables:

```sql
-- Current problematic query (from v_risk_dashboard view):
SELECT 
    (SELECT account_equity FROM risk_states WHERE id = 1) as equity,  -- V3: EMPTY!
    (SELECT daily_pnl FROM risk_states WHERE id = 1) as daily_pnl,    -- V3: EMPTY!
    ...
    COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_positions     -- V3: EMPTY!
FROM positions_v3  -- V3: EMPTY!
```

### Where Actual Data Exists

**V2 tables with actual trading data:**
- `risk_state`: Contains equity=39.72, daily_pnl=0.03, drawdown=0.29%, positions data
- `positions`: Contains 2 position records (though values are zeros - stale data)
- `orders`: Contains 43,024 historical orders

### Current System State

From `system_state` table:
- System running: YES (running=1)
- Loop count: 22
- Trades executed: 1
- Enabled symbols: ["ASTERUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "HYPEUSDT"]
- Recent signals available for all symbols

From `execution_tracker` table:
- Total signals: 114
- Total orders: 114  
- Active orders: 22
- Status distribution: {"ORDER_FAILED": 91, "ORDER_FILLED": 1, "SIGNAL_GENERATED": 22}

## Solutions Required

1. **Data Migration**: Populate V3 tables (`positions_v3`, `risk_states`) from V2 tables
2. **Dashboard Update**: Modify dashboard queries to use correct table sources or implement data sync
3. **Real-time Sync**: Ensure new trades/positions populate V3 tables going forward

## Evidence

**V2 risk_state data (actual trading data):**
```
account_equity: 39.72
daily_pnl: 0.03 (3%)
drawdown_pct: 0.0029 (0.29%)
Positions: SOLUSDT (0.31 @ 87.84), ETHUSDT (0.013 @ 2045.9)
```

**V3 risk_states table:**
```
(empty - 0 rows)
```

**V3 dashboard output:**
```
||||0||0
(equity|daily_pnl|drawdown|can_trade|open_positions|total_exposure|events_24h)
```

## Investigation Date
2026-03-02 22:52 UTC
