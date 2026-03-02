# Análisis Realista: Sistema de Trading LLM con Aster API

## Contexto: Restricciones Reales

### Tu Plan MiniMax
- **Límite:** 300 prompts / 5 horas
- **Throughput:** ~50-100 TPS (tokens por segundo)
- **Implicación:** Máximo ~1 llamada LLM por hora para estar seguro

### API Aster - Capacidades Reales

| Categoría | Endpoint | Estado | Latencia |
|-----------|----------|--------|----------|
| **Market Data** | | | |
| Order Book | `/fapi/v3/depth` | ✅ Disponible | ~100ms |
| Trades | `/fapi/v3/trades` | ✅ Disponible | ~100ms |
| Agg Trades | `/fapi/v3/aggTrades` | ✅ Disponible | ~100ms |
| Klines | `/fapi/v3/klines` | ✅ Disponible | ~100ms |
| 24h Ticker | `/fapi/v3/ticker/24hr` | ✅ Disponible | ~100ms |
| Mark Price | `/fapi/v3/markPrice` | ✅ Disponible | ~100ms |
| Funding History | `/fapi/v3/fundingRate` | ✅ Disponible | ~100ms |
| **WebSocket** | | | |
| Stream Base | `wss://fstream.asterdex.com` | ✅ Disponible | Tiempo real |
| Depth Stream | `<symbol>@depth20@100ms` | ✅ Disponible | 100ms |
| Trade Stream | `<symbol>@trade` | ✅ Disponible | Tiempo real |
| Ticker Stream | `<symbol>@ticker` | ✅ Disponible | ~1s |
| **Account** | | | |
| Balance | `/fapi/v3/balance` | ✅ Disponible | ~200ms |
| Positions | `/fapi/v3/positionRisk` | ✅ Disponible | ~200ms |
| Orders | `/fapi/v3/order` | ✅ Disponible | ~300ms |

### Rate Limits Aster
- **REQUEST_WEIGHT:** 2400/minuto
- **ORDERS:** 1200/minuto
- **WebSocket:** 10 mensajes/segundo máximo

---

## Arquitectura Realista v2.0

### Enfoque: Minimal LLM, Maximo Datos

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     ARQUITECTURA REALISTA                                │
│                 (1 llamada LLM/hora máximo)                              │
└─────────────────────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────────────┐
    │                     DATA LAYER (24/7)                           │
    ├──────────────────────────────────────────────────────────────────┤
    │  WebSocket ──► Cache Redis ──► Feature Engine ──► Models      │
    │                                                                  │
    │  • Order Book (100ms update)                                    │
    │  • Trade Flow (real-time)                                       │
    │  • Klines (1m, 5m, 15m, 1h)                                     │
    │  • Funding Rates                                                │
    └──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │               ML MODELS (NO LLM, 24/7)                          │
    ├──────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  1. Signal Generator (XGBoost/LightGBM)                         │
    │     • Features: 200+ technical indicators                       │
    │     • Training: Daily retrain                                   │
    │     • Inference: <10ms                                          │
    │                                                                  │
    │  2. Risk Classifier (XGBoost)                                   │
    │     • Features: Market regime, volatility                       │
    │     • Output: Risk score 0-1                                    │
    │                                                                  │
    │  3. Position Sizer (Rule-based)                                 │
    │     • Kelly Criterion adaptativo                                │
    │     • Max position: 5% equity                                   │
    │                                                                  │
    └──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │               LLM POLICY (1x/hora)                              │
    ├──────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  ÚNICAMENTE para:                                               │
    │  • Revisión de decisiones del ML                                │
    │  • Explicación de trades                                        │
    │  • Ajuste fino de parámetros (1 vez/día)                        │
    │  • Análisis de anomalías                                        │
    │                                                                  │
    │  Prompt: Contexto + Señales ML + Decisión                      │
    │                                                                  │
    └──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │               EXECUTION LAYER                                    │
    ├──────────────────────────────────────────────────────────────────┤
    │  • Risk Guard (pre-trade)                                       │
    │  • Order Executor                                               │
    │  • Bracket Manager (SL/TP)                                      │
    └──────────────────────────────────────────────────────────────────┘
```

---

## Implementación por Fases

### Fase 1: Infrastructure (Semanas 1-2)

**Objetivo:** WebSocket + Feature Store + ML básico

| Task | Tiempo Est. | LLM Calls |
|------|-------------|-----------|
| WebSocket manager Aster | 4 horas | 0 |
| Order book processor | 3 horas | 0 |
| Trade flow processor | 2 horas | 0 |
| Redis cache setup | 2 horas | 0 |
| Feature store basic | 3 horas | 0 |
| **TOTAL** | **14 horas** | **0** |

**Código inicial:**

```python
# websocket_manager.py
import asyncio
import websockets
import json
from typing import Dict, List, Callable

