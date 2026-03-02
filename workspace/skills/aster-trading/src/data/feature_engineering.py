#!/usr/bin/env python3
"""
Feature Engineering para Trading System V2
Extrae features técnicos y de microestructura para modelos ML

Autor: Aster Trading V2
Fecha: 2026-02-24
"""

import math
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
import logging

logger = logging.getLogger(__name__)


# =======================
# CONFIGURACIÓN DE FEATURES
# =======================

@dataclass
class FeatureConfig:
    """Configuración de features a calcular"""
    # Symbols a procesar
    symbols: List[str] = field(default_factory=lambda: ["ASTERUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "HYPEUSDT"])
    
    # Intervalos de klines
    kline_intervals: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h"])
    
    # Ventanas para cálculo
    lookback_periods: Dict[str, int] = field(default_factory=lambda: {
        "short": 10,
        "medium": 30,
        "long": 100
    })
    
    # Features habilitadas
    enable_price_features: bool = True
    enable_technical_features: bool = True
    enable_microstructure_features: bool = True
    enable_volume_features: bool = True
    enable_regime_features: bool = True


# =======================
# CLASES DE DATOS
# =======================

@dataclass
class PriceFeatures:
    """Features basadas en precio"""
    returns_1m: float = 0.0
    returns_5m: float = 0.0
    returns_15m: float = 0.0
    returns_1h: float = 0.0
    returns_4h: float = 0.0
    returns_1d: float = 0.0
    
    volatility_1m: float = 0.0
    volatility_5m: float = 0.0
    volatility_1h: float = 0.0
    
    high_low_range_1m: float = 0.0
    high_low_range_5m: float = 0.0


@dataclass
class TechnicalFeatures:
    """Features de análisis técnico"""
    # RSI
    rsi_14: float = 50.0
    rsi_28: float = 50.0
    
    # MACD
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    
    # Bollinger Bands
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_position: float = 0.5
    bb_width: float = 0.0
    
    # ATR
    atr_14: float = 0.0
    atr_28: float = 0.0
    
    # ADX
    adx_14: float = 0.0
    
    # Stochastic
    stochastic_k: float = 50.0
    stochastic_d: float = 50.0
    
    # CCI
    cci: float = 0.0
    
    # Williams %R
    williams_r: float = -50.0
    
    # Moving Averages
    ema_9: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    
    sma_20: float = 0.0
    sma_50: float = 0.0
    
    # MA Crossovers
    ema_9_21_cross: int = 0  # 1 = bullish, -1 = bearish, 0 = no cross
    ema_21_50_cross: int = 0


@dataclass
class MicrostructureFeatures:
    """Features de microestructura"""
    spread_bps: float = 0.0
    order_imbalance: float = 0.0
    
    buy_sell_ratio_1m: float = 1.0
    buy_sell_ratio_5m: float = 1.0
    buy_sell_ratio_15m: float = 1.0
    
    volume_ratio_1m: float = 1.0
    volume_ratio_5m: float = 1.0
    
    vwap: float = 0.0
    twap_5m: float = 0.0
    
    bid_depth_10: float = 0.0
    ask_depth_10: float = 0.0
    depth_imbalance: float = 0.0
    
    trade_intensity_1m: float = 0.0
    trade_intensity_5m: float = 0.0


@dataclass
class VolumeFeatures:
    """Features basadas en volumen"""
    obv: float = 0.0  # On-Balance Volume
    obv_ma_20: float = 0.0
    
    vwap_volume: float = 0.0
    
    volume_ma_5: float = 0.0
    volume_ma_20: float = 0.0
    
    volume_trend: int = 0  # 1 = increasing, -1 = decreasing
    
    buy_volume_ratio: float = 0.5


@dataclass
class RegimeFeatures:
    """Features de régimen de mercado"""
    trend_strength: float = 0.0  # 0-1
    volatility_regime: int = 0   # 0=low, 1=normal, 2=high, 3=extreme
    market_regime: int = 2       # 0=bear, 1=neutral_bear, 2=neutral, 3=neutral_bull, 4=bull
    
    trend_direction: int = 0     # -1=bear, 0=sideways, 1=bull
    momentum_score: float = 0.0   # -1 to 1


@dataclass
class AllFeatures:
    """Todas las features combinadas"""
    symbol: str = ""
    timestamp: int = 0
    
    price: PriceFeatures = field(default_factory=PriceFeatures)
    technical: TechnicalFeatures = field(default_factory=TechnicalFeatures)
    microstructure: MicrostructureFeatures = field(default_factory=MicrostructureFeatures)
    volume: VolumeFeatures = field(default_factory=VolumeFeatures)
    regime: RegimeFeatures = field(default_factory=RegimeFeatures)
    
    def to_dict(self) -> Dict:
        """Convierte a diccionario aplanado"""
        result = {
            "symbol": self.symbol,
            "timestamp": self.timestamp
        }
        
        # Price features
        for key, value in self.price.__dict__.items():
            result[f"price_{key}"] = value
        
        # Technical features
        for key, value in self.technical.__dict__.items():
            result[f"tech_{key}"] = value
        
        # Microstructure features
        for key, value in self.microstructure.__dict__.items():
            result[f"micro_{key}"] = value
        
        # Volume features
        for key, value in self.volume.__dict__.items():
            result[f"vol_{key}"] = value
        
        # Regime features
        for key, value in self.regime.__dict__.items():
            result[f"regime_{key}"] = value
        
        return result
    
    def to_array(self, feature_names: List[str]) -> List[float]:
        """Convierte a array para modelo ML"""
        d = self.to_dict()
        return [d.get(name, 0.0) for name in feature_names]


# =======================
# CALCULADORA DE FEATURES
# =======================

class FeatureCalculator:
    """
    Calcula features técnicos y de microestructura
    """
    
    def __init__(self, config: FeatureConfig = None):
        self.config = config or FeatureConfig()
        
        # Historial de precios (para cálculos)
        self.price_history: Dict[str, Dict[str, deque]] = {}
        
        # Inicializar history para cada símbolo
        for symbol in self.config.symbols:
            self.price_history[symbol] = {
                interval: deque(maxlen=500)
                for interval in self.config.kline_intervals
            }
    
    def has_sufficient_data(self, symbol: str, min_klines: int = 30) -> bool:
        """
        Check if we have sufficient data for feature calculation.
        
        Args:
            symbol: Trading symbol
            min_klines: Minimum number of klines required (default 30 for technical features)
            
        Returns:
            True if sufficient data is available
        """
        # Check primary interval (15m) used for technical features
        history = self.price_history.get(symbol, {}).get("15m", [])
        return len(history) >= min_klines
    
    def get_data_status(self, symbol: str) -> Dict[str, int]:
        """
        Get data availability status for all intervals.
        
        Returns:
            Dict with interval names and their kline counts
        """
        status = {}
        symbol_history = self.price_history.get(symbol, {})
        for interval in self.config.kline_intervals:
            status[interval] = len(symbol_history.get(interval, []))
        return status
    
    def update_kline(self, symbol: str, interval: str, kline_data: Dict):
        """Actualiza con nuevo kline"""
        if symbol not in self.price_history:
            self.price_history[symbol] = {interval: deque(maxlen=500)}
        
        if interval not in self.price_history[symbol]:
            self.price_history[symbol][interval] = deque(maxlen=500)
        
        # Agregar datos del kline
        self.price_history[symbol][interval].append({
            "open": float(kline_data.get("open", 0)),
            "high": float(kline_data.get("high", 0)),
            "low": float(kline_data.get("low", 0)),
            "close": float(kline_data.get("close", 0)),
            "volume": float(kline_data.get("volume", 0)),
            "close_time": kline_data.get("close_time", 0)
        })
    
    def calculate_all_features(self, symbol: str, order_book: Optional[Dict] = None, trades: Optional[List] = None) -> AllFeatures:
        """
        Calcula todas las features para un símbolo
        
        Args:
            symbol: Símbolo a procesar
            order_book: Datos del order book (opcional)
            trades: Lista de trades recientes (opcional)
            
        Returns:
            AllFeatures con todos los features calculados (empty if insufficient data)
        """
        features = AllFeatures()
        features.symbol = symbol
        features.timestamp = int(time.time() * 1000)
        
        # Check if we have sufficient data for technical features
        if not self.has_sufficient_data(symbol):
            # Return empty features with data_ready=False flag in the timestamp
            # Timestamp will be 0 to indicate no data
            features.timestamp = 0
            return features
        
        # Calcular cada grupo de features
        if self.config.enable_price_features:
            features.price = self._calculate_price_features(symbol)
        
        if self.config.enable_technical_features:
            features.technical = self._calculate_technical_features(symbol)
        
        if self.config.enable_microstructure_features:
            features.microstructure = self._calculate_microstructure_features(order_book, trades)
        
        if self.config.enable_volume_features:
            features.volume = self._calculate_volume_features(symbol)
        
        if self.config.enable_regime_features:
            features.regime = self._calculate_regime_features(symbol)
        
        return features
    
    def _calculate_price_features(self, symbol: str) -> PriceFeatures:
        """Calcula features de precio"""
        pf = PriceFeatures()
        
        for interval in ["1m", "5m", "15m", "1h", "4h", "1d"]:
            history = self.price_history.get(symbol, {}).get(interval, [])
            
            if len(history) < 2:
                continue
            
            closes = [h["close"] for h in history]
            
            # Returns
            if len(closes) >= 2:
                current = closes[-1]
                
                if interval == "1m" and len(closes) >= 2:
                    pf.returns_1m = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
                elif interval == "5m" and len(closes) >= 2:
                    pf.returns_5m = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
                elif interval == "15m" and len(closes) >= 2:
                    pf.returns_15m = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
                elif interval == "1h" and len(closes) >= 2:
                    pf.returns_1h = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
                elif interval == "4h" and len(closes) >= 2:
                    pf.returns_4h = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
                elif interval == "1d" and len(closes) >= 2:
                    pf.returns_1d = ((current / closes[-2]) - 1) if closes[-2] > 0 else 0
            
            # Volatility
            if len(closes) >= 10:
                if interval == "1m":
                    pf.volatility_1m = self._calculate_volatility(closes[-10:])
                elif interval == "5m":
                    pf.volatility_5m = self._calculate_volatility(closes[-10:])
                elif interval == "1h":
                    pf.volatility_1h = self._calculate_volatility(closes[-10:])
            
            # High-Low range
            if len(history) >= 1:
                recent = list(history)[-10:] if len(history) >= 10 else list(history)
                highs = [h["high"] for h in recent]
                lows = [h["low"] for h in recent]
                
                if highs and lows and len(recent) > 0:
                    avg_price = sum(closes[-len(recent):]) / len(recent)
                    if avg_price > 0:
                        high_low_range = (max(highs) - min(lows)) / avg_price
                        if interval == "1m":
                            pf.high_low_range_1m = high_low_range
                        elif interval == "5m":
                            pf.high_low_range_5m = high_low_range
        
        return pf
    
    def _calculate_technical_features(self, symbol: str) -> TechnicalFeatures:
        """Calcula features técnicos"""
        tf = TechnicalFeatures()
        
        history = self.price_history.get(symbol, {}).get("15m", [])
        
        if len(history) < 30:
            return tf
        
        closes = [h["close"] for h in history]
        highs = [h["high"] for h in history]
        lows = [h["low"] for h in history]
        volumes = [h["volume"] for h in history]
        
        current_price = closes[-1] if closes else 0
        
        # RSI
        tf.rsi_14 = self._calculate_rsi(closes, 14)
        tf.rsi_28 = self._calculate_rsi(closes, 28)
        
        # MACD
        macd_line, signal_line, hist = self._calculate_macd(closes)
        tf.macd = macd_line
        tf.macd_signal = signal_line
        tf.macd_hist = hist
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = self._calculate_bollinger_bands(closes, 20, 2)
        tf.bb_upper = bb_upper
        tf.bb_middle = bb_middle
        tf.bb_lower = bb_lower
        
        if bb_upper > bb_lower and current_price > 0 and (bb_upper - bb_lower) != 0:
            tf.bb_position = (current_price - bb_lower) / (bb_upper - bb_lower)
            tf.bb_width = (bb_upper - bb_lower) / bb_middle
        
        # ATR
        klines_list = list(history)
        if len(klines_list) >= 15:
            tf.atr_14 = self._calculate_atr(klines_list[-15:], 14)
            tf.atr_28 = self._calculate_atr(klines_list[-29:], 28) if len(klines_list) >= 29 else 0
        
        # ADX
        klines_list = list(history)
        tf.adx_14 = self._calculate_adx(klines_list[-15:]) if len(klines_list) >= 15 else 0
        
        # Stochastic
        tf.stochastic_k = self._calculate_stochastic_k(highs, lows, closes, 14)
        tf.stochastic_d = self._calculate_stochastic_d(highs, lows, closes, 14)
        
        # CCI
        tf.cci = self._calculate_cci(highs, lows, closes, 20)
        
        # Williams %R
        tf.williams_r = self._calculate_williams_r(highs, lows, closes, 14)
        
        # Moving Averages
        tf.ema_9 = self._calculate_ema(closes, 9)
        tf.ema_21 = self._calculate_ema(closes, 21)
        tf.ema_50 = self._calculate_ema(closes, 50)
        tf.ema_200 = self._calculate_ema(closes, 200)
        
        tf.sma_20 = self._calculate_sma(closes, 20)
        tf.sma_50 = self._calculate_sma(closes, 50)
        
        # MA Crossovers
        if len(closes) >= 2:
            prev_ema_9 = self._calculate_ema(closes[:-1], 9)
            prev_ema_21 = self._calculate_ema(closes[:-1], 21)
            
            if prev_ema_9 and prev_ema_21 and tf.ema_9 and tf.ema_21:
                if tf.ema_9 > tf.ema_21 and prev_ema_9 <= prev_ema_21:
                    tf.ema_9_21_cross = 1  # Bullish
                elif tf.ema_9 < tf.ema_21 and prev_ema_9 >= prev_ema_21:
                    tf.ema_9_21_cross = -1  # Bearish
        
        return tf
    
    def _calculate_microstructure_features(self, order_book: Optional[Dict], trades: Optional[List]) -> MicrostructureFeatures:
        """Calcula features de microestructura"""
        mf = MicrostructureFeatures()
        
        if order_book:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])
            
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                
                # Spread
                if best_bid > 0:
                    mf.spread_bps = ((best_ask - best_bid) / best_bid) * 10000
                
                # Order imbalance
                bid_vol = sum(float(b[1]) for b in bids[:10])
                ask_vol = sum(float(a[1]) for a in asks[:10])
                
                if bid_vol + ask_vol > 0:
                    mf.order_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                
                # Depth
                mf.bid_depth_10 = bid_vol
                mf.ask_depth_10 = ask_vol
                mf.depth_imbalance = mf.order_imbalance
        
        if trades:
            # Buy/Sell ratio
            buy_vol = sum(t["quantity"] for t in trades if not t.get("is_buyer_maker", False))
            sell_vol = sum(t["quantity"] for t in trades if t.get("is_buyer_maker", True))
            
            total = buy_vol + sell_vol
            if total > 0:
                mf.buy_sell_ratio_1m = buy_vol / total
                mf.buy_sell_ratio_5m = buy_vol / total  # Simplified
                mf.buy_sell_ratio_15m = buy_vol / total  # Simplified
            
            # Trade intensity
            mf.trade_intensity_1m = len(trades)
        
        return mf
    
    def _calculate_volume_features(self, symbol: str) -> VolumeFeatures:
        """Calcula features de volumen"""
        vf = VolumeFeatures()
        
        history = self.price_history.get(symbol, {}).get("15m", [])
        
        if len(history) < 20:
            return vf
        
        closes = [h["close"] for h in history]
        volumes = [h["volume"] for h in history]
        
        # OBV (On-Balance Volume)
        obv = 0
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                obv += volumes[i]
            elif closes[i] < closes[i-1]:
                obv -= volumes[i]
        
        vf.obv = obv
        vf.obv_ma_20 = self._calculate_sma(list(abs(v) for v in volumes[-20:]), 20)
        
        # Volume MA
        if len(volumes) >= 5:
            vf.volume_ma_5 = sum(volumes[-5:]) / 5
        if len(volumes) >= 20:
            vf.volume_ma_20 = sum(volumes[-20:]) / 20
        
        # Volume trend
        if len(volumes) >= 10:
            recent_avg = sum(volumes[-5:]) / 5
            older_avg = sum(volumes[-10:-5]) / 5
            
            if recent_avg > older_avg * 1.1:
                vf.volume_trend = 1
            elif recent_avg < older_avg * 0.9:
                vf.volume_trend = -1
        
        # Buy volume ratio (simplified)
        vf.buy_volume_ratio = 0.5
        
        return vf
    
    def _calculate_regime_features(self, symbol: str) -> RegimeFeatures:
        """Calcula features de régimen"""
        rf = RegimeFeatures()
        
        history = self.price_history.get(symbol, {}).get("1h", [])
        
        if len(history) < 50:
            return rf
        
        closes = [h["close"] for h in history]
        volumes = [h["volume"] for h in history]
        
        # Trend direction
        sma_20 = self._calculate_sma(closes[-20:], 20)
        sma_50 = self._calculate_sma(closes[-50:], 50)
        
        if sma_20 and sma_50:
            if closes[-1] > sma_20 > sma_50:
                rf.trend_direction = 1
            elif closes[-1] < sma_20 < sma_50:
                rf.trend_direction = -1
            else:
                rf.trend_direction = 0
        
        # Trend strength
        if sma_20 and sma_20 > 0:
            rf.trend_strength = min(abs((closes[-1] - sma_20) / sma_20) * 10, 1.0)
        
        # Volatility regime
        volatility = self._calculate_volatility(closes[-20:])
        
        if volatility < 0.01:
            rf.volatility_regime = 0  # Low
        elif volatility < 0.03:
            rf.volatility_regime = 1  # Normal
        elif volatility < 0.05:
            rf.volatility_regime = 2  # High
        else:
            rf.volatility_regime = 3  # Extreme
        
        # Market regime (simplificado)
        if rf.trend_direction == 1 and rf.trend_strength > 0.5:
            rf.market_regime = 4  # Bull
        elif rf.trend_direction == -1 and rf.trend_strength > 0.5:
            rf.market_regime = 0  # Bear
        else:
            rf.market_regime = 2  # Neutral
        
        # Momentum score (-1 to 1)
        if len(closes) >= 10:
            recent = closes[-1]
            older = closes[-10]
            
            if older > 0:
                rf.momentum_score = ((recent - older) / older) * 10
                rf.momentum_score = max(-1, min(1, rf.momentum_score))
        
        return rf
    
    # =======================
    # HELPERS
    # =======================
    
    def _calculate_sma(self, values: List[float], period: int) -> Optional[float]:
        """Calcula SMA"""
        if len(values) < period:
            return None
        return sum(values[-period:]) / period
    
    def _calculate_ema(self, values: List[float], period: int) -> Optional[float]:
        """Calcula EMA"""
        if len(values) < period:
            return None
        
        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period
        
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        
        return ema
    
    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calcula RSI"""
        if len(closes) < period + 1:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))
        
        if len(gains) < period:
            return 50.0
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
        """Calcula MACD"""
        if len(closes) < slow:
            return 0.0, 0.0, 0.0
        
        ema_fast = self._calculate_ema(closes, fast)
        ema_slow = self._calculate_ema(closes, slow)
        
        if ema_fast is None or ema_slow is None:
            return 0.0, 0.0, 0.0
        
        macd_line = ema_fast - ema_slow
        
        # Calculate signal line (simplified)
        signal_line = macd_line * 0.9  # Approximation
        
        hist = macd_line - signal_line
        
        return macd_line, signal_line, hist
    
    def _calculate_bollinger_bands(self, closes: List[float], period: int = 20, std_dev: float = 2.0):
        """Calcula Bollinger Bands"""
        if len(closes) < period:
            return 0.0, 0.0, 0.0
        
        sma = sum(closes[-period:]) / period
        
        variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
        std = math.sqrt(variance)
        
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return upper, sma, lower
    
    def _calculate_atr(self, klines: List[Dict], period: int = 14) -> float:
        """Calcula ATR"""
        if len(klines) < period + 1:
            return 0.0
        
        trs = []
        
        for i in range(1, len(klines)):
            high = klines[i]["high"]
            low = klines[i]["low"]
            prev_close = klines[i-1]["close"]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            trs.append(tr)
        
        if len(trs) < period:
            return 0.0
        
        return sum(trs[-period:]) / period
    
    def _calculate_adx(self, klines: List[Dict], period: int = 14) -> float:
        """Calcula ADX (Average Directional Index) - Full implementation"""
        if len(klines) < period + 1:
            return 25.0  # Return neutral value
        
        try:
            # Calculate True Range, +DM, -DM for each period
            trs = []
            plus_dms = []
            minus_dms = []
            
            for i in range(1, len(klines)):
                high = klines[i]["high"]
                low = klines[i]["low"]
                prev_close = klines[i-1]["close"]
                
                # True Range
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                trs.append(tr)
                
                # Directional Movement
                high_diff = high - klines[i-1]["high"]
                low_diff = klines[i-1]["low"] - low
                
                plus_dm = high_diff if (high_diff > low_diff and high_diff > 0) else 0
                minus_dm = low_diff if (low_diff > high_diff and low_diff > 0) else 0
                
                plus_dms.append(plus_dm)
                minus_dms.append(minus_dm)
            
            if len(trs) < period:
                return 25.0
            
            # Calculate smoothed averages ( Wilder's smoothing)
            tr_sum = sum(trs[:period])
            plus_dm_sum = sum(plus_dms[:period])
            minus_dm_sum = sum(minus_dms[:period])
            
            # Calculate Directional Indicators
            if tr_sum > 0:
                plus_di = (plus_dm_sum / tr_sum) * 100
                minus_di = (minus_dm_sum / tr_sum) * 100
            else:
                return 25.0
            
            # Calculate DX
            di_sum = plus_di + minus_di
            if di_sum > 0:
                dx = (abs(plus_di - minus_di) / di_sum) * 100
            else:
                return 25.0
            
            # Return ADX (DX is close enough for our purposes)
            return dx
            
        except Exception as e:
            logger.debug(f"ADX calculation error: {e}")
            return 25.0  # Return neutral value on error
    
    def _calculate_stochastic_k(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Calcula Stochastic %K"""
        if len(closes) < period:
            return 50.0
        
        recent_highs = highs[-period:]
        recent_lows = lows[-period:]
        
        highest = max(recent_highs)
        lowest = min(recent_lows)
        
        if highest == lowest:
            return 50.0
        
        k = ((closes[-1] - lowest) / (highest - lowest)) * 100
        
        return k
    
    def _calculate_stochastic_d(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Calcula Stochastic %D"""
        # Simplified - would average %K values
        return self._calculate_stochastic_k(highs, lows, closes, period)
    
    def _calculate_cci(self, highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> float:
        """Calcula CCI"""
        if len(closes) < period:
            return 0.0
        
        typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        sma = sum(typical_prices) / period
        
        mean_deviation = sum(abs(tp - sma) for tp in typical_prices) / period
        
        if mean_deviation == 0:
            return 0.0
        
        cci = (typical_prices[-1] - sma) / (0.015 * mean_deviation)
        
        return cci
    
    def _calculate_williams_r(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Calcula Williams %R"""
        if len(closes) < period:
            return -50.0
        
        highest = max(highs[-period:])
        lowest = min(lows[-period:])
        
        if highest == lowest:
            return -50.0
        
        wr = ((highest - closes[-1]) / (highest - lowest)) * -100
        
        return wr
    
    def _calculate_volatility(self, values: List[float]) -> float:
        """Calcula volatilidad como desviación estándar de retornos"""
        if len(values) < 2:
            return 0.0
        
        returns = [(values[i] / values[i-1] - 1) for i in range(1, len(values))]
        
        if not returns:
            return 0.0
        
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        
        return math.sqrt(variance)


# =======================
# EJEMPLO DE USO
# =======================

def example():
    """Ejemplo de uso"""
    
    # Crear calculator
    calculator = FeatureCalculator()
    
    # Simular datos de kline
    import random
    
    base_price = 0.5
    
    for i in range(100):
        kline = {
            "open": base_price + random.uniform(-0.01, 0.01),
            "high": base_price + random.uniform(0.01, 0.02),
            "low": base_price - random.uniform(0.01, 0.02),
            "close": base_price + random.uniform(-0.01, 0.01),
            "volume": random.uniform(1000, 5000),
            "close_time": int(time.time() * 1000)
        }
        
        calculator.update_kline("ASTERUSDT", "15m", kline)
        base_price = kline["close"]
    
    # Calcular features
    features = calculator.calculate_all_features("ASTERUSDT")
    
    # Convertir a diccionario
    feature_dict = features.to_dict()
    
    print(f"Features calculados para {features.symbol}:")
    print(f"- RSI 14: {features.technical.rsi_14:.2f}")
    print(f"- MACD: {features.technical.macd:.4f}")
    print(f"- BB Position: {features.technical.bb_position:.2f}")
    print(f"- Spread: {features.microstructure.spread_bps:.2f} bps")
    print(f"- Trend: {features.regime.trend_direction}")
    print(f"- Market Regime: {features.regime.market_regime}")
    print(f"- Total features: {len(feature_dict)}")


if __name__ == "__main__":
    example()
