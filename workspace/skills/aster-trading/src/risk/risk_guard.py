#!/usr/bin/env python3
import os
import sys
import json
import datetime
import logging

# Add BOTH paths - Python uses first match, so order matters per-module:
# - workspace/src for api.aster_api (has required dependencies)
# - backup/src for centralized_logger (has log_operation, log_risk_check) and trade_state (missing in workspace)
_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Use environment variable for backup path - allows server migration
_backup_src = os.environ.get("ASTER_TRADING_BACKUP_DIR", os.path.join(os.environ.get("ASTER_TRADING_DIR", "/Users/FIRMAS/.openclaw/skills/aster-trading"), "backup/src"))

if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _backup_src not in sys.path:
    sys.path.insert(1, _backup_src)  # Insert at position 1 so workspace takes priority

from api.aster_api import get_balance_v3, get_positions_v3
from trade_state import get_position_state  # reservado por si se usa más adelante
from centralized_logger import log_operation, log_risk_check

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RISK_CONFIG_PATH = os.path.join(BASE, "config/risk_config.json")
RUNTIME_STATE_PATH = os.path.join(BASE, "data/state/risk_runtime_state.json")


def load_json(path, default):
    """Load JSON from file with proper exception handling"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in {path}: {e}")
        return default
    except IOError as e:
        logger.error(f"IO error reading {path}: {e}")
        return default
    except PermissionError as e:
        logger.error(f"Permission denied reading {path}: {e}")
        return default
    except Exception as e:
        logger.error(f"Unexpected error reading {path}: {type(e).__name__}: {e}")
        return default


def save_json(path, data):
    """Save JSON to file with proper exception handling"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"IO error writing to {path}: {e}")
    except PermissionError as e:
        logger.error(f"Permission denied writing to {path}: {e}")
    except TypeError as e:
        logger.error(f"Type error serializing data to {path}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error writing to {path}: {type(e).__name__}: {e}")


def log_event(event: dict):
    """Función wrapper para compatibilidad - ahora usa logging centralizado."""
    try:
        log_operation(event)
    except Exception as e:
        logger.error(f"Error logging event: {type(e).__name__}: {e}")


