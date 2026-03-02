#!/usr/bin/env python3
"""
Dashboard API Server for Aster Trading System V2
Serves the dashboard and provides API endpoints for system control
"""

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, request

from dashboard.context import (
    BASE_DIR,
    CACHE_MAX_AGE_SECONDS,
    CONFIG_DIR,
    CRON_JOBS_FILE,
    DATA_SOURCE_FLAGS,
    EQUITY_FILE,
    HISTORY_DIR,
    LOG_STRUCTURED_RE,
    ROOT_DIR,
    ROOT_LOGS_DIR,
    SIGNAL_LOG_FILES,
    SIGNAL_STRENGTH_RE,
    STATE_FILE,
    TRADES_FILE,
    USE_LEGACY_STATE_CACHE,
    _first_non_none,
    _load_json_file,
    _parse_timestamp_ms,
    _safe_float,
    _safe_int,
    _tail_lines,
    _to_iso,
    get_enabled_symbols,
    get_global_trade_tracker,
    load_state,
    save_state,
    set_global_trade_tracker,
)

# Try to import Flask-SocketIO, fallback to regular Flask if not available
try:
    from flask_socketio import SocketIO, emit
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    SocketIO = None
    emit = None

sys.path.insert(0, str(BASE_DIR / 'src'))  # For API imports
sys.path.insert(0, str(BASE_DIR))  # For trading_system module

try:
    from trade_execution_tracker import TradeExecutionTracker
    TRACKER_AVAILABLE = True
except ImportError:
    TRACKER_AVAILABLE = False
    TradeExecutionTracker = None

try:
    from state.dashboard_cache import load_dashboard_snapshot
    DASHBOARD_CACHE_AVAILABLE = True
except ImportError:
    DASHBOARD_CACHE_AVAILABLE = False
    load_dashboard_snapshot = None

try:
    from services.risk_service import get_normalized_risk_config
except ImportError:
    get_normalized_risk_config = None

app = Flask(__name__, template_folder='.', static_folder='.')
logger = app.logger
logger.setLevel(logging.INFO)

# Legacy state file path (deprecated: DB is the source of truth)
# STATE_FILE provided by context

# History files and canonical dirs via context
PYTHON_BIN = ROOT_DIR / '.venv' / 'bin' / 'python3'
SYSTEM_CONTROL_SCRIPT = BASE_DIR / 'system_control.sh'
CONTROL_SCRIPT = BASE_DIR / 'control.sh'
SYSTEM_INIT_LOG = BASE_DIR / 'logs' / 'system_init.log'
ACCOUNT_LISTENER_PID_FILE = BASE_DIR / 'account_listener.pid'
MANAGE_BRACKETS_PID_FILE = BASE_DIR / 'manage_brackets.pid'
ACCOUNT_LISTENER_LOG = BASE_DIR / 'logs' / 'account_stream.log'
MANAGE_BRACKETS_LOG = BASE_DIR / 'logs' / 'manage_brackets.log'
# Same ordering used by the dashboard UI for subsystem controls
SUBSYSTEMS = ['gateway', 'cron', 'trading', 'account-listener']

# Primary runtime logs for signal/decision truth and log sources
LOG_SOURCE_FILES = {
    'TRADING': [BASE_DIR / 'logs' / 'v2_output.log', BASE_DIR / 'logs' / 'v2_system.log'],
    'DEBUG': [BASE_DIR / 'logs' / 'v2_debug.log'],
    'RISK': [BASE_DIR / 'logs' / 'risk_scheduler.log'],
    'GATEWAY': [ROOT_LOGS_DIR / 'gateway.log'],
    'SYSTEM': [ROOT_LOGS_DIR / 'dashboard.log', ROOT_LOGS_DIR / 'openclaw.log']
}

# System control state
system_control = {
    'running': False,
    'start_time': 0,
    'process': None
}

# Metrics cache for anomaly detection
metrics_cache = {
    'equity_history': [],
    'trade_frequency': [],
    'error_count': 0,
    'last_trade_time': None,
    'anomalies': []
}

# Cache settings provided by context

# Global trade tracker instance for dashboard access
# This will be initialized when the trading system connects
_global_trade_tracker = None


# Regex helpers provided by context


def _load_shared_risk_config():
    """Load risk config via shared adapter (runtime + dashboard), with safe fallback."""
    if get_normalized_risk_config is not None:
        try:
            adapted = get_normalized_risk_config()
            normalized = adapted.as_dict()
            raw = adapted.raw_config if isinstance(adapted.raw_config, dict) else normalized
            return normalized, raw
        except Exception as exc:
            print(f"Error loading shared risk config adapter: {exc}")

    risk_cfg = _load_json_file(CONFIG_DIR / 'risk_config.json', default={})
    if not isinstance(risk_cfg, dict):
        risk_cfg = {}
    return risk_cfg, risk_cfg


def _safe_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return int(value)
    except Exception:
        return default


def _first_non_none(*values):
    for val in values:
        if val is not None:
            return val
    return None


def _parse_timestamp_ms(value):
    """Parse mixed timestamp formats into epoch milliseconds."""
    if value is None:
        return 0

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return 0
        return int(numeric if numeric > 1e11 else numeric * 1000)

    text = str(value).strip()
    if not text:
        return 0

    if text.isdigit():
        numeric = int(text)
        return int(numeric if numeric > 1e11 else numeric * 1000)

    normalized = text.replace('Z', '+00:00')
    for fmt in (
        '%Y-%m-%d %H:%M:%S,%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S,%f%z',
        '%Y-%m-%dT%H:%M:%S%z',
    ):
        try:
            dt = datetime.strptime(normalized, fmt)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass

    try:
        dt = datetime.fromisoformat(normalized)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _to_iso(value):
    ms = _parse_timestamp_ms(value)
    if ms <= 0:
        return ''
    return datetime.utcfromtimestamp(ms / 1000).isoformat() + 'Z'


def _tail_lines(path: Path, limit=300):
    if not path.exists():
        return []
    try:
        with open(path, 'r', errors='ignore') as f:
            return list(deque((ln.rstrip('\n') for ln in f), maxlen=limit))
    except Exception:
        return []


def _load_json_file(path: Path, default=None):
    try:
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {} if default is None else default


def _error_response(error: str, message: str, status_code: int, details: dict | None = None):
    payload = {
        'error': error,
        'message': message,
    }
    if details:
        payload['details'] = details
    return jsonify(payload), status_code


def _parse_limit_arg(name='limit', default=500, minimum=1, maximum=5000):
    raw_value = request.args.get(name, None)
    if raw_value is None or raw_value == '':
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid '{name}' parameter. Expected integer in range [{minimum}, {maximum}]")

    if value < minimum or value > maximum:
        raise ValueError(f"Invalid '{name}' parameter. Expected integer in range [{minimum}, {maximum}]")

    return value


def _parse_bool_arg(name: str, default: bool = True) -> bool:
    raw_value = request.args.get(name, None)
    if raw_value is None:
        return default

    value = str(raw_value).strip().lower()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f"Invalid '{name}' parameter. Expected boolean true/false")


def _parse_symbol_arg(name='symbol'):
    symbol = str(request.args.get(name, '') or '').strip().upper()
    if not symbol:
        return None
    if not re.match(r'^[A-Z0-9_-]{2,30}$', symbol):
        raise ValueError(f"Invalid '{name}' parameter format")
    return symbol


















































def _redact_sensitive(obj):
    sensitive_tokens = ('key', 'secret', 'token', 'private', 'password', 'signer')
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if any(tok in str(k).lower() for tok in sensitive_tokens):
                redacted[k] = '***REDACTED***'
            else:
                redacted[k] = _redact_sensitive(v)
        return redacted
    if isinstance(obj, list):
        return [_redact_sensitive(x) for x in obj]
    return obj




















def _collect_logs_by_source(limit_per_source=250):
    containers = {}
    combined = []

    for source, candidate_files in LOG_SOURCE_FILES.items():
        src_entries = []
        for log_file in candidate_files:
            for line in _tail_lines(log_file, limit=limit_per_source):
                parsed = _parse_log_line(line, source)
                if parsed:
                    parsed['file'] = str(log_file)
                    src_entries.append(parsed)
        src_entries.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
        containers[source] = src_entries[:limit_per_source]
        combined.extend(src_entries[:limit_per_source])

    combined.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
    return containers, combined


def set_global_trade_tracker(tracker):
    """Set the global trade tracker instance from the trading system"""
    global _global_trade_tracker
    _global_trade_tracker = tracker
    print(f"Global trade tracker set: {type(tracker)}")


def get_global_trade_tracker():
    """Get the global trade tracker instance"""
    global _global_trade_tracker
    
    # If tracker not set, try to initialize from trading system module
    if _global_trade_tracker is None and TRACKER_AVAILABLE:
        try:
            # Try to import from trading_system if it's running in same process
            import sys
            if str(BASE_DIR / 'src') not in sys.path:
                sys.path.insert(0, str(BASE_DIR / 'src'))
            if str(BASE_DIR) not in sys.path:
                sys.path.insert(0, str(BASE_DIR))
            
            # Try to get the tracker from trading_system module
            from trading_system import TradingSystemV2
            # Note: This will only work if trading system is in same process
            # Otherwise, we'll rely on state file data
        except ImportError:
            pass
    
    return _global_trade_tracker


