#!/usr/bin/env python3
"""
Scale-In Position Module for Trading System V2

Adds to winning positions when:
1. Position is in profit (pnl_pct > 0)
2. DUAL-PATH APPROACH:
   - PRIMARY (Strict): TP2 has been hit (trailing is ACTIVE)
   - SECONDARY (Permissive): TP1 hit + SL at break-even + ADX > 30 (strong trend)
3. Total position doesn't exceed max_notional_usdt

The secondary path allows scaling earlier in strong trends while still
requiring momentum confirmation (ADX > 30). This catches opportunities
where price doesn't reach exact TP2 but trend is strong.

Uses decreasing scale sizes: 3% → 1.5% → 0.75% of available gap.
"""

import json
import logging
import os
import sys
import math
import time
from typing import Dict, Any, List, Optional

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.aster_api import get_positions_v3, place_order, set_leverage, get_exchange_info, get_mark_price, get_open_orders, cancel_order
from trade_state import (
    get_position_state as _get_position_state,
    set_position_state as _set_position_state,
    set_scale_in_pending,
)
from services.risk_service import get_shared_risk_guard, get_risk_status
from state.state_service import state_service
from state.models import Position

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# DB-backed position state helpers (DB is source of truth; trade_state is fallback/compat)
def get_position_state(symbol: str) -> dict:
    try:
        positions = {p.symbol: p for p in state_service.get_positions()}
        if symbol in positions:
            p = positions[symbol]
            meta = p.metadata or {}
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
                "tp1_hit": meta.get("tp1_hit"),  # For secondary path check
                "tp2_hit": meta.get("tp2_hit"),
                "sl_hit": meta.get("sl_hit"),
                "sl_price": meta.get("sl_price"),
                "last_scale_in_time": meta.get("last_scale_in_time"),
                "pyramid_scales": meta.get("pyramid_scales"),
                "last_scale_price": meta.get("last_scale_price"),
                "trailing_active": meta.get("trailing_active"),  # CRITICAL: trailing stop is active
                "surf_phase": meta.get("surf_phase"),  # Position is in surf phase
            }
    except Exception as exc:
        logger.warning(f"Could not read position {symbol} from DB: {exc}")

    # Legacy fallback (JSON file) to avoid breaking compatibility
    return _get_position_state(symbol)


def set_position_state(symbol: str, state: dict):
    # Update legacy JSON for backwards compatibility with other components still reading it
    try:
        _set_position_state(symbol, state)
    except Exception as exc:
        logger.warning(f"Could not persist position {symbol} to legacy trade_state: {exc}")

    # Persist authoritative snapshot into DB
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
                "sl_price": state.get("sl_price"),
                "last_scale_in_time": state.get("last_scale_in_time"),
                "pyramid_scales": state.get("pyramid_scales"),
                "last_scale_price": state.get("last_scale_price"),
                "last_scale_in_qty": state.get("last_scale_in_qty"),
                "last_scale_in_notional": state.get("last_scale_in_notional"),
            },
        )
        state_service.upsert_position(pos)
    except Exception as exc:
        logger.warning(f"Could not persist position {symbol} to DB: {exc}")


# Configuration - use centralized paths
try:
    from paths import BASE_DIR, CONFIG_DIR, RISK_CONFIG_FILE
    RISK_CONFIG_PATH = RISK_CONFIG_FILE
except ImportError:
    # Fallback
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RISK_CONFIG_PATH = os.path.join(BASE_DIR, "config/risk_config.json")

# Symbols to check for scale-in
SYMBOLS = ["ETHUSDT", "ASTERUSDT", "BNBUSDT", "SOLUSDT", "HYPEUSDT"]
try:
    SHARED_RISK_GUARD = get_shared_risk_guard()
except Exception as e:
    SHARED_RISK_GUARD = None
    logger.warning(f"Shared RiskGuard not available: {e}")

