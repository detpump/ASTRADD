from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime

from dashboard.context import (
    BASE_DIR,
    EQUITY_FILE,
    SIGNAL_LOG_FILES,
    SIGNAL_STRENGTH_RE,
    TRADES_FILE,
    USE_LEGACY_STATE_CACHE,
    _first_non_none,
    _load_json_file,
    _parse_timestamp_ms,
    _classify_order,
    _safe_float,
    _safe_int,
    _to_iso,
    _tail_lines,
    load_state,
)


logger = logging.getLogger(__name__)

def _normalize_trade_record(raw: dict) -> dict:
    ts_close = _first_non_none(raw.get('ts_close'), raw.get('timestamp'))
    ts_open = raw.get('ts_open')
    ts_primary = _first_non_none(ts_close, ts_open)
    timestamp_ms = _parse_timestamp_ms(_first_non_none(raw.get('timestamp_ms'), ts_primary))

    pnl_raw = _first_non_none(
        raw.get('realized_pnl_usdt'),
        raw.get('pnl'),
        raw.get('pnl_usdt'),
        raw.get('realized_pnl_gross_usdt')
    )
    fee_raw = raw.get('fee_approx_usdt')

    pnl = None if pnl_raw is None else _safe_float(pnl_raw)
    fee = None if fee_raw is None else _safe_float(fee_raw)
    pnl_after_fee = (pnl - fee) if (pnl is not None and fee is not None) else None

    symbol = str(raw.get('symbol', '')).upper()
    side = str(raw.get('side', '')).upper()
    status = str(raw.get('status', '')).upper()
    if not status:
        status = 'LEGACY'

    size = _safe_float(_first_non_none(raw.get('quantity'), raw.get('size'), raw.get('qty')), 0.0)
    entry_price = _first_non_none(raw.get('entry_price'), raw.get('price'))
    exit_price = _first_non_none(raw.get('exit_price'), raw.get('price'))

    normalized = {
        'id': _first_non_none(raw.get('order_id'), raw.get('orderId'), f"{symbol}_{timestamp_ms}"),
        'timestamp': _to_iso(ts_primary) if ts_primary else _to_iso(timestamp_ms),
        'timestamp_ms': timestamp_ms,
        'ts_open': _to_iso(ts_open) if ts_open else '',
        'ts_close': _to_iso(ts_close) if ts_close else _to_iso(timestamp_ms),
        'symbol': symbol,
        'side': side,
        'status': status,
        'size': size,
        'quantity': size,
        'price': _safe_float(raw.get('price', 0)),
        'entry_price': _safe_float(entry_price, 0),
        'exit_price': _safe_float(exit_price, 0),
        'realized_pnl': pnl,
        'realized_pnl_usdt': pnl,
        'fee': fee,
        'fee_approx_usdt': fee,
        'pnl_after_fee': pnl_after_fee,
        'pnl_pending': pnl is None,
        'pnl_status': 'PENDING' if pnl is None else 'FINAL',
        'order_id': _first_non_none(raw.get('order_id'), raw.get('orderId')),
        'notional': _safe_float(raw.get('notional', size * _safe_float(raw.get('price', 0)))),
        'reason_open': _first_non_none(raw.get('reason_open'), raw.get('setup_type')),
        'reason_close': _first_non_none(raw.get('reason_close'), raw.get('reason')),
        'leverage': _safe_int(raw.get('leverage', 1), 1),
        'raw': raw,
    }
    return normalized


def _load_normalized_trades(limit=None, include_errors=True):
    trades = []
    if not TRADES_FILE.exists():
        return trades

    try:
        with open(TRADES_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    normalized = _normalize_trade_record(raw)
                    if not include_errors and normalized.get('status') == 'ERROR':
                        continue
                    trades.append(normalized)
                except Exception:
                    continue
    except Exception:
        return []

    trades.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)
    if isinstance(limit, int) and limit > 0:
        return trades[:limit]
    return trades


