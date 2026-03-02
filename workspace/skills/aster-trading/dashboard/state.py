from __future__ import annotations

import time
from dashboard.context import (
    CACHE_MAX_AGE_SECONDS,
    DATA_SOURCE_FLAGS,
    DASHBOARD_CACHE_AVAILABLE,
    _first_non_none,
    _parse_timestamp_ms,
    _safe_float,
    _safe_int,
    _to_iso,
    get_global_trade_tracker,
    load_dashboard_snapshot,
)

def _is_open_position(pos: dict) -> bool:
    amt = abs(_safe_float(pos.get('positionAmt', pos.get('position_amt', 0))))
    return amt > 1e-12


def _normalize_position(pos: dict) -> dict:
    normalized = dict(pos)
    normalized['symbol'] = str(pos.get('symbol', '')).upper()
    normalized['positionAmt'] = _safe_float(pos.get('positionAmt', pos.get('position_amt', 0)))
    normalized['entryPrice'] = _safe_float(pos.get('entryPrice', pos.get('entry_price', 0)))
    normalized['markPrice'] = _safe_float(pos.get('markPrice', pos.get('mark_price', 0)))
    normalized['unRealizedProfit'] = _safe_float(pos.get('unRealizedProfit', pos.get('unrealizedProfit', pos.get('unrealized_pnl', 0))))
    normalized['liquidationPrice'] = _safe_float(pos.get('liquidationPrice', pos.get('liquidation_price', 0)))
    normalized['leverage'] = _safe_int(pos.get('leverage', 1), 1)
    normalized['updateTime'] = _safe_int(pos.get('updateTime', pos.get('update_time', 0)))
    normalized['side'] = 'LONG' if normalized['positionAmt'] > 0 else 'SHORT'
    normalized['size'] = abs(normalized['positionAmt'])
    normalized['unrealized_pnl'] = normalized['unRealizedProfit']
    return normalized


def _load_cached_dashboard_snapshot(max_age_seconds: int = CACHE_MAX_AGE_SECONDS):
    """Load cached dashboard snapshot written by trading_system if fresh."""
    if not DASHBOARD_CACHE_AVAILABLE or not load_dashboard_snapshot:
        return None

    try:
        snapshot = load_dashboard_snapshot()
    except Exception:
        return None

    if not isinstance(snapshot, dict) or not snapshot:
        return None

    ts_any = snapshot.get('timestamp') or snapshot.get('cache_timestamp')
    ts_ms = _parse_timestamp_ms(ts_any)
    now_ms = int(time.time() * 1000)
    if ts_ms <= 0:
        ts_ms = now_ms

    if max_age_seconds and (now_ms - ts_ms) > (max_age_seconds * 1000):
        return None

    snapshot['_timestamp_ms'] = ts_ms
    snapshot['_age_seconds'] = round((now_ms - ts_ms) / 1000.0, 3)
    return snapshot


def _normalize_cached_position(symbol: str, raw: dict, snapshot_ts_ms: int) -> dict | None:
    if not isinstance(raw, dict):
        return None

    symbol_upper = str(symbol).upper()
    size = _safe_float(raw.get('size', raw.get('positionAmt', 0)))
    if size <= 0:
        return None

    side = str(raw.get('side', 'LONG')).upper()
    multiplier = 1 if side in {'LONG', 'BUY'} else -1
    position_amt = size * multiplier
    entry_price = _safe_float(_first_non_none(
        raw.get('avg_entry_price'),
        raw.get('entry_price'),
        raw.get('entryPrice'),
    ), 0.0)
    mark_price = _safe_float(_first_non_none(
        raw.get('mark_price'),
        raw.get('markPrice'),
        raw.get('last_price'),
        raw.get('price'),
        entry_price,
    ), entry_price)
    unrealized = _safe_float(_first_non_none(
        raw.get('unrealized_pnl'),
        raw.get('unRealizedProfit'),
        raw.get('unrealizedProfit'),
        0.0,
    ), 0.0)
    update_time = _safe_int(_first_non_none(
        raw.get('last_update_ms'),
        raw.get('updateTime'),
        raw.get('open_time'),
        raw.get('timestamp'),
    ), snapshot_ts_ms)

    normalized = {
        'symbol': symbol_upper,
        'positionAmt': position_amt,
        'entryPrice': entry_price,
        'markPrice': mark_price,
        'unRealizedProfit': unrealized,
        'unrealized_pnl': unrealized,
        'liquidationPrice': _safe_float(raw.get('liquidationPrice', raw.get('liquidation_price', 0)), 0.0),
        'leverage': _safe_int(raw.get('leverage', raw.get('max_leverage', 1)), 1),
        'updateTime': update_time,
        'side': 'LONG' if position_amt >= 0 else 'SHORT',
        'size': abs(position_amt),
    }
    return normalized


