#!/bin/bash
# =============================================================================
# Auto-start Script for Aster Trading System
# =============================================================================
# This script is called by launchd to start the trading system on boot
# Usage: 
#   1. Copy this to ~/Library/LaunchAgents/com.aster.tradingv2.plist
#   2. Run: launchctl load ~/Library/LaunchAgents/com.aster.tradingv2.plist
# =============================================================================

TRADING_DIR="/Users/FIRMAS/.openclaw/workspace/skills/aster-trading"
CONTROL_SCRIPT="$TRADING_DIR/system_control.sh"

# Log startup
echo "$(date): Auto-starting Aster Trading System..." >> "$TRADING_DIR/logs/autostart.log"

# Start all systems in continuous mode
cd "$TRADING_DIR"
"$CONTROL_SCRIPT" start_all >> "$TRADING_DIR/logs/autostart.log" 2>&1

echo "$(date): Auto-start complete" >> "$TRADING_DIR/logs/autostart.log"
