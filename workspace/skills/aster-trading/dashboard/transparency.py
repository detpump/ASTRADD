from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime

from dashboard.context import (
    BASE_DIR,
    _first_non_none,
    _load_shared_risk_config,
    _safe_float,
    _safe_int,
    _to_iso,
)
from dashboard.metrics import (
    _extract_signal_strength_history,
    _load_normalized_trades,
)
from dashboard.context import get_enabled_symbols, load_state
from dashboard.state import (
    _classify_order,
    _get_truth_open_orders,
    _get_truth_positions,
    _is_open_position,
    _normalize_position,
)

def _build_system_cadence(system_cfg, cron_jobs):
    loop_seconds = _safe_int(system_cfg.get('system', {}).get('main_loop_seconds', 60), 60)
    state_refresh = _safe_int(system_cfg.get('system', {}).get('state_save_interval_ms', 60000), 60000)
    llm_hours = _safe_int(system_cfg.get('system', {}).get('llm_review_hours', 3), 3)

    cron_descriptions = []
    for job in cron_jobs:
        state = job.get('state', {})
        cron_descriptions.append({
            'name': job.get('name', job.get('id', 'unknown')),
            'expr': job.get('schedule', {}).get('expr', ''),
            'enabled': bool(job.get('enabled', False)),
            'next_run_at_ms': state.get('nextRunAtMs'),
            'last_run_at_ms': state.get('lastRunAtMs'),
            'last_status': state.get('lastStatus')
        })

    return {
        'main_loop_seconds': loop_seconds,
        'state_refresh_seconds': round(state_refresh / 1000, 2),
        'llm_review_hours': llm_hours,
        'cron_jobs': cron_descriptions
    }


def _load_trade_state_positions():
    """Load position state tracked by trade_state.py (if available)."""
    try:
        import sys
        project_root = '/Users/FIRMAS/.openclaw/workspace/skills/aster-trading'
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        src_dir = project_root + '/src'
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from trade_state import get_all_positions
        raw = get_all_positions()
        if not isinstance(raw, dict):
            return {}
        normalized = {}
        for symbol, pos in raw.items():
            if not isinstance(pos, dict):
                continue
            sym = str(symbol).upper()
            normalized[sym] = {
                'symbol': sym,
                'side': str(pos.get('side', '')).upper(),
                'size': _safe_float(pos.get('size', 0)),
                'entry_price': _safe_float(pos.get('entry_price', 0)),
                'avg_entry_price': _safe_float(pos.get('avg_entry_price', pos.get('entry_price', 0))),
                'last_update_ms': _safe_int(pos.get('last_update_ms', pos.get('updated_at', 0)), 0),
                'scale_count': _safe_int(pos.get('scale_count', 0), 0),
                'tp1_hit': bool(pos.get('tp1_hit', False)),
                'tp2_hit': bool(pos.get('tp2_hit', False)),
                'sl_hit': bool(pos.get('sl_hit', False)),
                'raw': pos,
            }
        return normalized
    except Exception:
        return {}


def _expected_max_leverage_for_equity(equity: float) -> int:
    """Mirror safety caps implemented in trading_system.py execution path."""
    if equity < 100:
        return 3
    if equity < 500:
        return 5
    return 10


def _safe_symbol_position(raw_pos, symbol):
    """Normalize state-file position shape to a comparable object."""
    if not isinstance(raw_pos, dict):
        return None
    merged = dict(raw_pos)
    merged['symbol'] = str(symbol).upper()
    return _normalize_position(merged)