# Scale-in parameters - DEFAULT values (can be overridden by config)
# Decreasing scale sizes: 3% → 1.5% → 0.75%
DEFAULT_SCALE_SIZES = [0.03, 0.015, 0.0075]
DEFAULT_MIN_DISTANCE_PCT = [1.0, 1.5, 2.0]
DEFAULT_COOLDOWN_MINUTES = 15
DEFAULT_ADX_THRESHOLD = 25  # For primary path (trailing active)
DEFAULT_ADX_THRESHOLD_SECONDARY = 30  # Higher threshold for secondary path (TP1 + BE)
DEFAULT_MAX_SCALES = 3


def load_json(path: str, default: Any = None) -> Any:
    """Load JSON file or return default."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    """Save data to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_risk_config() -> Dict[str, Any]:
    """Load risk configuration."""
    return load_json(RISK_CONFIG_PATH, {})


def get_symbol_config(symbol: str) -> Dict[str, Any]:
    """Get configuration for a specific symbol."""
    risk = load_risk_config()
    return risk.get("symbols", {}).get(symbol, {})


def get_pyramid_config() -> Dict[str, Any]:
    """Get pyramid configuration from risk config."""
    risk = load_risk_config()
    return risk.get("global", {}).get("pyramid", {})


def get_adx_for_symbol(symbol: str) -> float:
    """
    Get ADX (Average Directional Index) for a symbol.
    Returns 0 if unavailable.
    ADX >= 25 indicates strong trend - good for scaling.
    """
    try:
        from api.aster_api import public_get
        
        # Get klines to calculate ADX
        klines = public_get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": "1h",
            "limit": 100
        })
        
        if not klines or len(klines) < 14:
            return 0.0
        
        # Calculate ADX using high, low, close
        highs = []
        lows = []
        closes = []
        
        for k in klines:
            highs.append(float(k[2]))  # high
            lows.append(float(k[3]))   # low
            closes.append(float(k[4])) # close
        
        # Calculate True Range and Directional Movement
        trs = []
        pos_dm = []
        neg_dm = []
        
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
            
            up_move = highs[i] - highs[i-1]
            down_move = lows[i-1] - lows[i]
            
            pos_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
            neg_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        
        if len(trs) < 14:
            return 0.0
        
        # Calculate smoothed averages (14 period)
        tr_sum = sum(trs[:14])
        pos_dm_sum = sum(pos_dm[:14])
        neg_dm_sum = sum(neg_dm[:14])
        
        if tr_sum == 0:
            return 0.0
        
        plus_di = (pos_dm_sum / tr_sum) * 100
        minus_di = (neg_dm_sum / tr_sum) * 100
        
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        
        # ADX is smoothed DX
        adx = dx  # Simplified - would need more smoothing for production
        
        return adx
        
    except Exception as e:
        logger.warning(f"Could not calculate ADX for {symbol}: {e}")
        return 0.0


def get_funding_rate(symbol: str) -> float:
    """
    Get funding rate for a symbol.
    Returns 0 if unavailable.
    Negative funding means we're paying to hold the position.
    """
    try:
        from api.aster_api import public_get
        
        funding = public_get("/fapi/v1/fundingRate", {
            "symbol": symbol,
            "limit": 1
        })
        
        if funding and len(funding) > 0:
            return float(funding[0].get("fundingRate", 0))
        return 0.0
        
    except Exception as e:
        logger.warning(f"Could not get funding rate for {symbol}: {e}")
        return 0.0


def adjust_qty_to_lot(symbol: str, qty: float) -> float:
    """Adjust quantity to meet exchange lot size requirements."""
    try:
        info = get_exchange_info()
        filters = info.get("symbols", [])
        
        symbol_filters = None
        for s in filters:
            if s.get("symbol") == symbol:
                symbol_filters = s.get("filters", [])
                break
        
        if not symbol_filters:
            return qty
        
        min_qty = step_size = None
        for f in symbol_filters:
            if f.get("filterType") == "LOT_SIZE":
                min_qty = float(f["minQty"])
                step_size = float(f["stepSize"])
                break
        
        if min_qty is None or step_size is None:
            return qty
        
        # Round up to meet minimum lot size
        steps = math.ceil(qty / step_size - 1e-9)
        adj = steps * step_size
        if adj < min_qty:
            adj = min_qty
        return float(f"{adj:.8f}")
    except Exception as e:
        logger.warning(f"Error adjusting qty to lot: {e}")
        return qty


