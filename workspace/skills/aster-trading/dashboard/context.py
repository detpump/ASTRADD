"""Shared dashboard helpers/constants extracted from server module."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional

if TYPE_CHECKING:
    from services.risk_service import get_normalized_risk_config  # type: ignore[import]
    from state.dashboard_cache import load_dashboard_snapshot  # type: ignore[import]
    from state.state_service import state_service  # type: ignore[import]
    from state.models import SystemState  # type: ignore[import]

# Base paths shared with dashboard server - MUST be set before other imports
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = Path('/Users/FIRMAS/.openclaw')
ROOT_LOGS_DIR = ROOT_DIR / 'logs'
HISTORY_DIR = ROOT_LOGS_DIR / 'history'
SRC_DIR = BASE_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Now imports will work
try:
    from services.risk_service import get_normalized_risk_config  # type: ignore[import]
except Exception:
    get_normalized_risk_config = None

try:
    from state.dashboard_cache import load_dashboard_snapshot  # type: ignore[import]
    DASHBOARD_CACHE_AVAILABLE = True
except ImportError:
    DASHBOARD_CACHE_AVAILABLE = False
    load_dashboard_snapshot = None

# File references
STATE_FILE = BASE_DIR / 'logs' / 'v2_state.json'
EQUITY_FILE = ROOT_LOGS_DIR / 'history' / 'equity.jsonl'
TRADES_FILE = ROOT_LOGS_DIR / 'history' / 'trades.jsonl'
RISK_FILE = ROOT_LOGS_DIR / 'history' / 'risk.jsonl'
CONFIG_DIR = BASE_DIR / 'config'
CRON_JOBS_FILE = ROOT_DIR / 'cron' / 'jobs.json'

# Runtime controls
CACHE_MAX_AGE_SECONDS = 20
USE_LEGACY_STATE_CACHE = False
DATA_SOURCE_FLAGS: Dict[str, str] = {
    'positions': 'unknown',
    'orders': 'unknown',
}

# Log helpers
SIGNAL_LOG_FILES = [
    BASE_DIR / 'logs' / 'v2_output.log',
    BASE_DIR / 'logs' / 'v2_real_test.log',
    BASE_DIR / 'logs' / 'v2_fixed_test.log',
]

LOG_STRUCTURED_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[\.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+-\s+(?P<logger>[^-]+?)\s+-\s+(?P<level>[A-Z]+)\s+-\s+(?P<msg>.*)$'
)
SIGNAL_STRENGTH_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+__main__\s+-\s+INFO\s+-\s+(?P<symbol>[A-Z]+USDT):\s+(?P<action>BUY|SELL|HOLD)\s+\(strength=(?P<strength>-?\d+(?:\.\d+)?),\s+conf=(?P<confidence>-?\d+(?:\.\d+)?)\)'
)
SIGNAL_RECEIVED_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+__main__\s+-\s+INFO\s+-\s+.*SIGNAL RECIBIDO\s+\|\s+(?P<symbol>[A-Z]+USDT)\s+\|\s+(?P<action>BUY|SELL|HOLD)\s+\|\s+\$(?P<price>-?\d+(?:\.\d+)?)'
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == '':
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except Exception:
        return default


def _first_non_none(*values: Any) -> Any:
    for val in values:
        if val is not None:
            return val
    return None


def _parse_timestamp_ms(value: Any) -> int:
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
            continue

    try:
        dt = datetime.fromisoformat(normalized)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _to_iso(value: Any) -> str:
    ms = _parse_timestamp_ms(value)
    if ms <= 0:
        return ''
    return datetime.utcfromtimestamp(ms / 1000).isoformat() + 'Z'


def _tail_lines(path: Path, limit: int = 300) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, 'r', errors='ignore') as fh:
            return list(deque((ln.rstrip('\n') for ln in fh), maxlen=limit))
    except Exception:
        return []


def _load_json_file(path: Path, default: Optional[Any] = None) -> Any:
    try:
        if path.exists():
            with open(path, 'r') as fh:
                return json.load(fh)
    except Exception:
        pass
    return {} if default is None else default


def _load_shared_risk_config():
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


logger = logging.getLogger(__name__)


__all__ = [
    'BASE_DIR',
    'ROOT_DIR',
    'ROOT_LOGS_DIR',
    'HISTORY_DIR',
    'STATE_FILE',
    'EQUITY_FILE',
    'TRADES_FILE',
    'RISK_FILE',
    'CONFIG_DIR',
    'CRON_JOBS_FILE',
    'CACHE_MAX_AGE_SECONDS',
    'USE_LEGACY_STATE_CACHE',
    'DATA_SOURCE_FLAGS',
    'SIGNAL_LOG_FILES',
    'LOG_STRUCTURED_RE',
    'SIGNAL_STRENGTH_RE',
    'SIGNAL_RECEIVED_RE',
    'DASHBOARD_CACHE_AVAILABLE',
    'load_dashboard_snapshot',
    '_safe_float',
    '_safe_int',
    '_first_non_none',
    '_parse_timestamp_ms',
    '_to_iso',
    '_tail_lines',
    '_load_json_file',
    '_load_shared_risk_config',
    '_classify_order',
    'set_global_trade_tracker',
    'get_global_trade_tracker',
    'load_state',
    'save_state',
    'get_enabled_symbols',
]


_GLOBAL_TRADE_TRACKER = None
_STATE_ORDER_CLASSIFIER = None


def _get_state_order_classifier():
    global _STATE_ORDER_CLASSIFIER
    if _STATE_ORDER_CLASSIFIER is None:
        try:
            from dashboard.state import _classify_order as state_classify_order
            _STATE_ORDER_CLASSIFIER = state_classify_order
        except Exception as exc:
            logger.debug('State order classifier unavailable yet: %s', exc)
            return None
    return _STATE_ORDER_CLASSIFIER


def _classify_order(order: dict) -> str:
    classifier = _get_state_order_classifier()
    if classifier is None:
        logger.debug('Order classifier not initialized; returning UNKNOWN for symbol=%s', order.get('symbol'))
        return 'UNKNOWN'
    try:
        return classifier(order)
    except Exception as exc:
        logger.warning('Failed to classify order for symbol=%s id=%s: %s', order.get('symbol'), order.get('orderId') or order.get('order_id'), exc)
        return 'UNKNOWN'


def set_global_trade_tracker(tracker):
    global _GLOBAL_TRADE_TRACKER
    _GLOBAL_TRADE_TRACKER = tracker


def get_global_trade_tracker():
    return _GLOBAL_TRADE_TRACKER


def load_state():
    try:
        from state.state_service import state_service  # type: ignore[import]
        from state.models import SystemState  # type: ignore[import]

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
                'equity': system_state.equity or 0,
                'daily_pnl': system_state.daily_pnl or 0,
                'drawdown_pct': system_state.drawdown_pct or 0,
                'last_signals': system_state.last_signals or {},
                'market': system_state.market_data or {},
                'healing_metrics': system_state.healing_metrics or {},
                'symbols': system_state.enabled_symbols or [],
                'recent_errors': system_state.recent_errors or [],
            })

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
                'status_distribution': getattr(tracker, 'status_distribution', {}) or {},
                'active_order_symbols': getattr(tracker, 'active_order_symbols', []) or [],
            }
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
    except Exception as exc:
        print(f"Error loading state from DB: {exc}")

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
        'timestamp': int(time.time() * 1000),
    }


def save_state(state):
    try:
        from state.state_service import state_service  # type: ignore[import]
        from state.models import SystemState  # type: ignore[import]

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
    except Exception as exc:
        print(f"Error saving state: {exc}")


def get_enabled_symbols():
    try:
        config, _ = _load_shared_risk_config()
        symbols_config = config.get('symbols', {}) if isinstance(config, dict) else {}
        enabled = [s for s, cfg in symbols_config.items() if isinstance(cfg, dict) and cfg.get('enabled', False)]
        if enabled:
            return enabled
    except Exception:
        pass

    state = load_state()
    return state.get('symbols', [])
