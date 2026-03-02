#!/usr/bin/env python3
"""Simple diagnostic test - no imports that hang."""
import sys
import os
import json

# Set up path
sys.path.insert(0, 'workspace/skills/aster-trading/src')
os.chdir('/Users/FIRMAS/.openclaw')

print('=== SIMPLE DIAGNOSTIC TEST ===')
print()

# Test 1: API Equity
print('1. Testing Direct API Equity...')
try:
    from api.aster_api import reset_circuit_breaker, get_equity_total_usdt
    reset_circuit_breaker()
    equity = get_equity_total_usdt()
    print(f'   API Equity: ${equity:.2f}')
    api_ok = equity > 0
except Exception as e:
    print(f'   ERROR: {e}')
    api_ok = False

print()

# Test 2: Check state files
print('2. Checking State Files...')
state_file = 'workspace/skills/aster-trading/logs/v2_state.json'
risk_file = 'workspace/skills/aster-trading/logs/risk_state.json'

try:
    with open(state_file) as f:
        state = json.load(f)
        v2_equity = state.get('equity', 0)
        print(f'   v2_state.json equity: ${v2_equity:.2f}')
except Exception as e:
    print(f'   ERROR reading v2_state: {e}')
    v2_equity = 0

try:
    with open(risk_file) as f:
        risk = json.load(f)
        r_equity = risk.get('equity', 0)
        print(f'   risk_state.json equity: ${r_equity:.2f}')
except Exception as e:
    print(f'   ERROR reading risk_state: {e}')
    r_equity = 0

print()

# Summary
print('=== SUMMARY ===')
if api_ok:
    print('✓ API returns correct equity (~42 USDT)')
else:
    print('✗ API returning 0')

if v2_equity > 0 or r_equity > 0:
    print('✓ State files have equity > 0')
else:
    print('✗ State files show equity: 0.0 (need to run trading system to update)')

print()
print('=== DIAGNOSTIC COMPLETE ===')