def _get_cached_positions(enabled_symbols=None):
    snapshot = _load_cached_dashboard_snapshot()
    if not snapshot:
        return []

    raw_positions = snapshot.get('positions')
    if not isinstance(raw_positions, dict):
        return []

    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}
    results = []
    for symbol, raw in raw_positions.items():
        symbol_upper = str(symbol).upper()
        if enabled and symbol_upper not in enabled:
            continue
        normalized = _normalize_cached_position(symbol_upper, raw, snapshot.get('_timestamp_ms', int(time.time() * 1000)))
        if normalized and _is_open_position(normalized):
            results.append(normalized)
    return results


def _normalize_cached_order(raw: dict, fallback_ts_ms: int | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None

    symbol = str(raw.get('symbol', raw.get('symbol_id', ''))).upper()
    if not symbol:
        return None

    timestamps = raw.get('timestamps') if isinstance(raw.get('timestamps'), dict) else {}
    ts_source = _first_non_none(
        raw.get('timestamp'),
        raw.get('order_timestamp'),
        timestamps.get('ORDER_SENT'),
        timestamps.get('ORDER_CONFIRMED'),
        timestamps.get('SIGNAL_GENERATED'),
        fallback_ts_ms,
    )
    timestamp_ms = _parse_timestamp_ms(ts_source) if ts_source is not None else 0
    if timestamp_ms <= 0 and isinstance(fallback_ts_ms, int):
        timestamp_ms = fallback_ts_ms

    normalized = {
        'symbol': symbol,
        'status': str(raw.get('status', 'PENDING')).upper(),
        'side': str(raw.get('side', raw.get('action', 'BUY'))).upper(),
        'type': str(raw.get('type', raw.get('order_type', 'LIMIT'))).upper(),
        'origQty': _safe_float(_first_non_none(raw.get('origQty'), raw.get('quantity'), raw.get('size')), 0.0),
        'price': _safe_float(raw.get('price', raw.get('limit_price', 0.0)), 0.0),
        'clientOrderId': str(raw.get('clientOrderId') or raw.get('correlation_id') or f'{symbol}_CACHE'),
        'orderId': raw.get('order_id') or raw.get('orderId'),
        'timestamp_ms': timestamp_ms,
        'timestamp': _to_iso(timestamp_ms),
    }
    normalized['classification'] = _classify_order(normalized)
    normalized['is_pending'] = _is_pending_order(normalized)
    return normalized


def _get_cached_orders(enabled_symbols=None):
    snapshot = _load_cached_dashboard_snapshot()
    if not snapshot:
        return []

    raw_orders = snapshot.get('active_orders')
    if not isinstance(raw_orders, list):
        return []

    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}
    normalized = []
    for raw in raw_orders:
        order = _normalize_cached_order(raw, snapshot.get('_timestamp_ms', int(time.time() * 1000)))
        if not order:
            continue
        if enabled and order.get('symbol') not in enabled:
            continue
        normalized.append(order)
    return normalized


def _get_db_orders(enabled_symbols=None):
    """Load orders from DB via state_service (active only)."""
    try:
        from state.state_service import state_service
        rows = state_service.get_orders(active_only=True, limit=300)
    except Exception:
        return []

    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}
    now_ms = int(time.time() * 1000)
    normalized = []
    for o in rows:
        symbol = str(o.symbol).upper()
        if enabled and symbol not in enabled:
            continue
        norm = {
            'symbol': symbol,
            'status': str(o.status or '').upper(),
            'side': str(o.side or '').upper(),
            'type': str(o.type or '').upper(),
            'origQty': _safe_float(o.quantity, 0.0),
            'price': _safe_float(o.price, 0.0),
            'clientOrderId': str(o.client_order_id or o.order_id or f'{symbol}_DB'),
            'orderId': o.order_id,
            'timestamp_ms': _safe_int(o.update_time, now_ms),
            'timestamp': _to_iso(_safe_int(o.update_time, now_ms)),
        }
        norm['classification'] = _classify_order(norm)
        norm['is_pending'] = _is_pending_order(norm)
        normalized.append(norm)
    return normalized


def _get_tracker_fallback_orders(enabled_symbols=None):
    tracker = get_global_trade_tracker()
    if not tracker:
        return []

    try:
        tracker_orders = tracker.get_active_orders_summary()
    except Exception:
        return []

    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}
    now_ms = int(time.time() * 1000)
    normalized = []
    for raw in tracker_orders:
        order = _normalize_cached_order(raw, now_ms)
        if not order:
            continue
        if enabled and order.get('symbol') not in enabled:
            continue
        normalized.append(order)
    return normalized


