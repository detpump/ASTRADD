# Especificación de Requisitos de API Aster

## Resumen Ejecutivo

Este documento detalla los endpoints de API necesarios para implementar una estrategia de trading sofisticada basada en LLM con múltiples fuentes de datos.

---

## Endpoints de Mercado (Market Data)

### Endpoints Actuales (Implementados)

| Endpoint | Método | Descripción | Latencia |
|----------|--------|-------------|----------|
| `/fapi/v1/klines` | GET | OHLCV candles | ~100ms |
| `/fapi/v1/ticker/24hr` | GET | 24h ticker | ~100ms |
| `/fapi/v1/premiumIndex` | GET | Mark price & funding | ~100ms |
| `/fapi/v1/exchangeInfo` | GET | Exchange info | ~100ms |

### Endpoints Requeridos (No Implementados)

#### Prioridad CRÍTICA

| Endpoint | Método | Descripción | Latencia Objetivo |
|----------|--------|-------------|-------------------|
| `/fapi/v1/depth` | GET | Order book (20-100 levels) | <50ms |
| `/fapi/v1/trades` | GET | Recent trades (500-1000) | <50ms |
| `/fapi/v1/aggTrades` | GET | Aggregated trades | <100ms |

#### Prioridad ALTA

| Endpoint | Método | Descripción | Latencia Objetivo |
|----------|--------|-------------|-------------------|
| `/fapi/v1/ticker/bookTicker` | GET | Best bid/ask | <50ms |
| `/fapi/v1/fundingRate` | GET | Funding history | <100ms |
| `/fapi/v1/openInterest` | GET | Open interest | <100ms |
| `/fapi/v1/continuousKlines` | GET | Continuous klines | <100ms |

#### Prioridad MEDIA

| Endpoint | Método | Descripción | Latencia Objetivo |
|----------|--------|-------------|-------------------|
| `/fapi/v1/historicalVolatility` | GET | Historical volatility | <500ms |
| `/fapi/v1/allForceOrders` | GET | Force liquidations | <200ms |
| `/fapi/v1/ticker/price` | GET | Single symbol price | <50ms |
| `/fapi/v1/ticker/allPrices` | GET | All prices | <100ms |

---

## Endpoints de Trading (Execution)

### Endpoints Actuales (Implementados)

| Endpoint | Método | Descripción | Latencia |
|----------|--------|-------------|----------|
| `/fapi/v1/account` | GET | Account info | ~200ms |
| `/fapi/v1/positionRisk` | GET | Position risk | ~200ms |
| `/fapi/v1/order` | POST | Place order | ~300ms |
| `/fapi/v1/order` | DELETE | Cancel order | ~300ms |
| `/fapi/v1/order` | GET | Order status | ~200ms |
| `/fapi/v1/openOrders` | GET | Open orders | ~200ms |
| `/fapi/v1/allOrders` | GET | All orders | ~200ms |
| `/fapi/v1/leverage` | POST | Set leverage | ~300ms |

### Endpoints Requeridos

| Endpoint | Método | Descripción | Prioridad |
|----------|--------|-------------|-----------|
| `/fapi/v1/order/otest` | POST | Test order (validate only) | ALTA |
| `/fapi/v1/batchOrders` | POST | Batch orders | MEDIA |
| `/fapi/v1/myTrades` | GET | Account trades | MEDIA |

---

## WebSocket Streams

### Streams Requeridos

| Stream | Descripción | Frecuencia |
|--------|-------------|------------|
| `<symbol>@trade` | Trade stream | Tiempo real |
| `<symbol>@depth20@100ms` | Order book 20 levels | 100ms |
| `<symbol>@kline_<interval>` | Kline/candlestick | Tiempo real |
| `!ticker@arr` | All market tickers | ~1s |
| `<symbol>@ticker` | Individual ticker | ~1s |
| `<symbol>@continuousKline_<interval>` | Continuous klines | Tiempo real |

### Formato de Order Book para WebSocket

```json
{
  "lastUpdateId": 160,
  "bids": [
    ["0.0024", "10"],
    ["0.0023", "100"],
    ["0.0022", "200"]
  ],
  "asks": [
    ["0.0026", "100"],
    ["0.0027", "100"],
    ["0.0029", "300"]
  ]
}
```

---

## Estructura de Datos Requerida

### Order Book Feature Extraction

