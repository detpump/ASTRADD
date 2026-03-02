#!/usr/bin/env python3
"""
Risk Guard V2 para Trading System
Gestión de riesgo avanzada con controles dinámicos

Autor: Aster Trading V2
Fecha: 2026-02-24
"""

import os
import json
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta

from state.state_service import state_service
from state.models import RiskState

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Nivel de riesgo"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class RiskConfig:
    """Configuración de riesgo - Growth Optimized for Small Accounts"""
    # Position limits
    max_position_pct: float = 0.05      # 5% max per position (legacy default)
    max_total_exposure_pct: float = 0.20  # 20% total exposure
    max_leverage: int = 10               # Legacy default leverage cap
    
    # Dynamic risk adjustment settings
    min_position_pct: float = 0.02      # 2% minimum position
    enable_dynamic_adjustment: bool = True  # Enable dynamic adjustment
    
    # Loss limits - Adjusted for growth
    max_daily_loss_pct: float = 0.05   # 5% daily loss max
    max_weekly_loss_pct: float = 0.15   # 15% weekly loss max (legacy default)
    max_drawdown_pct: float = 0.20       # 20% drawdown max (legacy default)
    
    # Trading limits
    max_trades_per_day: int = 20        # Legacy default
    max_consecutive_losses: int = 5      # Legacy default
    min_trade_interval_minutes: int = 15
    
    # Risk controls
    enable_circuit_breaker: bool = True
    circuit_breaker_cooldown_minutes: int = 60
    
    # Dynamic sizing
    use_dynamic_sizing: bool = True
    reduce_on_drawdown: bool = True
    reduce_factor_per_5pct_dd: float = 0.5  # Reduce 50% every 5% drawdown


@dataclass
class PortfolioState:
    """Estado actual del portafolio"""
    equity: float = 0.0  # Will be fetched from API on init
    equity_peak: float = 0.0  # High water mark - set to equity on first run
    equity_start_day: float = 0.0
    equity_start_week: float = 0.0
    
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    
    positions: Dict[str, Dict] = field(default_factory=dict)
    open_positions_count: int = 0
    
    trades_today: int = 0
    consecutive_losses: int = 0
    
    drawdown_pct: float = 0.0
    last_trade_time: int = 0
    
    date: str = field(default_factory=lambda: datetime.now().date().isoformat())


@dataclass
class RiskCheckResult:
    """Resultado de verificación de riesgo"""
    approved: bool
    risk_level: RiskLevel
    reason: str
    position_size_multiplier: float = 1.0
    required_actions: List[str] = field(default_factory=list)


