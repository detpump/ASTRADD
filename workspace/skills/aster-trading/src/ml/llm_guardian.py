#!/usr/bin/env python3
"""
LLM Guardian para Trading System V2
Supervisión limitada de decisiones ML usando LLM

CUMPLE con límite: Máximo 1 llamada cada 2-3 horas (60 prompts/5 horas)

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

logger = logging.getLogger(__name__)

# Intentar importar MiniMax
try:
    from openai import OpenAI
    MINIMAX_AVAILABLE = True
except ImportError:
    MINIMAX_AVAILABLE = False
    logger.warning("MiniMax SDK no disponible")


class LLMProvider(Enum):
    """Proveedor LLM disponible"""
    MINIMAX = "minimax"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    NONE = "none"


class ReviewDecision(Enum):
    """Decisión de revisión del LLM"""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    HOLD = "HOLD"
    SKIP = "SKIP"


@dataclass
class LLMSettings:
    """Configuración del LLM"""
    provider: str = "minimax"
    
    # MiniMax settings
    minimax_api_key: str = ""
    minimax_model: str = "MiniMax-M2.1"
    minimax_group_id: str = ""
    
    # Anthropic settings  
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-haiku-20240307"
    
    # Rate limiting - CRÍTICO para tu plan
    max_calls_per_hour: int = 1        # 1 cada hora
    max_calls_per_day: int = 20         # 20 al día
    cooldown_seconds: int = 3600        # 1 hora entre llamadas
    
    # Cuándo llamar - OPTIMIZADO para minimizar tokens
    # SOLO llamar en casos dudosos/inseguros (no en señales obvias)
    call_on_doubtful: bool = True        # Solo cuando ML duda (low confidence)
    call_on_regime_change: bool = True   # Cambio de régimen
    call_on_anomaly: bool = True         # Anomalía detectada
    call_on_schedule: bool = True        # Revisión periódica
    schedule_interval_hours: int = 6      # Cada 6 horas (menos frecuente)


@dataclass
class ReviewResult:
    """Resultado de revisión del LLM"""
    decision: ReviewDecision
    reason: str
    confidence: float = 0.5
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    tokens_used: int = 0
    model: str = ""


class LLMGuardian:
    """
    Guardián LLM con límites estrictos
    
    Cumple con tu plan MiniMax:
    - Máximo 1 llamada cada hora
    - Solo en momentos críticos
    - Monitoreo de uso
    """
    
    def __init__(self, settings: LLMSettings = None):
        self.settings = settings or LLMSettings()
        
        # Cliente LLM
        self.client = None
        self.provider = LLMProvider.NONE
        
        # Estado
        self.last_call_time = 0
        self.total_calls_today = 0
        self.total_calls = 0
        self.last_reset_date = datetime.now().date()
        
        # Configurar cliente
        self._setup_client()
        
        logger.info(f"LLM Guardian inicializado: {self.provider.value}")
    
    def _setup_client(self):
        """Configura el cliente LLM"""
        if not MINIMAX_AVAILABLE:
            logger.warning("No hay cliente LLM disponible")
            return
        
        # MiniMax
        if self.settings.provider == "minimax" and self.settings.minimax_api_key:
            try:
                self.client = OpenAI(
                    api_key=self.settings.minimax_api_key,
                    base_url="https://api.minimax.chat/v1"
                )
                self.provider = LLMProvider.MINIMAX
                logger.info("✅ Cliente MiniMax configurado")
            except Exception as e:
                logger.error(f"Error configurando MiniMax: {e}")
        
        # Anthropic (alternativo)
        elif self.settings.provider == "anthropic" and self.settings.anthropic_api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
                self.provider = LLMProvider.ANTHROPIC
                logger.info("✅ Cliente Anthropic configurado")
            except Exception as e:
                logger.error(f"Error configurando Anthropic: {e}")
    
    def _check_limits(self) -> bool:
        """
        Verifica si puede hacer otra llamada
        
        Returns:
            True si puede llamar, False si no
        """
        now = time.time()
        current_date = datetime.now().date()
        
        # Reset contador diario si es nuevo día
        if current_date > self.last_reset_date:
            self.total_calls_today = 0
            self.last_reset_date = current_date
        
        # Verificar límites
        if self.total_calls_today >= self.settings.max_calls_per_day:
            logger.warning(f"Límite diario alcanzado: {self.total_calls_today}/{self.settings.max_calls_per_day}")
            return False
        
        # Verificar cooldown
        time_since_last = now - self.last_call_time
        if time_since_last < self.settings.cooldown_seconds:
            logger.debug(f"Cooldown activo: {self.settings.cooldown_seconds - time_since_last:.0f}s restantes")
            return False
        
        return True
    
    def should_review(self, signal: Dict, market_features: Dict) -> bool:
        """
        Determina si debe revisar con LLM
        
        Args:
            signal: Señal del modelo ML
            market_features: Features del mercado
            
        Returns:
            True si debe llamar al LLM
        """
        if not self._check_limits():
            return False
        
        signal_strength = signal.get("signal_strength", 0)
        confidence = signal.get("confidence", 0)
        
        # Solo llamar en casos DUBDOSOS/INCERTOS - no en señales obvias
        # Esto minimiza tokens: LLM solo interviene cuando el ML no está seguro
        
        # 1. ML tiene baja confianza - LLM ayuda a decidir
        if self.settings.call_on_doubtful:
            # Señales dudosas: confidence bajo O strength medio
            if confidence < 0.6 or (signal_strength > 0.3 and signal_strength < 0.7):
                logger.info(f"🔍 Caso dudoso - ML duda: strength={signal_strength:.2f}, confidence={confidence:.2f}")
                return True
        
        # 2. Cambio de régimen
        if self.settings.call_on_regime_change:
            current_regime = market_features.get("regime_market_regime", 2)
            # Aquí podrías comparar con régimen anterior
            # Por ahora, revisar si hay cambio dramático
            momentum = abs(market_features.get("regime_momentum_score", 0))
            if momentum > 0.7:
                logger.info(f"Cambio de régimen detectado: momentum={momentum:.2f}")
                return True
        
        # 3. Anomalía
        if self.settings.call_on_anomaly:
            # Features que podrían indicar anomalía
            spread = market_features.get("micro_spread_bps", 0)
            if spread > 50:  # Spread muy alto
                logger.info(f"Anomalía detectada: spread={spread:.2f} bps")
                return True
        
        # 4. Revisión programada
        if self.settings.call_on_schedule:
            hours_since_last = (time.time() - self.last_call_time) / 3600
            if hours_since_last >= self.settings.schedule_interval_hours:
                logger.info("Revisión programada activada")
                return True
        
        return False
    
    async def review_signal(self, signal: Dict, market_features: Dict) -> ReviewResult:
        """
        Revisa una señal con el LLM
        
        Args:
            signal: Señal del modelo ML
            market_features: Features del mercado
            
        Returns:
            ReviewResult con la decisión
        """
        if not self.client:
            logger.warning("No hay cliente LLM, aprobando señal")
            return ReviewResult(
                decision=ReviewDecision.APPROVE,
                reason="No LLM available, auto-approve",
                model="none"
            )
        
        if not self.should_review(signal, market_features):
            return ReviewResult(
                decision=ReviewDecision.SKIP,
                reason="Limits not met or no critical situation",
                model="none"
            )
        
        # Construir prompt conciso
        prompt = self._build_prompt(signal, market_features)
        
        try:
            # Llamar al LLM
            if self.provider == LLMProvider.MINIMAX:
                result = await self._call_minimax(prompt)
            elif self.provider == LLMProvider.ANTHROPIC:
                result = await self._call_anthropic(prompt)
            else:
                result = ReviewResult(
                    decision=ReviewDecision.APPROVE,
                    reason="No provider configured",
                    model="none"
                )
            
            # Actualizar contadores
            self.last_call_time = time.time()
            self.total_calls_today += 1
            self.total_calls += 1
            
            return result
            
        except Exception as e:
            logger.error(f"Error en llamada LLM: {e}")
            return ReviewResult(
                decision=ReviewDecision.HOLD,
                reason=f"LLM error: {str(e)}",
                confidence=0.0,
                model="error"
            )
    
    def _build_prompt(self, signal: Dict, features: Dict) -> str:
        """Construye el prompt para el LLM"""
        
        # Extraer info relevante
        action = signal.get("action", "HOLD")
        strength = signal.get("signal_strength", 0)
        confidence = signal.get("confidence", 0)
        price = signal.get("price", 0)
        
        rsi = features.get("tech_rsi_14", 50)
        macd_hist = features.get("tech_macd_hist", 0)
        bb_pos = features.get("tech_bb_position", 0.5)
        trend = features.get("regime_trend_direction", 0)
        regime = features.get("regime_market_regime", 2)
        
        spread = features.get("micro_spread_bps", 0)
        imbalance = features.get("micro_order_imbalance", 0)
        
        prompt = f"""Eres un supervisor de trading quant. Revisa esta señal de trading.

