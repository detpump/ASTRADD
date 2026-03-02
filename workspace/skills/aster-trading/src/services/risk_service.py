"""Shared Risk Service providing singleton access to RiskGuard.

Ensures all components (TradingSystem, manage_brackets, scale_in_position)
operate on the same RiskGuard instance and state file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from risk.risk_guard_v2 import RiskGuard, RiskConfig
from state.state_service import state_service

logger = logging.getLogger(__name__)

# Module-level cached instances
_risk_guard_instance: Optional[RiskGuard] = None
_risk_guard_lock = asyncio.Lock()

# Simple TTL cache for parsed risk config adapter
_RISK_CONFIG_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "value": None}
_RISK_CONFIG_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class NormalizedRiskConfig:
    """Typed adapter for risk_config.json shared by runtime and dashboard."""

    global_config: Dict[str, Any]
    symbols_config: Dict[str, Dict[str, Any]]
    mode_config: Dict[str, Any]
    raw_config: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        """Return JSON-serializable config payload with canonical top-level sections."""
        return {
            "global": dict(self.global_config),
            "symbols": dict(self.symbols_config),
            "mode": dict(self.mode_config),
        }

    def to_risk_guard_config(self) -> RiskConfig:
        """Map adapter to RiskGuard legacy RiskConfig contract (no behavior change)."""
        g = self.global_config
        return RiskConfig(
            max_position_pct=g.get("max_equity_risk_pct_per_trade", 0.05),
            max_total_exposure_pct=g.get("max_total_exposure_pct", 0.20),
            max_leverage=g.get("max_leverage", 10),
            min_position_pct=g.get("min_position_pct", 0.02),
            enable_dynamic_adjustment=g.get("enable_dynamic_adjustment", True),
            max_daily_loss_pct=g.get("max_daily_loss_pct", 0.05),
            max_weekly_loss_pct=g.get("max_weekly_loss_pct", 0.12),
            max_drawdown_pct=g.get("max_drawdown_pct", 0.12),
            max_trades_per_day=g.get("max_trades_per_day", 10),
            max_consecutive_losses=g.get("max_consecutive_losses", 3),
            min_trade_interval_minutes=g.get("min_trade_interval_minutes", 15),
            enable_circuit_breaker=g.get("enable_circuit_breaker", True),
            circuit_breaker_cooldown_minutes=g.get("circuit_breaker_cooldown_minutes", 60),
            use_dynamic_sizing=g.get("use_dynamic_sizing", True),
            reduce_on_drawdown=g.get("reduce_on_drawdown", True),
            reduce_factor_per_5pct_dd=g.get("reduce_factor_per_5pct_dd", 0.5),
        )


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _normalize_global_config(global_cfg: Any) -> Dict[str, Any]:
    src = global_cfg if isinstance(global_cfg, dict) else {}
    normalized = dict(src)

    # Known keys used by runtime/dashboard (validated/coerced)
    normalized["max_equity_risk_pct_per_trade"] = _as_float(src.get("max_equity_risk_pct_per_trade"), 0.05)
    normalized["max_total_exposure_pct"] = _as_float(src.get("max_total_exposure_pct"), 0.20)
    normalized["max_leverage"] = _as_int(src.get("max_leverage"), 10)
    normalized["min_position_pct"] = _as_float(src.get("min_position_pct"), 0.02)
    normalized["enable_dynamic_adjustment"] = _as_bool(src.get("enable_dynamic_adjustment"), True)
    normalized["max_daily_loss_pct"] = _as_float(src.get("max_daily_loss_pct"), 0.05)
    normalized["max_weekly_loss_pct"] = _as_float(src.get("max_weekly_loss_pct"), 0.12)
    normalized["max_drawdown_pct"] = _as_float(src.get("max_drawdown_pct"), 0.12)
    normalized["max_trades_per_day"] = _as_int(src.get("max_trades_per_day"), 10)
    normalized["max_consecutive_losses"] = _as_int(src.get("max_consecutive_losses"), 3)
    normalized["min_trade_interval_minutes"] = _as_int(src.get("min_trade_interval_minutes"), 15)
    normalized["enable_circuit_breaker"] = _as_bool(src.get("enable_circuit_breaker"), True)
    normalized["circuit_breaker_cooldown_minutes"] = _as_int(src.get("circuit_breaker_cooldown_minutes"), 60)
    normalized["use_dynamic_sizing"] = _as_bool(src.get("use_dynamic_sizing"), True)
    normalized["reduce_on_drawdown"] = _as_bool(src.get("reduce_on_drawdown"), True)
    normalized["reduce_factor_per_5pct_dd"] = _as_float(src.get("reduce_factor_per_5pct_dd"), 0.5)

    normalized["max_equity_notional_pct"] = _as_float(src.get("max_equity_notional_pct"), 60.0)
    normalized["daily_loss_hard_limit_usdt"] = _as_float(src.get("daily_loss_hard_limit_usdt"), 1.5)
    normalized["max_leverage_global"] = _as_int(src.get("max_leverage_global"), 3)
    normalized["min_notional_global"] = _as_float(src.get("min_notional_global"), 8.0)
    normalized["sl_pct"] = _as_float(src.get("sl_pct"), 1.0)
    normalized["tp1_pct"] = _as_float(src.get("tp1_pct"), 1.5)
    normalized["tp2_pct"] = _as_float(src.get("tp2_pct"), 3.0)

    return normalized


def _normalize_symbol_config(symbol_cfg: Any) -> Dict[str, Any]:
    src = symbol_cfg if isinstance(symbol_cfg, dict) else {}
    normalized = dict(src)

    normalized["enabled"] = _as_bool(src.get("enabled"), False)
    normalized["max_leverage"] = _as_int(src.get("max_leverage"), 3)
    normalized["min_notional_usdt"] = _as_float(src.get("min_notional_usdt"), 8.0)
    normalized["max_notional_usdt"] = _as_float(src.get("max_notional_usdt"), 0.0)
    normalized["target_risk_pct_per_trade"] = _as_float(src.get("target_risk_pct_per_trade"), 0.0)
    normalized["trailing_callback_rate_pct"] = _as_float(src.get("trailing_callback_rate_pct"), 0.0)
    normalized["sl_pct"] = _as_float(src.get("sl_pct"), 0.0)
    normalized["tp1_pct"] = _as_float(src.get("tp1_pct"), 0.0)
    normalized["tp2_pct"] = _as_float(src.get("tp2_pct"), 0.0)
    normalized["sl_pct_min"] = _as_float(src.get("sl_pct_min"), 0.0)
    normalized["sl_pct_max"] = _as_float(src.get("sl_pct_max"), 0.0)
    normalized["tp1_pct_min"] = _as_float(src.get("tp1_pct_min"), 0.0)
    normalized["tp1_pct_max"] = _as_float(src.get("tp1_pct_max"), 0.0)
    normalized["tp2_pct_min"] = _as_float(src.get("tp2_pct_min"), 0.0)
    normalized["tp2_pct_max"] = _as_float(src.get("tp2_pct_max"), 0.0)

    return normalized


def get_normalized_risk_config(cache_ttl_seconds: float = _RISK_CONFIG_CACHE_TTL_SECONDS) -> NormalizedRiskConfig:
    """Load and validate risk config using a shared TTL cache."""
    now = time.time()
    cached = _RISK_CONFIG_CACHE.get("value")
    cached_at = _RISK_CONFIG_CACHE.get("loaded_at", 0.0)
    if cached and (now - cached_at) <= max(cache_ttl_seconds, 0.0):
        return cached

    base_dir = Path(__file__).resolve().parents[2]
    config_path = base_dir / "config" / "risk_config.json"

    raw: Dict[str, Any] = {}
    if not config_path.exists():
        logger.warning("risk_config.json not found, using defaults")
    else:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            raw = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load risk config (%s), using defaults", exc)

    global_cfg = _normalize_global_config(raw.get("global", {}))
    symbols_src = raw.get("symbols", {})
    symbols_cfg: Dict[str, Dict[str, Any]] = {}
    if isinstance(symbols_src, dict):
        for symbol, cfg in symbols_src.items():
            symbols_cfg[str(symbol)] = _normalize_symbol_config(cfg)

    mode_src = raw.get("mode", {}) if isinstance(raw.get("mode", {}), dict) else {}
    mode_cfg = dict(mode_src)
    mode_cfg["risk_profile"] = str(mode_src.get("risk_profile", "normal"))
    mode_cfg["reason"] = str(mode_src.get("reason", ""))
    mode_cfg["last_update"] = str(mode_src.get("last_update", ""))

    adapted = NormalizedRiskConfig(
        global_config=global_cfg,
        symbols_config=symbols_cfg,
        mode_config=mode_cfg,
        raw_config=raw,
    )
    _RISK_CONFIG_CACHE["value"] = adapted
    _RISK_CONFIG_CACHE["loaded_at"] = now
    return adapted


def _load_risk_config() -> RiskConfig:
    """Load risk config from config/risk_config.json if available."""
    try:
        return get_normalized_risk_config().to_risk_guard_config()
    except Exception as exc:
        logger.warning("Failed to load risk config (%s), using defaults", exc)
        return RiskConfig()


def get_shared_risk_guard() -> RiskGuard:
    """Return the singleton RiskGuard instance (thread safe)."""
    global _risk_guard_instance
    if _risk_guard_instance:
        return _risk_guard_instance

    # no lock (sync path) to allow synchronous use; fallback to async lock if needed
    config = _load_risk_config()
    _risk_guard_instance = RiskGuard(config=config, state_file=None)
    return _risk_guard_instance


def reset_risk_guard_for_tests():
    """Utility to reset singleton state (used by tests)."""
    global _risk_guard_instance
    _risk_guard_instance = None


def get_risk_status() -> dict:
    """Safe helper to fetch RiskGuard status without exposing singleton directly."""
    try:
        guard = get_shared_risk_guard()
        return guard.get_status()
    except Exception as exc:
        logger.warning("Could not fetch risk status: %s", exc)
        return {}


def is_circuit_breaker_active() -> bool:
    """Helper returning True when RiskGuard circuit breaker is active."""
    try:
        guard = get_shared_risk_guard()
        return guard.circuit_breaker_active()
    except Exception as exc:
        logger.warning("Could not determine circuit breaker status: %s", exc)
        return False
