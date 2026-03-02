#!/usr/bin/env python3
"""
WebSocket Manager para Aster Finance Futures
Maneja conexiones WebSocket para datos de mercado en tiempo real

Autor: Aster Trading V2
Fecha: 2026-02-24
"""

import asyncio
import json
import time
import logging
from collections import deque
from typing import Dict, List, Callable, Optional, Any, Iterable, Union
from dataclasses import dataclass, field
from enum import Enum
import threading

# Intentar importar websockets, si no está disponible usar implementación alternativa
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("WARNING: websockets package not installed. Using fallback.")

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Input validation functions for WebSocket data
def _validate_numeric(value: Any, default: float = 0.0) -> float:
    """Validate and convert value to float safely"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _validate_int(value: Any, default: int = 0) -> int:
    """Validate and convert value to int safely"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _validate_list_of_lists(value: Any) -> List[List[float]]:
    """Validate and convert value to list of lists of floats"""
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, list) and len(item) >= 2:
            try:
                result.append([float(item[0]), float(item[1])])
            except (ValueError, TypeError):
                continue
    return result


class StreamType(Enum):
    """Tipos de streams disponibles en Aster"""
    DEPTH = "depth"
    TRADE = "trade"
    AGG_TRADE = "aggTrade"
    KLINE = "kline"
    MINI_TICKER = "miniTicker"
    TICKER = "ticker"
    BOOK_TICKER = "bookTicker"
    LIQUIDATION = "liquidationOrder"


@dataclass
class StreamConfig:
    """Configuración para un stream"""
    symbol: str
    stream_type: StreamType
    interval: Optional[str] = None  # Para klines
    depth: Optional[int] = None     # Para depth (5, 10, 20, 50, 100)
    
    def get_stream_name(self) -> str:
        """Genera el nombre del stream"""
        base = f"{self.symbol.lower()}"
        
        if self.stream_type == StreamType.DEPTH and self.depth:
            # Depth stream format: symbol@depth@levels (e.g., btcusdt@depth@20)
            base += f"@depth@{self.depth}"
        elif self.stream_type == StreamType.KLINE and self.interval:
            # Kline stream format: symbol@kline_interval (e.g., btcusdt@kline_1m)
            base += f"@kline_{self.interval}"
        else:
            # Other streams: symbol@type (e.g., btcusdt@trade)
            base += f"@{self.stream_type.value}"
            
        return base


@dataclass
class OrderBookData:
    """Datos del order book"""
    symbol: str
    last_update_id: int
    bids: List[List[float]]  # [[price, qty], ...]
    asks: List[List[float]]  # [[price, qty], ...]
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    
    def get_spread_bps(self) -> float:
        """Calcula el spread en basis points"""
        if not self.bids or not self.asks:
            return 0.0
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        if best_bid <= 0:
            return 0.0
        return ((best_ask - best_bid) / best_bid) * 10000
    
    def get_imbalance(self) -> float:
        """Calcula el imbalance del order book (-1 a 1)"""
        if not self.bids or not self.asks:
            return 0.0
            
        bid_vol = sum(float(b[1]) for b in self.bids[:10])
        ask_vol = sum(float(a[1]) for a in self.asks[:10])
        
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
            
        return (bid_vol - ask_vol) / total
    
    def get_mid_price(self) -> float:
        """Precio medio"""
        if not self.bids or not self.asks:
            return 0.0
        return (self.bids[0][0] + self.asks[0][0]) / 2


@dataclass
class TradeData:
    """Datos de un trade"""
    symbol: str
    trade_id: int
    price: float
    quantity: float
    is_buyer_maker: bool
    timestamp: int


@dataclass
class KlineData:
    """Datos de una vela/kline"""
    symbol: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float
    num_trades: int
    buy_base_volume: float
    taker_buy_quote_volume: float