@app.route('/api/trading/set-tracker', methods=['POST'])
def set_tracker_endpoint():
    """Endpoint to set the trade tracker from an external source (e.g., trading system)"""
    try:
        data = request.get_json() or {}
        tracker_type = data.get('type', '')
        
        if tracker_type == 'initialize' and TRACKER_AVAILABLE:
            # Initialize a new tracker
            tracker = TradeExecutionTracker()
            set_global_trade_tracker(tracker)
            return jsonify({'success': True, 'message': 'Tracker initialized'})
        else:
            return jsonify({'success': False, 'error': 'Tracker type not supported'}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def load_state():
    """Load trading system state from DB (single source of truth)."""
    try:
        import sys
        # Add src to path for imports
        src_dir = str(BASE_DIR / 'src')
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        
        from state.state_service import state_service
        from state.models import SystemState

        now_ms = int(time.time() * 1000)
        default_state = {
            'running': False,
            'start_time': 0,
            'loop_count': 0,
            'trades_executed': 0,
            'equity': 0,
            'daily_pnl': 0,
            'drawdown_pct': 0,
            'open_positions': 0,
            'positions': {},
            'last_signals': {},
            'market': {},
            'timestamp': now_ms,
            'execution_tracker': {},
            'healing_metrics': {},
            'symbols': [],
            'recent_errors': [],
        }

        state = dict(default_state)

        system_state = state_service.get_system_state()
        if system_state:
            state.update({
                'running': bool(system_state.running),
                'start_time': system_state.start_time or 0,
                'loop_count': system_state.loop_count or 0,
                'trades_executed': system_state.trades_executed or 0,
                'last_signals': system_state.last_signals or {},
                'market': system_state.market_data or {},
                'healing_metrics': system_state.healing_metrics or {},
                'symbols': system_state.enabled_symbols or [],
                'recent_errors': system_state.recent_errors or [],
            })
        
        # DB snapshots
        risk = state_service.get_risk_state()
        db_positions = state_service.get_positions()
        tracker = state_service.get_execution_tracker()
        last_signals = state_service.get_last_signals()
        market_prices = state_service.get_market_prices()
        
        positions_dict = {}
        for p in db_positions:
            positions_dict[p.symbol] = {
                'symbol': p.symbol,
                'side': p.side,
                'size': p.quantity,
                'entry_price': p.entry_price,
                'unrealized_pnl': p.unrealized_pnl,
                'notional': p.notional,
                'mark_price': p.mark_price,
                'leverage': p.leverage,
                'open_time': p.open_time,
            }

        state['positions'] = positions_dict
        state['open_positions'] = len(db_positions)

        if isinstance(last_signals, dict) and last_signals:
            state['last_signals'] = last_signals
        if isinstance(market_prices, dict) and market_prices:
            state['market'] = market_prices

        if tracker:
            state['execution_tracker'] = {
                'total_signals': tracker.total_signals or 0,
                'total_orders': tracker.total_orders or 0,
                'active_orders': tracker.active_orders or 0,
                'status_distribution': tracker.status_distribution or {},
                'active_order_symbols': tracker.active_order_symbols or [],
            }
            # Only backfill counters if missing in system_state snapshot.
            if not state.get('loop_count'):
                state['loop_count'] = tracker.total_signals or 0
            if not state.get('trades_executed'):
                state['trades_executed'] = tracker.total_orders or 0

        if risk:
            state['equity'] = risk.account_equity or 0
            state['daily_pnl'] = risk.daily_pnl or 0
            state['drawdown_pct'] = risk.drawdown_pct or 0
            if risk.risk_limits:
                state['risk_level'] = risk.risk_limits.get('risk_level', 'NORMAL')
                state['equity_peak'] = risk.risk_limits.get('equity_peak', 0)
                state['equity_start_day'] = risk.risk_limits.get('equity_start_day', 0)

        state['timestamp'] = now_ms
        return state
    except Exception as e:
        print(f"Error loading state from DB: {e}")

    return {
        'running': False,
        'start_time': 0,
        'loop_count': 0,
        'trades_executed': 0,
        'equity': 0,
        'daily_pnl': 0,
        'drawdown_pct': 0,
        'open_positions': 0,
        'positions': {},
        'last_signals': {},
        'market': {},
        'timestamp': int(time.time() * 1000)
    }


def save_state(state):
    """Persist dashboard/system state to DB."""
    try:
        from state.state_service import state_service
        from state.models import SystemState

        payload = SystemState(
            running=bool(state.get('running', False)),
            start_time=state.get('start_time'),
            loop_count=_safe_int(state.get('loop_count', 0), 0),
            trades_executed=_safe_int(state.get('trades_executed', 0), 0),
            recent_errors=state.get('recent_errors') if isinstance(state.get('recent_errors'), list) else [],
            enabled_symbols=state.get('symbols') if isinstance(state.get('symbols'), list) else [],
            last_signals=state.get('last_signals') if isinstance(state.get('last_signals'), dict) else {},
            market_data=state.get('market') if isinstance(state.get('market'), dict) else {},
            healing_metrics=state.get('healing_metrics') if isinstance(state.get('healing_metrics'), dict) else {},
        )
        state_service.upsert_system_state(payload)
    except Exception as e:
        print(f"Error saving state: {e}")


from dashboard.state import (
    _classify_order,
    _get_truth_open_orders,
    _get_truth_positions,
    _is_open_position,
    _normalize_position,
)
from dashboard.metrics import (
    _build_position_targets,
    _calculate_max_drawdown_pct,
    _extract_latest_signal_reason_map,
    _extract_signal_strength_history,
    _filter_valid_equity_history,
    _load_equity_history,
    _load_equity_series_for_charts,
    _load_normalized_trades,
    _recalculate_drawdown_pct,
)
from dashboard.transparency import (
    _build_execution_latency_snapshot,
    _build_reconciliation_snapshot,
    _build_risk_transparency_snapshot,
    _build_system_cadence,
)
from dashboard.control import _append_system_init_log, _command_response, _control_subsystem
from dashboard.anomalies import (
    build_anomalies_payload,
    build_internal_anomalies_payload,
    build_internal_anomalies_payload_fallback,
)
from dashboard.websocket import register_socketio_handlers

# ========================================
# Routes
# ========================================

@app.route('/')
def index():
    """Serve dashboard"""
    return render_template('index.html')

@app.route('/validation')
def validation():
    """Serve validation dashboard"""
    return render_template('validation.html')


@app.route('/api/trading/state')
def get_state():
    """Get current trading system state"""
    state = load_state()
    enabled_symbols = get_enabled_symbols()

    truth_positions = _get_truth_positions(enabled_symbols)
    positions_map = {p.get('symbol'): p for p in truth_positions if p.get('symbol')}

    risk_level = state.get('risk_level') or 'NORMAL'

    equity = _safe_float(state.get('equity'), 0)
    daily_pnl = _safe_float(state.get('daily_pnl'), 0)
    weekly_pnl = _safe_float(state.get('weekly_pnl'), 0)
    drawdown_pct = _recalculate_drawdown_pct(current_equity=equity if equity > 0 else None)
    risk_snapshot = _build_risk_transparency_snapshot(enabled_symbols=enabled_symbols)

    api_state = {
        'running': state.get('running', False),
        'start_time': state.get('start_time', 0),
        'loop_count': state.get('loop_count', 0),
        'trades_executed': state.get('trades_executed', 0),
        'equity': equity,
        'daily_pnl': daily_pnl,
        'weekly_pnl': weekly_pnl,
        'drawdown_pct': drawdown_pct,
        'open_positions': len(truth_positions),
        'positions': positions_map,
        'positions_list': truth_positions,
        'last_signals': state.get('last_signals', {}),
        'market': state.get('market', {}),
        'risk_level': risk_level,
        'symbols': enabled_symbols,
        'timestamp': state.get('timestamp', int(time.time() * 1000)),
        # Enhanced with new tracking data
        'execution_tracker': state.get('execution_tracker', {}),
        'healing_metrics': state.get('healing_metrics', {}),
        'circuit_breaker': state.get('circuit_breaker', {}),
        'risk_transparency': risk_snapshot,
        'data_sources': {
            'positions': DATA_SOURCE_FLAGS.get('positions', 'unknown'),
            'trades': str(TRADES_FILE),
            'equity': str(EQUITY_FILE),
            'risk': 'db:risk_state',
            'orders': DATA_SOURCE_FLAGS.get('orders', 'unknown')
        },
        'warnings': []
    }

    if not truth_positions:
        api_state['warnings'].append('No open positions from exchange/state')
    
    return jsonify(api_state)








@app.route('/api/subsystems/<string:name>', methods=['POST'])
def control_subsystem(name: str):
    name = name.lower()
    if name not in SUBSYSTEMS:
        return jsonify({'success': False, 'error': 'Invalid subsystem'}), 400
    return _control_subsystem(name)


def control_trading():
    return _control_subsystem('trading')


@app.route('/api/trading/start', methods=['POST'])
def start_trading():
    return _control_subsystem('trading')


@app.route('/api/trading/stop', methods=['POST'])
def stop_trading():
    return _control_subsystem('trading')


@app.route('/api/system/start_all', methods=['POST'])
def start_all():
    """Start all subsystems: gateway -> cron -> listener -> trading"""
    data = request.get_json(silent=True) or {}
    results = {}
    
    # Start gateway first
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh gateway start'],
        capture_output=True, text=True, timeout=30
    )
    results['gateway'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Wait for gateway to be ready
    time.sleep(2)
    
    # Start cron
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh cron start'],
        capture_output=True, text=True, timeout=30
    )
    results['cron'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Wait
    time.sleep(1)
    
    # Start listener explicitly
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh listener start'],
        capture_output=True, text=True, timeout=30
    )
    results['listener'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Start trading
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh trading start'],
        capture_output=True, text=True, timeout=30
    )
    results['trading'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    return {'success': True, 'results': results}


@app.route('/api/system/stop_all', methods=['POST'])
def stop_all():
    """Stop all subsystems in reverse order: trading -> listener -> cron -> gateway"""
    data = request.get_json(silent=True) or {}
    results = {}
    
    # Stop trading first
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh trading stop'],
        capture_output=True, text=True, timeout=30
    )
    results['trading'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Stop listener explicitly
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh listener stop'],
        capture_output=True, text=True, timeout=30
    )
    results['listener'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Stop cron
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh cron stop'],
        capture_output=True, text=True, timeout=30
    )
    results['cron'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    # Stop gateway last
    result = subprocess.run(
        ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh gateway stop'],
        capture_output=True, text=True, timeout=30
    )
    results['gateway'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    
    return {'success': True, 'results': results}


@app.route('/api/subsystems/account-listener', methods=['POST'])
def control_account_listener():
    """Control the account stream listener - start/stop/status"""
    # Use system_control.sh for consistency with other subsystems
    data = request.get_json(silent=True) or {}
    action = data.get('action', 'status').lower()

    # Use the same approach as _control_subsystem
    try:
        result = subprocess.run(
            ['bash', '-c', f'cd {BASE_DIR} && ./system_control.sh listener {action}'],
            capture_output=True, text=True, timeout=40
        )
        result_dict = {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        result_dict = {
            'success': False,
            'stdout': exc.stdout or '',
            'stderr': f'Timeout: {exc}',
            'returncode': -1,
        }
    except Exception as exc:
        result_dict = {
            'success': False,
            'stdout': '',
            'stderr': str(exc),
            'returncode': -1,
        }

    # Build metadata with PID info
    metadata = {
        'command': f'./system_control.sh listener {action}',
        'returncode': result_dict.get('returncode'),
        'pid_file': str(ACCOUNT_LISTENER_PID_FILE),
        'log': str(ACCOUNT_LISTENER_LOG),
        'timestamp': int(time.time() * 1000),
    }
    # Try to get actual PID if running
    pid_file = ACCOUNT_LISTENER_PID_FILE
    if pid_file.exists():
        try:
            metadata['actual_pid'] = int(pid_file.read_text().strip())
        except:
            pass

    return _command_response(result_dict, 'account-listener', action, metadata=metadata)


@app.route('/api/logs/system-init', methods=['GET'])
def get_system_init_log():
    entries = []
    try:
        if SYSTEM_INIT_LOG.exists():
            # Simple tail implementation without deque
            with open(SYSTEM_INIT_LOG, 'r', errors='ignore') as f:
                lines = f.readlines()
                # Get last 400 lines
                lines = lines[-400:] if len(lines) > 400 else lines
            for line in reversed(lines):
                try:
                    entries.append(json.loads(line.strip()))
                except Exception:
                    continue
    except Exception as exc:
        return jsonify({'entries': entries, 'error': str(exc)}), 500
    return jsonify({'entries': entries})


@app.route('/api/trading/status', methods=['GET'])
def get_system_status():
    """Get system status from DB-backed subsystem state."""
    try:
        from state.state_service import state_service
        components = {c.name: c for c in state_service.get_system_components(names=SUBSYSTEMS)}
        state = load_state()
        process_info = get_process_info()
        status_payload = {}
        for name in SUBSYSTEMS:
            component = components.get(name)
            proc = process_info.get(name, {})
            entry = {
                'status': component.status if component and component.status else 'UNKNOWN',
                'last_action': component.last_action if component else None,
                'updated_at': component.updated_at if component else None,
                'last_message': component.last_message if component else None,
                'metadata': component.metadata if component and component.metadata else {},
                'process': {
                    'pid': proc.get('pid'),
                    'uptime': proc.get('uptime'),
                    'status': proc.get('status'),
                }
            }
            status_payload[name] = entry
        status_payload['running'] = state.get('running', False)
        status_payload['loop_count'] = state.get('loop_count', 0)
        status_payload['trades_executed'] = state.get('trades_executed', 0)
        return jsonify(status_payload)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/trading/health', methods=['GET'])
def get_health():
    """Get detailed system health combining DB state and subsystem components."""
    try:
        from state.state_service import state_service
        state = load_state()
        components = {c.name: c for c in state_service.get_system_components(names=SUBSYSTEMS)}
        process_info = get_process_info()
        health = {
            'gateway': components.get('gateway').status if components.get('gateway') else 'UNKNOWN',
            'gateway_pid': process_info.get('gateway', {}).get('pid'),
            'cron': components.get('cron').status if components.get('cron') else 'UNKNOWN',
            'trading': components.get('trading').status if components.get('trading') else 'UNKNOWN',
            'trading_pid': process_info.get('trading', {}).get('pid'),
            'account-listener': components.get('account-listener').status if components.get('account-listener') else 'UNKNOWN',
            'account_listener_pid': process_info.get('account-listener', {}).get('pid'),
            'account_listener_status': process_info.get('account-listener', {}),
            'equity': _safe_float(state.get('equity', 0.0), 0.0),
            'loop_count': state.get('loop_count', 0),
            'trades_executed': state.get('trades_executed', 0),
            'running': state.get('running', False),
            'open_positions': state.get('open_positions', 0),
            'risk_level': state.get('risk_level', 'NORMAL'),
            'daily_pnl': _safe_float(state.get('daily_pnl', 0.0), 0.0),
            'processes': process_info,
            'circuit_breaker': state.get('circuit_breaker', {}),
        }
        return jsonify(health)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


def get_process_info():
    """Get detailed process information for all system components"""
    processes = {
        'gateway': {'pid': None, 'status': 'STOPPED', 'uptime': 0},
        'trading': {'pid': None, 'status': 'STOPPED', 'uptime': 0},
        'account-listener': {'pid': None, 'status': 'STOPPED', 'uptime': 0, 'pid_file': None, 'log': None},
    }

    # Account listener - check PID file first, then fallback to pgrep
    try:
        pid_file = Path(ACCOUNT_LISTENER_PID_FILE)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                # Verify process is actually running
                result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid='], capture_output=True, text=True)
                if result.stdout.strip():
                    processes['account-listener']['pid'] = pid
                    processes['account-listener']['status'] = 'RUNNING'
                    result = subprocess.run(['ps', '-p', str(pid), '-o', 'etime='], capture_output=True, text=True)
                    processes['account-listener']['uptime'] = result.stdout.strip()
                    processes['account-listener']['pid_file'] = str(pid_file)
                    processes['account-listener']['log'] = str(ACCOUNT_LISTENER_LOG)
            except (ValueError, IOError):
                pass
        if processes['account-listener']['status'] != 'RUNNING':
            # Fallback: check via pattern
            result = subprocess.run(['pgrep', '-f', 'services.account_stream_listener'], capture_output=True, text=True)
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                processes['account-listener']['pid'] = int(pids[0])
                processes['account-listener']['status'] = 'RUNNING'
                result = subprocess.run(['ps', '-p', str(pids[0]), '-o', 'etime='], capture_output=True, text=True)
                processes['account-listener']['uptime'] = result.stdout.strip()
                processes['account-listener']['pid_file'] = str(ACCOUNT_LISTENER_PID_FILE)
                processes['account-listener']['log'] = str(ACCOUNT_LISTENER_LOG)
    except:
        pass

    try:
        # Get gateway process
        result = subprocess.run(['pgrep', '-f', 'openclaw-gateway'], capture_output=True, text=True)
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            processes['gateway']['pid'] = int(pids[0])
            processes['gateway']['status'] = 'RUNNING'
            # Get uptime
            result = subprocess.run(['ps', '-p', str(pids[0]), '-o', 'etime='], capture_output=True, text=True)
            processes['gateway']['uptime'] = result.stdout.strip()
    except:
        pass

    try:
        # Get trading process (look for trading_system.py or control.sh)
        result = subprocess.run(['pgrep', '-f', 'trading_system.py'], capture_output=True, text=True)
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            processes['trading']['pid'] = int(pids[0])
            processes['trading']['status'] = 'RUNNING'
            # Get uptime
            result = subprocess.run(['ps', '-p', str(pids[0]), '-o', 'etime='], capture_output=True, text=True)
            processes['trading']['uptime'] = result.stdout.strip()
    except:
        pass

    return processes


def get_enabled_symbols():
    """Get list of enabled symbols from risk config"""
    try:
        config, _ = _load_shared_risk_config()
        symbols_config = config.get('symbols', {}) if isinstance(config, dict) else {}
        enabled = [s for s, cfg in symbols_config.items() if isinstance(cfg, dict) and cfg.get('enabled', False)]
        if enabled:
            return enabled
    except:
        pass
    # Fallback to symbols from v2_state.json
    state = load_state()
    return state.get('symbols', [])


@app.route('/api/logs/recent', methods=['GET'])
def get_recent_logs():
    """Get recent system logs from multiple sources"""
    try:
        import subprocess
        logs = []
        
        # Get trading logs
        try:
            result = subprocess.run(
                ['tail', '-n', '30', '/Users/FIRMAS/.openclaw/workspace/skills/aster-trading/logs/trading.log'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                logs.append({'source': 'TRADING', 'lines': result.stdout.split('\n')})
        except:
            pass
        
        # Get gateway logs
        try:
            result = subprocess.run(
                ['tail', '-n', '20', '/Users/FIRMAS/.openclaw/logs/gateway.log'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                logs.append({'source': 'GATEWAY', 'lines': result.stdout.split('\n')})
        except:
            pass
        
        # Get v2 state for recent events
        try:
            state = load_state()
            if state.get('recent_errors'):
                logs.append({'source': 'ERRORS', 'lines': state.get('recent_errors', [])})
        except:
            pass
        
        return jsonify({'logs': logs, 'timestamp': time.time()})
    except:
        return jsonify({'logs': [], 'timestamp': time.time()})


@app.route('/api/trading/restart', methods=['POST'])
def restart_trading():
    """Restart the trading system"""
    # Stop first
    stop_trading()
    time.sleep(1)
    # Then start
    return start_trading()


@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    state = load_state()
    
    # Get circuit breaker status from state
    circuit_breaker = state.get('circuit_breaker', {})
    cb_status = circuit_breaker.get('status', 'unknown') if isinstance(circuit_breaker, dict) else 'unknown'
    
    # Get healing metrics from state
    healing_metrics = state.get('healing_metrics', {})
    
    # Check various health indicators
    health = {
        'status': 'healthy' if state.get('running', False) else 'stopped',
        'timestamp': datetime.now().isoformat(),
        'uptime_seconds': (int(time.time() * 1000) - state.get('start_time', 0)) / 1000 if state.get('start_time', 0) > 0 else 0,
        'loop_count': state.get('loop_count', 0),
        'trades_executed': state.get('trades_executed', 0),
        'equity': state.get('equity', 0),
        'daily_pnl': state.get('daily_pnl', 0),
        'open_positions': state.get('open_positions', 0),
        'circuit_breaker': cb_status,
        'api_healthy': circuit_breaker.get('is_healthy', True) if isinstance(circuit_breaker, dict) else True,
        # Enhanced with new tracking data
        'healing_metrics': {
            'total_heals': healing_metrics.get('total_heals', 0) if isinstance(healing_metrics, dict) else 0,
            'sl_orders_added': healing_metrics.get('sl_orders_added', 0) if isinstance(healing_metrics, dict) else 0,
            'tp_orders_added': healing_metrics.get('tp_orders_added', 0) if isinstance(healing_metrics, dict) else 0,
            'last_heal_timestamp': healing_metrics.get('last_heal_timestamp', None) if isinstance(healing_metrics, dict) else None
        },
        'execution_tracker': state.get('execution_tracker', {})
    }
    
    return jsonify(health)


@app.route('/api/positions')
def get_positions():
    """Get open positions"""
    try:
        symbol_filter = _parse_symbol_arg('symbol')
        enabled_symbols = get_enabled_symbols()
        positions = _get_truth_positions(enabled_symbols)
        payload = {p.get('symbol'): p for p in positions if p.get('symbol')}

        if symbol_filter:
            if symbol_filter not in payload:
                return _error_response(
                    'not_found',
                    f"Position for symbol '{symbol_filter}' was not found",
                    404,
                )
            return jsonify({symbol_filter: payload[symbol_filter]})

        return jsonify(payload)
    except ValueError as e:
        return _error_response('invalid_parameter', str(e), 400)
    except FileNotFoundError as e:
        return _error_response('not_found', str(e), 404)
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_positions')
        return _error_response('service_unavailable', 'Data service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_positions')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/signals')
def get_signals():
    """Get current signals"""
    try:
        symbol_filter = _parse_symbol_arg('symbol')
        state = load_state()
        signals = state.get('last_signals', {})

        if not isinstance(signals, dict):
            signals = {}

        if symbol_filter:
            if symbol_filter not in signals:
                return _error_response(
                    'not_found',
                    f"Signal for symbol '{symbol_filter}' was not found",
                    404,
                )
            return jsonify({symbol_filter: signals.get(symbol_filter)})

        return jsonify(signals)
    except ValueError as e:
        return _error_response('invalid_parameter', str(e), 400)
    except FileNotFoundError as e:
        return _error_response('not_found', str(e), 404)
    except Exception:
        logger.exception('Unexpected error in get_signals')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/trades/history')
def get_trade_history():
    """Get trade history from exchange logs"""
    try:
        limit = _parse_limit_arg(name='limit', default=500, minimum=1, maximum=5000)
        include_errors = _parse_bool_arg(name='include_errors', default=True)
        normalized = _load_normalized_trades(limit=limit, include_errors=include_errors)
        return jsonify(normalized)
    except ValueError as e:
        return _error_response('invalid_parameter', str(e), 400)
    except FileNotFoundError as e:
        return _error_response('not_found', str(e), 404)
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_trade_history')
        return _error_response('service_unavailable', 'Data service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_trade_history')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/exchange/positions')
def get_exchange_positions():
    """Get live positions directly from exchange"""
    try:
        # Add src to path temporarily
        import sys
        from pathlib import Path
        if str(BASE_DIR / 'src') not in sys.path:
            sys.path.insert(0, str(BASE_DIR / 'src'))
        
        from api.aster_api import get_positions_v3
        positions = get_positions_v3()
        return jsonify(positions)
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_exchange_positions')
        return _error_response('service_unavailable', 'Exchange service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_exchange_positions')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/exchange/balance')
def get_exchange_balance():
    """Get live account balance from exchange"""
    try:
        import sys
        from pathlib import Path
        if str(BASE_DIR / 'src') not in sys.path:
            sys.path.insert(0, str(BASE_DIR / 'src'))
        
        from api.aster_api import get_equity_total_usdt
        equity = get_equity_total_usdt()
        return jsonify({'equity': equity})
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_exchange_balance')
        return _error_response('service_unavailable', 'Exchange service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_exchange_balance')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/validation/signals-vs-orders')
def get_signals_vs_orders():
    """Compare signals generated vs orders executed - detailed tracking"""
    try:
        state = load_state()
        enabled_symbols = get_enabled_symbols()
        signals = state.get('last_signals', {}) if isinstance(state.get('last_signals', {}), dict) else {}
        reconciliation = _build_reconciliation_snapshot(enabled_symbols)
        latency = _build_execution_latency_snapshot(symbols=enabled_symbols, signal_limit=1200, trade_limit=2500, window_minutes=30)

        tracking = []
        for row in reconciliation.get('symbols', []):
            has_signal = row.get('symbol') in signals
            has_position = bool(row.get('exchange_position'))
            has_orders = row.get('order_counts', {}).get('total', 0) > 0
            status = 'COMPLETE' if (has_signal and has_orders and has_position and not row.get('issues')) else (
                'PENDING' if has_signal and not has_orders else (
                    'ORPHAN' if has_orders and not has_signal else (
                        'POSITION' if has_position else 'IDLE'
                    )
                )
            )
            tracking.append({
                'symbol': row.get('symbol'),
                'signal': signals.get(row.get('symbol')),
                'orders': row.get('open_orders', []),
                'exchange': row.get('exchange_position'),
                'has_signal': has_signal,
                'has_orders': has_orders,
                'has_position': has_position,
                'issues': row.get('issues', []),
                'status': status,
            })

        return jsonify({
            'signals': signals,
            'trades': _load_normalized_trades(limit=50, include_errors=True),
            'exchange_positions': _get_truth_positions(enabled_symbols),
            'tracking': tracking,
            'reconciliation': reconciliation,
            'execution_latency': latency,
            'summary': {
                'total_signals': sum(1 for s in signals.values() if s),
                'total_orders': sum(item.get('order_counts', {}).get('total', 0) for item in reconciliation.get('symbols', [])),
                'total_positions': reconciliation.get('summary', {}).get('positions_exchange', 0),
                'signal_to_order_rate': _calculate_rate(signals, _load_normalized_trades(limit=500, include_errors=True)),
                'symbols_with_issues': reconciliation.get('summary', {}).get('symbols_with_issues', 0),
            }
        })
    except Exception as e:
        # Return error info
        return jsonify({
            'signals': {},
            'trades': [],
            'exchange_positions': [],
            'tracking': [],
            'reconciliation': {},
            'execution_latency': {},
            'error': str(e)
        })


@app.route('/api/transparency/reconciliation')
def get_transparency_reconciliation():
    """Deep consistency check across exchange, v2_state and trade_state."""
    try:
        enabled_symbols = get_enabled_symbols()
        snapshot = _build_reconciliation_snapshot(enabled_symbols=enabled_symbols)
        return jsonify(snapshot)
    except Exception as e:
        return jsonify({'error': str(e), 'symbols': [], 'summary': {}}), 500


@app.route('/api/transparency/execution-latency')
def get_transparency_execution_latency():
    """Signal-to-trade latency metrics for observability and KPI tracking."""
    try:
        window_minutes = max(5, min(_safe_int(request.args.get('window_minutes', 30), 30), 180))
        payload = _build_execution_latency_snapshot(
            symbols=get_enabled_symbols(),
            signal_limit=max(200, min(_safe_int(request.args.get('signal_limit', 1200), 1200), 5000)),
            trade_limit=max(200, min(_safe_int(request.args.get('trade_limit', 2500), 2500), 8000)),
            window_minutes=window_minutes,
        )
        return jsonify(payload)
    except Exception as e:
        return jsonify({'error': str(e), 'matches': [], 'summary': {}, 'by_symbol': {}}), 500


@app.route('/api/transparency/risk')
def get_transparency_risk():
    """Risk transparency payload with progress-to-limits and utilization."""
    try:
        payload = _build_risk_transparency_snapshot(enabled_symbols=get_enabled_symbols())
        return jsonify(payload)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/transparency/overview')
def get_transparency_overview():
    """Unified transparency endpoint combining reconciliation, risk and latency."""
    try:
        enabled_symbols = get_enabled_symbols()
        reconciliation = _build_reconciliation_snapshot(enabled_symbols=enabled_symbols)
        risk = _build_risk_transparency_snapshot(enabled_symbols=enabled_symbols)
        latency = _build_execution_latency_snapshot(symbols=enabled_symbols)
        return jsonify({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'reconciliation': reconciliation,
            'risk': risk,
            'execution_latency': latency,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _get_tracking_status(signal, trades, positions):
    """Determine tracking status for a symbol"""
    if signal and trades:
        return 'COMPLETE'  # Signal generated and order executed
    elif signal and not trades:
        return 'PENDING'  # Signal generated but no order yet
    elif not signal and trades:
        return 'ORPHAN'  # Order without signal
    elif positions:
        return 'POSITION'  # Has position
    else:
        return 'IDLE'  # Nothing


def _calculate_rate(signals, trades):
    """Calculate signal to order conversion rate"""
    signal_count = sum(1 for s in signals.values() if s)
    if signal_count > 0:
        return f"{(len(trades) / signal_count) * 100:.1f}%"
    return "0%"


# ========================================
# WebSocket Support for Real-Time Updates
# ========================================

# Initialize SocketIO if available
if WEBSOCKET_AVAILABLE and SocketIO:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    def _get_anomalies_internal():
        return build_internal_anomalies_payload(load_state)

    register_socketio_handlers(socketio, emit, load_state, _get_anomalies_internal)
else:
    # Fallback: no WebSocket support
    socketio = None

    def _get_anomalies_internal():
        return build_internal_anomalies_payload_fallback(load_state)


# ========================================
# Enhanced API Endpoints for Charts & Anomaly Detection
# ========================================

@app.route('/api/metrics/history')
def get_metrics_history():
    """Get historical metrics for charts - equity, P&L, trade volume"""
    try:
        # Load equity history - handle actual format from equity.jsonl
        equity_data = []
        if EQUITY_FILE.exists():
            with open(EQUITY_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            # Get equity - handle multiple field names
                            equity = entry.get('equity') or entry.get('equity_usdt', 0)
                            if equity and equity > 0:
                                # Get timestamp - handle multiple formats
                                ts = entry.get('timestamp') or entry.get('ts', '')
                                ts_ms = entry.get('timestamp_ms', 0)
                                # If no timestamp but have timestamp_ms, convert it
                                if not ts and ts_ms:
                                    ts = datetime.fromtimestamp(ts_ms / 1000).isoformat()
                                # Handle drawdown_pct - could be 0-1 or 0-100
                                dd_pct = entry.get('drawdown_pct', 0)
                                # Normalize to 0-100 range if it's 0-1
                                if dd_pct > 0 and dd_pct < 1:
                                    dd_pct = dd_pct * 100
                                equity_data.append({
                                    'timestamp': ts,
                                    'timestamp_ms': ts_ms,
                                    'equity': equity,
                                    'daily_pnl': entry.get('daily_pnl', 0),
                                    'drawdown_pct': dd_pct
                                })
                        except:
                            pass
        
        # Load trade history for volume metrics
        trade_data = []
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trade = json.loads(line)
                            trade_data.append({
                                'timestamp': trade.get('ts_close', ''),
                                'symbol': trade.get('symbol', ''),
                                'side': trade.get('side', ''),
                                'size': trade.get('size', 0),
                                'realized_pnl': trade.get('realized_pnl_usdt', 0),
                                'fee': trade.get('fee_approx_usdt', 0)
                            })
                        except:
                            pass
        
        # Calculate aggregated metrics
        # Group equity by hour for chart display
        hourly_equity = defaultdict(list)
        for e in equity_data:
            ts = e.get('timestamp') or e.get('ts', '')
            equity_val = e.get('equity') or e.get('equity_usdt', 0)
            if equity_val and equity_val > 0:
                try:
                    # Handle multiple timestamp formats
                    ts_clean = ts.replace('Z', '+00:00') if ts else ''
                    if ts_clean:
                        dt = datetime.fromisoformat(ts_clean)
                        hour_key = dt.strftime('%Y-%m-%d %H:00')
                        hourly_equity[hour_key].append(equity_val)
                except:
                    # If timestamp parsing fails, use a simple index
                    pass
        
        # Calculate hourly averages
        chart_equity = []
        for hour, values in sorted(hourly_equity.items()):
            chart_equity.append({
                'timestamp': hour,
                'equity': sum(values) / len(values) if values else 0
            })
        
        # If no hourly data, use individual data points
        if not chart_equity:
            for e in equity_data[-100:]:  # Last 100 points
                ts = e.get('timestamp') or ''
                chart_equity.append({
                    'timestamp': ts,
                    'equity': e.get('equity') or 0
                })
        
        # Calculate daily P&L
        daily_pnl = defaultdict(float)
        for t in trade_data:
            if t['timestamp']:
                try:
                    dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
                    day_key = dt.strftime('%Y-%m-%d')
                    daily_pnl[day_key] += t['realized_pnl']
                except:
                    pass
        
        chart_pnl = []
        for day, pnl in sorted(daily_pnl.items()):
            chart_pnl.append({
                'date': day,
                'pnl': pnl
            })
        
        # Calculate trade volume by hour
        hourly_volume = defaultdict(int)
        for t in trade_data:
            if t['timestamp']:
                try:
                    dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
                    hour_key = dt.strftime('%Y-%m-%d %H:00')
                    hourly_volume[hour_key] += 1
                except:
                    pass
        
        chart_volume = []
        for hour, count in sorted(hourly_volume.items()):
            chart_volume.append({
                'timestamp': hour,
                'count': count
            })
        
        # Calculate error rate (mock - would need actual error tracking)
        error_rate = 0
        total_trades = len(trade_data)
        if total_trades > 0:
            # For now, assume 0% error rate - would need error tracking
            error_rate = 0
        
        return jsonify({
            'equity': chart_equity[-100:] if len(chart_equity) > 100 else chart_equity,  # Last 100 hours
            'pnl': chart_pnl[-30:] if len(chart_pnl) > 30 else chart_pnl,  # Last 30 days
            'volume': chart_volume[-48:] if len(chart_volume) > 48 else chart_volume,  # Last 48 hours
            'summary': {
                'total_trades': total_trades,
                'total_pnl': sum(t['realized_pnl'] for t in trade_data),
                'total_fees': sum(t['fee'] for t in trade_data),
                'error_rate': error_rate,
                'win_rate': _calculate_win_rate(trade_data),
                'avg_trade_pnl': sum(t['realized_pnl'] for t in trade_data) / total_trades if total_trades > 0 else 0
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'equity': [], 'pnl': [], 'volume': []}), 500


def _calculate_win_rate(trades):
    """Calculate win rate from trade history"""
    if not trades:
        return 0
    wins = sum(1 for t in trades if t.get('realized_pnl', 0) > 0)
    return (wins / len(trades)) * 100 if trades else 0


@app.route('/api/anomalies')
def get_anomalies():
    """Detect anomalies in trading system metrics"""
    try:
        return jsonify(build_anomalies_payload(load_state, BASE_DIR, subprocess))
    except Exception as e:
        return jsonify({'error': str(e), 'anomalies': []}), 500


@app.route('/api/logs')
def get_logs():
    """Get logs with filtering, search, and categorization"""
    try:
        # Get filter parameters
        level_filter = request.args.get('level', 'ALL').upper()
        source_filter = request.args.get('source', 'ALL').upper()
        search = request.args.get('search', '').lower()
        limit = max(1, min(_safe_int(request.args.get('limit', 100), 100), 2000))

        containers, combined = _collect_logs_by_source(limit_per_source=max(250, limit))
        all_logs = list(combined)

        def _apply_filters(entries):
            data = entries
            if level_filter != 'ALL':
                data = [l for l in data if str(l.get('level', '')).upper() == level_filter]
            if source_filter != 'ALL':
                data = [l for l in data if str(l.get('source', '')).upper() == source_filter]
            if search:
                data = [l for l in data if search in str(l.get('message', '')).lower()]
            data.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
            return data

        filtered_logs = _apply_filters(combined)[:limit]

        filtered_containers = {}
        for src, rows in containers.items():
            if source_filter != 'ALL' and src != source_filter:
                continue
            filtered_containers[src] = _apply_filters(rows)[:limit]

        stats = {
            'total': len(all_logs),
            'ERROR': len([l for l in all_logs if l.get('level') == 'ERROR']),
            'WARN': len([l for l in all_logs if l.get('level') == 'WARN']),
            'INFO': len([l for l in all_logs if l.get('level') == 'INFO']),
            'DEBUG': len([l for l in all_logs if l.get('level') == 'DEBUG'])
        }
        
        return jsonify({
            'logs': filtered_logs,
            'containers': filtered_containers,
            'stats': stats,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'logs': [], 'stats': {}}), 500


def _parse_log_line(line, source):
    """Parse a log line into structured format"""
    try:
        if not line:
            return None

        timestamp_ms = 0
        level = 'INFO'
        message = line.strip()

        m = LOG_STRUCTURED_RE.match(line.strip())
        if m:
            ts_raw = m.group('ts')
            timestamp_ms = _parse_timestamp_ms(ts_raw)
            level = str(m.group('level') or 'INFO').upper().replace('WARNING', 'WARN')
            message = (m.group('msg') or '').strip()
        else:
            ts_guess = line[:23]
            parsed_guess = _parse_timestamp_ms(ts_guess)
            if parsed_guess > 0:
                timestamp_ms = parsed_guess

            upper = line.upper()
            for lvl in ['ERROR', 'WARN', 'WARNING', 'INFO', 'DEBUG', 'CRITICAL']:
                if lvl in upper:
                    level = 'WARN' if lvl == 'WARNING' else lvl
                    break

        if timestamp_ms <= 0:
            timestamp_ms = int(time.time() * 1000)

        return {
            'timestamp': _to_iso(timestamp_ms) or datetime.utcnow().isoformat() + 'Z',
            'timestamp_ms': timestamp_ms,
            'level': level,
            'source': source,
            'message': message[:500] if message else line[:500]  # Limit message length
        }
    except:
        ts_ms = int(time.time() * 1000)
        return {
            'timestamp': _to_iso(ts_ms),
            'timestamp_ms': ts_ms,
            'level': 'INFO',
            'source': source,
            'message': line[:500]
        }


@app.route('/api/alerts')
def get_alerts():
    """Get active alerts and notifications"""
    try:
        alerts = []
        
        # Get anomalies
        anomalies_response = get_anomalies()
        anomalies = json.loads(anomalies_response.data)
        
        # Convert anomalies to alerts
        for anomaly in anomalies.get('anomalies', []):
            alerts.append({
                'id': anomaly['type'] + '_' + anomaly['timestamp'],
                'type': anomaly['type'],
                'severity': anomaly['severity'],
                'message': anomaly['message'],
                'timestamp': anomaly['timestamp'],
                'acknowledged': False
            })
        
        # Sort by severity
        severity_order = {'critical': 0, 'warning': 1, 'info': 2}
        alerts.sort(key=lambda x: severity_order.get(x['severity'], 3))
        
        return jsonify({
            'alerts': alerts,
            'count': len(alerts),
            'unacknowledged': len([a for a in alerts if not a['acknowledged']])
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'alerts': []}), 500


@app.route('/api/config')
def get_config():
    """Get runtime configuration"""
    try:
        config_data = {}
        
        # Load risk config (shared adapter)
        _, raw_risk_cfg = _load_shared_risk_config()
        if isinstance(raw_risk_cfg, dict) and raw_risk_cfg:
            config_data['risk'] = raw_risk_cfg
        
        # Load trading config
        trading_config = BASE_DIR / 'config' / 'trading_config.json'
        if trading_config.exists():
            with open(trading_config, 'r') as f:
                config_data['trading'] = json.load(f)
        
        return jsonify(config_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/risk/current')
def get_risk_metrics():
    """Get current risk metrics"""
    try:
        state = load_state()
        risk_data = {
            'risk_level': 'NORMAL',
            'daily_pnl': 0,
            'drawdown_pct': 0,
            'positions_pnl': {},
            'max_drawdown': 0,
            'total_exposure': 0
        }

        risk_data['risk_level'] = state.get('risk_level', 'NORMAL')
        risk_data['daily_pnl'] = _safe_float(state.get('daily_pnl', 0), 0)
        risk_data['drawdown_pct'] = _safe_float(state.get('drawdown_pct', 0), 0)
        risk_data['equity'] = state.get('equity', 0)
        risk_data['positions'] = state.get('positions', {})
        risk_data['running'] = state.get('running', False)
        
        # Calculate total exposure
        positions = state.get('positions', {})
        total_exposure = sum(abs(p.get('positionAmt', 0) * p.get('entryPrice', 0)) for p in positions.values())
        risk_data['total_exposure'] = total_exposure
        
        return jsonify(risk_data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/transactions')
def get_transactions():
    """Get detailed transaction tracking"""
    try:
        transactions = []
        
        # Get trade history - handle actual format
        if TRADES_FILE.exists():
            with open(TRADES_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trade = json.loads(line)
                            # Normalize fields
                            transactions.append({
                                'id': trade.get('timestamp', trade.get('ts_close', '')),
                                'type': 'TRADE',
                                'symbol': trade.get('symbol', ''),
                                'side': trade.get('side', ''),
                                'size': trade.get('quantity', trade.get('size', 0)),
                                'entry_price': trade.get('price', trade.get('entry_price', 0)),
                                'exit_price': trade.get('price', trade.get('exit_price', 0)),
                                'pnl': trade.get('pnl', trade.get('realized_pnl_usdt', 0)),
                                'realized_pnl_usdt': trade.get('pnl', trade.get('realized_pnl_usdt', 0)),
                                'fee': trade.get('fee_approx_usdt', 0),
                                'status': trade.get('status', 'CLOSED'),
                                'reason': trade.get('reason_close', ''),
                                'setup': trade.get('setup_type', ''),
                                'timestamp': trade.get('timestamp', trade.get('ts_close', ''))
                            })
                        except:
                            pass
        
        # Get current positions (as open transactions)
        state = load_state()
        positions = state.get('positions', {})
        for symbol, pos in positions.items():
            transactions.append({
                'id': f'POS_{symbol}',
                'type': 'POSITION',
                'symbol': symbol,
                'side': 'LONG' if float(pos.get('positionAmt', 0)) > 0 else 'SHORT',
                'size': abs(float(pos.get('positionAmt', 0))),
                'entry_price': float(pos.get('entryPrice', 0)),
                'current_price': float(pos.get('markPrice', 0)),
                'unrealized_pnl': float(pos.get('unrealizedProfit', 0)),
                'status': 'OPEN',
                'timestamp': pos.get('updateTime', '')
            })
        
        # Sort by timestamp descending
        transactions.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'transactions': transactions[:100],  # Last 100
            'count': len(transactions)
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'transactions': []}), 500


# ========================================
# Unified Flow & Healing Metrics Endpoints
# ========================================

@app.route('/api/trading/unified-flow')
def get_unified_flow():
    """
    Get complete signal → order → position flow with healing metrics and circuit breaker status.
    Returns unified flow data combining signals, orders, exchange positions, healing metrics, and circuit breaker state.
    """
    try:
        # Load current state
        state = load_state()
        
        # Get enabled symbols from config
        enabled_symbols = get_enabled_symbols()
        
        # Get execution tracker data from state
        execution_tracker = state.get('execution_tracker', {})
        
        # Get healing metrics from state
        healing_metrics = state.get('healing_metrics', {})
        
        # Get circuit breaker status
        circuit_breaker = state.get('circuit_breaker', {})
        
        # Get current positions from exchange and filter by enabled symbols
        exchange_positions = []
        try:
            import sys
            if str(BASE_DIR / 'src') not in sys.path:
                sys.path.insert(0, str(BASE_DIR / 'src'))
            from api.aster_api import get_positions_v3
            all_positions = get_positions_v3()
            # Filter to only enabled symbols
            exchange_positions = [p for p in all_positions if p.get('symbol') in enabled_symbols]
        except Exception as e:
            print(f"Error getting exchange positions: {e}")
            exchange_positions = []
        
        # Get signals from state and filter by enabled symbols
        all_signals = state.get('last_signals', {})
        signals = {s: sig for s, sig in all_signals.items() if s in enabled_symbols}
        
        # Build unified flow response
        unified_flow = {
            'signals': signals,
            'execution_tracker': execution_tracker,
            'healing_metrics': healing_metrics,
            'circuit_breaker': circuit_breaker,
            'positions': {
                'exchange': exchange_positions,
                'state': state.get('positions', {})
            },
            'flow_summary': execution_tracker.get('flow_summary', {}) if isinstance(execution_tracker, dict) else {},
            'enabled_symbols': enabled_symbols,
            'timestamp': datetime.now().isoformat()
        }
        
        return jsonify(unified_flow)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/healing/metrics')
def get_healing_metrics():
    """
    Get healing metrics from the trading system.
    
    Returns:
        - total_heals: Total number of healing actions performed
        - sl_orders_added: Number of stop-loss orders added
        - tp_orders_added: Number of take-profit orders added
        - leverage_adjusted: Number of leverage adjustments made
        - stale_positions_cleaned: Number of stale positions cleaned
        - last_heal_timestamp: Timestamp of the last healing action
    """
    try:
        # Load current state
        state = load_state()
        
        # Get healing metrics from state
        healing_metrics = state.get('healing_metrics', {})
        
        # Ensure all required fields are present with defaults
        metrics = {
            'total_heals': healing_metrics.get('total_heals', 0),
            'sl_orders_added': healing_metrics.get('sl_orders_added', 0),
            'tp_orders_added': healing_metrics.get('tp_orders_added', 0),
            'leverage_adjusted': healing_metrics.get('leverage_adjusted', 0),
            'stale_positions_cleaned': healing_metrics.get('stale_positions_cleaned', 0),
            'last_heal_timestamp': healing_metrics.get('last_heal_timestamp', None),
            'heal_errors': healing_metrics.get('heal_errors', []),
            'timestamp': datetime.now().isoformat()
        }
        
        return jsonify(metrics)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trading/execution-tracker')
def get_execution_tracker():
    """
    Get active orders summary from the trade tracker.
    Returns active (non-terminal) orders with their current status.
    """
    try:
        # Try to get live tracker first
        tracker = get_global_trade_tracker()
        
        if tracker:
            # Use live tracker for real-time data
            active_orders = tracker.get_active_orders_summary()
            
            # Also try to get unified flow with exchange positions
            exchange_positions = []
            try:
                import sys
                if str(BASE_DIR / 'src') not in sys.path:
                    sys.path.insert(0, str(BASE_DIR / 'src'))
                from api.aster_api import get_positions_v3
                exchange_positions = get_positions_v3()
            except:
                pass
            
            unified_flow = tracker.get_unified_flow(exchange_positions)
            
            return jsonify({
                'active_orders': active_orders,
                'unified_flow': unified_flow,
                'timestamp': datetime.now().isoformat()
            })
        
        # Fallback to state file data
        state = load_state()
        execution_tracker = state.get('execution_tracker', {})
        
        return jsonify({
            'active_orders': execution_tracker.get('active_order_symbols', []),
            'execution_tracker': execution_tracker,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================================
# Main
# ========================================

# Additional API endpoints for enhanced dashboard features

@app.route('/api/signals/detailed')
def get_signals_detailed():
    """Get detailed signals with order/trade linkage and LLM reasons"""
    try:
        state = load_state()
        state_signals = state.get('last_signals', {}) if isinstance(state.get('last_signals', {}), dict) else {}

        enabled_symbols = get_enabled_symbols()
        symbols = enabled_symbols or sorted({str(s).upper() for s in state_signals.keys()})

        signal_history = _extract_signal_strength_history(symbols=symbols, limit=300)
        latest_history_by_symbol = {}
        for ev in signal_history:
            latest_history_by_symbol[ev.get('symbol')] = ev

        trades = _load_normalized_trades(limit=800, include_errors=False)
        orders = _get_truth_open_orders(symbols)
        orders_by_symbol = defaultdict(list)
        for o in orders:
            orders_by_symbol[o.get('symbol')].append(o)

        reasons = _extract_latest_signal_reason_map(symbols)

        detailed_signals = []
        for symbol in symbols:
            state_sig = state_signals.get(symbol, {}) if isinstance(state_signals.get(symbol, {}), dict) else {}
            hist_sig = latest_history_by_symbol.get(symbol, {})

            action = str(_first_non_none(hist_sig.get('action'), state_sig.get('action'), '')).upper()
            if not action:
                continue

            ts_ms = _first_non_none(hist_sig.get('timestamp_ms'), _parse_timestamp_ms(state_sig.get('timestamp')))
            ts_iso = _first_non_none(hist_sig.get('timestamp'), _to_iso(state_sig.get('timestamp')), _to_iso(ts_ms))

            symbol_trades = [t for t in trades if t.get('symbol') == symbol]
            latest_trade = symbol_trades[0] if symbol_trades else None

            traded = bool(hist_sig.get('trade_executed', False))
            if not traded and latest_trade:
                delta = abs(_safe_int(latest_trade.get('timestamp_ms', 0)) - _safe_int(ts_ms, 0))
                traded = delta <= 15 * 60 * 1000 if ts_ms else True

            reason_info = reasons.get(symbol, {})
            detailed_signals.append({
                'symbol': symbol,
                'action': action,
                'strength': _safe_float(_first_non_none(hist_sig.get('strength'), state_sig.get('strength')), 0),
                'confidence': _safe_float(_first_non_none(hist_sig.get('confidence'), state_sig.get('confidence')), 0),
                'price': _safe_float(_first_non_none(hist_sig.get('price'), state_sig.get('price')), 0),
                'llm_reason': _first_non_none(reason_info.get('llm_reason'), state_sig.get('llm_reason'), state_sig.get('reason'), 'N/A'),
                'trigger_type': _first_non_none(state_sig.get('trigger_type'), state_sig.get('type'), reason_info.get('trigger_source'), 'signal'),
                'trigger_source': reason_info.get('trigger_source', 'ML'),
                'trigger_reason': _first_non_none(reason_info.get('trigger_reason'), state_sig.get('trigger_reason')),
                'trade_opened': traded,
                'related_trade': latest_trade,
                'open_orders': orders_by_symbol.get(symbol, []),
                'timestamp': ts_iso,
                'timestamp_ms': _safe_int(ts_ms, 0)
            })

        detailed_signals.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)

        return jsonify({
            'signals': detailed_signals,
            'count': len(detailed_signals),
            'signal_history': signal_history,
            'empty_state': None if detailed_signals else 'No signal records found in state/runtime logs'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'signals': []}), 500


@app.route('/api/orders/open')
def get_open_orders_detailed():
    """Get detailed open orders with TP/SL/SURF status"""
    try:
        enabled_symbols = get_enabled_symbols()
        orders = _get_truth_open_orders(enabled_symbols)

        for order in orders:
            order['size'] = _safe_float(_first_non_none(order.get('origQty'), order.get('size'), order.get('quantity')), 0)

        tp_orders = [o for o in orders if o.get('classification') == 'TP']
        sl_orders = [o for o in orders if o.get('classification') == 'SL']
        surf_orders = [o for o in orders if o.get('classification') == 'SURF']
        entry_orders = [o for o in orders if o.get('classification') in {'ENTRY', 'REDUCE_ONLY'}]

        by_symbol = defaultdict(lambda: {'total': 0, 'tp': 0, 'sl': 0, 'surf': 0, 'entry': 0})
        for o in orders:
            symbol = o.get('symbol', 'UNKNOWN')
            by_symbol[symbol]['total'] += 1
            cls = o.get('classification')
            if cls == 'TP':
                by_symbol[symbol]['tp'] += 1
            elif cls == 'SL':
                by_symbol[symbol]['sl'] += 1
            elif cls == 'SURF':
                by_symbol[symbol]['surf'] += 1
            elif cls in {'ENTRY', 'REDUCE_ONLY'}:
                by_symbol[symbol]['entry'] += 1

        return jsonify({
            'orders': orders,
            'tp_orders': tp_orders,
            'sl_orders': sl_orders,
            'surf_orders': surf_orders,
            'entry_orders': entry_orders,
            'by_symbol': dict(by_symbol),
            'count': len(orders),
            'empty_state': None if orders else 'No open orders from exchange'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'orders': []}), 500


@app.route('/api/trades/detailed')
def get_trades_detailed():
    """Get detailed trade history with all columns and drill-down"""
    try:
        limit = max(1, min(_safe_int(request.args.get('limit', 500), 500), 5000))
        include_errors = request.args.get('include_errors', 'true').lower() != 'false'
        detailed_trades = _load_normalized_trades(limit=limit, include_errors=include_errors)

        finalized = [t for t in detailed_trades if not t.get('pnl_pending') and t.get('realized_pnl') is not None]
        total_pnl = sum(_safe_float(t.get('realized_pnl'), 0) for t in finalized)
        total_fees = sum(_safe_float(t.get('fee'), 0) for t in finalized)

        return jsonify({
            'trades': detailed_trades,
            'count': len(detailed_trades),
            'summary': {
                'finalized_count': len(finalized),
                'pending_count': len([t for t in detailed_trades if t.get('pnl_pending')]),
                'total_pnl': total_pnl,
                'total_fees': total_fees,
                'net_pnl': total_pnl - total_fees,
            }
        })
    except Exception as e:
        return jsonify({'error': str(e), 'trades': []}), 500


@app.route('/api/positions/detailed')
def get_positions_detailed():
    """Get detailed positions with TP/SL/SURF order info"""
    try:
        symbol_filter = _parse_symbol_arg('symbol')
        enabled_symbols = get_enabled_symbols()
        positions = _get_truth_positions(enabled_symbols)
        orders = _get_truth_open_orders(enabled_symbols)

        by_symbol_orders = defaultdict(list)
        for o in orders:
            by_symbol_orders[o.get('symbol')].append(o)

        detailed_positions = []
        for pos in positions:
            symbol = pos.get('symbol', '')

            pos_orders = by_symbol_orders.get(symbol, [])
            tp_orders, sl_orders, surf_orders, entry_orders = _build_position_targets(pos, pos_orders)

            tp_status = 'PENDING' if any(o.get('status') in ['NEW', 'PARTIALLY_FILLED', 'PENDING'] for o in tp_orders) else ('FILLED' if any(o.get('status') == 'FILLED' for o in tp_orders) else 'NONE')
            sl_status = 'PENDING' if any(o.get('status') in ['NEW', 'PARTIALLY_FILLED', 'PENDING'] for o in sl_orders) else ('FILLED' if any(o.get('status') == 'FILLED' for o in sl_orders) else 'NONE')
            surf_status = 'PENDING' if any(o.get('status') in ['NEW', 'PARTIALLY_FILLED', 'PENDING'] for o in surf_orders) else ('FILLED' if any(o.get('status') == 'FILLED' for o in surf_orders) else 'NONE')

            detailed_positions.append({
                'symbol': symbol,
                'side': 'LONG' if _safe_float(pos.get('positionAmt', 0)) > 0 else 'SHORT',
                'size': abs(_safe_float(pos.get('positionAmt', 0))),
                'entry_price': _safe_float(pos.get('entryPrice', 0)),
                'mark_price': _safe_float(pos.get('markPrice', 0)),
                'unrealized_pnl': _safe_float(_first_non_none(pos.get('unrealized_pnl'), pos.get('unRealizedProfit'), pos.get('unrealizedProfit')), 0),
                'leverage': _safe_int(pos.get('leverage', 1), 1),
                'liquidation_price': _safe_float(pos.get('liquidationPrice', 0)),
                'margin': _safe_float(_first_non_none(pos.get('isolatedMargin'), pos.get('margin')), 0),
                'tp_orders': tp_orders,
                'sl_orders': sl_orders,
                'surf_orders': surf_orders,
                'entry_orders': entry_orders,
                'tp_status': tp_status,
                'sl_status': sl_status,
                'surf_status': surf_status,
                'update_time': pos.get('updateTime', ''),
                'targets': {
                    'tp': tp_orders,
                    'sl': sl_orders,
                    'surf': surf_orders,
                    'entry': entry_orders,
                }
            })

        if symbol_filter:
            detailed_positions = [p for p in detailed_positions if p.get('symbol') == symbol_filter]
            if not detailed_positions:
                return _error_response(
                    'not_found',
                    f"Detailed position for symbol '{symbol_filter}' was not found",
                    404,
                )
        
        return jsonify({
            'positions': detailed_positions,
            'count': len(detailed_positions),
            'empty_state': None if detailed_positions else 'No open positions from exchange/state fallback'
        })
    except ValueError as e:
        return _error_response('invalid_parameter', str(e), 400)
    except FileNotFoundError as e:
        return _error_response('not_found', str(e), 404)
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_positions_detailed')
        return _error_response('service_unavailable', 'Data service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_positions_detailed')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


@app.route('/api/flow/detailed')
def get_flow_detailed():
    """Get detailed signal → order → trade flow with validation"""
    try:
        state = load_state()
        state_signals = state.get('last_signals', {}) if isinstance(state.get('last_signals', {}), dict) else {}
        enabled_symbols = get_enabled_symbols()

        trades = _load_normalized_trades(limit=1200, include_errors=True)
        orders = _get_truth_open_orders(enabled_symbols)
        positions = _get_truth_positions(enabled_symbols)

        signal_history = _extract_signal_strength_history(symbols=enabled_symbols, limit=400)
        latest_signal_from_logs = {}
        for ev in signal_history:
            latest_signal_from_logs[ev.get('symbol')] = ev

        reason_map = _extract_latest_signal_reason_map(enabled_symbols or list(state_signals.keys()))

        trades_by_symbol = defaultdict(list)
        for t in trades:
            trades_by_symbol[t.get('symbol')].append(t)
        for _, rows in trades_by_symbol.items():
            rows.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)

        orders_by_symbol = defaultdict(list)
        for o in orders:
            orders_by_symbol[o.get('symbol')].append(o)

        positions_by_symbol = {p.get('symbol'): p for p in positions if p.get('symbol')}

        flow_entries = []
        symbols = sorted({
            *[s for s in (enabled_symbols or [])],
            *[s for s in state_signals.keys()],
            *[t.get('symbol') for t in trades if t.get('symbol')],
            *[o.get('symbol') for o in orders if o.get('symbol')],
            *[p.get('symbol') for p in positions if p.get('symbol')],
        })

        for symbol in symbols:
            raw_signal = state_signals.get(symbol, {}) if isinstance(state_signals.get(symbol, {}), dict) else {}
            log_signal = latest_signal_from_logs.get(symbol, {})
            reason = reason_map.get(symbol, {})

            action = str(_first_non_none(log_signal.get('action'), raw_signal.get('action'), '')).upper()
            signal = None
            if action:
                sig_ts_ms = _safe_int(_first_non_none(log_signal.get('timestamp_ms'), _parse_timestamp_ms(raw_signal.get('timestamp'))), 0)
                signal = {
                    'symbol': symbol,
                    'action': action,
                    'strength': _safe_float(_first_non_none(log_signal.get('strength'), raw_signal.get('strength')), 0),
                    'confidence': _safe_float(_first_non_none(log_signal.get('confidence'), raw_signal.get('confidence')), 0),
                    'price': _safe_float(_first_non_none(log_signal.get('price'), raw_signal.get('price')), 0),
                    'timestamp': _first_non_none(log_signal.get('timestamp'), _to_iso(raw_signal.get('timestamp')), _to_iso(sig_ts_ms)),
                    'timestamp_ms': sig_ts_ms,
                    'trigger': _first_non_none(raw_signal.get('trigger_type'), raw_signal.get('type'), reason.get('trigger_source'), 'signal'),
                    'trigger_reason': _first_non_none(reason.get('trigger_reason'), raw_signal.get('trigger_reason')),
                    'llm_reason': _first_non_none(reason.get('llm_reason'), raw_signal.get('llm_reason'), raw_signal.get('reason')),
                }

            symbol_trades = trades_by_symbol.get(symbol, [])
            symbol_orders = orders_by_symbol.get(symbol, [])
            symbol_position = positions_by_symbol.get(symbol)

            latest_trade = symbol_trades[0] if symbol_trades else None

            if signal and latest_trade:
                status = 'COMPLETE'
            elif signal and symbol_orders:
                status = 'ORDER_PLACED'
            elif signal and symbol_position and not symbol_orders:
                status = 'POSITION_OPEN'
            elif signal and not symbol_orders and not symbol_position:
                status = 'SIGNAL_NO_ORDER'
            elif not signal and latest_trade:
                status = 'ORPHAN_TRADE'
            elif symbol_position:
                status = 'POSITION_OPEN'
            else:
                status = 'IDLE'

            errors = []
            if status == 'SIGNAL_NO_ORDER':
                errors.append('Signal generated but no order placed')
            if symbol_position:
                has_tp = any(o.get('classification') == 'TP' for o in symbol_orders)
                has_sl = any(o.get('classification') == 'SL' for o in symbol_orders)
                if not has_tp:
                    errors.append('Position missing TP order')
                if not has_sl:
                    errors.append('Position missing SL order')

            timestamp_ms = _safe_int(
                _first_non_none(
                    signal.get('timestamp_ms') if signal else None,
                    latest_trade.get('timestamp_ms') if latest_trade else None,
                    symbol_position.get('updateTime') if symbol_position else None,
                ),
                0
            )

            flow_entries.append({
                'symbol': symbol,
                'signal': signal,
                'trigger': signal.get('trigger') if signal else None,
                'trigger_reason': signal.get('trigger_reason') if signal else None,
                'llm_reason': signal.get('llm_reason') if signal else None,
                'orders': symbol_orders,
                'trade': latest_trade,
                'position': symbol_position,
                'status': status,
                'errors': errors,
                'timestamp': _to_iso(timestamp_ms),
                'timestamp_ms': timestamp_ms,
            })

        flow_entries.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)

        return jsonify({
            'flow': flow_entries,
            'summary': {
                'total_signals': sum(1 for f in flow_entries if f['signal'] and f['signal'].get('action') in {'BUY', 'SELL'}),
                'total_orders': len(orders),
                'total_trades': len(trades),
                'total_positions': len(positions),
                'complete_flows': sum(1 for f in flow_entries if f['status'] == 'COMPLETE'),
                'incomplete_flows': sum(1 for f in flow_entries if f['status'] not in {'COMPLETE', 'IDLE'}),
                'symbols_covered': len(flow_entries),
            },
            'enabled_symbols': enabled_symbols,
            'empty_state': None if flow_entries else 'No flow data available'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'flow': []}), 500


@app.route('/api/healing/detailed')
def get_healing_detailed():
    """Get detailed healing history with affected orders"""
    try:
        state = load_state()
        healing = state.get('healing_metrics', {})
        
        # Get healing history from state
        healing_history = healing.get('history', [])
        
        detailed_healing = []
        for h in healing_history:
            detailed_healing.append({
                'id': h.get('id', ''),
                'timestamp': h.get('timestamp', ''),
                'action': h.get('action', ''),
                'symbol': h.get('symbol', ''),
                'reason': h.get('reason', ''),
                'orders_added': h.get('orders_added', []),
                'orders_cancelled': h.get('orders_cancelled', []),
                'position_id': h.get('position_id', ''),
                'success': h.get('success', True),
                'error': h.get('error', '')
            })
        
        # Sort by timestamp descending
        detailed_healing.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'healing': detailed_healing,
            'metrics': {
                'total_heals': healing.get('total_heals', 0),
                'sl_orders_added': healing.get('sl_orders_added', 0),
                'tp_orders_added': healing.get('tp_orders_added', 0),
                'leverage_adjusted': healing.get('leverage_adjusted', 0),
                'stale_positions_cleaned': healing.get('stale_positions_cleaned', 0),
                'last_heal_timestamp': healing.get('last_heal_timestamp', None),
                'heal_errors': healing.get('heal_errors', [])
            }
        })
    except Exception as e:
        return jsonify({'error': str(e), 'healing': []}), 500


@app.route('/api/logs/system')
def get_system_logs():
    """Get system logs in same format as logs tab"""
    try:
        level_filter = request.args.get('level', 'ALL').upper()
        limit = max(1, min(_safe_int(request.args.get('limit', 100), 100), 2000))

        containers, _ = _collect_logs_by_source(limit_per_source=max(250, limit))
        selected_sources = ['SYSTEM', 'RISK', 'DEBUG', 'TRADING', 'GATEWAY']

        logs = []
        system_containers = {}
        for src in selected_sources:
            rows = list(containers.get(src, []))
            if level_filter != 'ALL':
                rows = [r for r in rows if str(r.get('level', '')).upper() == level_filter]
            rows.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
            system_containers[src] = rows[:limit]
            logs.extend(rows)

        logs.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
        logs = logs[:limit]

        return jsonify({
            'logs': logs,
            'containers': system_containers,
            'count': len(logs)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'logs': []}), 500


@app.route('/api/system/config')
def get_system_config():
    """Get user-friendly system configuration display"""
    try:
        risk_cfg, raw_risk_cfg = _load_shared_risk_config()
        trading_cfg = _load_json_file(CONFIG_DIR / 'trading_config.json', default={})
        symbols_cfg = _load_json_file(CONFIG_DIR / 'symbols.json', default={})
        system_cfg = _load_json_file(CONFIG_DIR / 'system_config.json', default={})
        exchange_cfg = _load_json_file(CONFIG_DIR / 'exchange_config.json', default={})

        cron_payload = _load_json_file(CRON_JOBS_FILE, default={})
        cron_jobs = cron_payload.get('jobs', []) if isinstance(cron_payload, dict) else []

        global_settings = risk_cfg.get('global', {}) if isinstance(risk_cfg, dict) else {}
        mode_settings = risk_cfg.get('mode', {}) if isinstance(risk_cfg, dict) else {}
        symbols_settings = risk_cfg.get('symbols', {}) if isinstance(risk_cfg, dict) else {}
        enabled_symbols = [s for s, cfg in symbols_settings.items() if isinstance(cfg, dict) and cfg.get('enabled', False)]

        loop_seconds = _safe_int(
            _first_non_none(trading_cfg.get('loop_interval_seconds'), system_cfg.get('system', {}).get('main_loop_seconds'), 60),
            60
        )

        cadence = _build_system_cadence(system_cfg if isinstance(system_cfg, dict) else {}, cron_jobs)

        watchlist = symbols_cfg.get('symbols', symbols_cfg.get('watchlist', enabled_symbols or list(symbols_settings.keys())))

        formatted_config = {
            'risk': {
                'max_position_size_pct': _safe_float(global_settings.get('max_equity_notional_pct', 60), 60),
                'max_leverage': _safe_int(global_settings.get('max_leverage_global', 3), 3),
                'max_daily_loss_pct': _safe_float(global_settings.get('daily_loss_hard_limit_usdt', 1.5), 1.5),
                'max_drawdown_pct': _safe_float(global_settings.get('max_drawdown_pct', 10), 10),
                'risk_levels': global_settings.get('risk_levels', {}),
                'mode': mode_settings.get('risk_profile', 'normal'),
                'sl_pct': _safe_float(global_settings.get('sl_pct', 1.0), 1.0),
                'tp1_pct': _safe_float(global_settings.get('tp1_pct', 1.5), 1.5),
                'tp2_pct': _safe_float(global_settings.get('tp2_pct', 3.0), 3.0),
                'pyramid_enabled': bool(global_settings.get('pyramid', {}).get('enabled', False)),
                'pyramid_max_scales': _safe_int(global_settings.get('pyramid', {}).get('max_scales', 0), 0),
                'enabled_symbols': enabled_symbols,
            },
            'trading': {
                'loop_interval_seconds': loop_seconds,
                'max_concurrent_positions': _safe_int(trading_cfg.get('max_concurrent_positions', 3), 3),
                'min_signal_strength': _safe_float(trading_cfg.get('min_signal_strength', 0.5), 0.5),
                'min_confidence': _safe_float(trading_cfg.get('min_confidence', 0.6), 0.6),
                'enabled': bool(trading_cfg.get('enabled', True)),
            },
            'symbols': {
                'watchlist': watchlist,
                'max_symbols': len(watchlist),
            },
            'system': {
                'state_save_interval_ms': _safe_int(system_cfg.get('system', {}).get('state_save_interval_ms', 60000), 60000),
                'llm_review_hours': _safe_int(system_cfg.get('system', {}).get('llm_review_hours', 3), 3),
                'debounce': system_cfg.get('system', {}).get('debounce', {}),
                'logging': system_cfg.get('logging', {}),
            },
            'cadence': cadence,
            'crons': cadence.get('cron_jobs', []),
            'explanations': [
                {
                    'name': 'main_loop_seconds',
                    'value': cadence.get('main_loop_seconds'),
                    'description': 'How often the trading loop evaluates signals and risk checks.'
                },
                {
                    'name': 'state_refresh_seconds',
                    'value': cadence.get('state_refresh_seconds'),
                    'description': 'How frequently runtime state is persisted for the dashboard/API.'
                },
                {
                    'name': 'llm_review_hours',
                    'value': cadence.get('llm_review_hours'),
                    'description': 'Cadence for LLM supervisory review cycles.'
                }
            ],
            'raw_configs': _redact_sensitive({
                'risk_config': raw_risk_cfg,
                'trading_config': trading_cfg,
                'symbols_config': symbols_cfg,
                'system_config': system_cfg,
                'exchange_config': exchange_cfg,
                'cron_jobs': cron_jobs,
            })
        }

        return jsonify(formatted_config)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/metrics/dashboard')
def get_metrics_dashboard():
    """Get complete metrics for dashboard charts tab"""
    try:
        state = load_state() or {}
        enabled_symbols = get_enabled_symbols()

        equity_series = _load_equity_series_for_charts(limit=400)
        if not equity_series:
            equity_series = _load_equity_history()
        equity_filtered = _filter_valid_equity_history(
            equity_series,
            current_equity=_safe_float(state.get('equity'), 0)
        )

        trade_data = _load_normalized_trades(limit=2500, include_errors=True)
        finalized_trades = [t for t in trade_data if not t.get('pnl_pending') and t.get('realized_pnl') is not None]

        total_trades = len(trade_data)
        winning_trades = [t for t in finalized_trades if _safe_float(t.get('realized_pnl'), 0) > 0]
        losing_trades = [t for t in finalized_trades if _safe_float(t.get('realized_pnl'), 0) < 0]

        final_count = len(finalized_trades)
        win_rate = (len(winning_trades) / final_count * 100) if final_count > 0 else 0
        avg_win = sum(_safe_float(t.get('realized_pnl'), 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(_safe_float(t.get('realized_pnl'), 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0

        total_pnl = sum(_safe_float(t.get('realized_pnl'), 0) for t in finalized_trades)
        total_fees = sum(_safe_float(t.get('fee'), 0) for t in finalized_trades)
        max_drawdown = _calculate_max_drawdown_pct(equity_filtered or equity_series)

        daily_volume = {}
        for t in trade_data:
            ts = t.get('timestamp')
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                day = dt.strftime('%Y-%m-%d')
                daily_volume[day] = daily_volume.get(day, 0) + 1
            except Exception:
                continue

        risk_snapshot = _build_risk_transparency_snapshot(enabled_symbols=enabled_symbols)
        signal_history = _extract_signal_strength_history(symbols=enabled_symbols, limit=200)
        live_signals = state.get('last_signals') or {}

        summary = {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'total_pnl': total_pnl,
            'total_fees': total_fees,
            'net_pnl': total_pnl - total_fees,
            'max_drawdown': max_drawdown,
            'volume_by_day': daily_volume,
            'pending_trades': len([t for t in trade_data if t.get('pnl_pending')]),
            'weekly_pnl': risk_snapshot.get('weekly_pnl'),
            'exposure_pct': risk_snapshot.get('exposure_pct'),
            'total_notional': risk_snapshot.get('total_notional'),
            'margin_ratio': risk_snapshot.get('margin_ratio'),
            'open_positions': risk_snapshot.get('positions_count'),
            'active_signals': len(live_signals),
        }

        realtime = {
            'running': state.get('running', False),
            'equity': _safe_float(state.get('equity'), 0),
            'daily_pnl': _safe_float(state.get('daily_pnl'), 0),
            'weekly_pnl': risk_snapshot.get('weekly_pnl'),
            'exposure_pct': risk_snapshot.get('exposure_pct'),
            'total_notional': risk_snapshot.get('total_notional'),
            'drawdown_pct': risk_snapshot.get('drawdown_pct'),
            'open_positions': risk_snapshot.get('positions_count'),
            'active_signals': len(live_signals),
            'circuit_breaker': state.get('circuit_breaker', {}),
        }

        return jsonify({
            'equity': equity_series[-150:] if len(equity_series) > 150 else equity_series,
            'trades': trade_data[-50:] if len(trade_data) > 50 else trade_data,
            'summary': summary,
            'risk': risk_snapshot,
            'signal_history': signal_history,
            'realtime': realtime,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/orders/history')
def get_orders_history():
    """Get order history (filled, cancelled, etc.)"""
    try:
        symbol_filter = _parse_symbol_arg('symbol')
        limit = _parse_limit_arg(name='limit', default=1000, minimum=1, maximum=10000)

        # Try to get from API
        orders = []
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR / 'src'))
            from api.aster_api import get_order_history
            orders = get_order_history()
        except (ConnectionError, TimeoutError):
            logger.exception('Exchange service unavailable in get_orders_history, using fallback sources')
            orders = []
        except Exception:
            # Fallback to state file, then derive from trades/open-orders if missing
            state = load_state()
            orders = state.get('order_history', [])
            if not orders:
                for t in _load_normalized_trades(limit=500, include_errors=True):
                    orders.append({
                        'orderId': t.get('order_id') or t.get('id'),
                        'symbol': t.get('symbol'),
                        'side': t.get('side'),
                        'status': t.get('status'),
                        'type': 'MARKET',
                        'price': t.get('price'),
                        'origQty': t.get('quantity'),
                        'timestamp': t.get('timestamp'),
                        'source': 'trades_history'
                    })
                for o in _get_truth_open_orders(get_enabled_symbols()):
                    enriched = dict(o)
                    enriched['source'] = 'open_orders'
                    orders.append(enriched)

        if symbol_filter:
            orders = [o for o in orders if str(o.get('symbol', '')).upper() == symbol_filter]
            if not orders:
                return _error_response(
                    'not_found',
                    f"Order history for symbol '{symbol_filter}' was not found",
                    404,
                )

        orders = orders[:limit]
        
        return jsonify({
            'orders': orders,
            'count': len(orders)
        })
    except ValueError as e:
        return _error_response('invalid_parameter', str(e), 400)
    except FileNotFoundError as e:
        return _error_response('not_found', str(e), 404)
    except (ConnectionError, TimeoutError) as e:
        logger.exception('Service unavailable in get_orders_history')
        return _error_response('service_unavailable', 'Data service is temporarily unavailable', 503, {'reason': str(e)})
    except Exception:
        logger.exception('Unexpected error in get_orders_history')
        return _error_response('internal_error', 'An unexpected error occurred', 500)


if __name__ == '__main__':
    print("=" * 50)
    print("Aster Trading Dashboard")
    print("=" * 50)
    print(f"Starting server on http://localhost:5100")
    if WEBSOCKET_AVAILABLE:
        print("WebSocket enabled for real-time updates")
    else:
        print("WebSocket not available - using polling (install flask-socketio for real-time)")
    print("Press Ctrl+C to stop")
    print("=" * 50)
    
    # Use socketio.run if available, otherwise regular app.run
    if socketio:
        socketio.run(app, host='0.0.0.0', port=5100, debug=True, allow_unsafe_werkzeug=True)
    else:
        app.run(host='0.0.0.0', port=5100, debug=True, use_reloader=False)
def _run_command(cmd: str, timeout: int = 30) -> dict:
    """Run bash command in trading directory and capture output."""
    try:
        result = subprocess.run(
            ['bash', '-c', f'cd {BASE_DIR} && {cmd}'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            'success': False,
            'stdout': exc.stdout or '',
            'stderr': f'Timeout: {exc}',
            'returncode': -1,
        }
    except Exception as exc:
        return {
            'success': False,
            'stdout': '',
            'stderr': str(exc),
            'returncode': -1,
        }


def _tail_file(path: Path, max_lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open('r', errors='ignore') as fh:
            return list(deque((ln.rstrip('\n') for ln in fh), maxlen=max_lines))
    except Exception:
        return []
