#!/usr/bin/env python3
"""Test API health and equity functions"""
import sys
sys.path.insert(0, 'src')

from api.aster_api import (
    get_equity_total_usdt, 
    get_balance_v3, 
    get_positions_v3, 
    get_api_health_status, 
    is_api_healthy, 
    reset_circuit_breaker
)

# First check API health status
print('=== API Health Status ===')
status = get_api_health_status()
print(f'Circuit breaker status: {status}')
print(f'is_api_healthy: {is_api_healthy()}')

# Reset circuit breaker if stuck
if not is_api_healthy():
    print('Resetting circuit breaker...')
    reset_circuit_breaker()

# Try to get balance
print('\n=== Testing get_balance_v3 ===')
try:
    balance = get_balance_v3()
    print(f'Balance result: {balance}')
except Exception as e:
    print(f'Error: {e}')

# Try to get equity
print('\n=== Testing get_equity_total_usdt ===')
try:
    equity = get_equity_total_usdt()
    print(f'Equity: {equity}')
except Exception as e:
    print(f'Error: {e}')
