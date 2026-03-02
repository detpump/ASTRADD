from __future__ import annotations

import json
from datetime import datetime


def build_internal_anomalies_payload(load_state_fn):
    """Internal function to get anomalies (used by both API and WebSocket)."""
    anomalies = []
    try:
        state = load_state_fn()

        # Check equity
        equity = state.get('equity', 0)
        if equity < 10:
            anomalies.append({
                'type': 'LOW_EQUITY', 'severity': 'critical',
                'message': f'Equity is critically low: ${equity:.2f}',
                'timestamp': datetime.now().isoformat()
            })
        elif equity < 50:
            anomalies.append({
                'type': 'LOW_EQUITY', 'severity': 'warning',
                'message': f'Equity is low: ${equity:.2f}',
                'timestamp': datetime.now().isoformat()
            })

        # Check drawdown
        drawdown = state.get('drawdown_pct', 0)
        if drawdown > 10:
            anomalies.append({
                'type': 'HIGH_DRAWDOWN', 'severity': 'critical',
                'message': f'Drawdown exceeded 10%: {drawdown:.2f}%',
                'timestamp': datetime.now().isoformat()
            })
        elif drawdown > 5:
            anomalies.append({
                'type': 'HIGH_DRAWDOWN', 'severity': 'warning',
                'message': f'Drawdown elevated: {drawdown:.2f}%',
                'timestamp': datetime.now().isoformat()
            })

        # Check system status
        if not state.get('running', False):
            anomalies.append({
                'type': 'SYSTEM_STOPPED', 'severity': 'info',
                'message': 'Trading system is stopped',
                'timestamp': datetime.now().isoformat()
            })

    except Exception as e:
        print(f"Anomaly detection error: {e}")

    return {
        'anomalies': anomalies,
        'count': len(anomalies),
        'timestamp': datetime.now().isoformat()
    }


def build_internal_anomalies_payload_fallback(load_state_fn):
    """Fallback internal function to get anomalies."""
    anomalies = []
    try:
        state = load_state_fn()
        equity = state.get('equity', 0)
        if equity < 10:
            anomalies.append({
                'type': 'LOW_EQUITY', 'severity': 'critical',
                'message': f'Equity is critically low: ${equity:.2f}',
                'timestamp': datetime.now().isoformat()
            })
        elif equity < 50:
            anomalies.append({
                'type': 'LOW_EQUITY', 'severity': 'warning',
                'message': f'Equity is low: ${equity:.2f}',
                'timestamp': datetime.now().isoformat()
            })
        if not state.get('running', False):
            anomalies.append({
                'type': 'SYSTEM_STOPPED', 'severity': 'info',
                'message': 'Trading system is stopped',
                'timestamp': datetime.now().isoformat()
            })
    except Exception as e:
        print(f"Anomaly detection error: {e}")
    return {'anomalies': anomalies, 'count': len(anomalies), 'timestamp': datetime.now().isoformat()}


def build_anomalies_payload(load_state_fn, base_dir, subprocess_module):
    """Detect anomalies in trading system metrics."""
    anomalies = []

    # Load current state
    state = load_state_fn()

    # Check 1: Unusual equity change
    equity = state.get('equity', 0)
    if equity < 10:
        anomalies.append({
            'type': 'LOW_EQUITY',
            'severity': 'critical',
            'message': f'Equity is critically low: ${equity:.2f}',
            'timestamp': datetime.now().isoformat()
        })
    elif equity < 50:
        anomalies.append({
            'type': 'LOW_EQUITY',
            'severity': 'warning',
            'message': f'Equity is low: ${equity:.2f}',
            'timestamp': datetime.now().isoformat()
        })

    # Check 2: High drawdown
    drawdown = state.get('drawdown_pct', 0)
    if drawdown > 10:
        anomalies.append({
            'type': 'HIGH_DRAWDOWN',
            'severity': 'critical',
            'message': f'Drawdown exceeded 10%: {drawdown:.2f}%',
            'timestamp': datetime.now().isoformat()
        })
    elif drawdown > 5:
        anomalies.append({
            'type': 'HIGH_DRAWDOWN',
            'severity': 'warning',
            'message': f'Drawdown elevated: {drawdown:.2f}%',
            'timestamp': datetime.now().isoformat()
        })

    # Check 3: No trades executed recently
    loop_count = state.get('loop_count', 0)
    if loop_count == 0:
        anomalies.append({
            'type': 'NO_LOOPS',
            'severity': 'warning',
            'message': 'Trading loop has not executed',
            'timestamp': datetime.now().isoformat()
        })

    # Check 4: Risk level
    try:
        risk_file = base_dir / 'logs' / 'risk_state.json'
        if risk_file.exists():
            with open(risk_file, 'r') as f:
                risk_data = json.load(f)
                risk_level = risk_data.get('risk_level', 'NORMAL')
                if risk_level == 'CRITICAL':
                    anomalies.append({
                        'type': 'RISK_CRITICAL',
                        'severity': 'critical',
                        'message': 'Risk level is CRITICAL - trading halted',
                        'timestamp': datetime.now().isoformat()
                    })
                elif risk_level == 'HIGH':
                    anomalies.append({
                        'type': 'RISK_HIGH',
                        'severity': 'warning',
                        'message': 'Risk level is HIGH',
                        'timestamp': datetime.now().isoformat()
                    })
    except Exception:
        pass

    # Check 5: System not running
    if not state.get('running', False):
        anomalies.append({
            'type': 'SYSTEM_STOPPED',
            'severity': 'info',
            'message': 'Trading system is stopped',
            'timestamp': datetime.now().isoformat()
        })

    # Check 6: Circuit breaker status
    try:
        result = subprocess_module.run(
            ['bash', '-c', 'cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading && ./system_control.sh health'],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout + result.stderr
        if 'OPEN' in output or 'TRIPPED' in output:
            anomalies.append({
                'type': 'CIRCUIT_BREAKER',
                'severity': 'critical',
                'message': 'Circuit breaker is OPEN - trading disabled',
                'timestamp': datetime.now().isoformat()
            })
    except Exception:
        pass

    # Check 7: No positions when signals exist
    signals = state.get('last_signals', {})
    positions = state.get('positions', {})
    active_signals = [s for s in signals.values() if s and s.get('action') in ['BUY', 'SELL']]

    if active_signals and not positions:
        anomalies.append({
            'type': 'SIGNALS_NO_POSITION',
            'severity': 'warning',
            'message': f'{len(active_signals)} active signals but no open positions',
            'timestamp': datetime.now().isoformat()
        })

    return {
        'anomalies': anomalies,
        'count': len(anomalies),
        'critical_count': sum(1 for a in anomalies if a['severity'] == 'critical'),
        'warning_count': sum(1 for a in anomalies if a['severity'] == 'warning'),
        'timestamp': datetime.now().isoformat()
    }

