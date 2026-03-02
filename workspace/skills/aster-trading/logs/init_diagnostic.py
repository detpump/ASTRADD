#!/usr/bin/env python3
"""
Detailed initialization diagnostic - identifies where timeout occurs
"""
import sys
import os
import asyncio
import time
import json

# Setup path
sys.path.insert(0, 'workspace/skills/aster-trading/src')
os.chdir('/Users/FIRMAS/.openclaw')

print('='*70)
print('DETAILED INITIALIZATION DIAGNOSTIC')
print('='*70)
print()

# Test 1: Direct API Equity
print('[STEP 0] Testing Direct API Equity...')
from api.aster_api import reset_circuit_breaker, get_equity_total_usdt, get_api_health_status

reset_circuit_breaker()

try:
    equity = get_equity_total_usdt()
    print(f'       API Equity: ${equity:.2f}')
    if equity > 0:
        print('       ✓ API returns correct equity!')
    else:
        print('       ✗ API returning 0')
except Exception as e:
    print(f'       ✗ API Error: {e}')
    equity = 0

print()

# Test each initialization step separately
print('[STEP 1] Testing TradingConfig creation...')
from trading_system import TradingConfig
try:
    start = time.time()
    config = TradingConfig()
    elapsed = time.time() - start
    print(f'       ✓ TradingConfig created in {elapsed:.2f}s')
    print(f'         Symbols: {config.symbols}')
    print(f'         Intervals: {config.intervals}')
except Exception as e:
    print(f'       ✗ Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

print('[STEP 2] Testing TradingSystemV2 instantiation...')
from trading_system import TradingSystemV2
try:
    start = time.time()
    system = TradingSystemV2(config=config)
    elapsed = time.time() - start
    print(f'       ✓ TradingSystemV2 created in {elapsed:.2f}s')
except Exception as e:
    print(f'       ✗ Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

print('[STEP 3] Testing individual initialize() components with timing...')
print()

# Step 3a: Feature calculator initialization
print('  [3a] Feature calculator init...')
try:
    start = time.time()
    # This is what initialize() does for feature calculator
    system.feature_calc = type(system.feature_calc).__new__(type(system.feature_calc))
    from data.feature_engineering import FeatureCalculator
    system.feature_calc = FeatureCalculator()
    elapsed = time.time() - start
    print(f'         ✓ Feature calculator in {elapsed:.2f}s')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

# Step 3b: Risk guard initialization
print('  [3b] Risk guard init...')
try:
    start = time.time()
    system.risk_guard = system._init_risk_guard()
    elapsed = time.time() - start
    print(f'         ✓ Risk guard in {elapsed:.2f}s')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

# Step 3c: Market prices
print('  [3c] Market prices fetch...')
try:
    start = time.time()
    market_prices = system._get_market_prices()
    elapsed = time.time() - start
    print(f'         ✓ Market prices in {elapsed:.2f}s')
    print(f'           Prices: {market_prices}')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

# Step 3d: API Equity fetch
print('  [3d] API equity fetch...')
try:
    start = time.time()
    api_equity = system._get_api_equity()
    elapsed = time.time() - start
    print(f'         ✓ API equity in {elapsed:.2f}s: ${api_equity:.2f}')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

# Step 3e: State save (equity sync)
print('  [3e] State save (equity sync)...')
try:
    start = time.time()
    # Call _get_equity_for_state which does the equity sync
    equity_for_state = system._get_equity_for_state()
    elapsed = time.time() - start
    print(f'         ✓ Equity for state in {elapsed:.2f}s: ${equity_for_state:.2f}')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

# Step 3f: Full state save
print('  [3f] Full state save...')
try:
    start = time.time()
    system._save_state()
    elapsed = time.time() - start
    print(f'         ✓ State saved in {elapsed:.2f}s')
except Exception as e:
    print(f'         ✗ Error: {e}')
    elapsed = time.time() - start
    print(f'         (took {elapsed:.2f}s before error)')

print()

# Check state files after partial initialization
print('[CHECK] State files after partial init:')
try:
    state_file = 'workspace/skills/aster-trading/logs/v2_state.json'
    risk_file = 'workspace/skills/aster-trading/logs/risk_state.json'
    
    with open(state_file) as f:
        state = json.load(f)
        print(f'       v2_state equity: ${state.get("equity", 0):.2f}')
    
    with open(risk_file) as f:
        risk = json.load(f)
        print(f'       risk_state equity: ${risk.get("equity", 0):.2f}')
except Exception as e:
    print(f'       ✗ Error reading state files: {e}')

print()

# Step 3g: Kline fetch - this is likely the timeout culprit
print('  [3g] Kline fetch (THIS IS LIKELY THE TIMEOUT CULPRIT)...')
print('       Testing single kline fetch with 10s timeout...')

async def test_klines():
    from api.aster_api import get_klines
    try:
        start = time.time()
        # Try single symbol/interval first
        klines = await asyncio.wait_for(
            asyncio.to_thread(get_klines, 'BTCUSDT', '1m', 50),
            timeout=10.0
        )
        elapsed = time.time() - start
        print(f'         ✓ BTCUSDT 1m in {elapsed:.2f}s: {len(klines)} klines')
        return True
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        print(f'         ✗ TIMEOUT after {elapsed:.2f}s fetching BTCUSDT 1m')
        return False
    except Exception as e:
        elapsed = time.time() - start
        print(f'         ✗ Error after {elapsed:.2f}s: {e}')
        return False

result = asyncio.run(test_klines())

print()

# Final state check
print('[FINAL CHECK] State files:')
try:
    state_file = 'workspace/skills/aster-trading/logs/v2_state.json'
    risk_file = 'workspace/skills/aster-trading/logs/risk_state.json'
    
    with open(state_file) as f:
        state = json.load(f)
        print(f'       v2_state equity: ${state.get("equity", 0):.2f}')
        print(f'       v2_state running: {state.get("running", False)}')
    
    with open(risk_file) as f:
        risk = json.load(f)
        print(f'       risk_state equity: ${risk.get("equity", 0):.2f}')
        
    if state.get('equity', 0) > 0 or risk.get('equity', 0) > 0:
        print()
        print('✓ STATE FILES UPDATED WITH CORRECT EQUITY!')
except Exception as e:
    print(f'       ✗ Error: {e}')

print()
print('='*70)
print('DIAGNOSTIC COMPLETE')
print('='*70)
