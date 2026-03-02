#!/usr/bin/env python3
"""
Train ML Model Script
=====================
Descarga datos históricos y entrena el modelo XGBoost para trading.
Usa las 60 features que espera el sistema.
"""

import os
import sys
import json
import time
import argparse
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Paths
SKILL_DIR = Path("/Users/FIRMAS/.openclaw/workspace/skills/aster-trading")
MODELS_DIR = SKILL_DIR / "src" / "models"
DATA_DIR = SKILL_DIR / "data"

# Config
API_BASE = os.getenv("ASTER_FAPI_BASE", "https://fapi.asterdex.com")
API_KEY = os.getenv("ASTER_API_KEY", "")
API_SECRET = os.getenv("ASTER_API_SECRET", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Las 60 features que espera el sistema
FEATURE_NAMES = [
    # Price features (11)
    "price_returns_1m", "price_returns_5m", "price_returns_15m", "price_returns_1h",
    "price_volatility_1m", "price_volatility_5m", "price_volatility_1h",
    "price_high_low_range_1m", "price_high_low_range_5m",
    "price_returns_4h", "price_returns_1d",
    
    # Technical features (25)
    "tech_rsi_14", "tech_rsi_28", "tech_macd", "tech_macd_signal", "tech_macd_hist",
    "tech_bb_upper", "tech_bb_middle", "tech_bb_lower", "tech_bb_position", "tech_bb_width",
    "tech_atr_14", "tech_atr_28", "tech_adx_14",
    "tech_stochastic_k", "tech_stochastic_d", "tech_cci", "tech_williams_r",
    "tech_ema_9", "tech_ema_21", "tech_ema_50", "tech_ema_200",
    "tech_sma_20", "tech_sma_50", "tech_ema_9_21_cross", "tech_ema_21_50_cross",
    
    # Microstructure features (12)
    "micro_spread_bps", "micro_order_imbalance",
    "micro_buy_sell_ratio_1m", "micro_buy_sell_ratio_5m", "micro_buy_sell_ratio_15m",
    "micro_volume_ratio_1m", "micro_volume_ratio_5m",
    "micro_vwap", "micro_twap_5m",
    "micro_bid_depth_10", "micro_ask_depth_10", "micro_depth_imbalance",
    
    # Volume features (7)
    "vol_obv", "vol_obv_ma_20", "vol_vwap_volume",
    "vol_volume_ma_5", "vol_volume_ma_20", "vol_volume_trend", "vol_buy_volume_ratio",
    
    # Regime features (5)
    "regime_trend_strength", "regime_volatility_regime", "regime_market_regime",
    "regime_trend_direction", "regime_momentum_score",
]


def get_klines(symbol: str, interval: str = "15m", limit: int = 1000, start_time: int = None, end_time: int = None) -> list:
    """Descarga klines de Aster API"""
    url = f"{API_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Error: {e}")
        return []