def calculate_pnl_pct(size: float, entry: float, mark: float, side: str) -> float:
    """Calculate position PnL percentage."""
    if size == 0 or entry == 0:
        return 0.0
    
    if side == "BUY":
        return (mark - entry) / entry * 100.0
    else:  # SELL
        return (entry - mark) / entry * 100.0


def update_trailing_stop_after_scale_in(symbol: str, side: str, new_size: float) -> bool:
    """
    Update trailing stop order after a scale-in executes.
    
    After scale-in, the position size increases but the trailing stop still has the old quantity.
    This function:
    1. Cancels the existing trailing stop order (if any)
    2. Places a new trailing stop with the correct new quantity
    
    Args:
        symbol: Trading symbol
        side: Position side (BUY or SELL)
        new_size: The new position size after scale-in
        
    Returns:
        True if trailing stop was updated successfully, False otherwise
    """
    try:
        # Get position state to find trailing configuration
        position_state = get_position_state(symbol) or {}
        
        # Check if trailing is active
        if not position_state.get("trailing_active", False):
            logger.debug(f"Trailing not active for {symbol}, skipping update")
            return True  # Not an error - trailing just isn't set up yet
        
        # Get trailing configuration from state
        trailing_activation = position_state.get("trailing_activation_dynamic")
        if not trailing_activation:
            # Try to get from legacy field
            trailing_activation = position_state.get("trailing_activation_price")
        
        if not trailing_activation:
            logger.warning(f"No trailing activation price found for {symbol}, cannot update trailing stop")
            return False
        
        # Get callback rate from risk config
        risk = load_risk_config()
        risk_global = risk.get("global", {})
        trailing_callback_pct = risk_global.get("trailing_callback_pct", 0.5)
        
        # Calculate new trailing quantity (should be the surf_qty portion)
        # After scale-in, we recalculate based on new total size
        surf_qty_pct = risk_global.get("surf_qty_pct", 0.3)
        trailing_qty = abs(new_size) * surf_qty_pct
        
        # Adjust to lot size
        trailing_qty = adjust_qty_to_lot(symbol, trailing_qty)
        
        if trailing_qty <= 0:
            logger.warning(f"Invalid trailing qty calculated for {symbol}: {trailing_qty}")
            return False
        
        # Get current mark price
        mark_price = get_mark_price(symbol)
        if mark_price <= 0:
            logger.error(f"Invalid mark price for {symbol}: {mark_price}")
            return False
        
        # Close side is opposite of position side
        close_side = "SELL" if side == "BUY" else "BUY"
        
        # First, cancel existing trailing stop orders
        try:
            open_orders = get_open_orders(symbol)
            for order in open_orders:
                order_type = str(order.get("type", "")).upper()
                if order_type == "TRAILING_STOP_MARKET":
                    order_id = order.get("orderId")
                    if order_id:
                        cancel_order(symbol, int(order_id))
                        logger.info(f"Cancelled old trailing stop {order_id} for {symbol}")
        except Exception as e:
            logger.warning(f"Error cancelling trailing stop for {symbol}: {e}")
            # Continue anyway - might not have an existing trailing stop
        
        # Place new trailing stop with correct quantity
        try:
            trailing_order = place_order({
                "symbol": symbol,
                "side": close_side,
                "type": "TRAILING_STOP_MARKET",
                "activationPrice": f"{trailing_activation:.8f}",
                "callbackRate": f"{trailing_callback_pct}",
                "quantity": f"{trailing_qty:.8f}",
                "reduceOnly": "true",
                "timeInForce": "GTC",
            })
            
            if trailing_order and trailing_order.get("orderId"):
                logger.info(
                    f"✅ Trailing stop UPDATED after scale-in for {symbol}: "
                    f"activation={trailing_activation}, callback={trailing_callback_pct}%, "
                    f"qty={trailing_qty:.8f}"
                )
                
                # Update position state with new trailing info
                position_state["trailing_order_id"] = trailing_order.get("orderId")
                position_state["trailing_qty"] = trailing_qty
                set_position_state(symbol, position_state)
                
                return True
            else:
                logger.error(f"Failed to place trailing stop for {symbol}: {trailing_order}")
                return False
                
        except Exception as e:
            logger.error(f"Error placing trailing stop for {symbol} after scale-in: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error in update_trailing_stop_after_scale_in for {symbol}: {e}")
        return False