class AsterWebSocketManager:
    def __init__(self):
        self.ws = None
        self.subscriptions = set()
        self.callbacks = {}
        
    async def connect(self):
        """Conectar a WebSocket de Aster"""
        self.ws = await websockets.connect(
            "wss://fstream.asterdex.com/ws",
            ping_interval=30
        )
        
    async def subscribe(self, streams: List[str], callback: Callable):
        """Suscribirse a streams"""
        for stream in streams:
            self.subscriptions.add(stream)
        self.callbacks.update({s: callback for s in streams})
        
        await self.ws.send(json.dumps({
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1
        }))
        
    async def listen(self):
        """Escuchar mensajes"""
        async for message in self.ws:
            data = json.loads(message)
            stream = data.get('stream')
            if stream in self.callbacks:
                self.callbacks[stream](data)
```

### Fase 2: ML Signals (Semanas 3-4)

**Objetivo:** Modelos ML entrenando y prediciendo

| Task | Tiempo Est. | LLM Calls |
|------|-------------|-----------|
| Feature engineering (200+ features) | 6 horas | 0 |
| XGBoost signal model | 8 horas | 2 |
| Training pipeline | 4 horas | 0 |
| Backtesting framework | 4 horas | 1 |
| Model evaluation | 2 horas | 1 |
| **TOTAL** | **24 horas** | **4** |

**Feature engineering:**

```python
# features.py
FEATURES = {
    # Price-based (50 features)
    "returns_1m": "float",
    "returns_5m": "float", 
    "returns_15m": "float",
    "returns_1h": "float",
    "returns_1d": "float",
    "volatility_1h": "float",
    "volatility_1d": "float",
    
    # Technical (100 features)
    "rsi_14": "float",
    "rsi_28": "float",
    "macd": "float",
    "macd_signal": "float",
    "macd_hist": "float",
    "bb_upper": "float",
    "bb_middle": "float",
    "bb_lower": "float",
    "bb_position": "float",
    "atr_14": "float",
    "atr_28": "float",
    "adx_14": "float",
    "stoch_k": "float",
    "stoch_d": "float",
    "cci": "float",
    "williams_r": "float",
    # ... 80+ more
    
    # Microstructure (30 features)
    "spread_bps": "float",
    "order_imbalance": "float",
    "buy_sell_ratio_1m": "float",
    "buy_sell_ratio_5m": "float",
    "volume_ratio": "float",
    "vwap": "float",
    "twap": "float",
    # ... 20+ more
    
    # Regime (20 features)
    "trend_强度": "float",
    "volatility_regime": "int",
    "market_regime": "int",
}
```

### Fase 3: LLM Integration (Semanas 5-6)

**Objetivo:** Integrar LLM con llamadas controladas

| Task | Tiempo Est. | LLM Calls |
|------|-------------|-----------|
| LLM decision prompt | 3 horas | 2 |
| Context builder | 2 horas | 1 |
| Output parser | 2 horas | 1 |
| Decision validation | 2 horas | 1 |
| Human-in-the-loop setup | 2 horas | 1 |
| **TOTAL** | **11 horas** | **6** |

**Llmited LLM Usage:**

```python
# llm_policy.py
class LimitedLLMPolicy:
    """
    Política con máximo 1 llamada por hora
    """
    def __init__(self):
        self.last_call = None
        self.min_interval = 3600  # 1 hora
        self.daily_budget = 24    # 24 llamadas/día
        self.today_calls = 0
        
    def should_call_llm(self, market_state: dict, ml_signals: dict) -> bool:
        """Decide si debe llamar al LLM"""
        now = time.time()
        
        # Verificar límite diario
        if self.today_calls >= self.daily_budget:
            return False
            
        # Verificar intervalo mínimo
        if self.last_call and (now - self.last_call) < self.min_interval:
            return False
            
        # Solo llamar en momentos críticos
        if self._is_critical_moment(ml_signals):
            return True
            
        return False
        
    def _is_critical_moment(self, signals: dict) -> bool:
        """Detectar momentos críticos"""
        # Alta convicción del ML
        if abs(signals.get('signal_strength', 0)) > 0.8:
            return True
        # Cambio de régimen
        if signals.get('regime_change', False):
            return True
        # Anomalía detectada
        if signals.get('anomaly_score', 0) > 0.9:
            return True
        return False
```

### Fase 4: Risk & Execution (Semanas 7-8)

| Task | Tiempo Est. | LLM Calls |
|------|-------------|-----------|
| Risk guard enhanced | 4 horas | 0 |
| Position sizing | 3 horas | 0 |
| Bracket management | 3 horas | 0 |
| Drawdown controls | 2 horas | 1 |
| Backtesting final | 4 horas | 1 |
| **TOTAL** | **16 horas** | **2** |

---

## Timeline Realista

```
SEMANA    1    2    3    4    5    6    7    8
          │    │    │    │    │    │    │    │