def _get_truth_positions(enabled_symbols=None):
    """Single source of truth for open positions with DB-first priority."""
    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}

    # 1) DB snapshot via state_service
    try:
        from state.state_service import state_service
        db_positions = state_service.get_positions()
        if db_positions:
            normalized = []
            for p in db_positions:
                symbol = str(p.symbol).upper()
                if enabled and symbol not in enabled:
                    continue
                merged = {
                    'symbol': symbol,
                    'positionAmt': p.quantity if p.side.upper() == 'BUY' else -abs(p.quantity),
                    'entryPrice': p.entry_price,
                    'markPrice': p.mark_price or p.entry_price,
                    'unRealizedProfit': p.unrealized_pnl or 0.0,
                    'liquidationPrice': 0.0,
                    'leverage': p.leverage or 1,
                    'updateTime': p.open_time or 0,
                }
                norm = _normalize_position(merged)
                if _is_open_position(norm):
                    normalized.append(norm)
            if normalized:
                DATA_SOURCE_FLAGS['positions'] = 'db'
                return normalized
    except Exception:
        pass

    # 2) Exchange REST fallback
    try:
        from api.aster_api import get_positions_v3
        raw = get_positions_v3(list(enabled)) if enabled else get_positions_v3()
        if isinstance(raw, list):
            positions = [_normalize_position(p) for p in raw if isinstance(p, dict)]
            positions = [p for p in positions if _is_open_position(p)]
            if enabled:
                positions = [p for p in positions if p.get('symbol') in enabled]
            if positions:
                DATA_SOURCE_FLAGS['positions'] = 'exchange_rest'
                return positions
    except Exception:
        pass

    DATA_SOURCE_FLAGS['positions'] = 'empty'
    return []


def _classify_order(order: dict) -> str:
    otype = str(order.get('type', '')).upper()
    client_id = str(order.get('clientOrderId', order.get('client_order_id', ''))).upper()
    reduce_only = bool(order.get('reduceOnly', False))

    if 'SURF' in client_id:
        return 'SURF'
    if 'TAKE_PROFIT' in otype or '_TP_' in client_id or client_id.endswith('_TP'):
        return 'TP'
    if 'STOP' in otype or '_SL_' in client_id or client_id.endswith('_SL'):
        return 'SL'
    if reduce_only:
        return 'REDUCE_ONLY'
    if otype in ('MARKET', 'LIMIT'):
        return 'ENTRY'
    return 'OTHER'


def _is_pending_order(order: dict) -> bool:
    return str(order.get('status', '')).upper() in {'NEW', 'PARTIALLY_FILLED', 'PENDING'}


def _get_truth_open_orders(enabled_symbols=None):
    """Single source of truth for open/pending orders with DB-first priority."""
    enabled = {s.upper() for s in (enabled_symbols or []) if isinstance(s, str)}

    db_orders = _get_db_orders(enabled)
    if db_orders:
        DATA_SOURCE_FLAGS['orders'] = 'db'
        return db_orders

    try:
        from api.aster_api import get_open_orders
        raw = get_open_orders()
        if isinstance(raw, dict) and raw.get('error'):
            return []
        if not isinstance(raw, list):
            return []

        orders = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get('symbol', '')).upper()
            if enabled and symbol and symbol not in enabled:
                continue
            enriched = dict(item)
            enriched['symbol'] = symbol
            enriched['status'] = str(item.get('status', '')).upper()
            enriched['side'] = str(item.get('side', '')).upper()
            enriched['type'] = str(item.get('type', '')).upper()
            enriched['origQty'] = _safe_float(item.get('origQty', item.get('quantity', item.get('qty', 0))))
            enriched['price'] = _safe_float(item.get('price', item.get('stopPrice', item.get('avgPrice', 0))))
            enriched['timestamp_ms'] = _parse_timestamp_ms(
                _first_non_none(item.get('updateTime'), item.get('time'), item.get('workingTime'))
            )
            enriched['timestamp'] = _to_iso(enriched['timestamp_ms'])
            enriched['classification'] = _classify_order(enriched)
            enriched['is_pending'] = _is_pending_order(enriched)
            orders.append(enriched)
        if orders:
            DATA_SOURCE_FLAGS['orders'] = 'exchange_rest'
        return orders
    except Exception:
        pass

    DATA_SOURCE_FLAGS['orders'] = 'empty'
    return []