def check_scale_in_conditions(
    symbol: str,
    size: float,
    entry: float,
    mark: float,
    side: str,
    sl: float,
    max_notional: float
) -> Dict[str, Any]:
    """
    Check if position qualifies for scale-in.
    
    Returns:
        Dict with 'can_scale_in' (bool) and 'reason' (str)
    """
    # Get position state from trade_state.py (includes tp2_hit tracking)
    position_state = get_position_state(symbol) or {}
    tp2_hit = position_state.get("tp2_hit", False)
    
    # Get pyramid config for scale-in parameters
    pyramid_cfg = get_pyramid_config()
    max_scales = pyramid_cfg.get("max_scales", DEFAULT_MAX_SCALES)
    scale_sizes = pyramid_cfg.get("scale_sizes", DEFAULT_SCALE_SIZES)
    min_distances = pyramid_cfg.get("min_scale_distance_pct", DEFAULT_MIN_DISTANCE_PCT)
    cooldown_minutes = pyramid_cfg.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
    adx_threshold = pyramid_cfg.get("adx_threshold", DEFAULT_ADX_THRESHOLD)
    use_adx_filter = pyramid_cfg.get("use_adx_filter", True)
    use_funding_filter = pyramid_cfg.get("use_funding_filter", True)
    
    # Condition 1: Position must be in profit
    pnl_pct = calculate_pnl_pct(size, entry, mark, side)
    if pnl_pct <= 0:
        return {
            "can_scale_in": False,
            "reason": f"Position not in profit (pnl_pct={pnl_pct:.2f}%)"
        }
    
    # Condition 2: DUAL-PATH approach for scale-in eligibility
    # PRIMARY (Strict): TP2 hit = trailing active, can scale in
    # SECONDARY (Permissive): TP1 hit + SL at break-even + strong ADX (>30)
    trailing_active = position_state.get("trailing_active", False)
    tp1_hit = position_state.get("tp1_hit", False)
    
    # Check if SL is at break-even
    sl_ok = False
    if side == "BUY" and sl >= entry:
        sl_ok = True
    elif side == "SELL" and sl <= entry:
        sl_ok = True
    
    # Get ADX for secondary path check
    adx = get_adx_for_symbol(symbol) if use_adx_filter else 0
    adx_secondary_threshold = pyramid_cfg.get("adx_secondary_threshold", DEFAULT_ADX_THRESHOLD_SECONDARY)
    
    # Determine which path (if any) allows scale-in
    scale_in_path = None
    
    # PRIMARY PATH: TP2 hit (trailing active) - always allowed
    if tp2_hit:
        scale_in_path = "PRIMARY"  # TP2 hit = trailing active
    
    # SECONDARY PATH: TP1 hit + SL at break-even + strong ADX
    # This allows scaling in strong trends where price doesn't reach exact TP2
    elif tp1_hit and sl_ok and adx >= adx_secondary_threshold:
        scale_in_path = "SECONDARY"  # TP1 + BE + strong trend
    
    if scale_in_path is None:
        # Neither path satisfied - build detailed rejection reason
        tp1_status = "hit" if tp1_hit else "not hit"
        tp2_status = "hit" if tp2_hit else "not hit"
        sl_status = "at BE" if sl_ok else f"at {sl}" if sl > 0 else "not set"
        adx_status = f"ADX={adx:.1f}"
        
        if not tp1_hit:
            reason = f"TP1={tp1_status} - need TP1 hit + SL at BE + ADX>={adx_secondary_threshold} for secondary path"
        elif not sl_ok:
            reason = f"TP1={tp1_status}, SL={sl_status} - need SL at break-even for secondary path"
        else:
            reason = f"{adx_status} < {adx_secondary_threshold} - need strong trend for secondary path"
        
        return {
            "can_scale_in": False,
            "reason": f"TP2={tp2_status}, {reason}"
        }
    
    # Log which path is being used
    logger.info(f"Scale-in path for {symbol}: {scale_in_path} (TP2_hit={tp2_hit}, TP1_hit={tp1_hit}, sl_ok={sl_ok}, ADX={adx:.1f})")
    
    # Condition 3: Check scale count (max scales)
    scales_done = position_state.get("pyramid_scales", 0)
    if scales_done >= max_scales:
        return {
            "can_scale_in": False,
            "reason": f"Max scales reached ({scales_done}/{max_scales})"
        }
    
    # Condition 4: Check cooldown
    last_scale_time = position_state.get("last_scale_in_time", 0)
    if last_scale_time > 0:
        time_since = (time.time() * 1000 - last_scale_time) / 60000  # minutes
        if time_since < cooldown_minutes:
            return {
                "can_scale_in": False,
                "reason": f"Cooldown active ({time_since:.1f}/{cooldown_minutes} min)"
            }
    
    # Condition 5: Check ADX only for PRIMARY path (secondary path already checked above)
    # For PRIMARY path (TP2 hit), we still check ADX for additional confirmation
    if scale_in_path == "PRIMARY" and use_adx_filter:
        if adx < adx_threshold:
            return {
                "can_scale_in": False,
                "reason": f"ADX={adx:.1f} < {adx_threshold} even with TP2 hit (weak trend)"
            }
    
    # Condition 6: Check funding rate
    if use_funding_filter:
        funding_rate = get_funding_rate(symbol)
        max_negative_funding = pyramid_cfg.get("max_negative_funding_pct", 0.01)
        if funding_rate < -max_negative_funding:
            return {
                "can_scale_in": False,
                "reason": f"Funding rate negative ({funding_rate*100:.3f}%)"
            }
    
    # Condition 7: Check price distance (scale size specific)
    current_scale_idx = min(scales_done, len(min_distances) - 1)
    min_distance_pct = min_distances[current_scale_idx]
    
    last_scale_price = position_state.get("last_scale_price", entry)
    price_moved_pct = abs(mark - last_scale_price) / last_scale_price * 100
    
    if price_moved_pct < min_distance_pct:
        return {
            "can_scale_in": False,
            "reason": f"Price moved {price_moved_pct:.2f}% < {min_distance_pct}% min distance"
        }
    
    # Condition 8: Respect max_notional_usdt
    current_notional = abs(size) * entry
    leverage = position_state.get("leverage", 1) if position_state else 1
    leveraged_exposure = current_notional * leverage
    
    if leveraged_exposure >= max_notional:
        return {
            "can_scale_in": False,
            "reason": f"Max notional reached ({leveraged_exposure:.2f} >= {max_notional:.2f}, leverage={leverage}x)"
        }
    
    # Calculate gap and scale size (DECREASING)
    gap = max_notional - leveraged_exposure
    if gap <= 0:
        return {
            "can_scale_in": False,
            "reason": "No available gap for scaling"
        }
    
    # Use decreasing scale size based on scale count
    scale_size = scale_sizes[current_scale_idx] if current_scale_idx < len(scale_sizes) else scale_sizes[-1]
    add_notional = gap * scale_size
    add_qty = add_notional / mark
    add_qty = adjust_qty_to_lot(symbol, add_qty)
    
    if add_qty <= 0:
        return {
            "can_scale_in": False,
            "reason": "Calculated quantity too small"
        }
    
    return {
        "can_scale_in": True,
        "pnl_pct": pnl_pct,
        "tp2_hit": tp2_hit,
        "tp1_hit": tp1_hit,
        "sl_ok": sl_ok,
        "trailing_active": trailing_active,
        "scale_in_path": scale_in_path,  # PRIMARY or SECONDARY
        "scales_done": scales_done,
        "scale_size": scale_size,
        "adx": adx,
        "funding_rate": get_funding_rate(symbol) if use_funding_filter else 0,
        "current_notional": current_notional,
        "leveraged_exposure": leveraged_exposure,
        "leverage": leverage,
        "max_notional": max_notional,
        "gap": gap,
        "add_notional": add_notional,
        "add_qty": add_qty,
        "reason": f"All conditions met via {scale_in_path} path"
    }