FASE 1    ████████
FASE 2              ████████████
FASE 3                         ████████████
FASE 4                                    ████████████

LLM Calls:  0    0    4    2    4    2    2    1
            └────────────────┬────────────────┘
                  Total: ~15 prompts/mes
                  (dentro del límite de 300/5h)
```

**Total LLM Calls del proyecto:** ~15 prompts
**Delimit remaining para producción:** ~285 prompts / 5 horas para trading activo

---

## Métricas Esperadas (Realistas)

| Métrica | Objetivo | Realista |
|---------|----------|----------|
| **Sharpe Ratio** | >1.5 | 0.8-1.2 |
| **Max Drawdown** | <15% | 10-20% |
| **Win Rate** | >45% | 40-50% |
| **Trades/Día** | 5-10 | 2-5 |
| **Latencia Señal** | <100ms | <50ms |
| **Uptime** | >99% | >99% |

---

## Costos Estimados

### API Externas
| Servicio | Costo/Mes | Necesario |
|----------|-----------|-----------|
| Aster API | $0 | ✅ Ya tienes |
| Redis (cloud) | $20 | Recomendado |
| API sentiment | $0-50 | Opcional |
| Cloud compute | $50-100 | Depends |

### Tu Límite MiniMax
- **300 prompts / 5 horas**
- **Proyecto:** ~15 prompts total
- **Producción:** ~1 prompt/hora = 120 prompts/día = 600 prompts/5 horas

⚠️ **ALERTA:** En producción continua, exceeds tu límite.
**Solución:** Reducir a 1 llamada cada 2 horas = 60 prompts/5 horas (OK)

---

## Código Completo: Sistema Realista

```python
# trading_system.py
"""
Sistema de Trading Realista para Aster
- ML-driven (no LLM para decisiones)
- LLM solo para supervisión (1x/hora máximo)
"""

import asyncio
import json
import time
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional

# =======================
# CONFIGURATION
# =======================
CONFIG = {
    # LLM Limits
    "max_llm_per_hour": 1,
    "max_llm_per_day": 20,
    
    # Risk
    "max_position_pct": 0.05,  # 5% max per trade
    "max_daily_loss_pct": 0.05,  # 5% max daily loss
    "max_drawdown_pct": 0.15,  # 15% max drawdown
    
    # Trading
    "symbols": ["ASTERUSDT", "ETHUSDT", "BTCUSDT"],
    "timeframe": "5m",
    
    # ML
    "model_path": "./models/signal_model.json",
    "feature_count": 200,
}


# =======================
# DATA LAYER
# =======================
class MarketDataManager:
    """Gestión de datos de mercado via WebSocket + REST"""
    
    def __init__(self):
        self.ws_manager = None
        self.orderbook_cache = {}
        self.trades_cache = {}
        self.klines_cache = {}
        
    async def initialize(self):
        """Inicializar conexiones"""
        # WebSocket para real-time
        self.ws_manager = AsterWebSocketManager()
        await self.ws_manager.connect()
        
        # Suscribirse a streams
        await self.ws_manager.subscribe([
            "asterusdt@depth20@100ms",
            "asterusdt@trade",
            "asterusdt@kline_1m",
        ], self._on_message)
        
    def _on_message(self, data):
        """Procesar mensaje entrante"""
        stream = data.get('stream', '')
        
        if 'depth' in stream:
            self.orderbook_cache[data['s']] = data['data']
        elif 'trade' in stream:
            self._update_trades(data['data'])
        elif 'kline' in stream:
            self._update_klines(data['data'])
            
    def get_features(self, symbol: str) -> Dict:
        """Extraer features para el modelo"""
        ob = self.orderbook_cache.get(symbol, {})
        trades = self.trades_cache.get(symbol, [])
        klines = self.klines_cache.get(symbol, [])
        
        return {
            # Microstructure (10 features)
            "spread_bps": self._calc_spread(ob),
            "order_imbalance": self._calc_imbalance(ob),
            "buy_sell_ratio": self._calc_buy_sell_ratio(trades),
            "volume_ratio": self._calc_volume_ratio(trades),
            # Technical (agregar más...)
            **self._calc_technical(klines),
        }
        
    def _calc_spread(self, ob) -> float:
        if not ob.get('bids') or not ob.get('asks'):
            return 0.0
        best_bid = float(ob['bids'][0][0])
        best_ask = float(ob['asks'][0][0])
        return ((best_ask - best_bid) / best_bid) * 10000


