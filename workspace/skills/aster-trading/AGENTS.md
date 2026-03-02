# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Non-Obvious Runtime Expectations
- `.env` is loaded via [`trading_system.py`](workspace/skills/aster-trading/src/trading_system.py:24) by walking up four directories. If the repo is moved outside `/Users/FIRMAS/.openclaw/skills/aster-trading`, env loading silently fails and the exchange/risk clients won’t bootstrap.
- All control scripts (`control.sh`, `system_control.sh`, `start_v2.sh`) hardcode `/Users/FIRMAS/.openclaw/.venv/bin/python`; activating another venv does nothing unless those scripts are updated.
- Shared `RiskGuard` must be retrieved through [`services/risk_service.get_shared_risk_guard()`](workspace/skills/aster-trading/src/services/risk_service.py:66). Instantiating `RiskGuard` yourself writes to a different `logs/risk_state.json`, causing split risk state and self-heal loops to malfunction.

## Risk & Execution Gotchas
- `_evaluate_trade()` ([`trading_system.py:1182`](workspace/skills/aster-trading/src/trading_system.py:1182)) clamps leverage based on live equity (≤3x under $100) regardless of `config/risk_config.json`. If you change config values beyond those caps the runtime still enforces the lower bound—update both locations when adjusting risk.
- Self-heal logic ([`trading_system.py:1980`](workspace/skills/aster-trading/src/trading_system.py:1980)) only adds SL/TP orders when missing; DO NOT delete/replace SL/TP elsewhere or you’ll trigger order spam each loop.
- Scale-ins ([`scale_in_position.py:235`](workspace/skills/aster-trading/src/scale_in_position.py:235)) require TP2 hit or SL at break-even; failing to toggle those flags in `trade_state` blocks scaling entirely.

## Testing & Simulation
- Real API tests rely on the custom mock server in [`tests/mocks/mock_api_server.py`](workspace/skills/aster-trading/tests/mocks/mock_api_server.py:1); point `API_BASE_URL` to `http://localhost:8899` or tests will hit production endpoints.
- Stress suites (`tests/stress/`) expect `pytest --stress` and environment `STRESS_TESTS=true`; running them without the flag silently skips scenarios and gives a false sense of coverage.
- Several pytest suites assume the repo root path to resolve `paths.py`. Run `pytest` from `workspace/skills/aster-trading`, otherwise fixtures like `TestSuite` in `tests/test_trade_execution_suite.py` fail to import.

## File/State Management
- State is spread across `logs/v2_state.json`, `logs/risk_state.json`, and `data/state/trade_state.json`. `sync_all_with_exchange()` ([`trade_state.py`](workspace/skills/aster-trading/src/trade_state.py:163)) must be invoked before any reconciliation work; editing state files manually without running this helper leaves phantom positions everywhere.
- Control scripts expect `.bak` siblings before modifications (see `*.phase1.bak`). If you patch without creating a `.bak`, operations tooling assumes an emergency rollback can’t happen.
- The dashboard service ([`dashboard/server.py`](workspace/skills/aster-trading/dashboard/server.py:3115)) reads JSONL history from `/Users/FIRMAS/.openclaw/logs/history/{equity,trades,risk}.jsonl` in addition to the local `logs/*.json`. When you change how equity/risk snapshots are persisted, keep those JSONL appenders in sync or the UI cards will flatline and operators lose metric coverage.
