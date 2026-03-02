# Workspace Identity & Safety
- **SOUL.md / IDENTITY.md / USER.md** define the assistant persona, mission, and current human. Read them at session start as instructed in [`AGENTS.md`](AGENTS.md:5).
- Memory hygiene: log short-term details in `memory/YYYY-MM-DD.md`, curate durable context into `MEMORY.md` only when in a trusted main session.
- Follow `HEARTBEAT.md` for proactive polls, and never leak MEMORY content in external/shared chats.

# Tooling & Filesystem Basics
- Root workspace is `/Users/FIRMAS/.openclaw`. Skills live under `skills/`, including this repo at `skills/aster-trading`.
- Virtualenv: `/Users/FIRMAS/.openclaw/.venv/bin/python` (used by control scripts). Activate manually via `source .venv/bin/activate` when running commands yourself.
- Git not initialized in repo; if you need version control, initialize locally and coordinate with human.

# Command Palette
- **Trading control:** `./control.sh` (start/stop/status/log), or `./system_control.sh` (start_all, health, monitor_dashboard) for OpenClaw + cron + trading orchestration.
- **End-to-end setup:** `bash setup.sh` ensures directories, cron notes, dependency checks.
- **Testing:** `python3 -m pytest tests/ -v` and coverage variants; see [`docs/TESTING_STRATEGY.md`](workspace/skills/aster-trading/docs/TESTING_STRATEGY.md:1) for structured plans.
- **Diagnostics:** `python3 workspace/skills/aster-trading/logs/*.py` scripts capture equity/risk snapshots; only run when investigating state inconsistencies.

# Safety & Incident Playbooks
- Before editing production-critical scripts, create `.bak` copies (matches existing `.phase1.bak` artifacts). Keep changes auditable.
- On anomalies follow [`docs/ROLLBACK_PROCEDURES.md`](workspace/skills/aster-trading/docs/ROLLBACK_PROCEDURES.md:15): stop trading, snapshot configs, roll back risk settings or git state, and run smoke tests.
- RiskGuard state (`logs/risk_state.json`) and trading system state (`logs/v2_state.json`) are authoritative; do not delete unless performing a controlled reset.

# Coding Standards & Expectations
- Python style: PEP 8, type hints when practical, docstrings for public methods (per [`README.md`](workspace/skills/aster-trading/README.md:327)).
- Config validation is essential: clamp SL/TP/leverages to sane bounds (see `_get_sl_tp_percentage()` etc.). Assume config files might hold unsafe values.
- Shared services: always use exported helpers (`get_shared_risk_guard`, `CentralizedLogger`, `TradeExecutionTracker`); avoid new singletons that duplicate state.
- Self-heal loops rely on JSON state + exchange reconciliation; keep state serialization (`trade_state.py`, `_save_state`) consistent when adding fields.

# Testing & Deployment Discipline
- Extend pytest suites before merging changes to risk, execution, or data ingestion components. Mock server + stress tests are already scaffolded (see `tests/mocks`, `tests/stress`).
- Stage changes per [`CODE_ANALYSIS_REPORT.md`](workspace/skills/aster-trading/CODE_ANALYSIS_REPORT.md:21) recommendations: circuit breaker tests, dedup tests, risk gating, trade execution.
- Use `system_control.sh health` after deployments to confirm gateway/cron/trading statuses and inspect `risk_state` metrics.

# Documentation & Memory
- Update `docs/implementation_plan.md` if you roadmap remediation phases. Align with `REMEDIATION_PLAN.md` priorities.
- If a task introduces new operational steps, add them to this file or `TOOLS.md` so future sessions inherit the knowledge.