def _build_reconciliation_snapshot(enabled_symbols=None):
    """Build full position/order/signal reconciliation for dashboard transparency."""
    state = load_state()
    enabled = {s.upper() for s in (enabled_symbols or get_enabled_symbols())}
    risk_cfg, _ = _load_shared_risk_config()
    global_cfg = risk_cfg.get('global', {}) if isinstance(risk_cfg, dict) else {}
    symbols_cfg = risk_cfg.get('symbols', {}) if isinstance(risk_cfg, dict) else {}

    exchange_positions = _get_truth_positions(enabled)
    exchange_map = {p.get('symbol'): p for p in exchange_positions if p.get('symbol')}

    state_positions_raw = state.get('positions', {}) if isinstance(state.get('positions', {}), dict) else {}
    state_map = {}
    for sym, pos in state_positions_raw.items():
        normalized = _safe_symbol_position(pos, sym)
        if normalized and _is_open_position(normalized):
            state_map[normalized.get('symbol')] = normalized

    trade_state_map = _load_trade_state_positions()
    open_orders = _get_truth_open_orders(enabled)
    orders_by_symbol = defaultdict(list)
    for order in open_orders:
        orders_by_symbol[order.get('symbol')].append(order)

    symbols_universe = sorted({
        *enabled,
        *[s for s in exchange_map.keys() if s],
        *[s for s in state_map.keys() if s],
        *[s for s in trade_state_map.keys() if s],
        *[s for s in orders_by_symbol.keys() if s],
    })

    account_equity = _safe_float(state.get('equity', 0), 0)
    expected_leverage_cap = _expected_max_leverage_for_equity(account_equity)
    configured_global_leverage = _safe_int(global_cfg.get('max_leverage_global', 3), 3)

    rows = []
    discrepancies = []
    now_ms = int(time.time() * 1000)

    for symbol in symbols_universe:
        ex = exchange_map.get(symbol)
        st = state_map.get(symbol)
        ts = trade_state_map.get(symbol)
        symbol_orders = orders_by_symbol.get(symbol, [])

        has_exchange = ex is not None
        has_state = st is not None
        has_trade_state = ts is not None

        has_tp = any(o.get('classification') == 'TP' and o.get('is_pending', False) for o in symbol_orders)
        has_sl = any(o.get('classification') == 'SL' and o.get('is_pending', False) for o in symbol_orders)
        has_surf = any(o.get('classification') == 'SURF' and o.get('is_pending', False) for o in symbol_orders)

        symbol_cfg = symbols_cfg.get(symbol, {}) if isinstance(symbols_cfg, dict) else {}
        cfg_lev = _safe_int(symbol_cfg.get('max_leverage', configured_global_leverage), configured_global_leverage)
        ex_lev = _safe_int(_first_non_none(ex.get('leverage') if ex else None, st.get('leverage') if st else None), 0)

        issues = []

        if has_exchange and not has_sl:
            issues.append({'code': 'MISSING_SL', 'severity': 'critical', 'message': 'Exchange position has no pending SL order'})
        if has_exchange and not has_tp:
            issues.append({'code': 'MISSING_TP', 'severity': 'high', 'message': 'Exchange position has no pending TP order'})
        if has_exchange and not has_state:
            issues.append({'code': 'MISSING_STATE_POSITION', 'severity': 'high', 'message': 'Exchange position missing from v2_state positions map'})
        if has_exchange and not has_trade_state:
            issues.append({'code': 'MISSING_TRADE_STATE_POSITION', 'severity': 'high', 'message': 'Exchange position missing from trade_state.json'})
        if has_state and not has_exchange:
            issues.append({'code': 'STALE_V2_STATE_POSITION', 'severity': 'warning', 'message': 'v2_state contains position not found on exchange'})
        if has_trade_state and not has_exchange:
            issues.append({'code': 'STALE_TRADE_STATE_POSITION', 'severity': 'warning', 'message': 'trade_state contains position not found on exchange'})

        if ex_lev > 0 and ex_lev > expected_leverage_cap:
            issues.append({
                'code': 'LEVERAGE_ABOVE_EQUITY_CAP',
                'severity': 'critical',
                'message': f'Leverage {ex_lev}x exceeds equity safety cap {expected_leverage_cap}x',
            })
        if ex_lev > 0 and ex_lev > max(cfg_lev, configured_global_leverage):
            issues.append({
                'code': 'LEVERAGE_ABOVE_CONFIG',
                'severity': 'high',
                'message': f'Leverage {ex_lev}x exceeds configured symbol/global cap',
            })

        ts_last = _safe_int(ts.get('last_update_ms', 0), 0) if ts else 0
        if has_trade_state and ts_last > 0 and (now_ms - ts_last) > (6 * 60 * 60 * 1000):
            issues.append({
                'code': 'STALE_TRADE_STATE_TIMESTAMP',
                'severity': 'warning',
                'message': 'trade_state position appears stale (>6h without update)',
            })

        protection_checks = [has_sl, has_tp]
        protection_score = int((sum(1 for chk in protection_checks if chk) / len(protection_checks)) * 100)

        row = {
            'symbol': symbol,
            'enabled': symbol in enabled,
            'exchange_position': ex,
            'v2_state_position': st,
            'trade_state_position': ts,
            'open_orders': symbol_orders,
            'order_counts': {
                'total': len(symbol_orders),
                'tp': len([o for o in symbol_orders if o.get('classification') == 'TP']),
                'sl': len([o for o in symbol_orders if o.get('classification') == 'SL']),
                'surf': len([o for o in symbol_orders if o.get('classification') == 'SURF']),
            },
            'protection': {
                'has_tp': has_tp,
                'has_sl': has_sl,
                'has_surf': has_surf,
                'score_pct': protection_score,
            },
            'leverage': {
                'exchange': ex_lev,
                'configured_symbol': cfg_lev,
                'configured_global': configured_global_leverage,
                'equity_cap': expected_leverage_cap,
            },
            'issues': issues,
            'status': 'OK' if not issues else ('CRITICAL' if any(i['severity'] == 'critical' for i in issues) else 'WARNING'),
        }
        rows.append(row)

        for issue in issues:
            discrepancies.append({
                'symbol': symbol,
                'code': issue['code'],
                'severity': issue['severity'],
                'message': issue['message'],
            })

    severity_counts = {
        'critical': len([d for d in discrepancies if d.get('severity') == 'critical']),
        'high': len([d for d in discrepancies if d.get('severity') == 'high']),
        'warning': len([d for d in discrepancies if d.get('severity') == 'warning']),
    }

    return {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'equity': account_equity,
        'enabled_symbols': sorted(enabled),
        'symbols': rows,
        'discrepancies': discrepancies,
        'summary': {
            'symbols_covered': len(rows),
            'positions_exchange': len(exchange_map),
            'positions_v2_state': len(state_map),
            'positions_trade_state': len(trade_state_map),
            'symbols_with_issues': len([r for r in rows if r.get('issues')]),
            'critical_symbols': len([r for r in rows if r.get('status') == 'CRITICAL']),
            'severity_counts': severity_counts,
        }
    }