# =======================
# ML LAYER
# =======================
class SignalGenerator:
    """Generador de señales usando ML (no LLM)"""
    
    def __init__(self, model_path: str):
        self.model = None
        self.load_model(model_path)
        
    def predict(self, features: Dict) -> Dict:
        """Generar señal de trading"""
        # Convertir features a array
        X = self._features_to_array(features)
        
        # Predicción del modelo
        signal = self.model.predict(X)[0]
        probability = self.model.predict_proba(X)[0]
        
        return {
            "action": "BUY" if signal > 0.5 else "SELL" if signal < 0.5 else "HOLD",
            "signal_strength": abs(probability - 0.5) * 2,
            "confidence": max(probability),
        }


# =======================
# LLM LAYER (LIMITADO)
# =======================
class LLMGuardian:
    """Supervisión LLM con límites estrictos"""
    
    def __init__(self):
        self.last_call_time = 0
        self.min_interval = 3600  # 1 hora
        self.call_count = 0
        
    def should_review(self, ml_signal: Dict, market_state: Dict) -> bool:
        """Decidir si revisar con LLM"""
        now = time.time()
        
        # Verificar límites
        if now - self.last_call_time < self.min_interval:
            return False
            
        # Solo en momentos críticos
        if ml_signal.get('signal_strength', 0) > 0.85:
            self.last_call_time = now
            self.call_count += 1
            return True
            
        return False
        
    async def review(self, signal: Dict, market: Dict) -> str:
        """Revisar señal con LLM (limitado)"""
        if not self.should_review(signal, market):
            return "SKIP"
            
        # Construir prompt conciso
        prompt = f"""
Eres un supervisor de trading. Revisa esta señal:

Signal: {signal['action']}
Strength: {signal['signal_strength']:.2f}
Confidence: {signal['confidence']:.2f}

Precio: {market.get('price')}
RSI: {market.get('rsi')}
Trend: {market.get('trend')}

¿Aprobar trade? Responde: APPROVE o REJECT + razón breve.
"""
        # LLamada LLM (1/hora máximo)
        # response = await llm.chat(prompt)
        # return parse_response(response)
        
        return "APPROVE - Signal strong"


# =======================
# RISK LAYER
# =======================
class RiskGuard:
    """Guardia de riesgo"""
    
    def __init__(self, config: Dict):
        self.max_position = config['max_position_pct']
        self.max_daily_loss = config['max_daily_loss_pct']
        self.max_drawdown = config['max_drawdown_pct']
        
    def check(self, trade: Dict, portfolio: Dict) -> bool:
        """Verificar si trade pasa risk checks"""
        # Position size
        if trade['notional'] > portfolio['equity'] * self.max_position:
            return False
            
        # Daily loss
        if portfolio['daily_pnl'] < -portfolio['equity'] * self.max_daily_loss:
            return False
            
        # Drawdown
        if portfolio['drawdown'] > self.max_drawdown:
            return False
            
        return True


# =======================
# MAIN LOOP
# =======================
class TradingSystem:
    """Sistema principal"""
    
    def __init__(self):
        self.data = MarketDataManager()
        self.signals = SignalGenerator(CONFIG['model_path'])
        self.llm = LLMGuardian()
        self.risk = RiskGuard(CONFIG)
        
    async def run(self):
        """Loop principal"""
        await self.data.initialize()
        
        while True:
            try:
                # 1. Obtener features
                features = self.data.get_features("ASTERUSDT")
                
                # 2. Generar señal ML
                signal = self.signals.predict(features)
                
                # 3. Risk check
                if self.risk.check(signal, {}):
                    # 4. LLM review (solo si necesario)
                    if self.llm.should_review(signal, features):
                        decision = await self.llm.review(signal, features)
                        if decision.startswith("APPROVE"):
                            await self.execute_trade(signal)
                    
                # 5. Esperar siguiente tick
                await asyncio.sleep(60)  # 1 minuto
                
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(60)
                
    async def execute_trade(self, signal):
        """Ejecutar trade"""
        # Implementar con Aster API
        pass


# =======================
# ENTRY POINT
# =======================
if __name__ == "__main__":
    system = TradingSystem()
    asyncio.run(system.run())
```

---

## Recomendaciones Finales

### ✅ HACER
- WebSocket para datos real-time (reduce rate limits)
- ML (XGBoost) como señal principal
- LLM solo para supervisión (1x/hora)
- Backtesting riguroso antes de producción

### ❌ NO HACER
- No依赖 LLM para cada decisión
- No hacer más de 1 llamada LLM/hora
- No ignorar rate limits de Aster
- No skiptear risk management

### ⚠️ WARNING
Con tu plan de 300 prompts/5 horas:
- **Desarrollo:** ~15 prompts total ✅
- **Producción (1x/hora):** ~120 prompts/día = 600/5 horas ❌

**Solución:** Reducir a 1 llamada cada 2-3 horas en producción, o actualizar tu plan MiniMax.

---

*Documento actualizado con enfoque realista - considera tus límites reales de MiniMax*