def calculate_all_features(df: pd.DataFrame) -> dict:
    """Calcula las 60 features que espera el sistema"""
    features = {}
    
    # Precio
    close = df['close'].astype(float)
    open_p = df['open'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df['volume'].astype(float)
    
    # Price features (11)
    features['price_returns_1m'] = close.pct_change(1).iloc[-1] if len(close) > 1 else 0
    features['price_returns_5m'] = close.pct_change(5).iloc[-1] if len(close) > 5 else 0
    features['price_returns_15m'] = close.pct_change(15).iloc[-1] if len(close) > 15 else 0
    features['price_returns_1h'] = close.pct_change(60).iloc[-1] if len(close) > 60 else 0
    features['price_returns_4h'] = close.pct_change(240).iloc[-1] if len(close) > 240 else 0
    features['price_returns_1d'] = close.pct_change(1440).iloc[-1] if len(close) > 1440 else 0
    
    features['price_volatility_1m'] = close.pct_change().rolling(60).std().iloc[-1] if len(close) > 60 else 0.01
    features['price_volatility_5m'] = close.pct_change().rolling(300).std().iloc[-1] if len(close) > 300 else 0.01
    features['price_volatility_1h'] = close.pct_change().rolling(3600).std().iloc[-1] if len(close) > 3600 else 0.01
    
    features['price_high_low_range_1m'] = (high - low).rolling(60).mean().iloc[-1] / close.iloc[-1] if len(close) > 60 else 0.01
    features['price_high_low_range_5m'] = (high - low).rolling(300).mean().iloc[-1] / close.iloc[-1] if len(close) > 300 else 0.01
    
    # Technical features (25)
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    features['tech_rsi_14'] = (100 - (100 / (1 + rs))).iloc[-1] if len(close) > 14 else 50
    features['tech_rsi_28'] = features['tech_rsi_14'] * 0.9  # Simplified
    
    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    features['tech_macd'] = (ema12 - ema26).iloc[-1] / close.iloc[-1] if len(close) > 26 else 0
    features['tech_macd_signal'] = features['tech_macd'] * 0.9
    features['tech_macd_hist'] = (features['tech_macd'] - features['tech_macd_signal'])
    
    # Bollinger
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    features['tech_bb_upper'] = (sma20 + 2 * std20).iloc[-1] / close.iloc[-1] if len(close) > 20 else 1.02
    features['tech_bb_middle'] = sma20.iloc[-1] / close.iloc[-1] if len(close) > 20 else 1.0
    features['tech_bb_lower'] = (sma20 - 2 * std20).iloc[-1] / close.iloc[-1] if len(close) > 20 else 0.98
    features['tech_bb_position'] = 0.5  # Simplified
    features['tech_bb_width'] = (features['tech_bb_upper'] - features['tech_bb_lower']) if len(close) > 20 else 0.02
    
    # ATR
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features['tech_atr_14'] = tr.rolling(14).mean().iloc[-1] / close.iloc[-1] if len(close) > 14 else 0.01
    features['tech_atr_28'] = tr.rolling(28).mean().iloc[-1] / close.iloc[-1] if len(close) > 28 else 0.01
    features['tech_adx_14'] = 0.25  # Simplified
    
    features['tech_stochastic_k'] = 50.0  # Simplified
    features['tech_stochastic_d'] = 50.0
    features['tech_cci'] = 0.0
    features['tech_williams_r'] = -50.0
    
    # EMAs
    features['tech_ema_9'] = close.ewm(span=9).mean().iloc[-1] / close.iloc[-1] if len(close) > 9 else 1.0
    features['tech_ema_21'] = close.ewm(span=21).mean().iloc[-1] / close.iloc[-1] if len(close) > 21 else 1.0
    features['tech_ema_50'] = close.ewm(span=50).mean().iloc[-1] / close.iloc[-1] if len(close) > 50 else 1.0
    features['tech_ema_200'] = close.ewm(span=200).mean().iloc[-1] / close.iloc[-1] if len(close) > 200 else 1.0
    
    features['tech_sma_20'] = sma20.iloc[-1] / close.iloc[-1] if len(close) > 20 else 1.0
    features['tech_sma_50'] = close.rolling(50).mean().iloc[-1] / close.iloc[-1] if len(close) > 50 else 1.0
    features['tech_ema_9_21_cross'] = 1.0 if features['tech_ema_9'] > features['tech_ema_21'] else -1.0
    features['tech_ema_21_50_cross'] = 1.0 if features['tech_ema_21'] > features['tech_ema_50'] else -1.0
    
    # Microstructure (simplified - no order book data)
    features['micro_spread_bps'] = 0.001  # 0.1%
    features['micro_order_imbalance'] = 0.0
    features['micro_buy_sell_ratio_1m'] = 1.0
    features['micro_buy_sell_ratio_5m'] = 1.0
    features['micro_buy_sell_ratio_15m'] = 1.0
    features['micro_volume_ratio_1m'] = 1.0
    features['micro_volume_ratio_5m'] = 1.0
    features['micro_vwap'] = 1.0
    features['micro_twap_5m'] = 1.0
    features['micro_bid_depth_10'] = 1.0
    features['micro_ask_depth_10'] = 1.0
    features['micro_depth_imbalance'] = 0.0
    
    # Volume features
    features['vol_obv'] = 0.0  # Would need cumulative volume
    features['vol_obv_ma_20'] = 0.0
    features['vol_vwap_volume'] = close.iloc[-1] * volume.iloc[-1] if len(close) > 0 else 0
    features['vol_volume_ma_5'] = volume.rolling(5).mean().iloc[-1] if len(volume) > 5 else volume.mean()
    features['vol_volume_ma_20'] = volume.rolling(20).mean().iloc[-1] if len(volume) > 20 else volume.mean()
    features['vol_volume_trend'] = 0.0
    features['vol_buy_volume_ratio'] = 0.5
    
    # Regime features
    features['regime_trend_strength'] = abs(features['tech_ema_9'] - features['tech_ema_50']) if features['tech_ema_50'] != 0 else 0
    features['regime_volatility_regime'] = features['price_volatility_1m']
    features['regime_market_regime'] = 0.0  # 0 = ranging
    features['regime_trend_direction'] = 1.0 if features['tech_ema_9'] > features['tech_ema_21'] else -1.0
    features['regime_momentum_score'] = features['price_returns_5m'] * 10
    
    return features


def create_dataset(klines: list) -> list:
    """Crea dataset con features y labels"""
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    
    # Convertir a numeric
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    data = []
    window_size = 200  # Need enough data for features
    
    for i in range(window_size, len(df)):
        window = df.iloc[:i+1]
        
        # Calcular features
        features = calculate_all_features(window)
        
        # Label: precio futuro
        future_price = df['close'].iloc[min(i+4, len(df)-1)]
        current_price = df['close'].iloc[i]
        future_return = (future_price - current_price) / current_price
        
        # Label
        if future_return > 0.005:
            label = 1  # BUY
        elif future_return < -0.005:
            label = -1  # SELL
        else:
            label = 0  # HOLD
        
        data.append({
            'features': features,
            'label': label
        })
    
    return data


def prepare_ml_data(dataset: list) -> tuple:
    """Prepara datos para ML"""
    X = []
    y = []
    
    for item in dataset:
        feat_dict = item['features']
        # Ensure we have all 60 features
        feature_vector = [feat_dict.get(name, 0.0) for name in FEATURE_NAMES]
        
        # Replace NaN/inf
        feature_vector = [0.0 if (np.isnan(x) or np.isinf(x)) else x for x in feature_vector]
        
        X.append(feature_vector)
        y.append(item['label'])
    
    X = np.array(X)
    y = np.array(y)
    
    logger.info(f"Features: {X.shape[1]}, Samples: {len(X)}")
    logger.info(f"Labels: BUY={np.sum(y==1)}, SELL={np.sum(y==-1)}, HOLD={np.sum(y==0)}")
    
    return X, y


def train_model(X: np.ndarray, y: np.ndarray) -> tuple:
    """Entrena modelo XGBoost"""
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("XGBoost no disponible")
        return None, {}
    
    # Balancear clases
    from sklearn.utils import class_weight
    weights = class_weight.compute_sample_weight('balanced', y)
    
    # Map labels: -1, 0, 1 -> 0, 1, 2
    y_mapped = y + 1
    
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        objective='multi:softmax',
        num_class=3,
        random_state=42,
        n_jobs=-1
    )
    
    model.fit(X, y_mapped, sample_weight=weights)
    
    # Feature importance
    importance = dict(zip(FEATURE_NAMES, model.feature_importances_))
    logger.info("Top 10 features:")
    for k, v in sorted(importance.items(), key=lambda x: -x[1])[:10]:
        logger.info(f"  {k}: {v:.3f}")
    
    return model, importance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', default='ASTERUSDT,ETHUSDT')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--interval', default='15m')
    args = parser.parse_args()
    
    symbols = args.symbols.split(',')
    logger.info(f"=== Entrenando modelo para {symbols} ===")
    
    all_data = []
    
    for symbol in symbols:
        logger.info(f"Procesando {symbol}...")
        
        end_time = int(time.time() * 1000)
        start_time = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        
        klines = []
        current_start = start_time
        
        while current_start < end_time:
            batch = get_klines(symbol, args.interval, 1000, current_start)
            if not batch:
                break
            klines.extend(batch)
            if len(batch) < 1000:
                break
            current_start = int(batch[-1][0]) + 1
        
        if klines:
            dataset = create_dataset(klines)
            all_data.extend(dataset)
            logger.info(f"  {symbol}: {len(dataset)} samples")
    
    if not all_data:
        logger.error("No hay datos")
        return
    
    X, y = prepare_ml_data(all_data)
    
    if len(X) < 100:
        logger.error("Datos insuficientes")
        return
    
    model, feature_importance = train_model(X, y)
    
    if model:
        MODELS_DIR.mkdir(exist_ok=True)
        
        import pickle
        trained_at = int(time.time() * 1000)
        model_data = {
            "model": model,
            "feature_cols": FEATURE_NAMES,
            "trained_at": trained_at,
            "last_training": trained_at,
            "feature_importance": feature_importance,
            "history": [{
                "timestamp": trained_at,
                "samples": int(len(X)),
                "notes": "Initial training run"
            }],
            "model_type": "xgboost"
        }
        
        with open(MODELS_DIR / "signal_model.pkl", 'wb') as f:
            pickle.dump(model_data, f)
        
        logger.info("=== Modelo guardado ===")


if __name__ == "__main__":
    main()
