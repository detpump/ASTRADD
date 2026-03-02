# Análisis Profundo: LLMs en Trading Algorítmico

## Sistema Aster Trading - Evaluación y Propuesta de Mejora

**Fecha:** 2026-02-24  
**Versión:** 1.0  
**Clasificación:** Técnico

---

## Tabla de Contenidos

1. [Mecanismos de Toma de Decisiones del LLM](#1-mecanismos-de-toma-de-decisiones-del-llm)
2. [Limitaciones Actuales y Sesgos Cognitivos](#2-limitaciones-actuales-y-sesgos-cognitivos)
3. [Arquitectura Propuesta Mejorada](#3-arquitectura-propuesta-mejorada)
4. [Optimización de Parámetros](#4-optimización-de-parámetros-para-risk-return)
5. [Ensembles, RL y Validación Cruzada](#5-ensembles-rl-adaptativo-y-validación-cruzada)
6. [Evaluación de API Aster](#6-evaluación-de-api-aster)
7. [Hoja de Ruta de Implementación](#7-hoja-de-ruta-de-implementación)

---

## 1. Mecanismos de Toma de Decisiones del LLM

### 1.1 Procesamiento de Información de Mercado

El sistema actual de Aster procesa información de mercado a través de múltiples capas:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                    │
├─────────────────────────────────────────────────────────────────────┤
│  OHLCV (5m, 30m, 1h)  │  Order Book  │  Ticker 24h  │  Funding    │
│         ✓              │      ✗       │       ✓       │      ✓      │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     PREPROCESSING                                    │
├─────────────────────────────────────────────────────────────────────┤
│  • SMA/EMA calculation                                              │
│  • RSI computation                                                  │
│  • ATR calculation                                                  │
│  • Support/Resistance detection                                    │
│  • Trend classification                                             │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       LLM POLICY                                     │
├─────────────────────────────────────────────────────────────────────┤
│  Prompt Engineering → LLM Inference → JSON Parsing → Decisions     │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      RISK GUARD                                      │
├─────────────────────────────────────────────────────────────────────┤
│  • Position sizing limits                                           │
│  • Daily loss limits                                                │
│  • Circuit breakers                                                │
│  • Cooldown rules                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Identificación de Patrones

El LLM actual analiza patrones a través de:

| Patrón | Método de Detección | Limitación Actual |
|--------|---------------------|-------------------|
| **Tendencias** | SMA crossover (fast/slow) | Solo temporal, no espacial |
| **Soporte/Resistencia** | Min/Max lookback 20 períodos | Fijo, no adaptativo |
| **Momentum** | RSI, Momentum % | Períodos fijos |
| **Volatilidad** | ATR % | No considera clustering |
| **Volume anomalies** | Ratio vs promedio | Muy básico |

### 1.3 Generación de Señales

El flujo actual en [`llm_policy_decider.py`](skills/aster-trading/src/llm_policy_decider.py):

```python
# Pipeline de decisión actual:
1. Cargar market_state + risk_cfg + trade_state
2. Prefilter: equity > $10 mínimo
3. Construir prompt con contexto histórico
4. Llamar LLM (Anthropic/MiniMax)
5. Parsear respuesta JSON
6. Clamp decisions contra límites de riesgo
7. Generar órdenes PENDING
```

**Problema crítico**: El LLM opera en modo "black box" sin validación sistemática de sus decisiones.

---

## 2. Limitaciones Actuales y Sesgos Cognitivos

### 2.1 Limitaciones Técnicas del Sistema Actual

| Aspecto | Estado Actual | Deficiencia |
|---------|--------------|-------------|
| **Latencia** | ~30 minutos (heartbeat) | No es real-time |
| **Datos de mercado** | Solo OHLCV básico | No order book, no flow |
| **Sentimiento** | Ninguno | N/A |
| **Macroeconomía** | Ninguna | N/A |
| **Datos alternativos** | Ninguno | N/A |
| **Cross-validation** | Ninguna | Overfitting probable |
| **Ensemble** | Un solo LLM | No diversidad |

### 2.2 Sesgos Cognitivos del LLM en Trading

#### Recency Bias
- Los últimos datos pesan más de lo que deberían
- Crea momentum falso en mercados laterales

#### Anchoring
- Precios de entrada sesgan decisiones
- Stop loss / Take profit ajustados incorrectamente

#### Overconfidence
- Confianza excesiva en señales
- Tamaños de posición inflados

#### Confirmation Bias
- El LLM busca datos que confirmen su decisión inicial
- Ignora señales contradictorias

#### Pattern Recognition
- Encuentra patrones donde solo hay ruido
- Sobreajuste a patrones históricos espurios

#### Temporal Myopia
- No considera ciclos largos (semanal, mensual)
- Suboptimiza en horizontes temporales largos

#### Hindsight Bias
- Explicaciones post-hoc convincentes pero engañosas
- Backtesting que parece rentable pero no lo es

---

## 3. Arquitectura Propuesta Mejorada

### 3.1 Arquitectura Integral Multi-Fuente

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                    │
├─────────────────┬─────────────────┬─────────────────┬───────────────┤
│ Market          │ Technical       │ Sentiment       │ Macro         │
│ Microstructure  │ Analysis        │ Real-time       │ Indicators    │
│                 │                 │                 │               │
│ • Order Book    │ • EMA Crossover │ • News API      │ • DXY         │
│ • Trade Flow    │ • RSI Div       │ • Social Media  │ • VIX         │
│ • VWAP          │ • MACD          │ • Fear/Greed    │ • Fed Rate    │
│ • Spread        │ • Bollinger     │ • Derivatives   │ • Yields      │
│ • Liquidity     │ • Patterns      │ • Whale Alert  │ • M2          │
└─────────────────┴─────────────────┴─────────────────┴───────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    ALTERNATIVE DATA                                  │
├─────────────────────────────────────────────────────────────────────┤
│ • On-Chain Metrics (Whale, Inflow, Hash Rate)                       │
│ • ETF Flows                                                         │
│ • Governance                                                        │
│ • Network Activity                                                  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     FEATURE ENGINEERING                              │
├─────────────────────────────────────────────────────────────────────┤
│ • Normalization (Z-score, Min-Max)                                   │
│ • Windowing (Rolling windows, Lookbacks)                           │
│ • Feature Selection (Importance-based)                              │
│ • Dimensionality Reduction (PCA if needed)                          │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      MODEL ENSEMBLE                                 │
├─────────────────┬─────────────────┬─────────────────┬───────────────┤
│ LLM Trading     │ LLM Risk        │ RL Optimizer    │ ML Classifiers│
│ Agent           │ Agent           │ (PPO/A2C)       │ (XGB/LightGBM)│
│                 │                 │                 │               │
│ • Policy        │ • Risk Assess   │ • Learn from    │ • Pattern     │
│ • Reasoning     │ • Validation   │   results       │   detection   │
│ • Explanation  │ • Guard        │ • Adapt         │ • Signals     │
└─────────────────┴─────────────────┴─────────────────┴───────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      VALIDATION LAYER                               │
├─────────────────────────────────────────────────────────────────────┤
│ • Temporal Cross-Validation (Walk-Forward)                          │
│ • Backtesting with transaction costs                               │
│ • Monte Carlo simulations                                          │
│ • Stress testing                                                    │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      EXECUTION LAYER                                │
├─────────────────────────────────────────────────────────────────────┤
│ • Risk Guard (pre-trade)                                            │
│ • Order Manager (execution)                                        │
│ • Portfolio Manager (allocation)                                    │
│ • Performance Tracker                                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Componentes Detallados

#### A. Market Microstructure (Microestructura)

```python
# Features de microestructura necesarios:
market_microstructure_features = {
    # Order Book Dynamics
    "bid_ask_spread": "float",           # Spread bid-ask
    "order_book_imbalance": "float",     # Ratio bids vs asks
    "volume_weighted_price": "float",   # VWAP
    "price_impact": "float",             # Impacto por orden
    
    # Flow Metrics
    "buy_sell_ratio_1m": "float",        # Ratio volumen buy/sell
    "buy_sell_ratio_5m": "float",
    "trade_flow_momentum": "float",       # Momentum del flujo
    
    # Liquidity
    "bid_depth_10": "float",             # Profundidad 10 niveles
    "ask_depth_10": "float",
    "liquidity_ratio": "float",          # Ratio liquidez
    
    # Execution Quality
    "slippage_estimate": "float",
    "market_impact_cost": "float"
}
```

#### B. Análisis Técnico Avanzado

```python
advanced_technical_features = {
    # Trend
    "ema_9_21_crossover": "signal",       # Crossover EMA
    "ema_9_21_distance_pct": "float",      # Distancia EMAs
    "trend_strength_adx": "float",         # ADX
    
    # Momentum
    "rsi_divergence": "signal",           # Divergencia RSI
    "macd_histogram": "float",            # Histograma MACD
    "stochastic_k": "float",              # %K
    "stochastic_d": "float",              # %D
    "cci": "float",                        # Commodity Channel Index
    
    # Volatility
    "bollinger_position": "float",        # Posición en BB
    "atr_ratio": "float",                  # ATR vs histórico
    "keltner_position": "float",           # Posición en canales
    
    # Patterns
    "pivot_points": "dict",               # Puntos pivote
    "fibonacci_levels": "dict",           # Niveles Fibonacci
    "candlestick_pattern": "signal",     # Patrones velas
    
    # Volume
    "obv": "float",                       # On-Balance Volume
    "vwap": "float",                      # VWAP
    "volume_profile": "dict",             # Perfil de volumen
}
```

#### C. Sentimiento de Mercado en Tiempo Real

```python
sentiment_features = {
    # News
    "news_sentiment_score": "float",      # Score -1 a 1
    "news_impact_estimate": "float",      # Impacto esperado
    "crypto_news_count": "int",           # Noticias recientes
    
    # Social
    "twitter_sentiment": "float",
    "reddit_sentiment": "float",
    "social_volume": "int",
    
    # Derivatives
    "funding_rate_history": "list",       # Historial funding
    "open_interest": "float",
    "long_short_ratio": "float",
    
    # Fear/Greed
    "fear_greed_index": "float",
}
```

#### D. Indicadores Macroeconómicos

```python
macro_features = {
    # USD
    "dxy": "float",                      # Dollar Index
    "dxy_change_1d": "float",
    
    # Rates
    "fed_funds_rate": "float",
    "us_10y_yield": "float",
    "tltd_yield_spread": "float",         # Spread TLTD
    
    # Risk Assets
    "vix": "float",
    "vix_change_1d": "float",
    "sp500_change_1d": "float",
    
    # Crypto Specific
    "btc_dominance": "float",
    "btc_dominance_trend": "signal",
    "altcoin_season_index": "float",
    
    # Global Liquidity
    "global_m2": "float",
    "m2_yoy_change": "float",
}
```

#### E. Datos Alternativos

```python
alternative_data = {
    # On-Chain
    "whale_transactions": "int",          # Transacciones >$100k
    "exchange_inflow": "float",           # Flujo a exchanges
    "exchange_outflow": "float",
    "stablecoin_supply": "float",
    "miner_revenue": "float",
    
    # Network
    "active_addresses": "int",
    "hash_rate": "float",
    "difficulty": "float",
    
    # Governance
    "proposal_sentiment": "float",
    
    # ETF Flows (Bitcoin)
    "etf_inflow_1d": "float",
    "etf_outflow_1d": "float",
}
```

---

## 4. Optimización de Parámetros para Risk-Return

### 4.1 Framework de Optimización

```python
optimization_objective = {
    "primary": "maximize_sharpe_ratio",
    "secondary": "minimize_max_drawdown",
    "constraints": {
        "max_drawdown_pct": 0.15,        # Max 15% drawdown
        "max_daily_loss_pct": 0.05,       # Max 5% pérdida diaria
        "min_win_rate": 0.45,             # Min 45% win rate
        "max_leverage": 10.0,             # Max 10x leverage
    },
    "targets": {
        "target_sharpe": 1.5,            # Objetivo Sharpe 1.5
        "target_sortino": 2.0,            # Objetivo Sortino 2.0
        "target_calmar": 2.0,            # Objetivo Calmar 2.0
    }
}
```

### 4.2 Parámetros a Optimizar

| Categoría | Parámetro | Rango | Método Optimización |
|-----------|-----------|-------|---------------------|
| **Position Sizing** | `risk_per_trade_pct` | 0.5-5% | Kelly Criterion |
| **Stop Loss** | `sl_atr_multiplier` | 0.5-3.0 | ATR adaptativo |
| **Take Profit** | `tp_risk_reward` | 1.0-5.0 | RR ratio |
| **Leverage** | `leverage_table` | 1-10x | Conditional |
| **Correlation** | `max_pairwise_corr` | 0.3-0.8 | Portfolio opt |

### 4.3 Control de Drawdown

```python
drawdown_protection = {
    # Dynamic Sizing
    "reduce_on_drawdown": {
        "drawdown_5pct": {"size_multiplier": 0.8},
        "drawdown_10pct": {"size_multiplier": 0.5},
        "drawdown_15pct": {"size_multiplier": 0.25},
    },
    
    # Circuit Breakers
    "circuit_breakers": {
        "daily_loss_5pct": "stop_trading_1h",
        "daily_loss_10pct": "stop_trading_24h",
        "consecutive_losses_5": "reduce_size_50pct",
    },
    
    # Cooldowns
    "cooldown_rules": {
        "after_large_loss": "30min",
        "after_whipsaw": "15min",
    }
}
```

---

## 5. Ensembles, RL Adaptativo y Validación Cruzada

### 5.1 Arquitectura de Ensemble

```
┌─────────────────────────────────────────────────────────────────────┐
│                      BASE MODELS                                    │
├─────────────────┬─────────────────┬─────────────────┬───────────────┤
│ LLM Trading     │ Technical       │ Sentiment       │ Macro         │
│ Agent           │ Agent           │ Agent           │ Agent         │
│                 │                 │                 │               │
│ • Policy        │ • Patterns      │ • News Impact   │ • Regime      │
│ • Reasoning     │ • Signals       │ • Social        │ • Cycles      │
│ • Explanation   │ • Indicators    │ • Fear/Greed    │ • Correlation │
└─────────────────┴─────────────────┴─────────────────┴───────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       META-LEARNER                                  │
├─────────────────────────────────────────────────────────────────────┤
│ • Weight optimization based on recent performance                   │
│ • Dynamic weighting (more weight to recent winners)               │
│ • Blending vs Stacking                                              │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      FINAL AGGREGATOR                               │
├─────────────────────────────────────────────────────────────────────┤
│ • Weighted voting                                                   │
│ • Confidence-based position sizing                                  │
│ • Risk-adjusted signal strength                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Reinforcement Learning Adaptativo

```python
class AdaptiveTradingRL:
    def __init__(self):
        self.state_dim = 500+  # Features
        self.action_dim = 3    # BUY, HOLD, SELL
        self.policy = PPOAgent(
            state_dim=self.state_dim,
            action_dim=self.action_dim,
            config={
                "learning_rate": 3e-4,
                "gamma": 0.99,           # Discount factor
                "lam": 0.95,              # GAE lambda
                "clip_ratio": 0.2,
                "value_coef": 0.5,
                "entropy_coef": 0.01,
            }
        )
        
    def get_reward(self, portfolio_return, risk_penalty, drawdown_penalty):
        """
        Reward composto:
        - Return positivo: +reward
        - Risk excesivo: -penalty
        - Drawdown: -penalty fuerte
        """
        reward = (
            portfolio_return * 100 +           # Return componente
            risk_penalty * 10 +                  # Riesgo componente
            drawdown_penalty * 50                # Drawdown componente
        )
        return reward
    
    def update_on_trade_result(self, trade_result):
        """
        Actualizar política basada en resultado real:
        1. Calcular reward
        2. Computar advantage
        3. Update policy network
        4. Store in experience replay
        """
        pass
```

### 5.3 Validación Cruzada Temporal

```python
class TemporalCrossValidation:
    """
    Walk-forward validation respecting temporal order
    Evita data leakage preservando orden temporal
    """
    def __init__(self, train_window=6, test_window=1, gap=0):
        """
        Args:
            train_window: Meses de entrenamiento
            test_window: Meses de test
            gap: Días de gap entre train y test
        """
        self.train_months = train_window   # 6 meses train
        self.test_months = test_window     # 1 mes test
        self.gap_days = gap                # Sin gap
    
    def generate_splits(self, start_date, end_date):
        """
        Genera splits temporales para walk-forward validation:
        
        Split 1: Train 2024-01 to 2024-06 → Test 2024-07
        Split 2: Train 2024-02 to 2024-07 → Test 2024-08
        ...
        """
        splits = []
        current = start_date
        
        while current + self.train_months <= end_date:
            train_end = current + self.train_months
            test_start = train_end + self.gap_days
            test_end = test_start + self.test_months
            
            if test_end > end_date:
                break
                
            splits.append({
                "train": (current, train_end),
                "test": (test_start, test_end),
            })
            
            current = current + timedelta(months=1)  # Slide by 1 month
            
        return splits
    
    def walk_forward_evaluate(self, model, data):
        """
        Ejecuta walk-forward validation:
        """
        results = []
        for split in self.generate_splits():
            train_data = data[split["train"]]
            test_data = data[split["test"]]
            
            # Reset model and train on train window
            model.reset()
            model.fit(train_data)
            
            # Predict on test window
            predictions = model.predict(test_data)
            
            # Calculate metrics
            metrics = calculate_metrics(predictions, test_data)
            results.append(metrics)
            
        return aggregate_results(results)
```

---

## 6. Evaluación de API Aster

### 6.1 Capacidades Actuales

| Endpoint | Función | Latencia | Estado |
|----------|---------|----------|--------|
| `GET /fapi/v1/klines` | OHLCV | ~100ms | ✅ Implementado |
| `GET /fapi/v1/ticker/24hr` | Precio 24h | ~100ms | ✅ Implementado |
| `GET /fapi/v1/premiumIndex` | Mark Price | ~100ms | ✅ Implementado |
| `GET /fapi/v1/account` | Balance | ~200ms | ✅ Implementado |
| `GET /fapi/v1/positionRisk` | Posiciones | ~200ms | ✅ Implementado |
| `POST /fapi/v1/order` | Orden | ~300ms | ✅ Implementado |

### 6.2 Endpoints Faltantes para Estrategia Avanzada

| Endpoint | Descripción | Latencia Requerida | Prioridad |
|----------|-------------|-------------------|-----------|
| `/fapi/v1/depth` | Order Book | 50ms | **CRÍTICO** |
| `/fapi/v1/trades` | Trades recientes | 50ms | **CRÍTICO** |
| `/fapi/v1/aggTrades` | Trades agregados | 100ms | ALTA |
| `/fapi/v1/fundingRate` | Historial funding | 100ms | MEDIA |
| `/fapi/v1/openInterest` | Interés abierto | 100ms | MEDIA |
| `/fapi/v1/ticker/bookTicker` | Book ticker | 100ms | MEDIA |
| `/fapi/v1/historicalVolatility` | Volatilidad histórica | 500ms | BAJA |
| `/fapi/v1/allForceOrders` | Liquidaciones | 200ms | MEDIA |

### 6.3 Latencia y Throughput Requeridos

| Componente | Latencia Actual | Latencia Objetivo |
|------------|-----------------|-------------------|
| Market Data (OHLCV) | ~100ms | <100ms |
| Order Book | ❌ N/A | <50ms |
| Trade Stream | ❌ N/A | <30ms |
| LLM Inference | ~2000ms | <2000ms |
| Risk Check | ~100ms | <100ms |
| Order Execution | ~300ms | <300ms |
| **Total Pipeline** | ~30,000ms | <500ms |

### 6.4 Modificaciones de Infraestructura Requeridas

#### 6.4.1 WebSocket para datos real-time

```python
# Endpoints WebSocket necesarios:
websocket_endpoints = {
    "trade_stream": "wss://stream.binance.com:9443/ws/ethusdt@trade",
    "depth_stream": "wss://stream.binance.com:9443/ws/ethusdt@depth20@100ms",
    "ticker_stream": "wss://stream.binance.com:9443/ws/!ticker@arr",
    "kline_stream": "wss://stream.binance.com:9443/ws/ethusdt@kline_1m",
}
```

#### 6.4.2 Cache en memoria para order book

```
Tecnología recomendada: Redis o LMDB

Estructura:
{
    "symbol": "ETHUSDT",
    "bids": [[precio, cantidad], ...],  # 20 niveles
    "asks": [[precio, cantidad], ...],    # 20 niveles
    "timestamp": 1234567890,
    "last_update_id": 12345
}
```

#### 6.4.3 Base de datos de features

```
Tecnología: TimescaleDB o InfluxDB

Retención:
- Hot (30 días): Datos granulares
- Warm (1 año): Datos agregados por hora
- Cold (永久): Datos diarios
```

#### 6.4.4 Modelo serving

```
Opciones:
- TensorFlow Serving
- TorchServe
- vLLM (para LLM inference)

Objetivo: <500ms latencia de inferencia
```

---

## 7. Hoja de Ruta de Implementación

### Fase 1: Fundamentos (Semanas 1-4)

- [ ] Implementar WebSocket feeds
- [ ] Procesamiento de Order Book
- [ ] Feature store básico
- [ ] Cache en Redis

### Fase 2: Datos Avanzados (Semanas 5-8)

- [ ] Integración de APIs de sentimiento
- [ ] Datos macroeconómicos
- [ ] Datos alternativos (on-chain)
- [ ] Pipeline de datos completo

### Fase 3: Modelo Mejorado (Semanas 9-12)

- [ ] Multi-model ensemble
- [ ] RL optimizer (PPO/A2C)
- [ ] Temporal cross-validation
- [ ] Meta-learner

### Fase 4: Optimización (Semanas 13-16)

- [ ] Hyperparameter tuning
- [ ] Drawdown controls
- [ ] Risk overlays
- [ ] Portfolio optimization

### Fase 5: Production (Semanas 17-20)

- [ ] A/B testing
- [ ] Monitoring & alerts
- [ ] Automated updates
- [ ] Documentation

---

## Anexo: Métricas de Evaluación

### Métricas de Trading

| Métrica | Definición | Objetivo |
|---------|------------|----------|
| **Sharpe Ratio** | (Return - Risk-free) / Volatility | > 1.5 |
| **Sortino Ratio** | (Return - Target) / Downside Dev | > 2.0 |
| **Calmar Ratio** | Annual Return / Max Drawdown | > 2.0 |
| **Win Rate** | Trades winners / Total trades | > 45% |
| **Profit Factor** | Gross profit / Gross loss | > 1.5 |
| **Max Drawdown** | Peak to trough | < 15% |
| **Recovery Time** | Days to recover from max DD | < 30 days |

### Métricas de Modelo

| Métrica | Definición |
|---------|------------|
| **Precision** | True Positives / (TP + FP) |
| **Recall** | True Positives / (TP + FN) |
| **F1 Score** | 2 * (Precision * Recall) / (Precision + Recall) |
| **AUC-ROC** | Area Under ROC Curve |
| **Log Loss** | Cross-entropy loss |

---

## Referencias

1. Binance API Documentation: https://binance-docs.github.io/apidocs/
2. Technical Analysis: Murphy, J.J. (1999). "Technical Analysis of the Financial Markets"
3. Machine Learning: Bishop, C.M. (2006). "Pattern Recognition and Machine Learning"
4. Reinforcement Learning: Sutton, R.S. & Barto, A.G. (2018). "Reinforcement Learning"

---

*Documento generado automáticamente para el proyecto Aster Trading*
