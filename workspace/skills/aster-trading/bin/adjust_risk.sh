#!/bin/bash
# Cron script for dynamic risk adjustment
# Run this every 3 hours: 0 */3 * * *

cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading

# Activate virtual environment and run
source /Users/FIRMAS/.openclaw/.venv/bin/activate

python3 -c "
import sys
sys.path.insert(0, './src')
from risk.dynamic_risk_adjuster import DynamicRiskAdjuster, AdjustmentConfig

config = AdjustmentConfig(
    risk_config_path='./config/risk_config.json',
    state_path='./logs/v2_state.json',
    trade_log_path='./logs/trade_log.jsonl',
    cooldown_hours=3.0,
    max_calls_per_day=8
)

adjuster = DynamicRiskAdjuster(config)
result = adjuster.adjust()

if result:
    print('Risk adjusted successfully')
    print(adjuster.get_status())
else:
    print('No adjustment made (rate limited or error)')
"
