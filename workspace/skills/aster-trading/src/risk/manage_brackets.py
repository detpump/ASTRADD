#!/usr/bin/env python3
from __future__ import annotations

"""
manage_brackets.py – gestión de SL / TP / trailing-stop y registro de resultados.

Cambios:
  - register_trade_result ahora usa cycle_id como clave de deduplicación
    para evitar doble-registro (sync_state_from_exchange.py también lo llama).
  - Se guarda pnl_pct en trades.jsonl.
"""

import os
import sys
import json
import math
import time
from datetime import datetime, timezone

from api.aster_api import (
    place_order,
    cancel_all_open_orders,
    public_get,
    get_exchange_info,
    get_positions_v3,
    get_open_orders,
    cancel_order,
)
from trade_state import (
    get_position_state as _get_position_state,
    get_all_positions,
    set_position_state as _set_position_state,
    clear_position_state as _clear_position_state,
    sync_all_with_exchange,
    is_scale_in_pending,
)
from state.state_service import state_service
from state.models import Position, Order
from services.account_stream_listener import (
    is_account_listener_active,
    start_account_listener,
)
from centralized_logger import log_trade
from services.risk_service import get_shared_risk_guard, get_risk_status

try:
    SHARED_RISK_GUARD = get_shared_risk_guard()
except Exception as e:
    SHARED_RISK_GUARD = None
    print(f"[manage_brackets] ⚠️ No se pudo inicializar RiskGuard compartido: {e}")

try:
    SHARED_RISK_GUARD = get_shared_risk_guard()
except Exception as e:
    SHARED_RISK_GUARD = None
    print(f"[manage_brackets] ⚠️ No se pudo inicializar RiskGuard compartido: {e}")

# Usar centralized paths - single point of change for server migration
try:
    from paths import BASE, DATA_DIR, CONFIG_DIR, STATE_DIR, OPENCLAW_LOGS_DIR
    ORDERS_LOG_PATH = os.path.join(STATE_DIR, "open_orders_log.json")
    PERF_PATH = os.path.join(STATE_DIR, "score_performance.json")
    RISK_CONFIG_PATH = os.path.join(CONFIG_DIR, "risk_config.json")
except ImportError:
    # Fallback para cuando paths.py no está disponible
    BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    OPENCLAW_LOGS_DIR = os.path.join(BASE, "logs")
    ORDERS_LOG_PATH = os.path.join(BASE, "data/state/open_orders_log.json")
    PERF_PATH = os.path.join(BASE, "data/state/score_performance.json")
    RISK_CONFIG_PATH = os.path.join(BASE, "config/risk_config.json")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def get_orders_from_db() -> list:
    """Get orders from DB, returns list in same format as JSON orders file.
    
    This provides DB-first order retrieval for manage_brackets functions.
    Falls back to JSON if DB is empty.
    """
    try:
        from state.state_service import state_service
        
        orders = state_service.get_orders(active_only=False, limit=1000)
        if not orders:
            return []
            
        # Convert DB Order objects to dict format matching JSON structure
        result = []
        for o in orders:
            d = {
                "order_id": o.order_id,
                "client_order_id": o.client_order_id,
                "symbol": o.symbol,
                "action": o.side,  # DB uses 'side', JSON uses 'action'
                "type": o.type,
                "status": o.status,
                "price": o.price,
                "quantity": o.quantity,
                "executed_qty": o.executed_qty,
                "reduce_only": o.reduce_only,
                "time_in_force": o.time_in_force,
                "position_side": o.position_side,
                "update_time": o.update_time,
                "cycle_id": o.cycle_id,
                "bracket_label": o.bracket_label,
            }
            # Include metadata if present
            if o.metadata:
                d.update(o.metadata)
            result.append(d)
        return result
    except Exception as e:
        print(f"[manage_brackets] ⚠️ Failed to get orders from DB: {e}")
        return []

# Trades van al log centralizado - usar paths centralizado
try:
    from paths import OPENCLAW_LOGS_DIR, STATE_DIR
    TRADES_HIST_PATH = os.path.join(OPENCLAW_LOGS_DIR, "trades.jsonl")
    MARKET_STATE_PATH = os.path.join(STATE_DIR, "market_state.json")
except ImportError:
    # Fallback
    TRADES_HIST_PATH = os.path.join(BASE, "logs/history/trades.jsonl")
    MARKET_STATE_PATH = os.path.join(BASE, "data/state/market_state.json")


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_symbol_filters(symbol: str):
    info = get_exchange_info()
    for s in info.get("symbols", []):
        if s.get("symbol") == symbol:
            return s.get("filters", [])
    raise RuntimeError(f"Symbol {symbol} not found in exchangeInfo")


def adjust_price_to_tick(symbol: str, price: float) -> float:
    filters = find_symbol_filters(symbol)
    tick_size = None
    for f in filters:
        if f.get("filterType") == "PRICE_FILTER":
            tick_size = float(f["tickSize"])
    if tick_size is None or tick_size <= 0:
        return price
    ticks = round(price / tick_size)
    return float(f"{ticks * tick_size:.8f}")


def adjust_qty_to_lot(symbol: str, qty: float) -> float:
    filters = find_symbol_filters(symbol)
    min_qty = step_size = None
    for f in filters:
        if f.get("filterType") == "LOT_SIZE":
            min_qty = float(f["minQty"])
            step_size = float(f["stepSize"])
    if min_qty is None or step_size is None:
        return qty
    steps = math.floor(qty / step_size + 1e-9)
    adj = steps * step_size
    if adj < min_qty:
        return 0.0
    return float(f"{adj:.8f}")