class AsterWebSocketManager:
    """
    Gestor de conexiones WebSocket para Aster Finance
    
    Maneja:
    - Conexiones múltiples simultáneas
    - Reconexión automática
    - Procesamiento de mensajes
    - Order book local
    """
    
    # Base URL for combined streams (more reliable)
    WS_BASE_URL = "wss://fstream.asterdex.com/stream"
    
    def __init__(self, testnet: bool = False):
        """
        Inicializa el gestor de WebSocket
        
        Args:
            testnet: Si True, usa el testnet en lugar de producción
        """
        if testnet:
            self.WS_BASE_URL = "wss://fstream5.asterdex-testnet.com"
            
        self.ws = None
        self.connected = False
        self.subscriptions: Dict[str, StreamConfig] = {}
        self.callbacks: Dict[str, Callable] = {}
        
        # Data caches
        self.order_books: Dict[str, OrderBookData] = {}
        self.recent_trades: Dict[str, List[TradeData]] = {}
        self.klines: Dict[str, List[KlineData]] = {}
        
        # Configuración de reconexión
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5
        self.reconnect_attempts = 0
        
        # Thread safety - use threading.Lock for sync methods, but don't hold during await
        self._lock = threading.Lock()
        
        # Callback de conexión
        self.on_connect_callback: Optional[Callable] = None
        self.on_disconnect_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None
        
        # Heartbeat / stale connection detection
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_pong_time: float = 0
        self._stale_timeout_seconds: float = 60.0  # Consider stale if no pong for 60s
        
        logger.info(f"Aster WebSocket Manager inicializado (URL: {self.WS_BASE_URL})")
    
    async def _start_heartbeat(self):
        """Start the heartbeat monitoring task."""
        self._running = True
        self._last_pong_time = asyncio.get_event_loop().time()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Heartbeat monitoring started")
    
    async def _stop_heartbeat(self):
        """Stop the heartbeat monitoring task."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info("Heartbeat monitoring stopped")
    
    async def _heartbeat_loop(self):
        """
        Send periodic heartbeats to detect stale connections.
        Monitors pong responses and triggers reconnection if connection is stale.
        """
        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if not self.connected or not self.ws:
                    continue
                
                current_time = asyncio.get_event_loop().time()
                time_since_pong = current_time - self._last_pong_time
                
                if time_since_pong > self._stale_timeout_seconds:
                    logger.warning(f"🔌 No pong received for {time_since_pong:.1f}s - connection may be stale")
                    # Trigger reconnection
                    await self._handle_stale_connection()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
    
    async def _handle_stale_connection(self):
        """Handle a stale WebSocket connection by attempting reconnection."""
        logger.warning("Attempting to reconnect due to stale connection...")
        self.connected = False
        
        if self.on_disconnect_callback:
            self.on_disconnect_callback()
        
        # Try to reconnect
        if self.reconnect_attempts < self.max_reconnect_attempts:
            await asyncio.sleep(self.reconnect_delay)
            success = await self.connect()
            if success:
                await self._resubscribe_all()
                logger.info("Successfully reconnected after stale connection detection")
            else:
                self.reconnect_attempts += 1
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)  # Exponential backoff
    
    async def connect(self) -> bool:
        """
        Conecta al WebSocket de Aster
        
        Returns:
            True si la conexión fue exitosa
        """
        try:
            if not WEBSOCKETS_AVAILABLE:
                logger.error("websockets package not available")
                return False
            
            logger.info(f"Conectando a {self.WS_BASE_URL}...")
            
            self.ws = await websockets.connect(
                self.WS_BASE_URL,
                ping_interval=30,
                ping_timeout=10,
                max_size=10 * 1024 * 1024  # 10MB
            )
            
            self.connected = True
            self.reconnect_attempts = 0
            
            # Start heartbeat monitoring
            await self._start_heartbeat()
            
            logger.info("✅ Conectado al WebSocket de Aster")
            
            if self.on_connect_callback:
                self.on_connect_callback()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error al conectar: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """Desconecta del WebSocket"""
        # Stop heartbeat monitoring first
        await self._stop_heartbeat()
        
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                logger.error(f"Error al desconectar: {e}")
        
        self.connected = False
        logger.info("Desconectado del WebSocket")
    
    async def subscribe(self, streams: Iterable[Union[StreamConfig, str]], callback: Callable[[Dict], None]) -> bool:
        """
        Suscribe a uno o más streams
        
        Args:
            streams: Lista de configuraciones de stream
            callback: Función a llamar cuando lleguen datos
            
        Returns:
            True si la suscripción fue exitosa
        """
        if not self.connected:
            logger.error("No conectado al WebSocket")
            return False

        try:
            normalized_streams: List[StreamConfig] = []
            stream_names: List[str] = []

            for stream in streams:
                stream_config = self._normalize_stream_input(stream)
                if not stream_config:
                    continue
                normalized_streams.append(stream_config)
                stream_names.append(stream_config.get_stream_name())

            if not normalized_streams:
                logger.warning("No valid streams provided for subscription")
                return False

            with self._lock:
                for stream_config in normalized_streams:
                    stream_name = stream_config.get_stream_name()
                    self.subscriptions[stream_name] = stream_config
                    self.callbacks[stream_name] = callback

            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": stream_names,
                "id": int(time.time() * 1000)
            }
            
            await self.ws.send(json.dumps(subscribe_msg))
            
            logger.info(f"✅ Suscrito a {len(streams)} streams: {stream_names}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error al suscribir: {e}")
            return False
    
    async def unsubscribe(self, streams: List[StreamConfig]) -> bool:
        """
        Cancela suscripción a streams
        
        Args:
            streams: Lista de configuraciones de stream
            
        Returns:
            True si fue exitoso
        """
        if not self.connected:
            return False
        
        try:
            stream_names = [s.get_stream_name() for s in streams]
            
            unsubscribe_msg = {
                "method": "UNSUBSCRIBE",
                "params": stream_names,
                "id": int(time.time() * 1000)
            }
            
            await self.ws.send(json.dumps(unsubscribe_msg))
            
            # Remover de suscripciones
            with self._lock:
                for stream in streams:
                    stream_name = stream.get_stream_name()
                    self.subscriptions.pop(stream_name, None)
                    self.callbacks.pop(stream_name, None)
            
            logger.info(f"✅ Cancelada suscripción a {len(streams)} streams")
            
            return True
            
        except Exception as e:
            logger.error(f"Error al cancelar suscripción: {e}")
            return False
    
    async def listen(self):
        """
        Escucha mensajes entrantes
        Debe correr en un loop de asyncio
        """
        if not self.connected:
            logger.error("No conectado")
            return
        
        logger.info("🎧 Escuchando mensajes...")
        
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    # Debug: log ALL messages briefly
                    if "e" in data:
                        logger.info(f"📊 WS Event: {data.get('e')} - {data.get('s')}")
                    elif "result" in data:
                        logger.info(f"📊 WS Response: result={data.get('result')}, id={data.get('id')}")
                    else:
                        logger.info(f"📊 WS Msg: {str(data)[:100]}")
                    await self._process_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON decode error: {e}")
                except Exception as e:
                    logger.error(f"Error procesando mensaje: {e}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 Conexión cerrada: {e}")
            self.connected = False
            if self.on_disconnect_callback:
                self.on_disconnect_callback()
            await self._attempt_reconnect()
    
    async def _process_message(self, data: Dict):
        """Procesa un mensaje entrante"""
        # Verificar si es un stream (combined format: {"stream": ..., "data": ...})
        if "stream" in data and "data" in data:
            stream_name = data["stream"]
            payload = data["data"]
        # Check if it's a direct kline message (raw stream format)
        elif "e" in data:
            # Direct event format - determine stream type from event type
            event_type = data.get("e", "")
            if event_type == "kline":
                stream_name = data.get("s", "").lower() + "@kline"
                payload = {"k": data.get("k", {})}
            elif event_type == "depthUpdate" or event_type == "depth":
                stream_name = data.get("s", "").lower() + "@depth"
                payload = data
            elif event_type == "trade":
                stream_name = data.get("s", "").lower() + "@trade"
                payload = data
            else:
                # Unknown event type, log for debugging
                logger.debug(f"Unknown event type: {event_type}")
                return
        else:
            # Es un mensaje de respuesta o suscripción
            # Check for subscription response
            if "result" in data or "id" in data:
                logger.debug(f"WS Response: {data}")
            return
        
        # FIX: Extract data under lock, then process outside to avoid blocking event loop
        # Determine stream type and get processing function
        process_func = None
        if "depth" in stream_name:
            process_func = self._process_depth
        elif "trade" in stream_name:
            process_func = self._process_trade
        elif "kline" in stream_name:
            process_func = self._process_kline
        elif "ticker" in stream_name:
            process_func = self._process_ticker
        elif "bookTicker" in stream_name:
            process_func = self._process_book_ticker
        
        # Process outside the lock to avoid blocking event loop during await
        if process_func:
            await process_func(stream_name, payload)
        
        # Llamar callback específico
        if stream_name in self.callbacks:
            try:
                self.callbacks[stream_name](payload)
            except Exception as e:
                logger.error(f"Error en callback {stream_name}: {e}")
    
    async def _process_depth(self, stream_name: str, data: Dict):
        """Procesa mensaje de depth/orderbook con validación"""
        symbol = data.get('s', stream_name.split('@')[0].upper())
        
        # Validate and sanitize input data
        last_update_id = _validate_int(data.get('lastUpdateId'), 0)
        bids = _validate_list_of_lists(data.get('bids', []))
        asks = _validate_list_of_lists(data.get('asks', []))
        
        order_book = OrderBookData(
            symbol=symbol,
            last_update_id=last_update_id,
            bids=bids,
            asks=asks,
            timestamp=int(time.time() * 1000)
        )
        
        self.order_books[symbol] = order_book
    
    async def _process_trade(self, stream_name: str, data: Dict):
        """Procesa mensaje de trade con validación"""
        symbol = data.get('s', stream_name.split('@')[0].upper())
        
        # Validate and sanitize input data
        trade_id = _validate_int(data.get('t'), 0)
        price = _validate_numeric(data.get('p'), 0.0)
        quantity = _validate_numeric(data.get('q'), 0.0)
        timestamp = _validate_int(data.get('T'), 0)
        
        # Validate is_buyer_maker
        is_buyer_maker = bool(data.get('m', False))
        
        trade = TradeData(
            symbol=symbol,
            trade_id=trade_id,
            price=price,
            quantity=quantity,
            is_buyer_maker=is_buyer_maker,
            timestamp=timestamp
        )
        
        # Use deque with maxlen for automatic bounded memory management
        if symbol not in self.recent_trades:
            self.recent_trades[symbol] = deque(maxlen=1000)
        
        self.recent_trades[symbol].append(trade)
    
    async def _process_kline(self, stream_name: str, data: Dict):
        """Procesa mensaje de kline con validación"""
        kline_data = data.get('k', {})
        if not kline_data:
            return
            
        symbol = kline_data.get('s', stream_name.split('@')[0].upper())
        
        # Validate and sanitize all numeric fields
        kline = KlineData(
            symbol=symbol,
            open_time=_validate_int(kline_data.get('t'), 0),
            open=_validate_numeric(kline_data.get('o'), 0.0),
            high=_validate_numeric(kline_data.get('h'), 0.0),
            low=_validate_numeric(kline_data.get('l'), 0.0),
            close=_validate_numeric(kline_data.get('c'), 0.0),
            volume=_validate_numeric(kline_data.get('v'), 0.0),
            close_time=_validate_int(kline_data.get('T'), 0),
            quote_volume=_validate_numeric(kline_data.get('q'), 0.0),
            num_trades=_validate_int(kline_data.get('n'), 0),
            buy_base_volume=_validate_numeric(kline_data.get('V'), 0.0),
            taker_buy_quote_volume=_validate_numeric(kline_data.get('Q'), 0.0)
        )
        
        # Use deque with maxlen for automatic bounded memory management
        if symbol not in self.klines:
            self.klines[symbol] = deque(maxlen=500)
        
        self.klines[symbol].append(kline)
    
    async def _process_ticker(self, stream_name: str, data: Dict):
        """Procesa mensaje de ticker"""
        # Por ahora solo logging
        logger.debug(f"Ticker: {data}")
    
    async def _process_book_ticker(self, stream_name: str, data: Dict):
        """Procesa mensaje de book ticker"""
        # Por ahora solo logging
        logger.debug(f"Book Ticker: {data}")
    
    async def _attempt_reconnect(self):
        """Intenta reconectar automáticamente con reconexión robusta"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("❌ Máximo de intentos de reconexión alcanzado")
            if self.on_error_callback:
                self.on_error_callback("Max reconnect attempts reached")
            return
        
        self.reconnect_attempts += 1
        # Exponential backoff with jitter: delay * 2^(attempts-1) + random(0-5)
        import random
        delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1)) + random.uniform(0, 5)
        
        logger.info(f"🔄 Intentando reconectar en {delay:.1f}s (intento {self.reconnect_attempts}/{self.max_reconnect_attempts})...")
        
        await asyncio.sleep(delay)
        
        # Intentar reconectar
        if await self.connect():
            logger.info("✅ Reconectado, restaurando suscripciones...")
            
            await self._resubscribe_all()
            
            # Reiniciar escucha
            asyncio.create_task(self.listen())
            logger.info("✅ Reconexión completa")
        else:
            logger.error("❌ Reconexión fallida, intentando de nuevo...")
            # Auto-retry: recursive call to attempt reconnect again
            await self._attempt_reconnect()
    
    # =======================
    # MÉTODOS PÚBLICOS
    # =======================
    
    def get_order_book(self, symbol: str) -> Optional[OrderBookData]:
        """Obtiene el order book actual de un símbolo"""
        with self._lock:
            return self.order_books.get(symbol)
    
    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[TradeData]:
        """Obtiene los trades recientes de un símbolo"""
        with self._lock:
            trades = self.recent_trades.get(symbol)
            if not trades:
                return []
            if isinstance(trades, deque):
                trades_list = list(trades)
            else:
                trades_list = list(trades)
        return trades_list[-limit:] if len(trades_list) > limit else trades_list
    
    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> List[KlineData]:
        """Obtiene los klines recientes de un símbolo"""
        with self._lock:
            klines = self.klines.get(symbol, [])
            # Filtrar por intervalo
            filtered = [k for k in klines if self._get_interval_name(k.close_time - k.open_time) == interval]
            return filtered[-limit:] if len(filtered) > limit else filtered
    
    def _get_interval_name(self, duration_ms: int) -> str:
        """Convierte duración en ms a nombre de intervalo"""
        minutes = duration_ms // 60000
        if minutes == 1:
            return "1m"
        elif minutes == 5:
            return "5m"
        elif minutes == 15:
            return "15m"
        elif minutes == 30:
            return "30m"
        elif minutes == 60:
            return "1h"
        elif minutes == 240:
            return "4h"
        elif minutes == 1440:
            return "1d"
        return "1m"
    
    def get_buy_sell_ratio(self, symbol: str, window: int = 100) -> float:
        """
        Calcula el ratio de compras/ventas en los últimos trades
        
        Returns:
            Ratio > 1 = más compras, < 1 = más ventas
        """
        trades = self.get_recent_trades(symbol, window)
        
        if not trades:
            return 1.0
        
        buy_volume = sum(t.quantity for t in trades if not t.is_buyer_maker)
        sell_volume = sum(t.quantity for t in trades if t.is_buyer_maker)
        
        if sell_volume <= 0:
            return 2.0  # Solo compras
        if buy_volume <= 0:
            return 0.5  # Solo ventas
            
        return buy_volume / sell_volume

    def _normalize_stream_input(self, stream: Union[StreamConfig, str]) -> Optional[StreamConfig]:
        """Ensure any stream input is converted into a StreamConfig."""
        if isinstance(stream, StreamConfig):
            return stream

        if not stream:
            return None

        stream_str = stream.strip().lower()
        if not stream_str:
            return None

        parts = stream_str.split('@')
        symbol = parts[0].upper()
        suffix_parts = parts[1:] if len(parts) > 1 else []

        def _match_stream_type(value: str) -> StreamType:
            value_lower = value.lower()
            for stream_type in StreamType:
                if stream_type.value.lower() == value_lower:
                    return stream_type
            return StreamType.TRADE

        stream_type = StreamType.TRADE
        interval = None
        depth = None

        if suffix_parts:
            primary = suffix_parts[0]
            if primary.startswith("kline"):
                stream_type = StreamType.KLINE
                if "_" in primary:
                    interval = primary.split("_", 1)[1]
                elif len(suffix_parts) > 1:
                    interval = suffix_parts[1]
                interval = interval or "1m"
            elif primary == "depth":
                stream_type = StreamType.DEPTH
                if len(suffix_parts) > 1:
                    try:
                        depth = int(suffix_parts[1])
                    except ValueError:
                        depth = None
            else:
                stream_type = _match_stream_type(primary)
        
        return StreamConfig(
            symbol=symbol,
            stream_type=stream_type,
            interval=interval,
            depth=depth
        )

    def _group_streams_by_callback(self) -> Dict[Callable, List[StreamConfig]]:
        """Group current subscriptions by their callback for efficient resubscribe."""
        grouped: Dict[Callable, List[StreamConfig]] = {}
        with self._lock:
            for stream_name, stream_config in self.subscriptions.items():
                callback = self.callbacks.get(stream_name)
                if not callback:
                    continue
                grouped.setdefault(callback, []).append(stream_config)
        return grouped

    async def _resubscribe_all(self):
        """Resubscribe every stream grouped by callback, preserving original intents."""
        if not self.connected:
            logger.warning("Cannot resubscribe because WebSocket is disconnected")
            return

        callback_streams = self._group_streams_by_callback()
        for callback, streams in callback_streams.items():
            if not streams:
                continue
            await self.subscribe(streams, callback)