async def execute_scale_in(
    symbol: str,
    side: str,
    add_qty: float,
    current_notional: float,
    max_notional: float
) -> Optional[Dict[str, Any]]:
    """
    Execute a scale-in order.
    
    Returns:
        Order result or None on failure
    """
    try:
        # CRITICAL FIX: Set scale_in_pending flag to prevent bracket manager from interfering
        set_scale_in_pending(symbol, True)
        logger.info(f"Scale-in pending flag set for {symbol} to prevent bracket interference")
        
        # Set leverage (use existing or default)
        symbol_config = get_symbol_config(symbol)
        leverage = symbol_config.get("max_leverage", 5)
        
        # P0 FIX: Cap leverage based on equity for safety
        # Get equity to determine safe leverage
        try:
            from api.aster_api import get_equity_total_usdt
            equity = get_equity_total_usdt()
        except:
            equity = 0
        
        # Apply equity-based leverage caps
        if equity > 0 and equity < 100:
            GLOBAL_MAX_LEVERAGE = 3
        elif equity > 0 and equity < 500:
            GLOBAL_MAX_LEVERAGE = 5
        else:
            GLOBAL_MAX_LEVERAGE = 10
        
        if leverage > GLOBAL_MAX_LEVERAGE:
            logger.warning(f"Leverage {leverage}x exceeds global max {GLOBAL_MAX_LEVERAGE}x (equity=${equity:.2f}), capping")
            leverage = GLOBAL_MAX_LEVERAGE
        
        set_leverage(symbol, leverage)
        
        # Get current mark price
        mark_price = get_mark_price(symbol)
        if mark_price <= 0:
            logger.error(f"Invalid mark price for {symbol}: {mark_price}")
            return None
        
        # Prepare order (MARKET order, NOT reduceOnly - we're adding to position)
        order_side = "BUY" if side == "BUY" else "SELL"
        
        order_params = {
            "symbol": symbol,
            "side": order_side,
            "type": "MARKET",
            "quantity": f"{add_qty:.8f}"
        }
        
        logger.info(f"Executing scale-in: {order_params}")
        result = place_order(order_params)
        
        if result and "orderId" in result:
            new_notional = (add_qty * mark_price) + current_notional
            logger.info(
                f"Scale-in SUCCESS: {symbol} {order_side} {add_qty:.8f} @ {mark_price:.6f}, "
                f"old_notional={current_notional:.2f}, new_notional={new_notional:.2f}"
            )
            
            # Update position state in trade_state.py to track the scaled position
            position_state = get_position_state(symbol) or {}
            position_state["last_scale_in_time"] = int(time.time() * 1000)
            position_state["last_scale_in_qty"] = add_qty
            position_state["last_scale_in_notional"] = add_qty * mark_price
            # Track pyramid scale count and price
            position_state["pyramid_scales"] = position_state.get("pyramid_scales", 0) + 1
            position_state["last_scale_price"] = mark_price
            set_position_state(symbol, position_state)
            
            # CRITICAL FIX: Update trailing stop after scale-in
            # Get the new position size from exchange and update trailing stop
            try:
                all_positions = get_positions_v3()
                new_position = next((p for p in all_positions if p.get("symbol", "").upper() == symbol.upper()), None)
                if new_position:
                    new_size = float(new_position.get("positionAmt", 0))
                    # Update trailing stop with new quantity
                    trailing_updated = update_trailing_stop_after_scale_in(symbol, side, new_size)
                    if trailing_updated:
                        logger.info(f"Trailing stop successfully updated for {symbol} after scale-in")
                    else:
                        logger.warning(f"Failed to update trailing stop for {symbol} after scale-in")
            except Exception as e:
                logger.error(f"Error updating trailing stop after scale-in for {symbol}: {e}")
            
            # CRITICAL FIX: Clear scale_in_pending flag after successful scale-in
            set_scale_in_pending(symbol, False)
            logger.info(f"Scale-in pending flag cleared for {symbol}")
            
            return {
                "success": True,
                "order_id": result.get("orderId"),
                "symbol": symbol,
                "side": order_side,
                "added_qty": add_qty,
                "added_notional": add_qty * mark_price,
                "old_notional": current_notional,
                "new_notional": new_notional
            }
        else:
            logger.warning(f"Scale-in order failed: {result}")
            # CRITICAL FIX: Clear scale_in_pending flag even on failure
            set_scale_in_pending(symbol, False)
            return {
                "success": False,
                "error": str(result)
            }
            
    except Exception as e:
        logger.error(f"Error executing scale-in: {e}")
        # CRITICAL FIX: Clear scale_in_pending flag on exception
        set_scale_in_pending(symbol, False)
        return {
            "success": False,
            "error": str(e)
        }


