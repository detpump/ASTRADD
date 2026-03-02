#!/usr/bin/env python3
"""
Diagnostic test to trace equity flow through trading system.
Save output to file to avoid overwhelming the conversation.
"""
import sys
import os

sys.path.insert(0, 'workspace/skills/aster-trading/src')
os.chdir('/Users/FIRMAS/.openclaw')


def run_diagnostic() -> str:
    output = []

    def log(msg: str) -> None:
        output.append(msg)

    log('=== DIAGNOSTIC TEST: Tracing Equity Flow ===')
    log('')

    # Reset circuit breaker first
    from api.aster_api import reset_circuit_breaker, get_equity_total_usdt, get_api_health_status, _make_signed_request
    reset_circuit_breaker()

    log('1. Testing Direct API Call...')
    equity = get_equity_total_usdt()
    log(f'   Direct API call result: ${equity:.2f}')
    log('')

    log('2. Testing via TradingSystem internal method...')
    # Simulate what trading_system does
    from trading_system import TradingSystemV2, TradingConfig

    config = TradingConfig()
    system = TradingSystemV2(config=config)

    # Ensure risk guard exists for diagnostics
    if system.risk_guard is None:
        from services.risk_service import get_shared_risk_guard
        system.risk_guard = get_shared_risk_guard()

    # Call _get_api_equity directly like init does
    equity_via_system = system._get_api_equity()
    log(f'   TradingSystem._get_api_equity(): ${equity_via_system:.2f}')
    log('')

    log('3. Testing _get_equity_for_state (used in _save_state)...')
    equity_for_state = system._get_equity_for_state()
    log(f'   _get_equity_for_state(): ${equity_for_state:.2f}')
    log('')

    log('4. Simulating _save_state flow...')
    # This is what happens in _save_state()
    api_equity = system._get_equity_for_state()
    log(f'   Equity from _get_equity_for_state: ${api_equity:.2f}')

    # Load risk data
    risk_data = system._load_risk_data_for_state()
    log(f'   Risk data equity: ${risk_data.get("equity", 0):.2f}')

    # Now let's manually test updating risk guard
    log('')
    log('5. Testing RiskGuard equity update...')
    system.risk_guard.update_equity(equity_for_state)
    status = system.risk_guard.get_status()
    log(f'   RiskGuard status equity: ${status.get("equity", 0):.2f}')
    log('')

    # Now save state and check
    log('6. Saving state and checking files...')
    system._save_state()

    # Read state snapshots (DB-first; legacy file fallback kept for diagnostics)
    import json
    from state.state_service import state_service

    base_dir = os.path.join('workspace', 'skills', 'aster-trading')
    state_file = os.path.join(base_dir, 'logs', 'v2_state.json')
    risk_file = os.path.join(base_dir, 'logs', 'risk_state.json')

    # v2_state equivalent: derive from DB system state when file is absent
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        state_equity = float(state.get("equity", 0) or 0)
    else:
        rs = state_service.get_risk_state()
        state_equity = float((rs.account_equity if rs else 0) or 0)
    log(f'   v2_state.json equity: ${state_equity:.2f}')

    # risk_state equivalent: derive from DB risk snapshot when file is absent
    if os.path.exists(risk_file):
        with open(risk_file) as f:
            risk = json.load(f)
        risk_equity = float(risk.get("equity", 0) or 0)
    else:
        rs = state_service.get_risk_state()
        risk_equity = float((rs.account_equity if rs else 0) or 0)
    log(f'   risk_state.json equity: ${risk_equity:.2f}')

    log('')
    log('=== DIAGNOSIS COMPLETE ===')

    # Write output to file
    output_text = '\n'.join(output)
    with open(os.path.join(base_dir, 'logs', 'diagnostic_test.txt'), 'w') as f:
        f.write(output_text)

    return output_text


if __name__ == '__main__':
    print(run_diagnostic())