def _percentile(values, pct):
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    idx = min(max(idx, 0), len(sorted_vals) - 1)
    return float(sorted_vals[idx])


def _build_execution_latency_snapshot(symbols=None, signal_limit=1200, trade_limit=2500, window_minutes=30):
    """Compute signal→trade latency metrics for transparency dashboards."""
    enabled = {s.upper() for s in (symbols or get_enabled_symbols())}
    history = _extract_signal_strength_history(symbols=enabled, limit=signal_limit)
    signals = [
        ev for ev in history
        if str(ev.get('action', '')).upper() in {'BUY', 'SELL'} and _safe_int(ev.get('timestamp_ms'), 0) > 0
    ]
    signals.sort(key=lambda x: x.get('timestamp_ms', 0), reverse=True)

    trades = [
        t for t in _load_normalized_trades(limit=trade_limit, include_errors=False)
        if t.get('symbol') in enabled and str(t.get('side', '')).upper() in {'BUY', 'SELL'} and _safe_int(t.get('timestamp_ms'), 0) > 0
    ]
    trades_by_symbol = defaultdict(list)
    for trade in trades:
        trades_by_symbol[trade.get('symbol')].append(trade)
    for _, rows in trades_by_symbol.items():
        rows.sort(key=lambda r: r.get('timestamp_ms', 0))

    max_delta_ms = window_minutes * 60 * 1000
    matches = []
    for signal in signals:
        symbol = signal.get('symbol')
        side = str(signal.get('action', '')).upper()
        sig_ms = _safe_int(signal.get('timestamp_ms'), 0)
        best = None
        for trade in trades_by_symbol.get(symbol, []):
            if str(trade.get('side', '')).upper() != side:
                continue
            tr_ms = _safe_int(trade.get('timestamp_ms'), 0)
            delta = tr_ms - sig_ms
            if delta < 0 or delta > max_delta_ms:
                continue
            if best is None or delta < best['delta_ms']:
                best = {'trade': trade, 'delta_ms': delta}
        if not best:
            continue
        matches.append({
            'symbol': symbol,
            'action': side,
            'signal_timestamp': _to_iso(sig_ms),
            'trade_timestamp': _to_iso(best['trade'].get('timestamp_ms')),
            'signal_strength': _safe_float(signal.get('strength'), 0),
            'signal_confidence': _safe_float(signal.get('confidence'), 0),
            'latency_ms': best['delta_ms'],
            'latency_sec': round(best['delta_ms'] / 1000.0, 3),
            'trade_id': best['trade'].get('id'),
        })

    all_latencies = [m['latency_sec'] for m in matches]
    by_symbol = {}
    for symbol in sorted(enabled):
        vals = [m['latency_sec'] for m in matches if m.get('symbol') == symbol]
        by_symbol[symbol] = {
            'count': len(vals),
            'avg_sec': round(sum(vals) / len(vals), 3) if vals else 0.0,
            'p50_sec': round(_percentile(vals, 50), 3) if vals else 0.0,
            'p95_sec': round(_percentile(vals, 95), 3) if vals else 0.0,
            'max_sec': round(max(vals), 3) if vals else 0.0,
        }

    return {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'window_minutes': window_minutes,
        'matches': matches[:400],
        'summary': {
            'matched_signals': len(matches),
            'avg_sec': round(sum(all_latencies) / len(all_latencies), 3) if all_latencies else 0.0,
            'p50_sec': round(_percentile(all_latencies, 50), 3) if all_latencies else 0.0,
            'p95_sec': round(_percentile(all_latencies, 95), 3) if all_latencies else 0.0,
            'max_sec': round(max(all_latencies), 3) if all_latencies else 0.0,
        },
        'by_symbol': by_symbol,
    }


