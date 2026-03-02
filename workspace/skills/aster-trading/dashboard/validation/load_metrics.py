#!/usr/bin/env python3
"""Utility script to summarize current trading telemetry for validation dashboards.

The intent is to provide a lightweight snapshot of the latest trading system state,
key risk metrics, and recent log activity so we can quickly validate dashboard
indicators without manually digging through JSON and log files.

This script reads:
  * logs/v2_state.json          → runtime snapshot persisted by trading_system
  * logs/history/equity.jsonl   → historical equity samples for trend context
  * logs/v2_system.log          → tail of the main system log for signal/risk info

Running the script prints a compact, human-readable summary covering:
  * Core runtime status (loop count, total signals, trades executed, etc.)
  * Open positions and last signal strength/confidence per symbol
  * Circuit breaker status + recent heal metrics
  * Most recent equity samples (for validating dashboard middle indicators)
  * Recent signal-related log entries (so we know if signals are firing)
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CURRENT_FILE = Path(__file__).resolve()
VALIDATION_DIR = CURRENT_FILE.parent
DASHBOARD_DIR = VALIDATION_DIR.parent
ROOT_DIR = DASHBOARD_DIR.parent
LOGS_DIR = ROOT_DIR / "logs"
HISTORY_DIR = LOGS_DIR / "history"

OPENCLAW_ROOT = Path(os.environ.get("OPENCLAW_BASE_DIR", "/Users/FIRMAS/.openclaw"))
OPENCLAW_HISTORY_DIR = OPENCLAW_ROOT / "logs" / "history"
OPENCLAW_LOGS_DIR = OPENCLAW_ROOT / "logs"


def _resolve_existing_path(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


V2_STATE_PATH = _resolve_existing_path(LOGS_DIR / "v2_state.json", OPENCLAW_LOGS_DIR / "v2_state.json")
EQUITY_HISTORY_PATH = _resolve_existing_path(HISTORY_DIR / "equity.jsonl", OPENCLAW_HISTORY_DIR / "equity.jsonl")
SYSTEM_LOG_PATH = _resolve_existing_path(LOGS_DIR / "v2_output.log", OPENCLAW_LOGS_DIR / "v2_output.log")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def read_jsonl_tail(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(rows)


def read_log_tail(path: Path, max_lines: int = 800) -> list[str]:
    if not path.exists():
        return []
    buffer: deque[str] = deque(maxlen=max_lines)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buffer.append(line.rstrip())
    return list(buffer)


def fmt_time(ms: int | float | None) -> str:
    if not ms:
        return "-"
    # Accept either epoch ms or already-in-seconds floats
    value = float(ms)
    if value > 1e12:  # probably milliseconds
        value /= 1000.0
    return datetime.utcfromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def print_state_snapshot(state: dict[str, Any]) -> None:
    print("=== CORE STATE (v2_state.json) ===")
    if not state:
        print("State file not found or empty.\n")
        return

    print(f"Running           : {state.get('running', False)}")
    print(f"Loop count        : {state.get('loop_count')}\t(main_loop_seconds=60)")
    print(f"Trades executed   : {state.get('trades_executed')}\tTotal signals: {state.get('total_signals')}")
    print(f"Equity            : {state.get('equity')} USDT\tDaily PnL: {state.get('daily_pnl')}")
    print(f"Drawdown %        : {state.get('drawdown_pct')}\tOpen positions: {state.get('open_positions')}")
    print(f"Last signal time  : {fmt_time(state.get('last_signal_time'))}\n")

    positions = state.get('positions', {}) or {}
    if positions:
        print("-- Open Positions")
        for symbol, pos in positions.items():
            entry = pos.get('entryPrice')
            mark = pos.get('markPrice')
            pnl = pos.get('unRealizedProfit')
            amt = pos.get('positionAmt')
            print(f"  {symbol:<9} amt={amt:<8} entry={entry:<12} mark={mark:<12} PnL={pnl}")
        print()

    signals = state.get('last_signals', {}) or {}
    if signals:
        print("-- Last Signals")
        for symbol, sig in signals.items():
            action = sig.get('action')
            strength = sig.get('strength')
            conf = sig.get('confidence')
            price = sig.get('price')
            print(f"  {symbol:<9} {action:<5} strength={strength:<6} conf={conf:<6} price={price}")
        print()

    heal = state.get('healing_metrics', {}) or {}
    if heal:
        print("-- Healing Metrics")
        print(
            f"  total_heals={heal.get('total_heals')}  sl_added={heal.get('sl_orders_added')}  "
            f"tp_added={heal.get('tp_orders_added')}  leverage_adj={heal.get('leverage_adjusted')}"
        )
        print(f"  stale_positions_cleaned={heal.get('stale_positions_cleaned')}  last_heal={fmt_time(heal.get('last_heal_timestamp'))}\n")

    breaker = state.get('circuit_breaker', {}) or {}
    if breaker:
        print("-- Circuit Breaker")
        print(
            f"  healthy={breaker.get('is_healthy')}  state={breaker.get('state')}  failures={breaker.get('failure_count')}  "
            f"success={breaker.get('success_count')}"
        )
        print(f"  last_failure={fmt_time(breaker.get('last_failure_time'))}\n")


def print_equity_tail(points: list[dict[str, Any]]) -> None:
    print("=== EQUITY HISTORY (last entries) ===")
    if not points:
        print("No equity samples found.\n")
        return
    for row in points:
        ts = row.get('timestamp') or row.get('timestamp_ms')
        ts_fmt = row.get('timestamp') or fmt_time(row.get('timestamp_ms'))
        eq = row.get('equity')
        daily = row.get('daily_pnl', '-')
        draw = row.get('drawdown_pct', '-')
        print(f"  {ts_fmt:<24} equity={eq:<10} daily_pnl={daily:<8} drawdown={draw}")
    print()


def print_signal_logs(lines: Iterable[str]) -> None:
    print("=== RECENT SIGNAL / BRACKET LOGS ===")
    if not lines:
        print("No log entries available.\n")
        return

    signal_pattern = re.compile(r"(Signal|SEÑAL|SIGNAL RECIBIDO|Signals)", re.IGNORECASE)
    bracket_pattern = re.compile(r"bracket|manage_brackets", re.IGNORECASE)

    signal_hits: list[str] = []
    bracket_hits: list[str] = []

    for line in lines:
        if signal_pattern.search(line):
            signal_hits.append(line)
        if bracket_pattern.search(line):
            bracket_hits.append(line)

    def print_group(title: str, entries: list[str]) -> None:
        print(title)
        if not entries:
            print("  (no entries found)")
        else:
            for row in entries[-10:]:  # show last few lines for brevity
                print(f"  {row}")
        print()

    print_group("-- Signal-related entries", signal_hits)
    print_group("-- Bracket manager entries", bracket_hits)


def main() -> None:
    state = load_json(V2_STATE_PATH)
    equity_tail = read_jsonl_tail(EQUITY_HISTORY_PATH, limit=15)
    log_tail = read_log_tail(SYSTEM_LOG_PATH, max_lines=1200)

    print_state_snapshot(state)
    print_equity_tail(equity_tail[-8:])
    print_signal_logs(log_tail)


if __name__ == "__main__":
    main()