async def process_scale_in() -> List[Dict[str, Any]]:
    """
    Main function to process scale-in for all positions.
    
    Returns:
        List of scale-in results
    """
    results = []
    
    # Load risk config
    risk_config = load_risk_config()
    symbols_cfg = risk_config.get("symbols", {})
    
    # Get all positions from exchange
    all_positions = get_positions_v3()
    
    # Filter to our symbols
    symbols_upper = [s.upper() for s in SYMBOLS]
    positions_by_symbol = {
        p["symbol"]: p 
        for p in all_positions 
        if p.get("symbol", "").upper() in symbols_upper
    }
    
    logger.info(f"Checking {len(positions_by_symbol)} positions for scale-in opportunities")
    risk_status = get_risk_status()
    if not risk_status:
        logger.warning("RiskGuard status unavailable, skipping scale-in run")
        return results
    if not risk_status.get("can_trade", True):
        logger.warning("RiskGuard blocking trades (can_trade=False), skipping scale-in")
        return results
    if risk_status.get("drawdown_pct", 0) >= 0.20:
        logger.warning("Drawdown above 20%, skipping scale-in for safety")
        return results
    total_exposure_pct = risk_status.get("exposure_pct", 0.0)
    
    for symbol, pos in positions_by_symbol.items():
        # Skip if symbol not enabled in config
        cfg = symbols_cfg.get(symbol, {})
        if not cfg.get("enabled", False):
            continue
        
        # Get max_notional from config
        max_notional = float(cfg.get("max_notional_usdt", 0.0))
        if max_notional <= 0:
            continue
        
        # Extract position data
        size = float(pos.get("positionAmt", "0"))
        entry = float(pos.get("entryPrice", "0"))
        mark = float(pos.get("markPrice", pos.get("markPrice", "0.0")))
        
        if size == 0.0 or entry == 0.0:
            continue
        
        side = "BUY" if size > 0 else "SELL"
        
        # Get SL from position data
        # FIX: In Binance API v3, the actual SL price is stored in 'stopPrice' for pending orders,
        # but for open positions, we need to check different fields:
        # - 'stopPrice' - only contains value if there's an active SL order
        # - We need to query open orders to get the actual SL price
        #
        # The fix: Check multiple fields AND fall back to trade_state storage
        sl = 0.0

        # First try direct position fields (for positions with active SL orders)
        if pos.get("stopPrice") and float(pos.get("stopPrice", 0)) > 0:
            sl = float(pos.get("stopPrice", 0))
        # Check if there's a stored SL in position data from our brackets
        elif pos.get("slPrice") and float(pos.get("slPrice", 0)) > 0:
            sl = float(pos.get("slPrice", 0))
        # Try another common field name used in Binance responses
        elif pos.get("sl") and float(pos.get("sl", 0)) > 0:
            sl = float(pos.get("sl", 0))
        # Try stop_loss field
        elif pos.get("stop_loss") and float(pos.get("stop_loss", 0)) > 0:
            sl = float(pos.get("stop_loss", 0))

        # FALLBACK: Get SL from trade_state if still not found
        if sl <= 0:
            position_state = get_position_state(symbol)
            if position_state:
                # Check sl_price from trade_state (set by manage_brackets.py)
                stored_sl = position_state.get("sl_price", 0.0)
                if stored_sl and float(stored_sl) > 0:
                    sl = float(stored_sl)
                # Also check legacy field names
                elif position_state.get("sl"):
                    sl = float(position_state.get("sl", 0))

        # If still 0, log warning for debugging (SL might have been hit or not set)
        if sl <= 0:
            logger.debug(f"No SL found for {symbol} position - entry={entry}, sl may be not set or hit")
        
        # Check scale-in conditions
        check_result = check_scale_in_conditions(
            symbol=symbol,
            size=size,
            entry=entry,
            mark=mark,
            side=side,
            sl=sl,
            max_notional=max_notional
        )
        
        if not check_result["can_scale_in"]:
            logger.debug(f"Scale-in skipped for {symbol}: {check_result['reason']}")
            continue
        
        # Reject if total exposure already near limit (guard clause)
        if total_exposure_pct >= risk_status.get("max_total_exposure_pct", 0.25):
            logger.info(
                "Scale-in skipped for %s: exposure %.2f%% >= limit", symbol, total_exposure_pct * 100
            )
            continue

        # Execute scale-in
        logger.info(
            f"Scale-in conditions met for {symbol}: "
            f"pnl_pct={check_result['pnl_pct']:.2f}%, "
            f"tp2_hit={check_result['tp2_hit']}, "
            f"trailing_active={check_result.get('trailing_active', False)}, "
            f"add_qty={check_result['add_qty']:.8f}"
        )
        
        result = await execute_scale_in(
            symbol=symbol,
            side=side,
            add_qty=check_result["add_qty"],
            current_notional=check_result["current_notional"],
            max_notional=check_result["max_notional"]
        )
        
        results.append({
            "symbol": symbol,
            "check_result": check_result,
            "execution_result": result
        })
    
    return results


