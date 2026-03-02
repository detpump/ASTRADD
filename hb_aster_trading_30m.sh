#!/usr/bin/env bash

cd /Users/FIRMAS/.openclaw || exit 1

JOB_ID="9ccb5502-d9ea-4f14-8d3a-141f3e916f65"
LOG_FILE="/Users/FIRMAS/.openclaw/logs/hb_trading_30m_wrapper.log"

echo "[$(date -Iseconds)] Lanzando cron $JOB_ID" >> "$LOG_FILE"
openclaw cron run "$JOB_ID" >> "$LOG_FILE" 2>&1
STATUS=$?

if [ $STATUS -eq 0 ]; then
  echo "[$(date -Iseconds)] Cron OK" >> "$LOG_FILE"
else
  echo "[$(date -Iseconds)] Cron ERROR (status=$STATUS)" >> "$LOG_FILE"
fi

exit $STATUS