```python
def extract_orderbook_features(orderbook_data: dict) -> dict:
    """
    Extrae features de microstructure del order book
    """
    bids = orderbook_data['bids']
    asks = orderbook_data['asks']
    
    # Calculate mid price
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid_price = (best_bid + best_ask) / 2
    
    # Calculate spread
    spread = (best_ask - best_bid) / mid_price
    
    # Calculate order book imbalance
    bid_volume = sum(float(b[1]) for b in bids[:10])
    ask_volume = sum(float(a[1]) for a in asks[:10])
    imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
    
    # Calculate weighted mid price
    bid_vwap = sum(float(b[0]) * float(b[1]) for b in bids[:10]) / bid_volume
    ask_vwap = sum(float(a[0]) * float(a[1]) for a in asks[:10]) / ask_volume
    vwap = (bid_vwap + ask_vwap) / 2
    
    return {
        'spread_bps': spread * 10000,  # Basis points
        'order_book_imbalance': imbalance,
        'mid_price': mid_price,
        'vwap': vwap,
        'bid_depth_10': bid_volume,
        'ask_depth_10': ask_volume,
        'best_bid': best_bid,
        'best_ask': best_ask,
    }
```

### Trade Flow Features

```python
def extract_trade_flow_features(trades: list) -> dict:
    """
    Extrae features de flow de trades
    """
    buy_volume = sum(t['qty'] for t in trades if t['isBuyerMaker'] == False)
    sell_volume = sum(t['qty'] for t in trades if t['isBuyerMaker'] == True)
    total_volume = buy_volume + sell_volume
    
    # Calculate ratio
    buy_ratio = buy_volume / total_volume if total_volume > 0 else 0.5
    
    # Calculate momentum (trend in buy/sell)
    # Recent trades vs older trades
    
    return {
        'buy_volume_1m': buy_volume,
        'sell_volume_1m': sell_volume,
        'buy_sell_ratio': buy_ratio,
        'total_volume': total_volume,
        'trade_count': len(trades),
    }
```

---

## Latencia Budget

### Target: <500ms End-to-End

| Componente | Latencia Objetivo |
|------------|-------------------|
| Data Fetch (REST) | 50-100ms |
| Data Fetch (WebSocket) | 10-30ms |
| Feature Engineering | 20ms |
| LLM Inference | 2000ms* |
| Risk Check | 50ms |
| Order Execution | 300ms |
| **Total** | ~2500ms |

*Nota: LLM inference es el bottleneck principal. Considerar:
- Caching de decisiones comunes
- Smaller/faster models para decisiones críticas
- Debouncing de señales

---

## QoS Requirements

### Disponibilidad
- **Target:** 99.9% uptime
- **Max downtime:** 8.76 horas/año
- **Retry logic:** 3 retries con exponential backoff

### Rate Limits
- **Weight limits:** 1200 requests/min (DEFAULT)
- **Order limits:** 1200 orders/min (OPEN)
- **Connection limits:** 5 connections/IP

### Manejo de Errores

```python
class APIError(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        
ERROR_CODES = {
    -1000: "Unknown error",
    -1001: "Disconnected",
    -1002: "Unauthorized",
    -1003: "Too many requests",
    -1010: "Rejected (custom)",
    -1020: "Invalid parameter",
    -2010: "New order rejected",
    -2011: "Cancel rejected",
}
```

---

## Recomendaciones de Implementación

### 1. WebSocket Management
```python
class WebSocketManager:
    def __init__(self):
        self.connections = {}
        self.reconnect_delay = 5
        self.max_reconnect = 10
        
    def subscribe(self, stream: str, callback: callable):
        # Implementar reconexión automática
        # Manejar heartbeats
        # Buffer de mensajes
        pass
```

### 2. Feature Cache
```python
class FeatureCache:
    def __init__(self):
        self.orderbook_cache = {}  # Redis/LMDB
        self.trades_cache = {}
        self.metrics_cache = {}
        
    def get_orderbook(self, symbol: str) -> dict:
        # Cache con TTL configurable
        # Invalidation strategy
        pass
```

### 3. Rate Limiter
```python
class RateLimiter:
    def __init__(self):
        self.window = 60  # seconds
        self.max_requests = 1200
        
    def acquire(self) -> bool:
        # Token bucket o sliding window
        pass
```

---

*Documento generado para el proyecto Aster Trading*