def _load_risk_from_db():
    """Load risk data from database"""
    debug_log = '/Users/FIRMAS/.openclaw/logs/risk_load_debug.log'
    try:
        import sys
        # Add workspace/skills/aster-trading to path for dashboard imports
        project_root = '/Users/FIRMAS/.openclaw/workspace/skills/aster-trading'
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        # Add src subdirectory
        src_dir = project_root + '/src'
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        
        with open(debug_log, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] _load_risk_from_db called\n")
        
        from state.state_service import state_service
        risk = state_service.get_risk_state()
        
        with open(debug_log, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] risk object: {risk}\n")
        
        if risk:
            result = {
                'equity': risk.account_equity,
                'daily_pnl': risk.daily_pnl,
                'drawdown_pct': risk.drawdown_pct,
                'risk_limits': risk.risk_limits or {}
            }
            with open(debug_log, 'a') as f:
                f.write(f"[{datetime.now().isoformat()}] returning: {result}\n")
            return result
        else:
            with open(debug_log, 'a') as f:
                f.write(f"[{datetime.now().isoformat()}] risk is None or falsy\n")
    except Exception as e:
        import traceback
        with open(debug_log, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] Error: {e}\n")
            f.write(traceback.format_exc())
    return {}


def _build_risk_transparency_snapshot(enabled_symbols=None):
    """Build risk transparency payload for dashboard validation."""
    debug_log = '/Users/FIRMAS/.openclaw/logs/risk_load_debug.log'
    
    state = load_state()
    # DB-only risk snapshot
    risk_data = _load_risk_from_db()
    
    with open(debug_log, 'a') as f:
        f.write(f"[{datetime.now().isoformat()}] _build_risk_transparency_snapshot: state.get('equity')={state.get('equity')}, risk_data.get('equity')={risk_data.get('equity')}\n")
    
    risk_cfg, _ = _load_shared_risk_config()
    global_cfg = risk_cfg.get('global', {}) if isinstance(risk_cfg, dict) else {}

    enabled = enabled_symbols or get_enabled_symbols()
    positions = _get_truth_positions(enabled)

    # Prefer risk_data from database if state values are zero/empty
    equity = _safe_float(risk_data.get('equity') or state.get('equity'), 0)
    daily_pnl = _safe_float(risk_data.get('daily_pnl') if risk_data.get('daily_pnl') is not None else state.get('daily_pnl'), 0)
    weekly_pnl = _safe_float(risk_data.get('weekly_pnl'), 0)
    drawdown_pct = _safe_float(risk_data.get('drawdown_pct') if risk_data.get('drawdown_pct') is not None else state.get('drawdown_pct'), 0)
    if drawdown_pct > 0 and drawdown_pct < 1:
        drawdown_pct *= 100.0

    total_notional = 0.0
    total_margin_used = 0.0
    for p in positions:
        mark = _safe_float(p.get('markPrice'), 0)
        amt = abs(_safe_float(p.get('positionAmt'), 0))
        notional = abs(_safe_float(_first_non_none(p.get('notional'), amt * mark), 0))
        total_notional += notional
        total_margin_used += abs(_safe_float(_first_non_none(p.get('isolatedMargin'), p.get('margin'), 0), 0))

    exposure_pct = (total_notional / equity * 100.0) if equity > 0 else 0.0
    margin_ratio = (total_margin_used / equity) if equity > 0 else 0.0

    daily_loss_limit_usdt = _safe_float(global_cfg.get('daily_loss_hard_limit_usdt'), 1.5)
    weekly_loss_limit_pct = _safe_float(global_cfg.get('max_weekly_loss_pct'), 12.0)
    drawdown_limit_pct = _safe_float(global_cfg.get('max_drawdown_pct'), 12.0)
    equity_cap_leverage = _expected_max_leverage_for_equity(equity)

    return {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'equity': equity,
        'risk_level': _first_non_none(state.get('risk_level'), risk_data.get('risk_level'), 'NORMAL'),
        'daily_pnl': daily_pnl,
        'weekly_pnl': weekly_pnl,
        'drawdown_pct': drawdown_pct,
        'positions_count': len(positions),
        'total_notional': total_notional,
        'exposure_pct': exposure_pct,
        'margin_ratio': margin_ratio,
        'limits': {
            'daily_loss_hard_limit_usdt': daily_loss_limit_usdt,
            'weekly_loss_limit_pct': weekly_loss_limit_pct,
            'drawdown_limit_pct': drawdown_limit_pct,
            'max_leverage_global': _safe_int(global_cfg.get('max_leverage_global', 3), 3),
            'expected_max_leverage_for_equity': equity_cap_leverage,
        },
        'progress': {
            'daily_loss_pct_of_limit': min(100.0, max(0.0, (abs(min(daily_pnl, 0.0)) / daily_loss_limit_usdt * 100.0))) if daily_loss_limit_usdt > 0 else 0.0,
            'weekly_loss_pct_of_limit': min(100.0, max(0.0, (abs(min(weekly_pnl, 0.0)) / (equity * (weekly_loss_limit_pct / 100.0)) * 100.0))) if equity > 0 and weekly_loss_limit_pct > 0 else 0.0,
            'drawdown_pct_of_limit': min(100.0, max(0.0, (drawdown_pct / drawdown_limit_pct * 100.0))) if drawdown_limit_pct > 0 else 0.0,
        }
    }