class RiskGuard:
    """
    Guardia de riesgo para el sistema de trading
    
    Controla:
    - Límites de posición
    - Pérdidas diarias/semanales
    - Drawdown
    - Circuit breakers
    - Tamaño dinámico de posiciones
    """
    
    # Deprecated default state file path (kept for compatibility; DB is source of truth)
    DEFAULT_STATE_FILE = None
    
    # Known possible base directories where the skill might be located
    SKILL_BASE_DIRS = [
        "/Users/FIRMAS/.openclaw/workspace/skills/aster-trading",
        "/Users/FIRMAS/.openclaw/workspace/skills/aster-trading",
        ".",
    ]
    
    def __init__(self, config: RiskConfig = None, state_file: str = None):
        self.config = config or RiskConfig()
        
        # Resolve state file path with fallbacks
        self.state_file = self._resolve_state_file_path(state_file) if state_file else None
        
        if self.state_file:
            logger.info(f"RiskGuard using legacy state file fallback: {self.state_file}")
        else:
            logger.info("RiskGuard running in DB-first mode (no file fallback)")
        
        # Cargar estado
        self.state = self._load_state()
        
        # Reset diario si es necesario
        self._check_daily_reset()
        
        logger.info("Risk Guard inicializado")
    
    def _resolve_state_file_path(self, state_file: str) -> str:
        """
        Resolve the state file path with multiple fallback options.
        
        The relative path ./logs/risk_state.json can resolve differently depending on
        the current working directory (CWD). This method tries multiple possible paths.
        
        Args:
            state_file: The requested state file path (may be relative)
            
        Returns:
            The resolved absolute path to the state file
        """
        # If it's already an absolute path and exists, use it
        if os.path.isabs(state_file) and os.path.exists(state_file):
            return state_file
        
        # If the relative path exists from current directory, use it
        if os.path.exists(state_file):
            return os.path.abspath(state_file)
        
        # Try different base directories
        for base_dir in self.SKILL_BASE_DIRS:
            # Construct absolute path from base dir
            if base_dir == ".":
                # Use CWD
                abs_path = os.path.abspath(state_file)
            else:
                # Use the base directory
                # Get just the relative part (e.g., "logs/risk_state.json")
                rel_path = state_file.lstrip("./")
                abs_path = os.path.join(base_dir, rel_path)
            
            if os.path.exists(abs_path):
                logger.info(f"Found state file at fallback path: {abs_path}")
                return abs_path
        
        # If no fallback found, return the original path (will fail with clear error)
        logger.warning(f"State file not found at any known location, using: {state_file}")
        return state_file
    
    def _load_state(self) -> PortfolioState:
        """Carga el estado del portafolio desde DB (RiskState)."""
        # Compatibility mode for tests/tools: if an explicit legacy state file was
        # provided and exists, prefer it over DB snapshot.
        if self.state_file and os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                data = json.load(f)
            rs = None
        else:
            rs = None
            try:
                rs = state_service.get_risk_state()
            except Exception as exc:
                logger.warning(f"DB risk_state read failed, will fallback to file: {exc}")

            if rs:
                limits = rs.risk_limits if isinstance(rs.risk_limits, dict) else {}
                data = {
                    "equity": rs.account_equity,
                    "equity_peak": limits.get("equity_peak"),
                    "equity_start_day": limits.get("equity_start_day"),
                    "equity_start_week": limits.get("equity_start_week"),
                    "daily_pnl": rs.daily_pnl,
                    "drawdown_pct": rs.drawdown_pct,
                    "positions": limits.get("positions", {}),
                    "open_positions_count": limits.get("open_positions_count", 0),
                    "trades_today": limits.get("trades_today", 0),
                    "consecutive_losses": limits.get("consecutive_losses", 0),
                    "last_trade_time": limits.get("last_trade_time", 0),
                    "date": limits.get("date"),
                }
            else:
                error_msg = "CRITICAL: No risk state found (DB). Trading halted until equity is initialized."
                logger.error(error_msg)
                raise ValueError(error_msg)

        state = PortfolioState()

        equity_value = data.get("equity", 0.0) or 0.0
        if equity_value <= 0:
            logger.warning("Equity missing/zero in risk state, will fetch from API on startup")
            equity_value = 0.0

        state.equity = equity_value
        state.equity_peak = data.get("equity_peak") or state.equity
        state.equity_start_day = data.get("equity_start_day") or state.equity
        state.equity_start_week = data.get("equity_start_week") or state.equity
        state.daily_pnl = data.get("daily_pnl", 0.0)
        state.weekly_pnl = data.get("weekly_pnl", 0.0)
        state.positions = data.get("positions", {})
        state.open_positions_count = data.get("open_positions_count", 0)
        state.trades_today = data.get("trades_today", 0)
        state.consecutive_losses = data.get("consecutive_losses", 0)
        state.drawdown_pct = data.get("drawdown_pct", 0.0)
        state.last_trade_time = data.get("last_trade_time", 0)
        state.date = data.get("date", datetime.now().date().isoformat())

        if state.drawdown_pct > 0.5:
            logger.warning(f"Detected high drawdown ({state.drawdown_pct*100:.1f}%), resetting for fresh start")
            state.equity_peak = state.equity
            state.drawdown_pct = 0.0

        return state
    
    def _save_state(self):
        """Guarda el estado del portafolio en DB (RiskState)."""
        try:
            risk_limits = {
                "equity_peak": self.state.equity_peak,
                "equity_start_day": self.state.equity_start_day,
                "equity_start_week": self.state.equity_start_week,
                "positions": self.state.positions,
                "open_positions_count": self.state.open_positions_count,
                "trades_today": self.state.trades_today,
                "consecutive_losses": self.state.consecutive_losses,
                "last_trade_time": self.state.last_trade_time,
                "date": self.state.date,
            }
            rs = RiskState(
                account_equity=self.state.equity,
                daily_pnl=self.state.daily_pnl,
                drawdown_pct=self.state.drawdown_pct,
                risk_limits=risk_limits,
            )
            state_service.upsert_risk_state(rs)
        except Exception as e:
            logger.error(f"Error guardando estado en DB: {e}")
    
    def _check_daily_reset(self):
        """Resetea contadores diarios si es un nuevo día"""
        today = datetime.now().date().isoformat()
        
        if self.state.date != today:
            logger.info(f"Nuevo día: {self.state.date} -> {today}")
            
            # Reset diario
            self.state.equity_start_day = self.state.equity
            self.state.daily_pnl = 0.0
            self.state.trades_today = 0
            self.state.date = today
            
            # Reset semanal si es lunes
            if datetime.now().weekday() == 0:
                self.state.equity_start_week = self.state.equity
                self.state.weekly_pnl = 0.0
            
            self._save_state()
    
    def check_trade(self, symbol: str, side: str, notional: float, 
                   entry_price: float, current_price: float, 
                   leverage: int = 1, min_notional: float = 5.0) -> RiskCheckResult:
        """
        Verifica si un trade pasa los controles de riesgo
        
        Args:
            symbol: Símbolo a tradear
            side: BUY o SELL
            notional: Valor del trade en USDT (antes de leverage)
            entry_price: Precio de entrada
            current_price: Precio actual
            leverage: Multiplicador de leverage (default: 1)
            min_notional: Minimum notional USDT allowed (default: 5.0)
            
        Returns:
            RiskCheckResult con la decisión
        """
        # CRITICAL FIX: Cap leverage at maximum allowed
        max_leverage = self.config.max_leverage
        effective_leverage = min(leverage, max_leverage)
        
        # Calculate leveraged notional with capped leverage
        leveraged_notional = notional * effective_leverage
        
        # CRITICAL FIX: Check BASE notional BEFORE leverage to prevent $0.04 trades
        # The base position must be large enough to survive leverage application
        # We need: base_notional >= min_notional / effective_leverage
        min_base_required = min_notional / effective_leverage if effective_leverage > 0 else min_notional
        
        if notional < min_base_required:
            # Base notional too small - even with leverage can't meet minimum
            # Check if we can adjust upward
            if self.state.equity * effective_leverage >= min_notional:
                # We can meet minimum by increasing base
                adjusted_base = min_base_required
                adjusted_notional = adjusted_base
                adjusted_leveraged = adjusted_notional * effective_leverage
                return RiskCheckResult(
                    approved=True,
                    risk_level=RiskLevel.NORMAL,
                    reason=f"Posición ajustada de base ${notional:.2f} a ${adjusted_base:.2f} para cumplir min_notional ${min_notional:.2f} con leverage {effective_leverage}x",
                    position_size_multiplier=adjusted_notional / notional if notional > 0 else 1.0,
                    required_actions=[f"Ajust base position to ${adjusted_base:.2f} to meet min_notional"]
                )
            else:
                # Cannot meet minimum even with max leverage
                max_possible = self.state.equity * effective_leverage
                return RiskCheckResult(
                    approved=False,
                    risk_level=RiskLevel.CRITICAL,
                    reason=f"Insuficiente equity para min_notional ${min_notional:.2f}: equity=${self.state.equity:.2f}, max_possible=${max_possible:.2f}",
                    position_size_multiplier=0.0
                )
        
        # Legacy check: Also verify leveraged notional meets minimum
        if leveraged_notional < min_notional:
            # Try to adjust: calculate minimum required notional to meet min_notional
            required_notional = min_notional / leverage if leverage > 0 else min_notional
            
            # Check if we can reach minimum with max leverage
            max_possible = self.state.equity * self.config.max_leverage
            if max_possible < min_notional:
                return RiskCheckResult(
                    approved=False,
                    risk_level=RiskLevel.CRITICAL,
                    reason=f"Insuficiente equity para min_notional ${min_notional:.2f}: equity=${self.state.equity:.2f}, max_con_leverage=${max_possible:.2f}",
                    position_size_multiplier=0.0
                )
            
            # Adjust position to meet minimum
            return RiskCheckResult(
                approved=True,
                risk_level=RiskLevel.NORMAL,
                reason=f"Posición ajustada de ${leveraged_notional:.2f} a ${min_notional:.2f} (leverage {leverage}x) para cumplir min_notional",
                position_size_multiplier=min_notional / leveraged_notional if leveraged_notional > 0 else 1.0,
                required_actions=[f"Ajust position to min_notional ${min_notional:.2f}"]
            )
        
        # 0. Verificar circuit breaker primero
        if self.circuit_breaker_active():
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                reason="Circuit breaker activo -Trading detenido",
                position_size_multiplier=0.0
            )
        
        # 1. Verificar pérdida diaria
        if self.state.daily_pnl < -self.state.equity * self.config.max_daily_loss_pct:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                reason=f"Pérdida diaria máxima alcanzada: {self.state.daily_pnl:.2f}",
                position_size_multiplier=0.0
            )
        
        # 1.5. Verificar pérdida semanal (P0 CRITICAL - was configured but NEVER enforced)
        if self.state.weekly_pnl < -self.state.equity * self.config.max_weekly_loss_pct:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                reason=f"Pérdida semanal máxima alcanzada: {self.state.weekly_pnl:.2f}",
                position_size_multiplier=0.0
            )
        
        # 2. Verificar drawdown
        if self.state.drawdown_pct > self.config.max_drawdown_pct:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                reason=f"Drawdown máximo alcanzado: {self.state.drawdown_pct*100:.1f}%",
                position_size_multiplier=0.0
            )
        
        # 3. Verificar límite de trades diarios
        if self.state.trades_today >= self.config.max_trades_per_day:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.HIGH,
                reason=f"Límite de trades diarios: {self.state.trades_today}",
                position_size_multiplier=0.0
            )
        
        # 4. Verificar intervalo mínimo entre trades - SIEMPRE aplicar después de un trade
        # El cooldown previene revenge trading y sobre-operación, sin importar si hay posiciones abiertas
        # FIX: Agregar expiración de sesión de 24 horas para evitar cooldowns staleness
        # MEJORA: Usar el exchange como fuente de verdad para el cooldown
        cooldown_time = self._get_cooldown_time(source="auto")
        
        if cooldown_time > 0:
            if cooldown_time > 1440:  # 24 horas en minutos - expirado
                logger.warning(f"Cooldown expired (session reset): {cooldown_time:.1f} min > 1440 min. Resetting last_trade_time.")
                self.state.last_trade_time = 0
                self._save_state()
            elif cooldown_time < self.config.min_trade_interval_minutes:
                return RiskCheckResult(
                    approved=False,
                    risk_level=RiskLevel.NORMAL,
                    reason=f"Cooldown activo: {cooldown_time:.1f}/{self.config.min_trade_interval_minutes} min",
                    position_size_multiplier=0.0
                )
        
        # 5. Verificar tamaño de posición - usar leveraged notional
        # P0 FIX: Add equity validation guard to prevent division by zero
        if self.state.equity <= 0:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                reason=f"Equity inválido o cero: {self.state.equity:.2f}",
                position_size_multiplier=0.0
            )
        
        # DYNAMIC RISK ADJUSTMENT: Calculate max_position_pct based on min_notional
        # This ensures the position can meet the minimum notional requirement
        effective_max_position_pct = self.config.max_position_pct
        
        if self.config.enable_dynamic_adjustment and min_notional > 0:
            # Calculate the minimum position percentage needed to meet min_notional
            # min_position_pct * equity * leverage >= min_notional
            # Therefore: min_position_pct >= min_notional / (equity * leverage)
            min_required_pct = min_notional / (self.state.equity * leverage) if leverage > 0 else 0
            
            # Ensure we use at least min_position_pct to meet min_notional
            effective_min_pct = max(self.config.min_position_pct, min_required_pct)
            
            # Cap effective max at the higher of base max or min required
            effective_max_position_pct = max(self.config.max_position_pct, effective_min_pct)
            
            logger.info(f"  📊 Dynamic risk adjustment: equity=${self.state.equity:.2f}, min_notional=${min_notional:.2f}, "
                       f"leverage={leverage}x, min_required_pct={min_required_pct*100:.2f}%, "
                       f"effective_max={effective_max_position_pct*100:.2f}%")
        
        position_pct = leveraged_notional / self.state.equity
        if position_pct > effective_max_position_pct:
            # Reducir al máximo permitido
            max_leveraged_notional = self.state.equity * self.config.max_position_pct
            # Calculate the multiplier for the base notional (not leveraged)
            multiplier = max_leveraged_notional / leveraged_notional if leveraged_notional > 0 else 1.0
            
            return RiskCheckResult(
                approved=True,
                risk_level=RiskLevel.NORMAL,
                reason=f"Posición reducida de ${leveraged_notional:.0f} a ${max_leveraged_notional:.0f} (leveraged)",
                position_size_multiplier=multiplier,
                required_actions=[f"Reduce position to ${max_leveraged_notional:.0f} USDT leveraged"]
            )
        
        # 6. Verificar exposición total - usar leveraged notional de posiciones existentes
        # IMPORTANTE: Usar el valor con leverage para el cálculo de exposición total
        total_exposure = self._calculate_total_exposure()
        
        new_exposure = total_exposure + leveraged_notional
        exposure_pct = new_exposure / self.state.equity if self.state.equity > 0 else 0
        
        if exposure_pct > self.config.max_total_exposure_pct:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.HIGH,
                reason=f"Exposición total máxima: {exposure_pct*100:.1f}%",
                position_size_multiplier=0.0
            )
        
        # 7. Verificar pérdidas consecutivas
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            return RiskCheckResult(
                approved=False,
                risk_level=RiskLevel.HIGH,
                reason=f"Pérdidas consecutivas máximas: {self.state.consecutive_losses}",
                position_size_multiplier=0.0
            )
        
        # 8. Calcular sizing dinámico basado en drawdown
        multiplier = self._calculate_position_multiplier()
        
        # Aprobar trade
        return RiskCheckResult(
            approved=True,
            risk_level=RiskLevel.LOW if multiplier >= 1.0 else RiskLevel.NORMAL,
            reason="Trade aprobado",
            position_size_multiplier=multiplier
        )
    
    def _calculate_position_multiplier(self) -> float:
        """Calcula el multiplicador de posición basado en drawdown"""
        if not self.config.use_dynamic_sizing:
            return 1.0
        
        dd = self.state.drawdown_pct
        
        if dd < 0.05:
            return 1.0
        elif dd < 0.10:
            return 0.75
        elif dd < 0.15:
            return 0.50
        else:
            return 0.25
    
    def _calculate_total_exposure(self) -> float:
        """
        Calcula la exposición total con leverage del portafolio.
        Maneja correctamente scale-ins y cierres parciales.
        Usa leverage para calcular la exposición total real.
        """
        total = 0.0
        for symbol, pos in self.state.positions.items():
            # Usar el notional actual de la posición (no el original)
            # FIX: Handle both string and numeric types for notional (API returns string)
            notional_raw = pos.get("notional", 0)
            try:
                notional = abs(float(notional_raw))
            except (ValueError, TypeError):
                # If conversion fails, skip this position
                logger.warning(f"Could not convert notional to float: {notional_raw} for {symbol}")
                continue
            # Obtener leverage de la posición, por defecto 1
            leverage_raw = pos.get("leverage", 1)
            try:
                leverage = float(leverage_raw) if leverage_raw else 1.0
            except (ValueError, TypeError):
                leverage = 1.0
            # Calcular exposición con leverage
            leveraged_notional = notional * leverage
            total += leveraged_notional
        return total
    
    def on_trade_executed(self, symbol: str, side: str, notional: float, 
                         entry_price: float, pnl: float = 0.0,
                         is_scale_in: bool = False,
                         is_partial_close: bool = False,
                         close_quantity: float = 0.0):
        """
        Actualiza el estado después de ejecutar un trade.
        
        Args:
            symbol: Símbolo
            side: BUY o SELL
            notional: Valor del trade (USDT)
            entry_price: Precio de entrada
            pnl: P&L del trade (si es cierre)
            is_scale_in: Si es una entrada adicional (scale-in)
            is_partial_close: Si es un cierre parcial (TP1/TP2 hit)
            close_quantity: Cantidad cerrada (para cierres parciales)
        """
        now = int(time.time() * 1000)
        
        # Actualizar P&L
        self.state.daily_pnl += pnl
        self.state.weekly_pnl += pnl
        
        # Actualizar equity
        self.state.equity += pnl
        
        # Actualizar peak
        if self.state.equity > self.state.equity_peak:
            self.state.equity_peak = self.state.equity
        
        # Calcular drawdown with zero-check
        if self.state.equity_peak > 0:
            self.state.drawdown_pct = (self.state.equity_peak - self.state.equity) / self.state.equity_peak
        else:
            self.state.drawdown_pct = 0.0  # Prevent division by zero
        
        # Actualizar posiciones
        if side == "BUY":
            if is_scale_in and symbol in self.state.positions:
                # Scale-in: actualizar posición existente con weighted average
                existing = self.state.positions[symbol]
                old_notional = existing.get("notional", 0)
                old_entry = existing.get("entry_price", entry_price)
                
                # Calculate weighted average
                total_notional = old_notional + notional
                if total_notional > 0:
                    new_entry = (old_notional * old_entry + notional * entry_price) / total_notional
                else:
                    new_entry = entry_price
                
                self.state.positions[symbol] = {
                    "side": "LONG",
                    "notional": total_notional,
                    "entry_price": new_entry,
                    "open_time": existing.get("open_time", now),
                    "last_update": now,
                    "is_scale_in": True,
                    "scale_in_count": existing.get("scale_in_count", 0) + 1
                }
            else:
                # Nueva posición o primera entrada
                self.state.positions[symbol] = {
                    "side": "LONG",
                    "notional": notional,
                    "entry_price": entry_price,
                    "open_time": now,
                    "last_update": now,
                    "is_scale_in": False,
                    "scale_in_count": 0
                }
                self.state.open_positions_count += 1
                
        elif side == "SELL" and symbol in self.state.positions:
            if is_partial_close and close_quantity > 0:
                # Cierre parcial: actualizar tamaño de posición
                existing = self.state.positions[symbol]
                old_notional = existing.get("notional", notional)
                
                # Reducir notional
                new_notional = old_notional - notional
                
                if new_notional <= 0:
                    # Posición completamente cerrada
                    del self.state.positions[symbol]
                    self.state.open_positions_count = max(0, self.state.open_positions_count - 1)
                else:
                    # Actualizar con cantidad reducida
                    self.state.positions[symbol] = {
                        "side": "LONG",
                        "notional": new_notional,
                        "entry_price": existing.get("entry_price", entry_price),
                        "open_time": existing.get("open_time", now),
                        "last_update": now,
                        "is_scale_in": existing.get("is_scale_in", False),
                        "scale_in_count": existing.get("scale_in_count", 0),
                        "partial_close": True,
                        "original_notional": old_notional
                    }
                
                # Actualizar consecutive losses
                if pnl < 0:
                    self.state.consecutive_losses += 1
                else:
                    self.state.consecutive_losses = 0
            else:
                # Cierre completo de posición
                del self.state.positions[symbol]
                self.state.open_positions_count = max(0, self.state.open_positions_count - 1)
                
                # Actualizar consecutive losses
                if pnl < 0:
                    self.state.consecutive_losses += 1
                else:
                    self.state.consecutive_losses = 0
        
        # Actualizar contadores
        self.state.trades_today += 1
        self.state.last_trade_time = now
        
        # Guardar estado
        self._save_state()
        
        logger.info(f"Estado actualizado: equity={self.state.equity:.2f}, dd={self.state.drawdown_pct*100:.1f}%, posiciones={len(self.state.positions)}")
    
    def update_equity(self, new_equity: float):
        """Actualiza el equity del portafolio"""
        # Validate equity input
        if new_equity is None or new_equity != new_equity:  # Check for None or NaN
            logger.warning("Invalid equity value, skipping update")
            return
        
        old_equity = self.state.equity
        self.state.equity = float(new_equity)
        
        # Actualizar peak si es mayor
        if self.state.equity > self.state.equity_peak:
            self.state.equity_peak = self.state.equity
        
        # Recalcular drawdown with zero-check
        if self.state.equity_peak > 0:
            self.state.drawdown_pct = (self.state.equity_peak - self.state.equity) / self.state.equity_peak
        else:
            self.state.drawdown_pct = 0.0
        
        # Actualizar P&L with zero-check for equity_start_day
        if self.state.equity_start_day > 0:
            self.state.daily_pnl = self.state.equity - self.state.equity_start_day
        else:
            self.state.daily_pnl = 0.0
        
        self._save_state()
    
    def get_status(self) -> Dict:
        """Obtiene el estado actual del risk guard"""
        total_exposure = self._calculate_total_exposure()
        exposure_pct = total_exposure / self.state.equity if self.state.equity > 0 else 0
        
        return {
            "equity": self.state.equity,
            "equity_peak": self.state.equity_peak,
            "daily_pnl": self.state.daily_pnl,
            "daily_pnl_pct": (self.state.daily_pnl / self.state.equity_start_day) if self.state.equity_start_day > 0 else 0,
            "drawdown_pct": self.state.drawdown_pct,
            "exposure": total_exposure,
            "exposure_pct": exposure_pct,
            "open_positions": self.state.open_positions_count,
            "positions": dict(self.state.positions),
            "trades_today": self.state.trades_today,
            "consecutive_losses": self.state.consecutive_losses,
            "position_multiplier": self._calculate_position_multiplier(),
            "risk_level": self._get_current_risk_level().name,
        }
    
    def _get_current_risk_level(self) -> RiskLevel:
        """Determina el nivel de riesgo actual"""
        if self.state.drawdown_pct > 0.15:
            return RiskLevel.CRITICAL
        elif self.state.drawdown_pct > 0.10:
            return RiskLevel.HIGH
        elif self.state.drawdown_pct > 0.05 or self.state.daily_pnl < 0:
            return RiskLevel.NORMAL
        return RiskLevel.LOW
    
    def sync_with_exchange(self, exchange_positions: Dict[str, Dict]) -> bool:
        """
        Sincroniza el estado interno con las posiciones reales del exchange.
        
        Si el exchange no tiene posiciones pero el estado interno tiene stale data,
        resetea last_trade_time para evitar cooldown incorrecto.
        
        Args:
            exchange_positions: Diccionario de posiciones del exchange {symbol: {positionAmt, entryPrice, ...}}
            
        Returns:
            True si se sincronizó correctamente
        """
        # Filtrar solo posiciones abiertas (positionAmt != 0)
        actual_open_positions = {
            symbol: data for symbol, data in exchange_positions.items()
            if float(data.get("positionAmt", 0)) != 0
        }
        
        # Si el exchange no tiene posiciones pero el estado interno cree que sí,
        # reseteamos las posiciones (mantenemos last_trade_time para preservar cooldown)
        if len(actual_open_positions) == 0 and len(self.state.positions) > 0:
            logger.warning(
                f"Stale positions detected in risk state: {list(self.state.positions.keys())}. "
                "Exchange has no open positions. Clearing stale positions (cooldown preserved)."
            )
            self.state.positions = {}
            self.state.open_positions_count = 0
            # NOT resetting last_trade_time - cooldown should persist to protect against re-entry
            self._save_state()
            return True
        
        # Si hay posiciones en el exchange, actualizar el estado interno
        if len(actual_open_positions) > 0:
            self.state.positions = actual_open_positions
            self.state.open_positions_count = len(actual_open_positions)
            self._save_state()
            
        return True
    
    def _get_cooldown_time(self, source: str = "auto") -> float:
        """
        Obtiene el tiempo de cooldown en minutos.
        
        Args:
            source: Fuente de datos
                - "local": Usar solo estado local
                - "exchange": Usar exchange como fuente de verdad
                - "auto": Usar estado local, con fallback a exchange
            
        Returns:
            Minutos desde el último trade (0 = sin cooldown activo)
        """
        now = int(time.time() * 1000)
        
        # Si no hay trade anterior, no hay cooldown
        if self.state.last_trade_time <= 0:
            return 0.0
        
        # Usar solo estado local
        if source == "local":
            return (now - self.state.last_trade_time) / 60000
        
        # Intentar obtener del exchange como fuente de verdad
        if source in ("exchange", "auto"):
            try:
                # Import dinámico para evitar dependencia circular
                from api.aster_api import get_last_trade_time_from_exchange
                exchange_time = get_last_trade_time_from_exchange()
                
                if exchange_time > 0:
                    # Usar el tiempo del exchange como fuente de verdad
                    exchange_cooldown = (now - exchange_time) / 60000
                    
                    if source == "exchange":
                        return exchange_cooldown
                    
                    # Auto: verificar si hay discrepancia significativa (>5 min)
                    local_cooldown = (now - self.state.last_trade_time) / 60000
                    diff = abs(local_cooldown - exchange_cooldown)
                    
                    if diff > 5:
                        logger.warning(
                            f"Cooldown discrepancy detected: local={local_cooldown:.1f}min, "
                            f"exchange={exchange_cooldown:.1f}min. Using exchange as source."
                        )
                    
                    # Usar el mayor de los dos (más seguro)
                    return max(local_cooldown, exchange_cooldown)
            except Exception as e:
                logger.warning(f"Could not fetch exchange cooldown: {e}")
        
        # Fallback a estado local
        return (now - self.state.last_trade_time) / 60000
    
    # EMERGENCY: Hard circuit breaker threshold for extreme drawdown (80%)
    # This is a last-resort protection that cannot be bypassed
    EMERGENCY_DRAWDOWN_THRESHOLD = 0.80
    
    # LIQUIDATION GUARD: Margin ratio threshold (70% = warning, 80% = liquidation)
    LIQUIDATION_GUARD_PCT = 0.70  # Close positions when margin ratio reaches 70%
    
    def _is_emergency_stop_required(self) -> bool:
        """Check if emergency stop is required due to extreme drawdown"""
        if self.state.drawdown_pct >= self.EMERGENCY_DRAWDOWN_THRESHOLD:
            logger.critical(
                f"EMERGENCY STOP TRIGGERED: Drawdown {self.state.drawdown_pct*100:.1f}% "
                f"exceeds emergency threshold {self.EMERGENCY_DRAWDOWN_THRESHOLD*100:.1f}%! "
                f"Trading halted until equity is restored above {(self.EMERGENCY_DRAWDOWN_THRESHOLD - 0.05)*100:.0f}% drawdown."
            )
            return True
        return False
    
    def _get_margin_ratio(self, symbol: str = None) -> float:
        """
        Get the current margin ratio for a position or entire portfolio.
        Margin Ratio = Total Margin Used / Total Wallet Balance
        
        Returns:
            Margin ratio as decimal (e.g., 0.5 = 50%)
        """
        try:
            from api.aster_api import get_positions_v3, public_get
            
            # Get wallet balance
            balance_resp = public_get("/fapi/v3/balance")
            if isinstance(balance_resp, list) and balance_resp:
                wallet_balance = float(balance_resp[0].get("availableBalance", 0))
            else:
                wallet_balance = float(balance_resp.get("availableBalance", 0))
            
            if wallet_balance <= 0:
                return 0.0
            
            # Get positions and calculate total margin used
            positions = get_positions_v3()
            total_margin_used = 0.0
            
            for pos in positions:
                pos_amt = float(pos.get("positionAmt", 0))
                if pos_amt == 0:
                    continue
                
                # Skip if symbol filter is set and doesn't match
                if symbol and pos.get("symbol") != symbol:
                    continue
                
                # Get isolated margin for this position
                isolated_margin = float(pos.get("isolatedMargin", 0))
                total_margin_used += isolated_margin
            
            margin_ratio = total_margin_used / wallet_balance if wallet_balance > 0 else 0.0
            return margin_ratio
            
        except Exception as e:
            logger.warning(f"Could not calculate margin ratio: {e}")
            return 0.0
    
    def check_liquidation_risk(self, symbol: str = None) -> Dict:
        """
        Check if any position is at risk of liquidation.
        
        Args:
            symbol: Optional symbol to check. If None, checks all positions.
            
        Returns:
            Dict with 'at_risk' (bool), 'margin_ratio' (float), 'action' (str)
        """
        margin_ratio = self._get_margin_ratio(symbol)
        
        if margin_ratio >= self.LIQUIDATION_GUARD_PCT:
            logger.warning(
                f"⚠️ LIQUIDATION GUARD: Margin ratio {margin_ratio*100:.1f}% "
                f"exceeds guard threshold {self.LIQUIDATION_GUARD_PCT*100:.1f}%!"
            )
            return {
                "at_risk": True,
                "margin_ratio": margin_ratio,
                "action": "CLOSE_POSITIONS",
                "message": f"Margin ratio {margin_ratio*100:.1f}% >= {self.LIQUIDATION_GUARD_PCT*100:.1f}%"
            }
        
        return {
            "at_risk": False,
            "margin_ratio": margin_ratio,
            "action": "NONE",
            "message": f"Margin ratio {margin_ratio*100:.1f}% is safe"
        }
    
    def circuit_breaker_active(self) -> bool:
        """
        Verifica si el circuit breaker está activo.
        
        P0 FIX: Now includes emergency drawdown check (>80%) as last-resort protection.
        This check is performed FIRST before any other circuit breaker logic.
        """
        # FIRST: Check emergency drawdown threshold (cannot be bypassed)
        if self._is_emergency_stop_required():
            logger.critical("EMERGENCY: Circuit breaker active due to extreme drawdown!")
            return True
        
        # THEN: Check risk-threshold circuit breaker
        if self.state.equity > 0 and self.state.daily_pnl < -self.state.equity * self.config.max_daily_loss_pct:
            logger.warning(
                f"Circuit breaker: daily loss threshold breached ({self.state.daily_pnl:.2f})"
            )
            return True

        if self.state.drawdown_pct > self.config.max_drawdown_pct:
            logger.warning(
                f"Circuit breaker: drawdown threshold breached ({self.state.drawdown_pct*100:.1f}%)"
            )
            return True

        # FINALLY: Check cooldown-based circuit breaker
        if self.config.enable_circuit_breaker:
            if self.state.last_trade_time > 0:
                cooldown_minutes = (time.time() * 1000 - self.state.last_trade_time) / 60000
                if cooldown_minutes < self.config.circuit_breaker_cooldown_minutes:
                    logger.info(f"Circuit breaker: Cooldown active: {cooldown_minutes:.1f}/{self.config.circuit_breaker_cooldown_minutes} min")
                    return True
        
        return False


