#!/bin/bash
# Heartbeat ping script for BetterStack monitoring
# This script updates the heartbeat file to signal the system is running

HEARTBEAT_FILE="/Users/FIRMAS/.openclaw/logs/history/heartbeat.jsonl"

# Check if the heartbeat file exists, if not create it
if [ ! -f "$HEARTBEAT_FILE" ]; then
    touch "$HEARTBEAT_FILE"
fi

# Add a heartbeat ping entry
echo "{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"source\": \"cron_heartbeat_ping\", \"status\": \"alive\"}" >> "$HEARTBEAT_FILE"

# Exit successfully
exit 0