def _load_equity_history():
    history = []

    # DB-first: risk_state snapshot
    try:
        from state.state_service import state_service
        rs = state_service.get_risk_state()
        if rs:
            ts_ms = int(time.time() * 1000)
            history.append({
                'timestamp_ms': ts_ms,
                'timestamp': _to_iso(ts_ms),
                'equity': _safe_float(rs.account_equity, 0),
                'daily_pnl': _safe_float(rs.daily_pnl, 0),
                'drawdown_pct': _safe_float(rs.drawdown_pct, 0),
            })
    except Exception:
        pass

    # Legacy state file fallback (guarded by flag)
    if USE_LEGACY_STATE_CACHE:
        state = load_state()
        if state:
            ts = state.get('timestamp')
            history.append({
                'timestamp_ms': ts,
                'timestamp': _to_iso(ts) if ts else None,
                'equity': _safe_float(state.get('equity'), 0),
                'daily_pnl': _safe_float(state.get('daily_pnl'), 0),
                'drawdown_pct': _safe_float(state.get('drawdown_pct'), 0),
            })

    # Legacy equity history file fallback
    if not history and EQUITY_FILE.exists():
        try:
            with open(EQUITY_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue

                    equity = _first_non_none(row.get('equity'), row.get('equity_usdt'))
                    if equity is None:
                        continue

                    equity_val = _safe_float(equity, 0)
                    ts_any = _first_non_none(row.get('timestamp_ms'), row.get('timestamp'), row.get('ts'))
                    ts_ms = _parse_timestamp_ms(ts_any)
                    history.append({
                        'timestamp_ms': ts_ms,
                        'timestamp': _to_iso(ts_any) if ts_any else _to_iso(ts_ms),
                        'equity': equity_val,
                        'daily_pnl': _safe_float(row.get('daily_pnl', 0)),
                        'drawdown_pct': _safe_float(row.get('drawdown_pct', 0)),
                    })
        except Exception:
            return []

    history.sort(key=lambda x: x.get('timestamp_ms', 0))
    return history


def _load_equity_series_for_charts(limit=200):
    history = _load_equity_history()
    merged: dict[int, dict] = {}

    def _normalize_point(point: dict, source: str | None = None):
        if not point:
            return None
        ts_ms = _safe_int(point.get('timestamp_ms'), 0)
        if not ts_ms:
            ts_ms = _parse_timestamp_ms(point.get('timestamp')) or int(time.time() * 1000)
        normalized = {
            'timestamp_ms': ts_ms,
            'timestamp': point.get('timestamp') or _to_iso(ts_ms),
            'equity': _safe_float(point.get('equity'), 0),
        }
        if point.get('daily_pnl') is not None:
            normalized['daily_pnl'] = _safe_float(point.get('daily_pnl'), 0)
        if point.get('drawdown_pct') is not None:
            normalized['drawdown_pct'] = _safe_float(point.get('drawdown_pct'), 0)
        if source:
            normalized['source'] = source
        return normalized

    for item in history:
        normalized = _normalize_point(item, source=item.get('source'))
        if not normalized:
            continue
        merged[normalized['timestamp_ms']] = normalized

    state = load_state() or {}
    state_point = _normalize_point({
        'timestamp_ms': state.get('timestamp'),
        'equity': state.get('equity'),
        'daily_pnl': state.get('daily_pnl'),
        'drawdown_pct': state.get('drawdown_pct'),
    }, source='state')
    if state_point:
        merged[state_point['timestamp_ms']] = state_point

    risk_point = _normalize_point({
        'timestamp_ms': state.get('timestamp') or int(time.time() * 1000),
        'equity': state.get('equity'),
        'daily_pnl': state.get('daily_pnl'),
        'drawdown_pct': state.get('drawdown_pct'),
    }, source='risk_state')
    if risk_point:
        merged[risk_point['timestamp_ms']] = risk_point

    ordered = [merged[k] for k in sorted(merged.keys())]
    return ordered[-limit:]


def _filter_valid_equity_history(history, current_equity=None):
    if not history:
        return []

    positive = [h for h in history if h.get('equity', 0) > 0]
    if not positive:
        return []

    recent = positive[-300:] if len(positive) > 300 else positive
    sorted_vals = sorted(h['equity'] for h in recent)
    median_eq = sorted_vals[len(sorted_vals) // 2]
    baseline = current_equity if (current_equity and current_equity > 0) else median_eq
    lower = max(0.01, baseline * 0.1)
    upper = baseline * 10.0

    valid = [h for h in positive if lower <= h['equity'] <= upper]
    return valid if len(valid) >= 5 else recent


def _calculate_max_drawdown_pct(history):
    if not history:
        return 0.0

    peak = max(history[0].get('equity', 0), 0.0)
    max_dd = 0.0
    for item in history:
        equity = max(item.get('equity', 0), 0.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = ((peak - equity) / peak) * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _recalculate_drawdown_pct(current_equity=None):
    history = _load_equity_history()
    valid = _filter_valid_equity_history(history, current_equity=current_equity)
    return _calculate_max_drawdown_pct(valid)


def _extract_signal_strength_history(symbols=None, limit=200):
    allowed = {s.upper() for s in (symbols or [])}
    events = []

    state = load_state()
    state_signals = state.get('last_signals', {}) if isinstance(state.get('last_signals', {}), dict) else {}
    for symbol, sig in state_signals.items():
        symbol_upper = str(symbol).upper()
        if allowed and symbol_upper not in allowed:
            continue
        ts_ms = _parse_timestamp_ms(sig.get('timestamp'))
        events.append({
            'symbol': symbol_upper,
            'action': str(sig.get('action', '')).upper(),
            'strength': _safe_float(sig.get('strength'), 0),
            'confidence': _safe_float(sig.get('confidence'), 0),
            'timestamp_ms': ts_ms,
            'timestamp': _to_iso(ts_ms),
        })

    if len(events) < limit:
        for log_path in SIGNAL_LOG_FILES:
            lines = _tail_lines(log_path, limit=5000)
            for line in lines:
                match = SIGNAL_STRENGTH_RE.match(line)
                if not match:
                    continue
                symbol = match.group('symbol').upper()
                if allowed and symbol not in allowed:
                    continue
                ts_text = match.group('ts')
                ts_ms = _parse_timestamp_ms(ts_text)
                events.append({
                    'symbol': symbol,
                    'action': match.group('action'),
                    'strength': _safe_float(match.group('strength'), 0),
                    'confidence': _safe_float(match.group('confidence'), 0),
                    'timestamp_ms': ts_ms,
                    'timestamp': _to_iso(ts_text),
                })

    dedup = {}
    for ev in events:
        key = (ev['symbol'], ev['timestamp_ms'])
        dedup[key] = ev
    merged = list(dedup.values())
    merged.sort(key=lambda x: x.get('timestamp_ms', 0))

    if isinstance(limit, int) and limit > 0 and len(merged) > limit:
        merged = merged[-limit:]

    trades = _load_normalized_trades(limit=500, include_errors=False)
    for ev in merged:
        symbol = ev.get('symbol')
        action = ev.get('action')
        ts_ms = ev.get('timestamp_ms', 0)
        executed = False
        for tr in trades:
            if tr.get('symbol') != symbol:
                continue
            if action and tr.get('side') and tr.get('side') != action:
                continue
            delta = abs(tr.get('timestamp_ms', 0) - ts_ms)
            if delta <= 10 * 60 * 1000:
                executed = True
                break
        ev['trade_executed'] = executed
    return merged


def _extract_latest_signal_reason_map(symbols):
    """Extract latest trigger/LLM reason per symbol from runtime logs."""
    reason_map = {}
    selected_log = None
    for candidate in SIGNAL_LOG_FILES:
        if candidate.exists():
            selected_log = candidate
            break

    if not selected_log:
        return reason_map

    lines = _tail_lines(selected_log, limit=6000)
    for symbol in symbols:
        token = f"SIGNAL RECIBIDO | {symbol} |"
        idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if token in lines[i]:
                idx = i
                break
        if idx < 0:
            continue

        trigger_reason = None
        llm_reason = None
        trigger_source = 'ML'

        # forward scan until next signal block
        for j in range(idx + 1, min(idx + 40, len(lines))):
            line = lines[j]
            if 'SIGNAL RECIBIDO |' in line:
                break
            if 'Decisión:' in line:
                llm_reason = line.split('Decisión:', 1)[-1].strip()
                trigger_source = 'LLM_REVIEW'
            elif 'Revisión LLM' in line:
                trigger_source = 'LLM_REVIEW'
            elif 'Reason:' in line and 'posición abierta' in line.lower():
                trigger_reason = line.split('Reason:', 1)[-1].strip()
                trigger_source = 'POSITION_GUARD'
            elif 'SEÑAL BLOQUEADA' in line:
                trigger_reason = line.split('SEÑAL BLOQUEADA:', 1)[-1].strip()

        reason_map[symbol] = {
            'trigger_source': trigger_source,
            'trigger_reason': trigger_reason,
            'llm_reason': llm_reason,
        }
    return reason_map


def _distance_pct(mark_price, target_price, is_long):
    if mark_price <= 0 or target_price <= 0:
        return None
    raw = ((target_price - mark_price) / mark_price) * 100.0
    return raw if is_long else -raw


def _build_position_targets(position, symbol_orders):
    is_long = _safe_float(position.get('positionAmt', 0)) > 0
    mark = _safe_float(position.get('markPrice', 0))

    tp = []
    sl = []
    surf = []
    entry = []

    for order in symbol_orders:
        classification = _classify_order(order)
        if classification == 'UNKNOWN':
            logger.warning(
                'Order classification UNKNOWN for symbol=%s order=%s type=%s client_id=%s',
                order.get('symbol'),
                order.get('orderId') or order.get('order_id'),
                order.get('type'),
                order.get('clientOrderId') or order.get('client_order_id'),
            )

        price_value = _safe_float(order.get('price', order.get('stopPrice', 0)))
        if price_value <= 0:
            logger.debug(
                'Order price missing/zero for symbol=%s order=%s, using stopPrice fallback',
                order.get('symbol'),
                order.get('orderId') or order.get('order_id'),
            )

        level = {
            'id': _first_non_none(order.get('orderId'), order.get('order_id')),
            'price': price_value,
            'status': str(order.get('status', '')).upper(),
            'type': str(order.get('type', '')).upper(),
            'classification': classification,
            'gap_pct': _distance_pct(mark, price_value, is_long),
            'gap_abs': abs(price_value - mark) if mark > 0 else None,
        }
        if classification == 'TP':
            tp.append(level)
        elif classification == 'SL':
            sl.append(level)
        elif classification == 'SURF':
            surf.append(level)
        elif classification in {'ENTRY', 'REDUCE_ONLY'}:
            entry.append(level)

    tp.sort(key=lambda x: x.get('price', 0), reverse=not is_long)
    sl.sort(key=lambda x: x.get('price', 0), reverse=is_long)
    surf.sort(key=lambda x: x.get('price', 0), reverse=not is_long)
    return tp, sl, surf, entry
