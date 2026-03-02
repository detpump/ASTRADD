#!/usr/bin/env python3
"""
Dynamic Risk Adjuster - LLM-Driven Risk Parameter Adjustment

This module periodically analyzes trading performance and adjusts risk parameters
using an LLM. It mirrors the v1 adjust_risk_config.py functionality adapted for v2.

Features:
- Analyzes trade performance (win rate, avg win/loss)
- Monitors equity and market conditions
- Suggests adjustments to risk_config parameters
- Rate limited to control token costs (max 1 call per 3 hours)

Author: Aster Trading V2
"""

import os
import sys
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# ==============================
# CONFIGURATION
# ==============================

# Risk profile thresholds (from v1)
SMALL_EQ_MAX = 100.0
MEDIUM_EQ_MAX = 500.0

# SMALL: small account, GROWTH optimized
SMALL_MAX_EQ_RISK_PCT = 3.0
SMALL_MIN_NOTIONAL = 8.0
SMALL_MAX_NOTIONAL = 25.0
SMALL_DAILY_LOSS = 12.0

# MEDIUM: moderate - growth oriented
MED_MAX_EQ_RISK_PCT = 2.0
MED_MIN_NOTIONAL = 8.0
MED_MAX_NOTIONAL = 25.0
MED_DAILY_LOSS = 25.0

# LARGE: larger account, contained risk
LARGE_MAX_EQ_RISK_PCT = 2.5
LARGE_MIN_NOTIONAL = 10.0
LARGE_MAX_NOTIONAL = 25.0
LARGE_DAILY_LOSS = 60.0

# Global bounds - Soft limits (LLM can suggest within these)
GLOBAL_MIN_NOTIONAL = 8.0
GLOBAL_MAX_NOTIONAL = 25.0
GLOBAL_MIN_EQ_RISK_PCT = 2.0
GLOBAL_MAX_EQ_RISK_PCT = 5.0
GLOBAL_MIN_DAILY_LOSS = 2.0
GLOBAL_MAX_DAILY_LOSS = 5.0
GLOBAL_MIN_EQ_NOTIONAL_PCT = 50.0
GLOBAL_MAX_EQ_NOTIONAL_PCT = 100.0

# HARDCAPS - These can NEVER be exceeded (safety limits)
HARD_MAX_LEVERAGE = 15  # Absolute maximum leverage
HARD_MIN_NOTIONAL = 8.0  # Absolute minimum notional
HARD_MAX_EQ_RISK_PCT = 5.0  # Absolute max position % per trade
HARD_MAX_DAILY_LOSS_PCT = 5.0  # Absolute max daily loss %

# Trailing defaults by symbol
TRAILING_DEFAULTS = {
    "ETHUSDT": 1.0,
    "ASTERUSDT": 1.5,
    "BNBUSDT": 1.0,
    "SOLUSDT": 1.2,
    "HYPEUSDT": 2.0,
}

# Rate limiting
DEFAULT_COOLDOWN_HOURS = 3


@dataclass
class AdjustmentConfig:
    """Configuration for the risk adjuster"""
    # Paths
    risk_config_path: str = "./config/risk_config.json"
    state_path: str = None  # deprecated; state now from DB
    trade_log_path: str = "./logs/trade_log.jsonl"
    
    # Rate limiting
    cooldown_hours: float = 3.0
    max_calls_per_day: int = 8
    
    # LLM settings
    llm_provider: str = "minimax"  # or "anthropic"
    model: str = "MiniMax-M2.1"
    
    # Risk adjustment bounds
    min_position_pct: float = 0.01  # 1%
    max_position_pct: float = 0.10  # 10%
    min_daily_loss_pct: float = 0.01
    max_daily_loss_pct: float = 0.15