def main():
    risk = load_json(RISK_CONFIG_PATH, {})
    global_cfg = risk.get("global", {})
    symbols_cfg = risk.get("symbols", {})

    max_equity_notional_pct = float(global_cfg.get("max_equity_notional_pct", 60.0))
    daily_loss_hard_limit_usdt = float(global_cfg.get("daily_loss_hard_limit_usdt", 0.0))
    cooldown_minutes_after_hard_loss = int(global_cfg.get("cooldown_minutes_after_hard_loss", 0))

    balances = get_balance_v3()
    usdt = next((b for b in balances if b.get("asset") == "USDT"), {})
    usdc = next((b for b in balances if b.get("asset") == "USDC"), {})
    equity_total = float(usdt.get("balance", "0")) + float(usdc.get("balance", "0"))

    # Get enabled symbols from config
    enabled_symbols = {s for s, cfg in symbols_cfg.items() if cfg.get("enabled", False)}
    # Filter positions to only include enabled symbols
    positions = get_positions_v3(symbols=list(enabled_symbols))
    open_positions = [p for p in positions if float(p.get("positionAmt", "0") or "0") != 0.0]

    runtime = load_json(RUNTIME_STATE_PATH, {})
    now = datetime.datetime.utcnow()
    today_str = now.date().isoformat()

    equity_max = float(runtime.get("equity_max", equity_total))
    equity_start_day = float(runtime.get("equity_start_day", equity_total))
    start_day_str = runtime.get("start_day", today_str)
    circuit_until_str = runtime.get("circuit_breaker_until")

    if today_str != start_day_str:
        equity_start_day = equity_total
        start_day_str = today_str

    if equity_total > equity_max:
        equity_max = equity_total

    breached = False
    breaches = []

    num_open = len(open_positions)
    max_allowed_positions = len(enabled_symbols)
    if num_open > max_allowed_positions > 0:
        breaches.append(f"too_many_positions: {num_open} > {max_allowed_positions}")
        breached = True

    total_notional = 0.0
    symbol_notional = {sym: 0.0 for sym in enabled_symbols}
    for p in open_positions:
        sym = p.get("symbol")
        size = abs(float(p.get("positionAmt", "0") or "0"))
        entry = float(p.get("entryPrice", "0") or "0")
        notional = size * entry
        total_notional += notional
        if sym in symbol_notional:
            symbol_notional[sym] += notional

    if equity_total > 0:
        notional_pct = (total_notional / equity_total) * 100.0
    else:
        notional_pct = 0.0

    if notional_pct > max_equity_notional_pct:
        breaches.append(
            f"notional_pct_exceeded: {notional_pct:.2f}% > {max_equity_notional_pct}%"
        )
        breached = True

    for sym, notional in symbol_notional.items():
        cfg = symbols_cfg.get(sym, {})
        max_n = float(cfg.get("max_notional_usdt", 0.0))
        if max_n > 0.0 and notional > max_n + 1e-6:
            breaches.append(
                f"symbol_notional_exceeded:{sym}:{notional:.2f} > {max_n:.2f}"
            )
            breached = True

    pnl_today = equity_total - equity_start_day
    if daily_loss_hard_limit_usdt > 0 and pnl_today < -daily_loss_hard_limit_usdt:
        breaches.append(
            f"daily_loss_exceeded:{pnl_today:.2f} < -{daily_loss_hard_limit_usdt:.2f}"
        )
        breached = True
        if cooldown_minutes_after_hard_loss > 0:
            circuit_until = now + datetime.timedelta(minutes=cooldown_minutes_after_hard_loss)
            runtime["circuit_breaker_until"] = circuit_until.isoformat() + "Z"

    circuit_active = False
    if circuit_until_str:
        try:
            circuit_until = datetime.datetime.fromisoformat(
                circuit_until_str.replace("Z", "")
            )
            if now < circuit_until:
                circuit_active = True
                breaches.append(
                    f"circuit_breaker_active_until:{circuit_until.isoformat()}Z"
                )
                breached = True
            else:
                runtime["circuit_breaker_until"] = None
        except ValueError as e:
            logger.warning(f"Invalid circuit_breaker_until date format '{circuit_until_str}': {e}")
            runtime["circuit_breaker_until"] = None
        except TypeError as e:
            logger.warning(f"Type error parsing circuit_breaker_until '{circuit_until_str}': {e}")
        except Exception as e:
            logger.error(f"Unexpected error parsing circuit_breaker_until: {type(e).__name__}: {e}")

    runtime["equity_max"] = equity_max
    runtime["equity_start_day"] = equity_start_day
    runtime["start_day"] = start_day_str
    save_json(RUNTIME_STATE_PATH, runtime)

    if breached:
        event = {
            "script": "risk_guard",
            "action": "risk_breach",
            "equity_total": equity_total,
            "equity_start_day": equity_start_day,
            "equity_max": equity_max,
            "pnl_today": pnl_today,
            "total_notional": total_notional,
            "notional_pct": notional_pct,
            "num_open_positions": num_open,
            "symbol_notional": symbol_notional,
            "breaches": breaches,
        }
        log_event(event)
        print(json.dumps(event, ensure_ascii=False, indent=2))
    else:
        event = {
            "script": "risk_guard",
            "action": "ok",
            "equity_total": equity_total,
            "equity_start_day": equity_start_day,
            "equity_max": equity_max,
            "pnl_today": pnl_today,
            "total_notional": total_notional,
            "notional_pct": notional_pct,
            "num_open_positions": num_open,
            "symbol_notional": symbol_notional,
        }
        log_event(event)
        print(json.dumps(event, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
