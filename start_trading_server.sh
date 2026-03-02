#!/bin/zsh

# OpenClaw Trading Server Startup Script
# This script activates the virtual environment and coordinates all subsystems

# Base directories
ROOT_DIR="/Users/FIRMAS/.openclaw"
PROJECT_DIR="$ROOT_DIR/workspace/skills/aster-trading"
CONTROL_SCRIPT="$PROJECT_DIR/system_control.sh"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

# Configure PYTHONPATH for dashboard imports (used by downstream scripts)
export PYTHONPATH="$PROJECT_DIR:$PROJECT_DIR/src:$PYTHONPATH"

# Ensure we run from project root
cd "$ROOT_DIR"

# Activate virtual environment if available
if [ -f "$VENV_ACTIVATE" ]; then
    source "$VENV_ACTIVATE"
fi

# Kill any existing OpenClaw processes
pkill -9 -f openclaw || true
pkill -9 -f "dashboard/server.py" || true

# Clean up any stale locks
rm -f /tmp/openclaw/*.lock 2>/dev/null || true
rm -f ~/.openclaw/*.lock 2>/dev/null || true

# Start the gateway in the background
echo "Starting OpenClaw Gateway on port 18789..."
"$VENV_PYTHON" -m openclaw gateway --port 18789 --verbose --allow-unconfigured > "$ROOT_DIR/logs/gateway.log" 2>&1 &
GATEWAY_PID=$!
echo "Gateway started with PID: $GATEWAY_PID"

# Wait a moment for gateway to initialize
sleep 2

# Start the dashboard server in the background (ensure dashboard package is importable)
echo "Starting Dashboard Server on port 5001..."
cd "$PROJECT_DIR"
"$VENV_PYTHON" dashboard/server.py > "$ROOT_DIR/logs/dashboard.log" 2>&1 &
DASHBOARD_PID=$!
echo "Dashboard started with PID: $DASHBOARD_PID"

# Start trading subsystems via system_control (spins up listener + trading engine)
if [ -x "$CONTROL_SCRIPT" ]; then
    echo "Starting trading subsystems via system_control.sh..."
    "$CONTROL_SCRIPT" start_all
else
    echo "WARNING: system_control.sh not found or not executable; trading subsystems not started"
fi

echo "OpenClaw trading system started successfully"
echo "Gateway PID: $GATEWAY_PID"
echo "Dashboard PID: $DASHBOARD_PID"

# Keep the script running to maintain the background processes
wait