def run_sync() -> List[Dict[str, Any]]:
    """Synchronous wrapper for running scale-in check."""
    import asyncio
    return asyncio.run(process_scale_in())


def mark_tp2_hit(symbol: str) -> bool:
    """
    Mark TP2 as hit for a symbol in the scale-in state.
    Called when TP2 order is filled to enable scale-in.
    
    Args:
        symbol: The trading symbol (e.g., 'ASTERUSDT')
        
    Returns:
        True if successfully marked, False otherwise
    """
    try:
        state = get_position_state(symbol) or {}
        state["tp2_hit"] = True
        state["tp2_hit_time"] = int(time.time() * 1000)
        set_position_state(symbol, state)
        logger.info(f"TP2 marked as hit for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Error marking TP2 hit for {symbol}: {e}")
        return False


def get_scale_in_state(symbol: str = None) -> Dict[str, Any]:
    """
    Get the current scale-in state for a symbol or all symbols.
    
    Args:
        symbol: Optional symbol to get state for. If None, returns state for all tracked symbols.
        
    Returns:
        Dictionary with scale-in state information
    """
    try:
        if symbol:
            state = get_position_state(symbol) or {}
            return {
                "symbol": symbol,
                "tp2_hit": state.get("tp2_hit", False),
                "tp2_hit_time": state.get("tp2_hit_time"),
                "last_scale_in_time": state.get("last_scale_in_time"),
                "last_scale_in_qty": state.get("last_scale_in_qty"),
                "last_scale_in_notional": state.get("last_scale_in_notional"),
            }
        else:
            # Return state for all tracked symbols
            return {}
    except Exception as e:
        logger.error(f"Error getting scale-in state: {e}")
        return {}


# Entry point for standalone execution
if __name__ == "__main__":
    import asyncio
    
    print("=" * 60)
    print("  SCALE-IN POSITION CHECK")
    print("=" * 60)
    
    results = asyncio.run(process_scale_in())
    
    print(f"\nResults: {json.dumps(results, indent=2)}")