# =======================
# EJEMPLO DE USO
# =======================

def example():
    """Ejemplo de uso"""
    
    # Crear risk guard
    guard = RiskGuard()
    
    # Simular verificación de trade
    result = guard.check_trade(
        symbol="ASTERUSDT",
        side="BUY",
        notional=500,  # 500 USDT
        entry_price=0.5,
        current_price=0.5
    )
    
    print(f"Trade check para ASTERUSDT:")
    print(f"  Aprobado: {result.approved}")
    print(f"  Nivel riesgo: {result.risk_level.name}")
    print(f"  Razón: {result.reason}")
    print(f"  Multiplier: {result.position_size_multiplier}")
    
    # Simular trade ejecutado
    if result.approved:
        actual_notional = 500 * result.position_size_multiplier
        guard.on_trade_executed("ASTERUSDT", "BUY", actual_notional, 0.5)
    
    # Estado
    status = guard.get_status()
    print(f"\nEstado del portafolio:")
    print(f"  Equity: ${status['equity']:.2f}")
    print(f"  Daily P&L: ${status['daily_pnl']:.2f} ({status['daily_pnl_pct']*100:.2f}%)")
    print(f"  Drawdown: {status['drawdown_pct']*100:.2f}%")
    print(f"  Posiciones abiertas: {status['open_positions']}")
    print(f"  Nivel riesgo: {status['risk_level']}")


if __name__ == "__main__":
    example()