def get_min_lot_qty(symbol: str) -> float:
    """Return the minimal tradable quantity for `symbol` (LOT_SIZE.minQty)."""
    filters = find_symbol_filters(symbol)
    for f in filters:
        if f.get("filterType") == "LOT_SIZE":
            try:
                return max(float(f.get("minQty", 0.0)), 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _compute_break_even_qty(symbol: str, state: dict, pct_hint: float, fallback_pct: float = 0.5) -> tuple[float, bool]:
    """Calculate a valid reduce-only quantity for break-even SL placement.

    Returns (qty, used_fallback) where qty is already adjusted to LOT_SIZE. If qty is 0,
    caller should skip the order.
    """
    total_size = abs(float(state.get("size", 0.0)))
    if total_size <= 0:
        return 0.0, False

    attempted = [total_size * pct_hint, total_size * fallback_pct, total_size]
    used_fallback = False

    for candidate in attempted:
        if candidate <= 0:
            continue
        adj = adjust_qty_to_lot(symbol, candidate)
        if adj > 0:
            if candidate != attempted[0]:
                used_fallback = True
            return adj, used_fallback

    min_lot = get_min_lot_qty(symbol)
    if 0 < min_lot <= total_size:
        adj = adjust_qty_to_lot(symbol, min_lot)
        if adj > 0:
            return adj, True

    return 0.0, True


def get_mark_price(symbol: str) -> float:
    try:
        mark = public_get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if isinstance(mark, list) and mark:
            mark = mark[0]
        return float(mark.get("markPrice", 0))
    except Exception:
        return 0.0


def _ensure_position_state_cache() -> dict[str, dict]:
    """Ensure trade_state cache has current positions, syncing from exchange if empty."""
    state = get_all_positions()
    if state:
        return state
    try:
        synced = sync_all_with_exchange()
        if synced:
            print(f"[manage_brackets] Position cache repoblado desde exchange ({len(synced)} símbolos)")
            state = synced
            # Persist synced positions into DB for downstream consumers
            for sym, s in synced.items():
                _persist_position(sym, s)
    except Exception as exc:
        print(f"[manage_brackets] ⚠️ No se pudo sincronizar posiciones del exchange: {exc}")
    return state


def _persist_position(symbol: str, state: dict) -> None:
    try:
        pos = Position(
            symbol=symbol,
            side=state.get("side", ""),
            quantity=float(state.get("size", 0.0) or 0.0),
            entry_price=float(state.get("entry_price", 0.0) or 0.0),
            mark_price=float(state.get("mark_price", state.get("entry_price", 0.0)) or 0.0),
            unrealized_pnl=state.get("unrealized_pnl"),
            notional=abs(float(state.get("size", 0.0) or 0.0) * float(state.get("entry_price", 0.0) or 0.0)),
            leverage=int(state.get("leverage", 1) or 1),
            open_time=state.get("open_time"),
            cycle_id=state.get("cycle_id"),
            metadata={
                "tp1_hit": state.get("tp1_hit"),
                "tp2_hit": state.get("tp2_hit"),
                "sl_hit": state.get("sl_hit"),
                "last_exchange_sync": state.get("last_exchange_sync"),
            },
        )
        state_service.upsert_position(pos)
    except Exception as exc:
        print(f"[manage_brackets] ⚠️ No se pudo persistir posición {symbol} en DB: {exc}")


def set_position_state(symbol: str, state: dict):
    _set_position_state(symbol, state)
    _persist_position(symbol, state)


def clear_position_state(symbol: str):
    _clear_position_state(symbol)
    try:
        state_service.delete_position(symbol)
    except Exception as exc:
        print(f"[manage_brackets] ⚠️ No se pudo borrar posición {symbol} en DB: {exc}")


def get_position_state(symbol: str) -> dict:
    """Read position preferring DB snapshot, fallback to trade_state."""
    # Prefer DB snapshot for latest persisted data
    try:
        positions = {p.symbol: p for p in state_service.get_positions()}
        if symbol in positions:
            p = positions[symbol]
            return {
                "symbol": p.symbol,
                "side": p.side,
                "size": p.quantity,
                "entry_price": p.entry_price,
                "mark_price": p.mark_price,
                "unrealized_pnl": p.unrealized_pnl,
                "notional": p.notional,
                "leverage": p.leverage,
                "open_time": p.open_time,
                "cycle_id": p.cycle_id,
                "tp1_hit": (p.metadata or {}).get("tp1_hit"),
                "tp2_hit": (p.metadata or {}).get("tp2_hit"),
                "sl_hit": (p.metadata or {}).get("sl_hit"),
                "last_exchange_sync": (p.metadata or {}).get("last_exchange_sync"),
            }
    except Exception as exc:
        print(f"[manage_brackets] ⚠️ No se pudo leer posición {symbol} desde DB: {exc}")

    # Fallback to legacy trade_state
    return _get_position_state(symbol)


def register_trade_result(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl_usdt: float,
    reason_close: str = "unknown",
    score: float = 0.0,
    setup_type: str = "unknown",
    ts_open: str = "",
    ts_close: str = "",
    cycle_id: str = "",
    pnl_pct: float = 0.0,
):
    """Registro de resultado de trade usando implementación local consolidada."""
    _register_trade_result_local(
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_usdt=pnl_usdt,
        reason_close=reason_close,
        score=score,
        setup_type=setup_type,
        ts_open=ts_open,
        ts_close=ts_close,
        cycle_id=cycle_id,
        pnl_pct=pnl_pct,
    )


def _register_trade_result_local(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl_usdt: float,
    reason_close: str = "unknown",
    score: float = 0.0,
    setup_type: str = "unknown",
    ts_open: str = "",
    ts_close: str = "",
    cycle_id: str = "",
    pnl_pct: float = 0.0,
):
    """Implementación local de register_trade_result (fallback)."""
    perf = load_json(
        PERF_PATH,
        {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "by_score_range": {},
            "by_setup": {},
            "_registered_cycles": [],
        },
    )

    registered = perf.get("_registered_cycles", [])
    if cycle_id and cycle_id in registered:
        print(f"[manage_brackets] trade {cycle_id} ya registrado — skip dedup")
        return

    perf.setdefault("total_trades", 0)
    perf.setdefault("wins", 0)
    perf.setdefault("losses", 0)
    perf.setdefault("total_pnl", 0.0)
    perf.setdefault("by_score_range", {})
    perf.setdefault("by_setup", {})
    perf.setdefault("_registered_cycles", [])

    perf["total_trades"] += 1
    perf["total_pnl"] += pnl_usdt
    if pnl_usdt >= 0:
        perf["wins"] += 1
    else:
        perf["losses"] += 1

    if score >= 4.0:
        srange = "4.0+"
    elif score >= 3.0:
        srange = "3.0-3.9"
    elif score >= 2.0:
        srange = "2.0-2.9"
    else:
        srange = "<2.0"

    sr = perf["by_score_range"].setdefault(
        srange, {"trades": 0, "wins": 0, "pnl": 0.0}
    )
    sr["trades"] += 1
    sr["pnl"] += pnl_usdt
    if pnl_usdt >= 0:
        sr["wins"] += 1

    su = perf["by_setup"].setdefault(
        setup_type, {"trades": 0, "wins": 0, "pnl": 0.0}
    )
    su["trades"] += 1
    su["pnl"] += pnl_usdt
    if pnl_usdt >= 0:
        su["wins"] += 1

    if cycle_id:
        perf["_registered_cycles"].append(cycle_id)
        if len(perf["_registered_cycles"]) > 200:
            perf["_registered_cycles"] = perf["_registered_cycles"][-200:]

    save_json(PERF_PATH, perf)

    record = {
        "ts_close": ts_close or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ts_open": ts_open,
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_usdt": round(pnl_usdt, 4),
        "pnl_pct": round(pnl_pct, 4),
        "reason_close": reason_close,
        "score": score,
        "setup_type": setup_type,
        "cycle_id": cycle_id,
    }
    append_jsonl(TRADES_HIST_PATH, record)
    # Pass cycle_id for deduplication to prevent duplicate logging
    log_trade(
        symbol=symbol,
        side=side,
        quantity=0,  # Not available in this context
        price=exit_price,
        notional=abs(pnl_usdt),
        status="FILLED",
        pnl=pnl_usdt,
        cycle_id=cycle_id
    )

    print(
        f"[manage_brackets] trade registrado: {symbol} {side} "
        f"pnl={pnl_usdt:.4f} ({pnl_pct*100:.2f}%) reason={reason_close} cycle={cycle_id}"
    )


def _update_tp_hit_status_local(symbol: str, tp1_order_id: str = None, tp2_order_id: str = None):
    """
    Implementación local para actualizar el estado de TP hit cuando trade_utils no está disponible.
    Consulta las órdenes abiertas del exchange para determinar si TP1 o TP2 fueron ejecutados.
    
    FIX: Ahora identifica correctamente las órdenes TP1 vs TP2 basándose en los precios
    almacenados en el estado de la posición.
    """
    try:
        from api.aster_api import get_open_orders
        open_orders = get_open_orders(symbol)
    except Exception as e:
        print(f"[brackets] Error obteniendo órdenes abiertas para {symbol}: {e}")
        return
    
    # Obtener estado de la posición para saber los precios objetivo
    state = get_position_state(symbol)
    if not state:
        return
    
    # Get target prices from state
    tp1_price = state.get("tp1_price", 0)
    tp2_price = state.get("tp2_price", 0)
    
    # Mapeo de orderId a tipo de orden
    order_type_map = {}
    
    for o in open_orders:
        order_id = str(o.get("orderId", ""))
        order_type = o.get("type", "")
        stop_price = float(o.get("stopPrice", 0))
        
        if order_type == "TAKE_PROFIT_MARKET" and stop_price > 0:
            # Determinar si es TP1 o TP2 comparando precios
            if tp1_price > 0 and abs(stop_price - tp1_price) < tp1_price * 0.01:
                order_type_map[order_id] = "TP1"
            elif tp2_price > 0 and abs(stop_price - tp2_price) < tp2_price * 0.01:
                order_type_map[order_id] = "TP2"
    
    # Obtener IDs de órdenes actualmente abiertas
    current_open_ids = {str(o.get("orderId", "")) for o in open_orders}
    
    # Si faltan IDs (por reinicio) pero encontramos órdenes por precio, reasignarlas
    if not tp1_order_id and tp1_price > 0:
        for order_id, order_label in order_type_map.items():
            if order_label == "TP1":
                state["tp1_order_id"] = order_id
                tp1_order_id = order_id
                print(f"[brackets] TP1 orderId restaurado para {symbol}: {order_id}")
                break
    if not tp2_order_id and tp2_price > 0:
        for order_id, order_label in order_type_map.items():
            if order_label == "TP2":
                state["tp2_order_id"] = order_id
                tp2_order_id = order_id
                print(f"[brackets] TP2 orderId restaurado para {symbol}: {order_id}")
                break

    # Verificar cada orden objetivo
    # TP1
    if tp1_order_id:
        tp1_str = str(tp1_order_id)
        if tp1_str not in current_open_ids:
            # Orden ejecutada (ya no está abierta)
            if not state.get("tp1_hit", False):
                state["tp1_hit"] = True
                state["tp1_hit_time"] = int(time.time() * 1000)
                print(f"[brackets] TP1 ejecutado para {symbol} @ price {tp1_price}")
    
    # TP2
    if tp2_order_id:
        tp2_str = str(tp2_order_id)
        if tp2_str not in current_open_ids:
            # Orden ejecutada (ya no está abierta)
            if not state.get("tp2_hit", False):
                state["tp2_hit"] = True
                state["tp2_hit_time"] = int(time.time() * 1000)
                print(f"[brackets] TP2 ejecutado para {symbol} @ price {tp2_price}")
    
    #También verificar SL
    sl_order_id = state.get("sl_order_id")
    if sl_order_id:
        sl_str = str(sl_order_id)
        if sl_str not in current_open_ids:
            if not state.get("sl_hit", False):
                state["sl_hit"] = True
                state["sl_hit_time"] = int(time.time() * 1000)
                print(f"[brackets] SL ejecutado para {symbol}")
    
    set_position_state(symbol, state)


def update_tp_hit_tracking():
    """
    Actualiza el estado de tp1_hit/tp2_hit consultando las órdenes del exchange.
    Esta función debe llamarse antes de manage_brackets para mantener el estado actualizado.
    """
    # Try DB first, fallback to JSON
    db_orders = get_orders_from_db()
    orders = db_orders if db_orders else load_json(ORDERS_LOG_PATH, {"orders": []}).get("orders", [])
    symbols_to_check: set[str] = set()

    for o in orders:
        if o.get("action") != "OPEN" or o.get("status") != "EXECUTED":
            continue
        symbol = o.get("symbol")
        if symbol:
            symbols_to_check.add(symbol)

    # Fallback: revisar todos los símbolos con posiciones activas en trade_state
    position_states = _ensure_position_state_cache()
    symbols_to_check.update(position_states.keys())

    listener_active = False
    try:
        listener_active = is_account_listener_active()
        if not listener_active:
            started = start_account_listener()
            if started:
                print("[brackets] ▶️ Account listener iniciado desde update_tp_hit_tracking")
                listener_active = True
    except Exception as exc:
        print(f"[brackets] ⚠️ No se pudo verificar/iniciar el account listener: {exc}")

    for symbol in symbols_to_check:
        state = get_position_state(symbol)
        if not state:
            continue

        tp1_order_id = state.get("tp1_order_id")
        tp2_order_id = state.get("tp2_order_id")

        if not (tp1_order_id or tp2_order_id):
            continue

        # Incluso si el websocket está activo, verificamos vía REST para garantizar
        # que los estados tp1_hit/tp2_hit se sincronicen correctamente.
        _update_tp_hit_status_local(symbol, tp1_order_id, tp2_order_id)


def _extract_order_price(order: dict) -> float:
    for key in ("stopPrice", "price", "avgPrice", "triggerPrice", "activationPrice"):
        try:
            value = float(order.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 0.0


def _extract_order_quantity(order: dict) -> float:
    for key in ("origQty", "origQuantity", "quantity", "qty", "cumQty", "ExecutedQty"):
        try:
            value = abs(float(order.get(key)))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 0.0


def _build_open_order_index(symbol: str) -> tuple[list[dict], dict[str, dict]]:
    try:
        open_orders = get_open_orders(symbol)
    except Exception as exc:
        print(f"[manage_brackets] ⚠️ No se pudieron obtener órdenes abiertas para {symbol}: {exc}")
        open_orders = []
    order_map: dict[str, dict] = {}
    for order in open_orders:
        oid = order.get("orderId")
        if oid is not None:
            order_map[str(oid)] = order
    return open_orders, order_map


def _audit_position_state(symbol: str, state: dict, risk: dict) -> dict:
    """Revisa que la configuración de brackets coincida con el estado esperado."""
    result = {"state_mutated": False, "needs_initial_brackets": False}
    entry = float(state.get("entry_price", state.get("entry", 0)))
    size = abs(float(state.get("size", 0)))
    side = state.get("side", "BUY")
    if entry <= 0 or size <= 0:
        return result

    _, order_map = _build_open_order_index(symbol)

    def order_missing(key: str) -> bool:
        oid = state.get(key)
        return not oid or str(oid) not in order_map

    tp1_hit = state.get("tp1_hit", False)
    tp2_hit = state.get("tp2_hit", False)

    if not tp1_hit:
        brackets = _calculate_brackets(entry, side, risk, symbol)
        surf_qty = adjust_qty_to_lot(symbol, size * brackets["surf_qty_pct"])
        tp_distribution_ok = True
        tolerance = max(size * 0.02, 1e-8)
        if abs(float(state.get("surf_qty", 0)) - surf_qty) > tolerance:
            tp_distribution_ok = False
        rest_qty = size - surf_qty
        tp1_qty = adjust_qty_to_lot(symbol, rest_qty * 0.5)
        tp2_qty = adjust_qty_to_lot(symbol, rest_qty * 0.5)
        if abs(float(state.get("tp1_qty", 0)) - tp1_qty) > tolerance:
            tp_distribution_ok = False
        if abs(float(state.get("tp2_qty", 0)) - tp2_qty) > tolerance:
            tp_distribution_ok = False

        primary_missing = any(order_missing(key) for key in ("sl_order_id", "tp1_order_id", "tp2_order_id"))
        if primary_missing or not tp_distribution_ok:
            result["needs_initial_brackets"] = True
        return result

    if tp1_hit and not tp2_hit:
        break_even_price = float(state.get("break_even_price", entry))
        break_even_qty = abs(float(state.get("break_even_qty", state.get("size", 0))))
        sl_ok = not order_missing("sl_order_id")
        if sl_ok:
            sl_order = order_map.get(str(state.get("sl_order_id")))
            sl_price = _extract_order_price(sl_order)
            price_tol = max(break_even_price * 0.002, 1e-8)
            qty_tol = max(break_even_qty * 0.05, 1e-8)
            if not break_even_price or abs(sl_price - break_even_price) > price_tol:
                sl_ok = False
            else:
                sl_qty = _extract_order_quantity(sl_order)
                if break_even_qty > 0 and abs(sl_qty - break_even_qty) > qty_tol:
                    sl_ok = False
        if not (state.get("sl_at_break_even") and sl_ok):
            state["tp1_handled"] = False
            result["state_mutated"] = True
        return result

    if tp2_hit:
        open_orders, _ = _build_open_order_index(symbol)
        trailing_ok = False
        for order in open_orders:
            order_type = str(order.get("type", "")).upper()
            if order_type == "TRAILING_STOP_MARKET":
                trailing_ok = True
                break
        if not trailing_ok or not state.get("trailing_active"):
            state["tp2_handled"] = False
            state["trailing_active"] = False
            result["state_mutated"] = True
        return result

    return result


def _calculate_brackets(entry: float, side: str, risk: dict, symbol: str = None) -> dict:
    """
    Calcula los niveles de precio para SL, TP1, TP2 y trailing stop.
    
    Distribución de posición (basado en v1):
    - Surf: porción que queda con trailing (ej: 30%)
    - Rest: tamaño - surf
    - TP1: 50% del rest
    - TP2: 50% del rest
    """
    sl_pct = float(risk.get("sl_pct", 1.5)) / 100.0
    tp1_pct = float(risk.get("tp1_pct", 2.0)) / 100.0
    # TP2 usa un valor por defecto si no está configurado
    tp2_pct = float(risk.get("tp2_pct", 3.0)) / 100.0
    trailing_trigger_pct = float(risk.get("trailing_trigger_pct", 1.5)) / 100.0
    trailing_callback_pct = float(risk.get("trailing_callback_pct", 0.5))
    # Porcentaje de la posición que va a surf (trailing)
    surf_qty_pct = float(risk.get("surf_qty_pct", 0.3))

    if side == "BUY":
        sl_price = entry * (1 - sl_pct)
        tp1_price = entry * (1 + tp1_pct)
        tp2_price = entry * (1 + tp2_pct)
        trailing_activation = entry * (1 + trailing_trigger_pct)
        close_side = "SELL"
    else:
        sl_price = entry * (1 + sl_pct)
        tp1_price = entry * (1 - tp1_pct)
        tp2_price = entry * (1 - tp2_pct)
        trailing_activation = entry * (1 - trailing_trigger_pct)
        close_side = "BUY"

    if symbol:
        sl_price = adjust_price_to_tick(symbol, sl_price)
        tp1_price = adjust_price_to_tick(symbol, tp1_price)
        tp2_price = adjust_price_to_tick(symbol, tp2_price)
        trailing_activation = adjust_price_to_tick(symbol, trailing_activation)

    return {
        "sl_price": sl_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "trailing_activation": trailing_activation,
        "trailing_callback_pct": trailing_callback_pct,
        "trailing_trigger_pct": trailing_trigger_pct,  # Needed for dynamic calculation
        "surf_qty_pct": surf_qty_pct,
        "close_side": close_side,
    }


def _check_and_handle_tp1_hit(symbol: str, state: dict, brackets: dict) -> bool:
    """
    Maneja la transición cuando TP1 es alcanzado:
    - Marca TP1 como hit
    - Mantiene TP2 order (se ejecutará después)
    - La posición restante se convierte en Surf (sin trailing hasta TP2)
    
    IMPORTANTE: No cancelamos TP2. Dejamos que TP2 se ejecute naturalmente.
    Cuando TP2 luego se ejecuta,，我们将激活 trailing stop。
    
    Returns True si TP1 fue alcanzado y se procesó.
    """
    tp1_hit = state.get("tp1_hit", False)
    if tp1_hit:
        return False  # Ya procesado
    
    # Verificar si tenemos el order ID de TP1
    tp1_order_id = state.get("tp1_order_id")
    if not tp1_order_id:
        return False
    
    entry = float(state.get("entry_price", state.get("entry", 0)))
    if entry <= 0:
        return False
    
    side = state.get("side", "BUY")
    close_side = "SELL" if side == "BUY" else "BUY"
    
    print(f"[brackets] TP1 alcanzado para {symbol} - moviendo SL a break-even")
    
    # ========== NUEVO: Mover SL a break-even cuando TP1 es alcanzado ==========
    # Esto protege el profit ganado en TP1 inmediatamente
    break_even_price = adjust_price_to_tick(symbol, entry)
    
    # Cancelar solo la orden de SL existente para no borrar TP2
    sl_cancelled = False
    sl_order_id = state.get("sl_order_id")
    if sl_order_id:
        try:
            cancel_order(symbol, int(sl_order_id))
            sl_cancelled = True
            print(f"[brackets] SL anterior cancelado ({sl_order_id}) para {symbol}")
        except Exception as e:
            print(f"[brackets] Error cancelando SL {sl_order_id} para {symbol}: {e}")
    if not sl_cancelled:
        try:
            open_orders = get_open_orders(symbol)
            for order in open_orders:
                order_type = str(order.get("type", "")).upper()
                if "STOP" in order_type and "TAKE" not in order_type:
                    cancel_order(symbol, int(order.get("orderId")))
                    sl_cancelled = True
                    print(f"[brackets] SL sin ID cancelado ({order.get('orderId')}) para {symbol}")
                    break
        except Exception as e:
            print(f"[brackets] Error identificando SL para {symbol}: {e}")
    if not sl_cancelled:
        print(f"[brackets] Advertencia: no se pudo cancelar SL previo para {symbol}, continuando con break-even")
    
    # Colocar SL en break-even para proteger la posición restante
    try:
        remaining_size, used_fallback = _compute_break_even_qty(symbol, state, pct_hint=0.65)
        if remaining_size <= 0:
            print(f"[brackets] ⚠️ No se pudo calcular qty para break-even {symbol}; size={state.get('size')}" )
            return False

        if used_fallback:
            print(
                f"[brackets] ⚙️ qty break-even ajustado para {symbol}: {remaining_size} (fallback por LOT_SIZE)"
            )

        sl_order = place_order(
            {
                "symbol": symbol,
                "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": f"{break_even_price:.8f}",
                "quantity": f"{remaining_size:.8f}",
                "reduceOnly": "true",
                "timeInForce": "GTC",
                "workingType": "MARK_PRICE",
            }
        )
        if sl_order and sl_order.get("orderId"):
            state["sl_order_id"] = sl_order.get("orderId")
        state["sl_at_break_even"] = True
        state["break_even_price"] = break_even_price
        state["break_even_qty"] = remaining_size
        print(f"[brackets] ✅ SL movido a break-even: {symbol} @ {break_even_price} qty={remaining_size}")
    except Exception as e:
        print(f"[brackets] Error SL break-even {symbol}: {e}")

    # NO cancelamos TP2 - lo mantenemos para que se ejecute
    # La posición restante (después de TP1 + TP2) será el Surf con trailing
    
    # Actualizar estado: TP1 hit, posición ahora es Surf (esperando TP2)
    state["tp1_hit"] = True
    state["surf_phase"] = True  # Marca que estamos en fase Surf (esperando trailing después de TP2)
    
    # Guardar el precio de entrada para referencia
    state["surf_entry"] = entry
    
    set_position_state(symbol, state)
    print(f"[brackets] TP1 hit registrado para {symbol} - TP2 mantiene activo")
    
    return True


def _check_and_handle_tp2_hit(symbol: str, state: dict, brackets: dict) -> bool:
    """
    Verifica si TP2 ha sido alcanzado y ejecuta las acciones necesarias:
    - Mueve SL a break-even
    - Activa trailing stop para la posición restante (Surf)
    
    IMPORTANTE: 
    - Solo activamos trailing si TP1 también fue hit (ambas parciales ejecutadas)
    - Calculate trailing_activation DINÁMICAMENTE basado en el precio actual
      cuando TP2 realmente se ejecuta, no en el precio de entrada.
    
    Returns True si TP2 fue alcanzado y se ejecutaron las acciones.
    """
    tp2_hit = state.get("tp2_hit", False)
    if tp2_hit:
        return False  # Ya procesado
    
    # Verificar si tenemos el order ID de TP2
    tp2_order_id = state.get("tp2_order_id")
    if not tp2_order_id:
        # TP2 order might have been filled but we can't track it
        # Check if both TP1 and TP2 are marked as hit
        tp1_hit = state.get("tp1_hit", False)
        if not tp1_hit:
            return False  # Neither TP1 nor TP2 has been hit
        # TP1 was hit but TP2 order_id is gone - assume TP2 was executed
    
    # Aquí usamos la lógica de que si TP2 ya fue hit (desde trade_utils),
    # movemos el SL a break-even y activamos trailing
    entry = float(state.get("entry_price", state.get("entry", 0)))
    if entry <= 0:
        return False
    
    side = state.get("side", "BUY")
    close_side = "SELL" if side == "BUY" else "BUY"
    
    # Calcular break-even price
    break_even_price = adjust_price_to_tick(symbol, entry)
    
    # Obtener el tamaño restante después de TP1 y TP2
    # Asumimos que TP1 y TP2 cierran el 70% (35% cada uno) y Surf es 30%
    surf_qty_pct = brackets.get("surf_qty_pct", 0.3)
    remaining_pct = surf_qty_pct  # Lo que queda después de TP1+TP2
    
    try:
        # Cancelar órdenes existentes para reemplazar SL
        cancel_all_open_orders(symbol)
    except Exception as e:
        print(f"[brackets] Error cancelando órdenes para TP2 hit {symbol}: {e}")
    
    remaining_size = 0.0
    # Colocar SL en break-even
    try:
        remaining_size, used_fallback = _compute_break_even_qty(symbol, state, pct_hint=remaining_pct)
        if remaining_size <= 0:
            print(f"[brackets] ⚠️ No qty para SL post-TP2 {symbol}; size={state.get('size')}" )
        else:
            if used_fallback:
                print(
                    f"[brackets] ⚙️ qty post-TP2 ajustado para {symbol}: {remaining_size} (fallback)"
                )
            sl_order = place_order(
                {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "STOP_MARKET",
                    "stopPrice": f"{break_even_price:.8f}",
                    "quantity": f"{remaining_size:.8f}",
                    "reduceOnly": "true",
                    "timeInForce": "GTC",
                    "workingType": "MARK_PRICE",
                }
            )
            if sl_order and sl_order.get("orderId"):
                state["sl_order_id"] = sl_order.get("orderId")
            state["break_even_qty"] = remaining_size
            print(f"[brackets] SL movido a break-even tras TP2: {symbol} @ {break_even_price} qty={remaining_size}")
    except Exception as e:
        print(f"[brackets] Error SL break-even {symbol}: {e}")
    
    # ========== DINÁMICO: Calcular trailing_activation cuando TP2 REALMENTE se ejecuta ==========
    # Obtener precio actual del mercado
    current_price = get_mark_price(symbol)
    if current_price <= 0:
        # Fallback al precio de entrada si no podemos obtener precio actual
        current_price = entry
        print(f"[brackets] Advertencia: no se pudo obtener precio actual, usando entry para {symbol}")
    
    # Calcular activación del trailing basada en el precio ACTUAL, no en el de entrada
    trailing_trigger_pct = float(brackets.get("trailing_trigger_pct", 1.5)) / 100.0
    if side == "BUY":
        # Para LONG: activation es mayor que precio actual
        trailing_activation = current_price * (1 + trailing_trigger_pct)
    else:
        # Para SHORT: activation es menor que precio actual
        trailing_activation = current_price * (1 - trailing_trigger_pct)
    
    trailing_activation = adjust_price_to_tick(symbol, trailing_activation)
    trailing_callback = brackets.get("trailing_callback_pct", 0.5)
    
    print(f"[brackets] Trailing activation DINÁMICO para {symbol}: current={current_price} activation={trailing_activation}")
    
    if trailing_activation and remaining_size > 0:
        try:
            place_order(
                {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "TRAILING_STOP_MARKET",
                    "activationPrice": f"{trailing_activation:.8f}",
                    "callbackRate": f"{trailing_callback}",
                    "quantity": f"{remaining_size:.8f}",
                    "reduceOnly": "true",
                    "timeInForce": "GTC",
                }
            )
            print(
                f"[brackets] Trailing activado tras TP2: {symbol} activation={trailing_activation} "
                f"cb={trailing_callback}% (precio actual={current_price})"
            )
            # Actualizar estado
            state["trailing_active"] = True
            state["sl_price"] = break_even_price
            state["trailing_activation_dynamic"] = trailing_activation
            state["trailing_activated_at_price"] = current_price
            set_position_state(symbol, state)
        except Exception as e:
            print(f"[brackets] Error trailing tras TP2 {symbol}: {e}")
    
    return True


def manage_brackets():
    """
    Coloca SL/TP/trailing para las órdenes OPEN ejecutadas que aún no tienen brackets.
    Primero actualiza el tracking de TP hit.
    
    Distribución de posición:
    - Surf: porción que queda con trailing (configurable, ej: 30%)
    - Rest: tamaño - surf
    - TP1: 50% del rest
    - TP2: 50% del rest
    """
    # Actualizar estado de TP hit antes de gestionar brackets
    update_tp_hit_tracking()
    
    # Try DB first, fallback to JSON
    db_orders = get_orders_from_db()
    orders = db_orders if db_orders else load_json(ORDERS_LOG_PATH, {"orders": []}).get("orders", [])
    risk = load_json(RISK_CONFIG_PATH, {})
    risk_status = get_risk_status()
    can_place_brackets = True
    if risk_status:
        equity = risk_status.get("equity", 0)
        drawdown = risk_status.get("drawdown_pct", 0)
        exposure_pct = risk_status.get("exposure_pct", 0)
        risk_level = risk_status.get("risk_level", "UNKNOWN")
        can_trade = risk_status.get("can_trade", True)
        print(
            f"[manage_brackets] RiskGuard status -> equity={equity:.2f}, drawdown={drawdown*100:.2f}%, "
            f"exposure={exposure_pct*100:.2f}%, level={risk_level}, can_trade={can_trade}"
        )
        # Basic guardrails: pause bracket placement if RiskGuard is blocking trading
        if not can_trade:
            can_place_brackets = False
            print("[manage_brackets] ⚠️ RiskGuard reports can_trade=False, skipping bracket placement")
        # Additional safety: avoid placing new brackets when drawdown > 20%
        if drawdown >= 0.20:
            can_place_brackets = False
            print(
                f"[manage_brackets] ⚠️ Drawdown {drawdown*100:.2f}% above safety threshold, skipping brackets"
            )
    else:
        print("[manage_brackets] ⚠️ RiskGuard status not available, proceeding with default behavior")

    if not can_place_brackets:
        return

    for o in orders:
        if o.get("action") != "OPEN" or o.get("status") != "EXECUTED":
            continue

        symbol = o.get("symbol")
        if not symbol:
            continue

        # CRITICAL FIX: Skip positions with pending scale-in to prevent race condition
        if is_scale_in_pending(symbol):
            print(f"[manage_brackets] ⏭️ Skipping {symbol} - scale-in pending")
            continue

        state = get_position_state(symbol) or {}
        audit = _audit_position_state(symbol, state, risk)
        if audit.get("state_mutated"):
            set_position_state(symbol, state)
        if audit.get("needs_initial_brackets"):
            o["brackets_placed"] = False
        elif o.get("brackets_placed"):
            continue

        side = o.get("side")
        if not side:
            continue

        state = get_position_state(symbol) or {}
        # Soportar tanto "entry" como "entry_price" para compatibilidad
        entry = float(state.get("entry_price", state.get("entry", 0)))
        size = float(state.get("size", 0))

        if size <= 0 or entry <= 0:
            continue

        # Calcular todos los niveles de brackets
        brackets = _calculate_brackets(entry, side, risk, symbol)
        
        sl_price = brackets["sl_price"]
        tp1_price = brackets["tp1_price"]
        tp2_price = brackets["tp2_price"]
        trailing_activation = brackets["trailing_activation"]
        trailing_callback_pct = brackets["trailing_callback_pct"]
        surf_qty_pct = brackets["surf_qty_pct"]
        close_side = brackets["close_side"]

        # Calcular cantidades según distribución v1
        # surf_qty = size * surf_qty_pct
        # rest = size - surf_qty
        # tp1_qty = rest * 0.5
        # tp2_qty = rest * 0.5
        surf_qty = adjust_qty_to_lot(symbol, size * surf_qty_pct)
        rest_qty = size - surf_qty
        tp1_qty = adjust_qty_to_lot(symbol, rest_qty * 0.5)
        tp2_qty = adjust_qty_to_lot(symbol, rest_qty * 0.5)
        
        # Validar que las cantidades cubran toda la posición
        total_parts = tp1_qty + tp2_qty + surf_qty
        if total_parts < size * 0.99:  # Allow 1% tolerance
            # Ajustar para cubrir toda la posición
            surf_qty = adjust_qty_to_lot(symbol, size - tp1_qty - tp2_qty)

        try:
            cancel_all_open_orders(symbol)
        except Exception:
            pass

        # Colocar SL
        sl_order_id = None
        try:
            sl_qty = adjust_qty_to_lot(symbol, size)
            if sl_qty > 0:
                sl_order = place_order(
                    {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "STOP_MARKET",
                        "stopPrice": f"{sl_price:.8f}",
                        "quantity": f"{sl_qty:.8f}",
                        "reduceOnly": "true",
                        "timeInForce": "GTC",
                        "workingType": "MARK_PRICE",
                    }
                )
                sl_order_id = sl_order.get("orderId")
                state["sl_order_id"] = sl_order_id
                print(f"[brackets] SL colocado: {symbol} @ {sl_price}")
                try:
                    state_service.insert_order(
                        Order(
                            order_id=str(sl_order_id),
                            client_order_id=None,
                            symbol=symbol,
                            side=close_side,
                            type="STOP_MARKET",
                            status="NEW",
                            price=sl_price,
                            quantity=sl_qty,
                            executed_qty=0,
                            avg_price=None,
                            reduce_only=True,
                            time_in_force="GTC",
                            position_side=state.get("position_side"),
                            update_time=int(time.time() * 1000),
                            cycle_id=state.get("cycle_id"),
                            bracket_label="SL",
                            metadata={"placed_by": "manage_brackets"},
                        )
                    )
                except Exception as exc:
                    print(f"[manage_brackets] ⚠️ No se pudo registrar SL en DB: {exc}")
        except Exception as e:
            print(f"[brackets] Error SL {symbol}: {e}")

        # Colocar TP1
        if tp1_qty > 0:
            try:
                tp1_order = place_order(
                    {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "TAKE_PROFIT_MARKET",
                        "stopPrice": f"{tp1_price:.8f}",
                        "quantity": f"{tp1_qty:.8f}",
                        "reduceOnly": "true",
                        "timeInForce": "GTC",
                        "workingType": "MARK_PRICE",
                    }
                )
                tp1_order_id = tp1_order.get("orderId")
                print(f"[brackets] TP1 colocado: {symbol} @ {tp1_price} qty={tp1_qty}")
                
                # Guardar TP1 order ID en estado
                state["tp1_order_id"] = tp1_order_id
                try:
                    state_service.insert_order(
                        Order(
                            order_id=str(tp1_order_id),
                            client_order_id=None,
                            symbol=symbol,
                            side=close_side,
                            type="TAKE_PROFIT_MARKET",
                            status="NEW",
                            price=tp1_price,
                            quantity=tp1_qty,
                            executed_qty=0,
                            avg_price=None,
                            reduce_only=True,
                            time_in_force="GTC",
                            position_side=state.get("position_side"),
                            update_time=int(time.time() * 1000),
                            cycle_id=state.get("cycle_id"),
                            bracket_label="TP1",
                            metadata={"placed_by": "manage_brackets"},
                        )
                    )
                except Exception as exc:
                    print(f"[manage_brackets] ⚠️ No se pudo registrar TP1 en DB: {exc}")
            except Exception as e:
                print(f"[brackets] Error TP1 {symbol}: {e}")

        # Colocar TP2
        if tp2_qty > 0:
            try:
                tp2_order = place_order(
                    {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "TAKE_PROFIT_MARKET",
                        "stopPrice": f"{tp2_price:.8f}",
                        "quantity": f"{tp2_qty:.8f}",
                        "reduceOnly": "true",
                        "timeInForce": "GTC",
                        "workingType": "MARK_PRICE",
                    }
                )
                tp2_order_id = tp2_order.get("orderId")
                print(f"[brackets] TP2 colocado: {symbol} @ {tp2_price} qty={tp2_qty}")
                
                # Guardar TP2 order ID en estado
                state["tp2_order_id"] = tp2_order_id
                try:
                    state_service.insert_order(
                        Order(
                            order_id=str(tp2_order_id),
                            client_order_id=None,
                            symbol=symbol,
                            side=close_side,
                            type="TAKE_PROFIT_MARKET",
                            status="NEW",
                            price=tp2_price,
                            quantity=tp2_qty,
                            executed_qty=0,
                            avg_price=None,
                            reduce_only=True,
                            time_in_force="GTC",
                            position_side=state.get("position_side"),
                            update_time=int(time.time() * 1000),
                            cycle_id=state.get("cycle_id"),
                            bracket_label="TP2",
                            metadata={"placed_by": "manage_brackets"},
                        )
                    )
                except Exception as exc:
                    print(f"[manage_brackets] ⚠️ No se pudo registrar TP2 en DB: {exc}")
            except Exception as e:
                print(f"[brackets] Error TP2 {symbol}: {e}")

        # NOT placing trailing stop here - it will be placed AFTER TP2 hits
        # This ensures the trailing stop is only activated when TP2 is hit
        # The Surf portion stays in the position without a trailing stop until then
        # See _check_and_handle_tp2_hit() for trailing activation after TP2
        # 
        # REMOVED: Early trailing stop placement (was causing duplicate orders)
        # trailing_qty = adjust_qty_to_lot(symbol, size - tp1_qty - tp2_qty)
        # if trailing_qty > 0:
        #     try:
        #         place_order(
        #             {
        #                 "symbol": symbol,
        #                 "side": close_side,
        #                 "type": "TRAILING_STOP_MARKET",
        #                 "activationPrice": f"{trailing_activation:.8f}",
        #                 "callbackRate": f"{trailing_callback_pct}",
        #                 "quantity": f"{trailing_qty:.8f}",
        #                 "reduceOnly": "true",
        #                 "timeInForce": "GTC",
        #             }
        #         )
        #         print(
        #             f"[brackets] Trailing colocado: {symbol} activation={trailing_activation} "
        #             f"cb={trailing_callback_pct}%"
        #         )
        #         # Marcar trailing_active en estado
        #         state["trailing_active"] = True
        #     except Exception as e:
        #         print(f"[brackets] Error trailing {symbol}: {e}")

        # Guardar niveles en estado para referencia
        state["sl_price"] = sl_price
        state["tp1_price"] = tp1_price
        state["tp2_price"] = tp2_price
        state["tp1_hit"] = state.get("tp1_hit", False)
        state["tp2_hit"] = state.get("tp2_hit", False)
        state["surf_qty"] = surf_qty
        state["tp1_qty"] = tp1_qty
        state["tp2_qty"] = tp2_qty
        
        set_position_state(symbol, state)

        o["brackets_placed"] = True
        o["brackets_ts"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    save_json(ORDERS_LOG_PATH, {"orders": orders})

    # Also persist to DB for querying
    _persist_orders_to_db(orders)


def _persist_orders_to_db(orders: list) -> None:
    """Persist orders list to DB for querying.
    
    This ensures orders are available in the DB for dashboards and analytics.
    """
    try:
        from state.state_service import state_service
        from state.models import Order
        
        for o in orders:
            # Skip if no order_id
            if not o.get("order_id"):
                continue
                
            order = Order(
                order_id=str(o.get("order_id", "")),
                client_order_id=o.get("client_order_id", ""),
                symbol=o.get("symbol", ""),
                side=o.get("action", ""),
                type=o.get("type", "MARKET"),
                status=o.get("status", "UNKNOWN"),
                price=float(o.get("price", 0) or 0),
                quantity=float(o.get("quantity", 0) or 0),
                executed_qty=float(o.get("executed_qty", 0) or 0),
                avg_price=float(o.get("price", 0) or 0),
                reduce_only=o.get("reduce_only", False),
                time_in_force=o.get("time_in_force", "GTC"),
                position_side=o.get("position_side", "BOTH"),
                update_time=int(o.get("update_time", 0) or 0),
                cycle_id=o.get("cycle_id"),
                bracket_label=o.get("bracket_label"),
                metadata={
                    "sl_price": o.get("sl_price"),
                    "tp1_price": o.get("tp1_price"),
                    "tp2_price": o.get("tp2_price"),
                    "brackets_placed": o.get("brackets_placed", False),
                    "action": o.get("action"),
                },
            )
            state_service.insert_order(order)
    except Exception as e:
        print(f"[manage_brackets] ⚠️ Failed to persist orders to DB: {e}")


def check_closed_positions():
    """
    Revisa posiciones cerradas y, si todavía existe state, registra el trade.
    Usa cycle_id para deduplicar con sync_state_from_exchange.
    """
    # Load symbols from config
    risk_cfg = load_json(RISK_CONFIG_PATH, {})
    symbols_cfg = risk_cfg.get("symbols", {})
    enabled_symbols = [s for s, cfg in symbols_cfg.items() if cfg.get("enabled", False)]
    
    # Filter positions to only include enabled symbols
    positions = get_positions_v3(symbols=enabled_symbols)
    exchange_open = set()

    for p in positions:
        try:
            sym = p["symbol"]
            amt = float(p.get("positionAmt", 0.0))
        except Exception:
            continue
        if abs(amt) > 0:
            exchange_open.add(sym)

    # Try DB first, fallback to JSON
    db_orders = get_orders_from_db()
    orders = db_orders if db_orders else load_json(ORDERS_LOG_PATH, {"orders": []}).get("orders", [])

    for o in orders:
        if o.get("action") != "OPEN" or o.get("status") != "EXECUTED":
            continue
        if o.get("trade_registered"):
            continue

        symbol = o.get("symbol")
        if not symbol:
            continue

        if symbol in exchange_open:
            continue

        state = get_position_state(symbol)
        if not state:
            # sync_state ya limpió y registró
            o["trade_registered"] = True
            continue

        # Soportar tanto "entry" como "entry_price" para compatibilidad
        entry = float(state.get("entry_price", state.get("entry", 0)))
        side = state.get("side", "BUY")
        size = float(state.get("size", 0))
        score = float(state.get("score", 0))
        setup = state.get("setup_type", "unknown")
        ts_open = state.get("ts_open", "")
        cycle_id = state.get("cycle_id", "")

        if entry <= 0:
            o["trade_registered"] = True
            clear_position_state(symbol)
            continue

        mark = get_mark_price(symbol)
        exit_price = mark if mark > 0 else entry

        if side == "BUY":
            pnl_usdt = (exit_price - entry) * size
            pnl_pct = (exit_price - entry) / entry if entry > 0 else 0.0
        else:
            pnl_usdt = (entry - exit_price) * size
            pnl_pct = (entry - exit_price) / entry if entry > 0 else 0.0

        register_trade_result(
            symbol=symbol,
            side=side,
            entry_price=entry,
            exit_price=exit_price,
            pnl_usdt=pnl_usdt,
            reason_close="detected_by_brackets",
            score=score,
            setup_type=setup,
            ts_open=ts_open,
            ts_close=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            cycle_id=cycle_id,
            pnl_pct=pnl_pct,
        )

        o["trade_registered"] = True
        clear_position_state(symbol)

    save_json(ORDERS_LOG_PATH, {"orders": orders})

    # Also persist to DB for querying
    _persist_orders_to_db(orders)


def _persist_orders_to_db(orders: list) -> None:
    """Persist orders list to DB for querying.
    
    This ensures orders are available in the DB for dashboards and analytics.
    """
    try:
        from state.state_service import state_service
        from state.models import Order
        
        for o in orders:
            # Skip if no order_id
            if not o.get("order_id"):
                continue
                
            order = Order(
                order_id=str(o.get("order_id", "")),
                client_order_id=o.get("client_order_id", ""),
                symbol=o.get("symbol", ""),
                side=o.get("action", ""),
                type=o.get("type", "MARKET"),
                status=o.get("status", "UNKNOWN"),
                price=float(o.get("price", 0) or 0),
                quantity=float(o.get("quantity", 0) or 0),
                executed_qty=float(o.get("executed_qty", 0) or 0),
                avg_price=float(o.get("price", 0) or 0),
                reduce_only=o.get("reduce_only", False),
                time_in_force=o.get("time_in_force", "GTC"),
                position_side=o.get("position_side", "BOTH"),
                update_time=int(o.get("update_time", 0) or 0),
                cycle_id=o.get("cycle_id"),
                bracket_label=o.get("bracket_label"),
                metadata={
                    "sl_price": o.get("sl_price"),
                    "tp1_price": o.get("tp1_price"),
                    "tp2_price": o.get("tp2_price"),
                    "brackets_placed": o.get("brackets_placed", False),
                    "action": o.get("action"),
                },
            )
            state_service.insert_order(order)
    except Exception as e:
        print(f"[manage_brackets] ⚠️ Failed to persist orders to DB: {e}")


def main():
    """
    Función principal que:
    1. Actualiza el tracking de TP1/TP2 hit
    2. Verifica y maneja TP1 hits (transición a Surf)
    3. Verifica y maneja TP2 hits (mover SL a break-even, activar trailing)
    4. Coloca brackets para posiciones nuevas
    5. Verifica posiciones cerradas
    """
    # Actualizar tracking de TP hits primero
    update_tp_hit_tracking()
    
    # Cargar estado y riesgo para manejar TP1/TP2 hits
    risk = load_json(RISK_CONFIG_PATH, {})
    # Try DB first, fallback to JSON
    db_orders = get_orders_from_db()
    orders = db_orders if db_orders else load_json(ORDERS_LOG_PATH, {"orders": []}).get("orders", [])
    
    # Procesar cada posición abierta para verificar TP1 y TP2 hits
    for o in orders:
        if o.get("action") != "OPEN" or o.get("status") != "EXECUTED":
            continue
            
        symbol = o.get("symbol")
        if not symbol:
            continue
            
        state = get_position_state(symbol)
        if not state:
            continue
        
        # ========== TP1 Hit Handler ==========
        # Manejar transición TP1→Surf (sin TP2)
        tp1_hit = state.get("tp1_hit", False)
        if tp1_hit and not state.get("tp1_handled", False):
            entry = float(state.get("entry_price", state.get("entry", 0)))
            side = state.get("side", "BUY")
            
            if entry > 0:
                brackets = _calculate_brackets(entry, side, risk, symbol)
                
                # Manejar TP1 hit (transición a Surf)
                if _check_and_handle_tp1_hit(symbol, state, brackets):
                    state["tp1_handled"] = True
                    set_position_state(symbol, state)
                    print(f"[brackets] TP1 hit procesado para {symbol}")
        
        # ========== TP2 Hit Handler ==========
        # Verificar si TP2 fue alcanzado y manejarlo (activar trailing dinámico)
        tp2_hit = state.get("tp2_hit", False)
        if tp2_hit and not state.get("tp2_handled", False):
            # Calcular brackets para obtener precios
            entry = float(state.get("entry_price", state.get("entry", 0)))
            side = state.get("side", "BUY")
            
            if entry > 0:
                brackets = _calculate_brackets(entry, side, risk, symbol)
                
                # Verificar y manejar TP2 hit (con trailing activation DINÁMICO)
                if _check_and_handle_tp2_hit(symbol, state, brackets):
                    state["tp2_handled"] = True
                    set_position_state(symbol, state)
                    print(f"[brackets] TP2 hit procesado para {symbol}")
    
    # Colocar brackets para posiciones nuevas
    manage_brackets()
    
    # Verificar posiciones cerradas
    check_closed_positions()
    print("[manage_brackets] done")


if __name__ == "__main__":
    main()