# =======================
# EJEMPLO DE USO
# =======================
async def example():
    """Ejemplo de uso del WebSocket Manager"""
    
    # Crear manager
    wsm = AsterWebSocketManager()
    
    # Definir callback
    def on_depth(data):
        print(f"Depth update: {data}")
    
    # Conectar
    if await wsm.connect():
        # Suscribirse a streams
        streams = [
            StreamConfig(symbol="ASTERUSDT", stream_type=StreamType.DEPTH, depth=20),
            StreamConfig(symbol="ASTERUSDT", stream_type=StreamType.TRADE),
            StreamConfig(symbol="ASTERUSDT", stream_type=StreamType.KLINE, interval="1m"),
        ]
        
        await wsm.subscribe(streams, on_depth)
        
        # Escuchar por 30 segundos
        await asyncio.sleep(30)
        
        # Obtener datos
        ob = wsm.get_order_book("ASTERUSDT")
        if ob:
            print(f"Spread: {ob.get_spread_bps():.2f} bps")
            print(f"Imbalance: {ob.get_imbalance():.3f}")
        
        ratio = wsm.get_buy_sell_ratio("ASTERUSDT")
        print(f"Buy/Sell ratio: {ratio:.2f}")
        
        # Desconectar
        await wsm.disconnect()


if __name__ == "__main__":
    asyncio.run(example())