## SEÑAL ML
- Acción: {action}
- Strength: {strength:.2f}
- Confidence: {confidence:.2f}
- Precio: ${price}

## INDICADORES TÉCNICOS
- RSI(14): {rsi:.1f}
- MACD Hist: {macd_hist:.5f}
- BB Position: {bb_pos:.2f}
- Trend: {trend} (-1=bear, 0=side, 1=bull)
- Regime: {regime} (0=bear, 2=neutral, 4=bull)

## MICROSTRUCTURE
- Spread: {spread:.2f} bps
- Order Imbalance: {imbalance:.3f}

## INSTRUCCIONES
Responde en EXACTAMENTE este formato:
APPROVE|REJECT|HOLD + razón breve

Ejemplos:
APPROVE - RSI sobrevendido, momentum positivo
REJECT - Spread muy alto, slippage risk
HOLD - Datos insuficientes

Tu decisión:"""
        
        return prompt
    
    async def _call_minimax(self, prompt: str) -> ReviewResult:
        """Llama a MiniMax"""
        try:
            response = self.client.chat.completions.create(
                model=self.settings.minimax_model,
                messages=[
                    {"role": "system", "content": "Eres un experto en trading cuantitativo."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=100,
                temperature=0.3
            )
            
            content = response.choices[0].message.content
            
            return self._parse_response(content, self.settings.minimax_model)
            
        except Exception as e:
            logger.error(f"Error en MiniMax: {e}")
            raise
    
    async def _call_anthropic(self, prompt: str) -> ReviewResult:
        """Llama a Anthropic"""
        try:
            response = self.client.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=100,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            content = response.content[0].text
            
            return self._parse_response(content, self.settings.anthropic_model)
            
        except Exception as e:
            logger.error(f"Error en Anthropic: {e}")
            raise
    
    def _parse_response(self, content: str, model: str) -> ReviewResult:
        """Parsea la respuesta del LLM"""
        content = content.strip().upper()
        
        if "APPROVE" in content:
            decision = ReviewDecision.APPROVE
            reason = content.replace("APPROVE", "").strip(" -:")
        elif "REJECT" in content:
            decision = ReviewDecision.REJECT
            reason = content.replace("REJECT", "").strip(" -:")
        elif "HOLD" in content:
            decision = ReviewDecision.HOLD
            reason = content.replace("HOLD", "").strip(" -:")
        else:
            decision = ReviewDecision.HOLD
            reason = content[:100]
        
        return ReviewResult(
            decision=decision,
            reason=reason,
            confidence=0.8,
            model=model
        )
    
    def get_usage_stats(self) -> Dict:
        """Obtiene estadísticas de uso"""
        now = time.time()
        
        return {
            "provider": self.provider.value,
            "total_calls": self.total_calls,
            "calls_today": self.total_calls_today,
            "max_per_day": self.settings.max_calls_per_day,
            "seconds_since_last": int(now - self.last_call_time),
            "can_call": self._check_limits(),
        }


# =======================
# EJEMPLO DE USO
# =======================

async def example():
    """Ejemplo de uso"""
    
    # Crear guardian
    guardian = LLMGuardian()
    
    # Simular señal
    signal = {
        "action": "BUY",
        "signal_strength": 0.9,
        "confidence": 0.85,
        "price": 0.5,
        "symbol": "ASTERUSDT"
    }
    
    features = {
        "tech_rsi_14": 28.5,
        "tech_macd_hist": 0.001,
        "tech_bb_position": 0.15,
        "regime_trend_direction": 1,
        "regime_momentum_score": 0.3,
        "regime_market_regime": 3,
        "micro_spread_bps": 15.0,
        "micro_order_imbalance": 0.2,
    }
    
    # Verificar si debe revisar
    should = guardian.should_review(signal, features)
    print(f"Debe revisar: {should}")
    
    if should:
        # Revisar
        result = await guardian.review_signal(signal, features)
        print(f"Decisión: {result.decision.name}")
        print(f"Razón: {result.reason}")
        print(f"Modelo: {result.model}")
    
    # Stats
    stats = guardian.get_usage_stats()
    print(f"\nStats: {stats}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example())
