#!/bin/bash
# Recreated control script - add core functionality

OPENCLAW_DIR="/Users/FIRMAS/.openclaw"
TRADING_DIR="/Users/FIRMAS/.openclaw/workspace/skills/aster-trading"
CONTROL_SCRIPT="$TRADING_DIR/control.sh"
PYTHON_BIN="$OPENCLAW_DIR/.venv/bin/python3"
LISTENER_PID_FILE="$TRADING_DIR/account_listener.pid"
TRADING_PID_FILE="$TRADING_DIR/v2_process.pid"
ACCOUNT_LISTENER_LOG="$TRADING_DIR/logs/account_stream.log"
TRADING_LOG="$TRADING_DIR/logs/v2_output.log"
LISTENER_PATTERN="services.account_stream_listener"

log() { echo "[$(date +%H:%M:%S)] $1"; }

ensure_logs_dir() {
    mkdir -p "$TRADING_DIR/logs" "$OPENCLAW_DIR/logs"
}

is_pid_running() {
    local pid="$1"
    if [ -z "$pid" ]; then
        return 1
    fi
    if ps -p "$pid" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

build_metadata() {
    local pid="$1"
    local pid_file="$2"
    local log_path="$3"
    printf '{"pid":"%s","pid_file":"%s","log":"%s"}' "$pid" "$pid_file" "$log_path"
}

record_state() {
    local name="$1"
    local status="$2"
    local action="$3"
    local message="$4"
    local metadata="$5"
    python3 - <<'PY'
import json
import os
import sqlite3
import time
from pathlib import Path

BASE_DIR = Path("/Users/FIRMAS/.openclaw/workspace/skills/aster-trading")
DB_PATH = BASE_DIR / "logs" / "aster.db"
name = os.environ.get("SYS_COMP_NAME")
status = os.environ.get("SYS_COMP_STATUS")
action = os.environ.get("SYS_COMP_ACTION")
message = os.environ.get("SYS_COMP_MESSAGE")
metadata_raw = os.environ.get("SYS_COMP_METADATA")
metadata = None
if metadata_raw:
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        metadata = {"raw": metadata_raw}

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=30000")
ts = int(time.time() * 1000)
metadata_json = json.dumps(metadata) if metadata is not None else None
conn.execute(
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
    (name, status, action, message, metadata_json, ts),
)
conn.execute(
    """
    INSERT INTO system_activity (subsystem, action, status, message, created_at)
    VALUES (?, ?, ?, ?, ?)
    """,
    (name, action, status, message, ts),
)
conn.commit()
conn.close()
PY
}

update_component() {
    local name="$1"
    local status="$2"
    local action="$3"
    local message="$4"
    local metadata="$5"
    SYS_COMP_NAME="$name" \
    SYS_COMP_STATUS="$status" \
    SYS_COMP_ACTION="$action" \
    SYS_COMP_MESSAGE="$message" \
    SYS_COMP_METADATA="$metadata" \
    record_state
}

get_pid() {
    local pattern="$1"
    pgrep -f "$pattern" | head -1
}

start_gateway() {
    log "Starting gateway"
    ensure_logs_dir
    cd "$OPENCLAW_DIR" || exit 1
    # Use node directly like launchd does, with proper environment
    export HOME="${HOME:-/Users/FIRMAS}"
    export PATH="/Users/FIRMAS/.local/bin:/Users/FIRMAS/.npm-global/bin:/Users/FIRMAS/bin:/Users/FIRMAS/.volta/bin:/Users/FIRMAS/.asdf/shims:/Users/FIRMAS/.bun/bin:/Users/FIRMAS/Library/Application Support/fnm/aliases/default/bin:/Users/FIRMAS/.fnm/aliases/default/bin:/Users/FIRMAS/Library/pnpm:/Users/FIRMAS/.local/share/pnpm:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    if nohup /opt/homebrew/bin/node /opt/homebrew/lib/node_modules/openclaw/dist/index.js gateway --port 18789 > "$OPENCLAW_DIR/logs/gateway.log" 2>&1 & then
        sleep 2
        local pid=$(pgrep -f "openclaw.*gateway")
        if [ -n "$pid" ]; then
            update_component "gateway" "RUNNING" "start" "Gateway started (PID: $pid)" "$(build_metadata "$pid" "" "$OPENCLAW_DIR/logs/gateway.log")"
        else
            update_component "gateway" "RUNNING" "start" "Gateway started (command sent)" "$(build_metadata "" "" "$OPENCLAW_DIR/logs/gateway.log")"
        fi
    else
        update_component "gateway" "ERROR" "start" "Gateway start command failed" "{}"
    fi
}

stop_gateway() {
    log "Stopping gateway"
    # Use launchctl stop to tell launchd to stop and not restart (since KeepAlive=false)
    launchctl stop ai.openclaw.gateway
    sleep 1
    # Also kill any remaining processes as fallback
    pkill -f "openclaw-gateway" 2>/dev/null || true
    update_component "gateway" "STOPPED" "stop" "Gateway stop command issued" "$(build_metadata "" "$OPENCLAW_DIR/logs/openclaw-gateway.pid" "$OPENCLAW_DIR/logs/gateway.log")"
}

start_cron() {
    log "Starting cron"
    ensure_logs_dir
    cd "$OPENCLAW_DIR" || exit 1
    if nohup openclaw cron run 0f65bca4-8d31-40d6-8d98-d1f8e1a97258 > "$OPENCLAW_DIR/logs/cron.log" 2>&1 & then
        sleep 2
        local pid=$(get_pid "cron.*run")
        update_component "cron" "RUNNING" "start" "Cron started (PID: $pid)" "$(build_metadata "$pid" "$OPENCLAW_DIR/logs/cron.pid" "$OPENCLAW_DIR/logs/cron.log")"
    else
        update_component "cron" "ERROR" "start" "Cron start command failed" "{}"
    fi
}

stop_cron() {
    log "Stopping cron (implicit with gateway stop)"
    pkill -f "cron.*run" 2>/dev/null || true
    update_component "cron" "STOPPED" "stop" "Cron stop command issued" "$(build_metadata "" "$OPENCLAW_DIR/logs/cron.pid" "$OPENCLAW_DIR/logs/cron.log")"
}

start_listener() {
    log "Starting account listener"
    ensure_logs_dir
    local src_dir="$TRADING_DIR/src"
    if [ ! -x "$PYTHON_BIN" ]; then
        update_component "account-listener" "ERROR" "start" "Python binary missing" "{}"
        return 1
    fi
    if [ -f "$LISTENER_PID_FILE" ]; then
        local existing_pid=$(cat "$LISTENER_PID_FILE")
        if is_pid_running "$existing_pid"; then
            update_component "account-listener" "RUNNING" "start" "Listener already running" "$(build_metadata "$existing_pid" "$LISTENER_PID_FILE" "$ACCOUNT_LISTENER_LOG")"
            return 0
        fi
        rm -f "$LISTENER_PID_FILE"
    fi

    # Start listener in background - use nohup with proper shell execution
    cd "$TRADING_DIR"
    nohup env PYTHONPATH="$src_dir" "$PYTHON_BIN" -m services.account_stream_listener >> "$ACCOUNT_LISTENER_LOG" 2>&1 &
    local bg_pid=$!

    # Wait for the listener to start and connect
    local pid=""
    local max_wait=15
    local waited=0
    while [ $waited -lt $max_wait ]; do
        sleep 1
        ((waited++))
        # Check if our background process is still running
        if ! kill -0 $bg_pid 2>/dev/null; then
            # Process exited - check logs for error
            log "Listener process exited early"
            break
        fi
        # Check if Python process is running
        pid=$(pgrep -f "services.account_stream_listener" | head -1)
        if [ -n "$pid" ] && is_pid_running "$pid"; then
            # Found the listener - wait a bit more to ensure connection
            sleep 2
            # Check if still running
            if is_pid_running "$pid"; then
                echo "$pid" > "$LISTENER_PID_FILE"
                update_component "account-listener" "RUNNING" "start" "Listener started and connected" "$(build_metadata "$pid" "$LISTENER_PID_FILE" "$ACCOUNT_LISTENER_LOG")"
                return 0
            fi
        fi
    done

    # Check final status
    pid=$(pgrep -f "services.account_stream_listener" | head -1)
    if is_pid_running "$pid"; then
        echo "$pid" > "$LISTENER_PID_FILE"
        update_component "account-listener" "RUNNING" "start" "Listener started" "$(build_metadata "$pid" "$LISTENER_PID_FILE" "$ACCOUNT_LISTENER_LOG")"
        return 0
    fi

    update_component "account-listener" "ERROR" "start" "Listener failed to start or connect" "$(build_metadata "" "$LISTENER_PID_FILE" "$ACCOUNT_LISTENER_LOG")"
    return 1
}

stop_listener() {
    log "Stopping account listener"
    local pid=""
    if [ -f "$LISTENER_PID_FILE" ]; then
        pid=$(cat "$LISTENER_PID_FILE")
    fi
    if is_pid_running "$pid"; then
        kill "$pid" 2>/dev/null || true
        sleep 1
    fi
    pkill -f "$LISTENER_PATTERN" 2>/dev/null || true
    rm -f "$LISTENER_PID_FILE"
    update_component "account-listener" "STOPPED" "stop" "Listener stop issued" "$(build_metadata "" "$LISTENER_PID_FILE" "$ACCOUNT_LISTENER_LOG")"
}

start_trading() {
    log "Starting trading"
    start_listener
    if [ -x "$CONTROL_SCRIPT" ]; then
        if "$CONTROL_SCRIPT" start; then
            local pid=$(get_pid "trading_system.py")
            update_component "trading" "RUNNING" "start" "Trading start command succeeded" "{\"pid\":$pid}"
        else
            update_component "trading" "ERROR" "start" "Trading start command failed" "{}"
        fi
    else
        log "control.sh missing"
        update_component "trading" "ERROR" "start" "control.sh missing" "{}"
        return 1
    fi
}

stop_trading() {
    log "Stopping trading"
    stop_listener
    if [ -x "$CONTROL_SCRIPT" ]; then
        if "$CONTROL_SCRIPT" stop; then
            update_component "trading" "STOPPED" "stop" "Trading stop command succeeded" "{}"
        else
            update_component "trading" "ERROR" "stop" "Trading stop command failed" "{}"
        fi
    else
        log "control.sh missing"
        update_component "trading" "ERROR" "stop" "control.sh missing" "{}"
        return 1
    fi
}

check_gateway_status() {
    if pgrep -f "openclaw-gateway" > /dev/null 2>&1; then
        echo "RUNNING"
    else
        echo "STOPPED"
    fi
}

check_cron_status() {
    # Check for any cron-related processes
    if pgrep -f "cron.*run" > /dev/null 2>&1 || pgrep -f "schedule" > /dev/null 2>&1; then
        echo "RUNNING"
    else
        echo "STOPPED"
    fi
}

check_listener_status() {
    # Check via PID file first
    if [ -f "$LISTENER_PID_FILE" ]; then
        local pid=$(cat "$LISTENER_PID_FILE")
        if is_pid_running "$pid"; then
            echo "RUNNING"
            return
        fi
    fi
    # Fallback: check via process pattern
    if pgrep -f "$LISTENER_PATTERN" > /dev/null 2>&1; then
        echo "RUNNING"
    else
        echo "STOPPED"
    fi
}

check_trading_status() {
    if [ -x "$CONTROL_SCRIPT" ]; then
        if "$CONTROL_SCRIPT" status 2>/dev/null | grep -q "running\|started\|RUNNING"; then
            echo "RUNNING"
        else
            echo "STOPPED"
        fi
    else
        echo "STOPPED"
    fi
}

show_status() {
    gateway_status=$(check_gateway_status)
    cron_status=$(check_cron_status)
    trading_status=$(check_trading_status)
    listener_status=$(check_listener_status)
    update_component "gateway" "$gateway_status" "status" "Gateway status check" "{}"
    update_component "cron" "$cron_status" "status" "Cron status check" "{}"
    update_component "trading" "$trading_status" "status" "Trading status check" "{}"
    update_component "account-listener" "$listener_status" "status" "Listener status check" "{}"
    
    echo "Gateway: $gateway_status"
    echo "Cron: $cron_status"
    echo "Trading: $trading_status"
    echo "Account Listener: $listener_status"
}

show_status_all() {
    gateway_status=$(check_gateway_status)
    cron_status=$(check_cron_status)
    trading_status=$(check_trading_status)
    listener_status=$(check_listener_status)
    
    echo "gateway:$gateway_status"
    echo "cron:$cron_status"
    echo "trading:$trading_status"
    echo "account-listener:$listener_status"
    update_component "gateway" "$gateway_status" "status_all" "Gateway status_all" "{}"
    update_component "cron" "$cron_status" "status_all" "Cron status_all" "{}"
    update_component "trading" "$trading_status" "status_all" "Trading status_all" "{}"
    update_component "account-listener" "$listener_status" "status_all" "{}"
}

show_health() {
    gateway_status=$(check_gateway_status)
    cron_status=$(check_cron_status)
    trading_status=$(check_trading_status)
    listener_status=$(check_listener_status)
    
    # Get more detailed health info
    echo "Gateway: $gateway_status"
    echo "Cron: $cron_status"
    echo "Trading: $trading_status"
    echo "Account Listener: $listener_status"
    
    # Check if control.sh exists and get its status
    if [ -x "$CONTROL_SCRIPT" ]; then
        "$CONTROL_SCRIPT" status 2>/dev/null || true
    fi
    update_component "gateway" "$gateway_status" "health" "Gateway health" "{}"
    update_component "cron" "$cron_status" "health" "Cron health" "{}"
    update_component "trading" "$trading_status" "health" "Trading health" "{}"
    update_component "account-listener" "$listener_status" "health" "{}"
}

case "$1" in
    start_all)
        start_gateway
        sleep 2
        start_cron
        sleep 2
        start_trading
        ;;
    stop_all)
        stop_trading
        stop_cron
        stop_gateway
        ;;
    gateway)
        case "$2" in
            start) start_gateway ;;
            stop) stop_gateway ;;
            *) echo "Usage: $0 gateway {start|stop}" ;;
        esac
        ;;
    cron)
        case "$2" in
            start) start_cron ;;
            stop) stop_cron ;;
            *) echo "Usage: $0 cron {start|stop}" ;;
        esac
        ;;
    trading)
        case "$2" in
            start) start_trading ;;
            stop) stop_trading ;;
            *) echo "Usage: $0 trading {start|stop}" ;;
        esac
        ;;
    listener)
        case "$2" in
            start) start_listener ;;
            stop) stop_listener ;;
            status) check_listener_status ;;
            *) echo "Usage: $0 listener {start|stop|status}" ;;
        esac
        ;;
    status)
        show_status
        ;;
    status_all)
        show_status_all
        ;;
    health)
        show_health
        ;;
    *)
        cat <<USAGE
aster system control

Usage: $0 COMMAND [ARGS]

Commands:
  start_all   start gateway -> cron -> trading
  stop_all    stop trading -> cron -> gateway
  gateway     {start|stop}
  cron        {start|stop}
  trading     {start|stop}
  status      show status of all subsystems
  status_all  show status (machine-readable)
  health      show health of all subsystems
USAGE
        ;;
esac