class DynamicRiskAdjuster:
    """
    LLM-driven risk parameter adjustment system.
    
    Periodically analyzes performance and adjusts:
    - max_position_pct (position size)
    - max_daily_loss_pct
    - sl_pct, tp1_pct, tp2_pct (stop loss / take profit levels)
    - trailing_callback_pct (trailing stop sensitivity)
    - max_trades_per_day
    """
    
    def __init__(self, config: AdjustmentConfig = None):
        self.config = config or AdjustmentConfig()
        
        # State tracking
        self._last_adjustment_time: Optional[float] = None
        self._adjustment_count_today: int = 0
        self._last_day: int = 0
        
        # Load paths
        self.base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.risk_path = os.path.join(self.base_path, self.config.risk_config_path.lstrip("./"))
        self.state_path = None  # deprecated
        self.trade_log_path = os.path.join(self.base_path, self.config.trade_log_path.lstrip("./"))
        
    def _load_json(self, path: str, default: Any = None) -> Any:
        """Load JSON from file with fallback"""
        if not path or not os.path.exists(path):
            return default or {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading {path}: {e}")
            return default or {}
    
    def _save_json(self, path: str, data: Any) -> bool:
        """Save JSON to file"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving {path}: {e}")
            return False
    
    def _get_trade_history(self, max_lines: int = 200) -> list:
        """Get recent trade history from trade log"""
        if not os.path.exists(self.trade_log_path):
            return []
        
        lines = []
        try:
            with open(self.trade_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            lines.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse trade log line: {e}")
                        except Exception as e:
                            logger.error(f"Unexpected error parsing trade log: {e}")
            return lines[-max_lines:]
        except Exception as e:
            logger.warning(f"Error reading trade log: {e}")
            return []
    
    def _calculate_performance_stats(self, trades: list) -> Dict[str, Any]:
        """Calculate performance statistics from trade history"""
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "recent_drawdown": 0.0
            }
        
        wins = []
        losses = []
        
        for trade in trades:
            pnl = trade.get("pnl", 0) or 0
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))
        
        total = len(wins) + len(losses)
        win_rate = len(wins) / total if total > 0 else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        
        # Profit factor
        profit_factor = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
        
        # Recent drawdown (last 10 trades)
        equity_curve = [0]
        for trade in trades[-10:]:
            pnl = trade.get("pnl", 0) or 0
            equity_curve.append(equity_curve[-1] + pnl)
        
        peak = max(equity_curve) if equity_curve else 0
        current = equity_curve[-1] if equity_curve else 0
        # FIX: Add explicit peak validation to prevent division by zero
        drawdown = (peak - current) / peak if peak > 0 else 0.0
        
        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "recent_drawdown": drawdown
        }
    
    def _get_equity(self) -> float:
        """Get current equity from API"""
        try:
            from api.aster_api import get_equity_total_usdt
            equity = get_equity_total_usdt()
            if equity and equity > 0:
                return equity
        except Exception as e:
            logger.warning(f"Could not get equity from API: {e}")
        
        # Fallback: try to get from state
        state = self._load_json(self.state_path, {})
        return state.get("equity", 40.71)  # Use real equity, not 10000
    
    def _choose_profile(self, equity: float) -> tuple:
        """Choose risk profile based on equity"""
        if equity <= SMALL_EQ_MAX:
            return (
                "SMALL",
                SMALL_MAX_EQ_RISK_PCT,
                SMALL_MIN_NOTIONAL,
                SMALL_MAX_NOTIONAL,
                SMALL_DAILY_LOSS,
            )
        elif equity <= MEDIUM_EQ_MAX:
            return (
                "MEDIUM",
                MED_MAX_EQ_RISK_PCT,
                MED_MIN_NOTIONAL,
                MED_MAX_NOTIONAL,
                MED_DAILY_LOSS,
            )
        else:
            return (
                "LARGE",
                LARGE_MAX_EQ_RISK_PCT,
                LARGE_MIN_NOTIONAL,
                LARGE_MAX_NOTIONAL,
                LARGE_DAILY_LOSS,
            )
    
    def _get_api_key(self) -> str:
        """Get LLM API key from environment"""
        # Try MiniMax first (v1 style)
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        if api_key:
            return api_key
        
        # Fallback to Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            return api_key
        
        return ""
    
    def should_adjust(self) -> bool:
        """
        Check if risk adjustment should run.
        Respects rate limiting.
        """
        current_time = time.time()
        current_day = int(current_time / 86400)
        
        # Reset daily counter if new day
        if current_day != self._last_day:
            self._adjustment_count_today = 0
            self._last_day = current_day
        
        # Check daily limit
        if self._adjustment_count_today >= self.config.max_calls_per_day:
            logger.info(f"Daily adjustment limit reached ({self.config.max_calls_per_day})")
            return False
        
        # Check cooldown
        if self._last_adjustment_time:
            hours_since = (current_time - self._last_adjustment_time) / 3600
            if hours_since < self.config.cooldown_hours:
                logger.debug(f"Cooldown active: {hours_since:.1f}h since last adjustment")
                return False
        
        return True
    
    def _call_llm(self, prompt: str) -> Optional[Dict]:
        """Call LLM to get risk adjustment suggestions"""
        api_key = self._get_api_key()
        if not api_key:
            logger.error("No LLM API key found")
            return None
        
        try:
            import anthropic
            
            # Use MiniMax endpoint or Anthropic
            if self.config.llm_provider == "minimax":
                client = anthropic.Anthropic(
                    base_url="https://api.minimax.io/anthropic",
                    api_key=api_key,
                )
                model = self.config.model
            else:
                client = anthropic.Anthropic(api_key=api_key)
                model = "claude-3-haiku-20240307"
            
            message = client.messages.create(
                model=model,
                max_tokens=4096,
                system=(
                    "Eres un módulo experto en gestión de riesgo cuantitativo para trading de derivados. "
                    "Siempre devuelves SOLO un objeto JSON válido, sin ningún texto adicional."
                ),
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Extract response
            content_blocks = message.content or []
            text_parts = []
            for block in content_blocks:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            raw_text = "".join(text_parts).strip()
            
            # Clean up response
            if raw_text.startswith("```"):
                raw_text = raw_text.strip().lstrip("`")
                if raw_text.lower().startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.rstrip("`").strip()
            
            # Find JSON object
            first_brace = raw_text.find("{")
            last_brace = raw_text.rfind("}")
            if first_brace != -1 and last_brace != -1:
                raw_text = raw_text[first_brace:last_brace + 1]
            
            return json.loads(raw_text)
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None
    
    def _build_prompt(self, equity: float, profile: str, risk_config: Dict, 
                      state: Dict, performance: Dict) -> str:
        """Build prompt for LLM analysis"""
        
        (
            profile_name,
            max_eq_risk_pct_target,
            min_notional_target,
            max_notional_target,
            daily_loss_target,
        ) = profile
        
        # Get current positions
        positions = state.get("positions", {})
        
        prompt = (
            "Eres un motor de gestión de riesgo para un bot de trading de futuros en Aster DEX.\n\n"
            f"Equity_total_usdt actual: {equity:.2f}. Perfil sugerido: {profile_name}.\n"
            "Tramos de cuenta:\n"
            f"- SMALL (<= {SMALL_EQ_MAX} USDT): hasta {SMALL_MAX_EQ_RISK_PCT}% equity/trade, "
            f"notional {SMALL_MIN_NOTIONAL}-{SMALL_MAX_NOTIONAL} USDT.\n"
            f"- MEDIUM ({SMALL_EQ_MAX}-{MEDIUM_EQ_MAX} USDT): hasta {MED_MAX_EQ_RISK_PCT}%/trade, "
            f"notional {MED_MIN_NOTIONAL}-{MED_MAX_NOTIONAL} USDT.\n"
            f"- LARGE (> {MEDIUM_EQ_MAX} USDT): hasta {LARGE_MAX_EQ_RISK_PCT}%/trade, "
            f"notional {LARGE_MIN_NOTIONAL}-{LARGE_MAX_NOTIONAL} USDT.\n\n"
            f"ESTADÍSTICAS DE RENDIMIENTO (últimos trades):\n"
            f"- Total trades: {performance['total_trades']}\n"
            f"- Win rate: {performance['win_rate']*100:.1f}%\n"
            f"- Avg win: ${performance['avg_win']:.2f}, Avg loss: ${performance['avg_loss']:.2f}\n"
            f"- Profit factor: {performance['profit_factor']:.2f}\n"
            f"- Drawdown reciente: {performance['recent_drawdown']*100:.1f}%\n\n"
            f"POSICIONES ABIERTAS ACTUALMENTE: {len(positions)}\n\n"
            "Debes devolver un NUEVO risk_config.json ajustando parámetros según el rendimiento.\n\n"
            "SCHEMA DEL JSON DE RESPUESTA:\n"
            "{\n"
            '  "global": {\n'
            '    "max_equity_risk_pct_per_trade": 0.0-3.5,\n'
            '    "max_equity_notional_pct": 50-250,\n'
            '    "daily_loss_hard_limit_usdt": 5-100,\n'
            '    "cooldown_minutes_after_hard_loss": 30-240,\n'
            '    "sl_pct": 0.5-5.0,\n'
            '    "tp1_pct": 0.5-5.0,\n'
            '    "tp2_pct": 1.0-10.0,\n'
            '    "trailing_callback_pct": 0.3-3.0\n'
            "  },\n"
            '  "symbols": {\n'
            '    "SYMBOL": {\n'
            '      "max_notional_usdt": 3-25,\n'
            '      "trailing_callback_rate_pct": 0.5-3.0,\n'
            '      "sl_pct_min": 0.5-3.0,\n'
            '      "sl_pct_max": 1.0-5.0,\n'
            '      "tp1_pct_min": 0.5-2.5,\n'
            '      "tp1_pct_max": 1.0-5.0,\n'
            '      "tp2_pct_min": 1.0-5.0,\n'
            '      "tp2_pct_max": 2.0-10.0\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            "REGLAS:\n"
            "- Si hay buenas rachas (win rate > 50%, profit factor > 1.5), PUEDES subir notional y daily_loss.\n"
            "- Si hay malas rachas (drawdown > 20%,连续 pérdidas), BAJA riesgo.\n"
            "- NUNCA bajes min_notional por debajo de 5 USDT.\n"
            "- Mantén todos los símbolos presentes.\n"
            "- Responde SOLO con JSON válido, sin texto.\n\n"
            f"risk_config_actual:\n{json.dumps(risk_config, ensure_ascii=False)}\n"
        )
        
        return prompt
    
    def _sanitize_config(self, new_config: Dict, equity: float, profile: tuple) -> Dict:
        """Sanitize and validate the new config"""
        
        (
            profile_name,
            max_eq_risk_pct_target,
            min_notional_target,
            max_notional_target,
            daily_loss_target,
        ) = profile
        
        # Load original for merging
        original = self._load_json(self.risk_path, {})
        
        # Ensure global section
        if "global" not in new_config:
            new_config["global"] = {}
        
        g = new_config.get("global", {})
        g_old = original.get("global", {})
        
        # Sanitize global values
        max_eq_risk = float(g.get("max_equity_risk_pct_per_trade", max_eq_risk_pct_target))
        max_eq_risk = min(max(max_eq_risk, GLOBAL_MIN_EQ_RISK_PCT), GLOBAL_MAX_EQ_RISK_PCT)
        
        daily_loss = float(g.get("daily_loss_hard_limit_usdt", daily_loss_target))
        daily_loss = min(max(daily_loss, GLOBAL_MIN_DAILY_LOSS), GLOBAL_MAX_DAILY_LOSS)
        
        max_notional_pct = float(g.get("max_equity_notional_pct", g_old.get("max_equity_notional_pct", 100.0)))
        max_notional_pct = min(max(max_notional_pct, GLOBAL_MIN_EQ_NOTIONAL_PCT), GLOBAL_MAX_EQ_NOTIONAL_PCT)
        
        cooldown = int(g.get("cooldown_minutes_after_hard_loss", g_old.get("cooldown_minutes_after_hard_loss", 90)))
        cooldown = min(max(cooldown, 30), 240)
        
        # Trailing and SL/TP
        trailing = float(g.get("trailing_callback_pct", g_old.get("trailing_callback_pct", 1.0)))
        trailing = min(max(trailing, 0.3), 3.0)
        
        sl = float(g.get("sl_pct", g_old.get("sl_pct", 1.5)))
        sl = min(max(sl, 0.5), 5.0)
        
        tp1 = float(g.get("tp1_pct", g_old.get("tp1_pct", 2.0)))
        tp1 = min(max(tp1, 0.5), 5.0)
        
        tp2 = float(g.get("tp2_pct", g_old.get("tp2_pct", 3.0)))
        tp2 = min(max(tp2, 1.0), 10.0)
        
        # CRITICAL: Enforce HARD CAP on global max_leverage
        global_leverage = int(g.get("max_leverage_global", g_old.get("max_leverage_global", 10)))
        global_leverage = min(global_leverage, HARD_MAX_LEVERAGE)  # Never exceed 15x
        
        # Also enforce hard caps on risk parameters
        max_eq_risk = min(max_eq_risk, HARD_MAX_EQ_RISK_PCT)
        daily_loss_usdt = min(float(g.get("daily_loss_hard_limit_usdt", daily_loss)), 
                            equity * HARD_MAX_DAILY_LOSS_PCT)
        
        new_config["global"] = {
            "max_equity_risk_pct_per_trade": max_eq_risk,
            "max_equity_notional_pct": max_notional_pct,
            "daily_loss_hard_limit_usdt": daily_loss_usdt,
            "cooldown_minutes_after_hard_loss": cooldown,
            "max_leverage_global": global_leverage,
            "min_notional_global": HARD_MIN_NOTIONAL,
            "sl_pct": sl,
            "tp1_pct": tp1,
            "tp2_pct": tp2,
            "trailing_callback_pct": trailing,
            "equity_hint_usdt": equity
        }
        
        # Sanitize symbols
        if "symbols" not in new_config:
            new_config["symbols"] = {}
        
        original_symbols = original.get("symbols", {})
        llm_symbols = new_config.get("symbols", {})
        
        merged_symbols = {}
        
        # Start with original symbols, update with LLM values
        for sym, orig_cfg in original_symbols.items():
            cfg = dict(orig_cfg)
            if sym in llm_symbols and isinstance(llm_symbols[sym], dict):
                cfg.update(llm_symbols[sym])
            
            # Sanitize each symbol
            min_notional = float(cfg.get("min_notional_usdt", min_notional_target))
            max_notional = float(cfg.get("max_notional_usdt", max_notional_target))
            
            min_notional = max(min_notional, GLOBAL_MIN_NOTIONAL, min_notional_target)
            # FIX: Use max() to ensure max_notional >= min_notional (was incorrectly using min())
            max_notional = max(max_notional, min_notional, GLOBAL_MIN_NOTIONAL)
            max_notional = min(max_notional, GLOBAL_MAX_NOTIONAL, max_notional_target)
            
            if max_notional < min_notional:
                max_notional = min_notional
            
            cfg["min_notional_usdt"] = min_notional
            cfg["max_notional_usdt"] = max_notional
            cfg["enabled"] = True
            
            # CRITICAL: Enforce HARD CAP on leverage - can NEVER exceed 15x
            leverage = int(cfg.get("max_leverage", 5))
            leverage = min(leverage, HARD_MAX_LEVERAGE)  # Never exceed hard cap
            cfg["max_leverage"] = leverage
            
            # Trailing
            t_default = TRAILING_DEFAULTS.get(sym, 1.0)
            t_val = float(cfg.get("trailing_callback_rate_pct", t_default))
            cfg["trailing_callback_rate_pct"] = min(max(t_val, 0.5), 3.0)
            
            # SL/TP ranges
            for key in ["sl_pct_min", "sl_pct_max", "tp1_pct_min", "tp1_pct_max", 
                        "tp2_pct_min", "tp2_pct_max"]:
                if key not in cfg:
                    continue
                val = float(cfg[key])
                if "min" in key:
                    cfg[key] = min(max(val, 0.5), 3.0)
                else:
                    cfg[key] = min(max(val, 1.0), 10.0)
            
            merged_symbols[sym] = cfg
        
        # Add any new symbols from LLM
        for sym, llm_cfg in llm_symbols.items():
            if sym not in merged_symbols:
                merged_symbols[sym] = dict(llm_cfg)
        
        new_config["symbols"] = merged_symbols
        
        # Update mode info
        new_config["mode"] = new_config.get("mode", {})
        new_config["mode"]["risk_profile"] = f"adaptive-{profile_name.lower()}"
        new_config["mode"]["reason"] = (
            f"ajuste automático LLM {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}"
        )
        
        return new_config
    
    def adjust(self) -> Optional[Dict]:
        """
        Main method: Analyze performance and adjust risk config.
        Returns the new config if successful, None otherwise.
        """
        if not self.should_adjust():
            logger.info("Risk adjustment skipped: rate limit")
            return None
        
        logger.info("🔄 Starting dynamic risk adjustment...")
        
        # Load current state
        risk_config = self._load_json(self.risk_path, {})
        state = self._load_json(self.state_path, {})
        
        if not risk_config:
            logger.warning("No risk config found, skipping adjustment")
            return None
        
        # Get equity and performance
        equity = self._get_equity()
        trades = self._get_trade_history()
        performance = self._calculate_performance_stats(trades)
        
        logger.info(f"  Equity: ${equity:.2f}, Trades: {performance['total_trades']}, "
                   f"Win rate: {performance['win_rate']*100:.1f}%")
        
        # Choose profile
        profile = self._choose_profile(equity)
        
        # Build prompt
        prompt = self._build_prompt(equity, profile, risk_config, state, performance)
        
        # Call LLM
        new_config = self._call_llm(prompt)
        
        if not new_config:
            logger.error("LLM adjustment failed")
            return None
        
        # Sanitize and validate
        new_config = self._sanitize_config(new_config, equity, profile)
        
        # Save
        if self._save_json(self.risk_path, new_config):
            logger.info("✅ Risk config updated successfully")
            
            # Update rate limiting
            self._last_adjustment_time = time.time()
            self._adjustment_count_today += 1
            
            return new_config
        
        return None
    
    def get_status(self) -> Dict:
        """Get status of the risk adjuster"""
        current_time = time.time()
        
        last_adjustment = "Never"
        if self._last_adjustment_time:
            hours_ago = (current_time - self._last_adjustment_time) / 3600
            last_adjustment = f"{hours_ago:.1f}h ago"
        
        return {
            "last_adjustment": last_adjustment,
            "adjustments_today": self._adjustment_count_today,
            "max_per_day": self.config.max_calls_per_day,
            "cooldown_hours": self.config.cooldown_hours,
            "can_adjust": self.should_adjust()
        }


# =======================
# STANDALONE EXECUTION
# =======================

def main():
    """Standalone execution for cron/script usage"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    config = AdjustmentConfig()
    adjuster = DynamicRiskAdjuster(config)
    
    result = adjuster.adjust()
    
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("No adjustment made")


if __name__ == "__main__":
    main()
