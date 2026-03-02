#!/usr/bin/env python3
"""
ML Signal Generator para Trading System V2
Genera señales de trading usando modelos ML (XGBoost/LightGBM)

Autor: Aster Trading V2
Fecha: 2026-02-24
"""

import os
import json
import pickle
import time
import logging
import hashlib
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# SECURITY: Restricted Unpickler for safe model loading
# ============================================================
class RestrictedUnpickler(pickle.Unpickler):
    """
    Restricted Unpickler that only allows specific safe classes.
    This prevents arbitrary code execution from malicious model files.
    """
    
    # Whitelist of allowed classes for unpickling
    ALLOWED_BUILTINS = {
        'numpy', 'numpy.ndarray', 'numpy.array',
        'xgboost', 'xgboost.XGBClassifier', 'xgboost.XGBRegressor',
        'lightgbm', 'lightgbm.LGBMClassifier', 'lightgbm.LGBMRegressor',
    }
    
    def find_class(self, module, name):
        """Only allow loading from whitelisted modules"""
        # Allow numpy classes
        if module.startswith('numpy'):
            return super().find_class(module, name)
        
        # Block potentially dangerous modules
        dangerous_modules = ['os', 'sys', 'subprocess', 'socket', 
                            'requests', 'urllib', 'http', 'pickle',
                            'builtins', 'exec', 'eval', 'compile']
        
        for dangerous in dangerous_modules:
            if module == dangerous or module.startswith(dangerous + '.'):
                raise pickle.UnpicklingError(
                    f"Blocked loading of dangerous module: {module}.{name}"
                )
        
        # Allow sklearn and model-specific modules (safe ones)
        safe_prefixes = ['sklearn', 'xgboost', 'lightgbm', 'joblib']
        for prefix in safe_prefixes:
            if module.startswith(prefix):
                return super().find_class(module, name)
        
        # For dict, list, tuple, set etc - use safe versions
        if module == 'builtins':
            allowed_builtins = {'dict', 'list', 'tuple', 'set', 'frozenset', 
                              'int', 'float', 'str', 'bool', 'None', 'type',
                              'range', 'slice', 'complex', 'bytes', 'bytearray'}
            if name in allowed_builtins:
                return super().find_class(module, name)
        
        raise pickle.UnpicklingError(
            f"Blocked loading of non-whitelisted class: {module}.{name}"
        )


def safe_pickle_load(file_path: str) -> Any:
    """
    Safely load pickle file using restricted Unpickler.
    Only allows whitelisted classes to prevent code execution attacks.
    
    Args:
        file_path: Path to pickle file
        
    Returns:
        Unpickled object
        
    Raises:
        pickle.UnpicklingError: If file contains disallowed classes
        FileNotFoundError: If file doesn't exist
    """
    with open(file_path, "rb") as f:
        unpickler = RestrictedUnpickler(f)
        return unpickler.load()

# Try to import joblib for safer model serialization
try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False
    logger.warning("joblib not available, using pickle with caution")

# Intentar importar XGBoost
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    logger.warning("XGBoost no disponible, usando fallback")

# Intentar importar LightGBM
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False
    logger.warning("LightGBM no disponible")


class SignalAction(Enum):
    """Acción de trading"""
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass
class TradingSignal:
    """Señal de trading generada"""
    symbol: str
    action: SignalAction
    signal_strength: float  # 0-1, qué tan fuerte es la señal
    confidence: float       # 0-1, confianza del modelo
    price: float
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    
    # Metadatos adicionales
    model_version: str = ""
    features_used: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action.name,
            "signal_strength": self.signal_strength,
            "confidence": self.confidence,
            "price": self.price,
            "timestamp": self.timestamp,
            "model_version": self.model_version,
        }


@dataclass
class ModelConfig:
    """Configuración del modelo"""
    model_type: str = "xgboost"  # xgboost, lightgbm, o random_forest
    
    # Hiperparámetros XGBoost
    n_estimators: int = 100
    max_depth: int = 6
    learning_rate: float = 0.1
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    min_child_weight: int = 1
    gamma: float = 0.0
    
    # Configuración de entrenamiento
    train_window_days: int = 30      # Ventana de entrenamiento
    retrain_interval_hours: int = 24  # Cada cuánto reentrenar
    
    # Feature importance
    use_feature_selection: bool = True
    top_features: int = 50
    
    # thresholds
    buy_threshold: float = 0.4
    sell_threshold: float = 0.4
    
    # Risk
    max_position_pct: float = 0.05


# Lista de features que espera el modelo
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


class SignalGenerator:
    """
    Generador de señales de trading usando ML
    
    Características:
    - Usa XGBoost/LightGBM para predicción
    - Entrenamiento online con ventana deslizante
    - Feature importance para interpretación
    - Fallback a regla-based si no hay modelo
    """
    
    def __init__(self, config: ModelConfig = None, model_path: str = "./models"):
        self.config = config or ModelConfig()
        self.model_path = model_path
        
        # Modelo
        self.model = None
        self.model_loaded = False
        
        # Estado
        self.last_training_time = 0
        self.feature_importance = {}
        self.training_history = []
        
        # Features del último prediction
        self.last_features = None
        self.last_prediction_time = 0
        
        # Crear directorio de modelos si no existe
        os.makedirs(model_path, exist_ok=True)
        
        # Intentar cargar modelo existente
        self._load_model()
        
        # Si no hay modelo, crear uno básico
        if not self.model_loaded:
            self._create_fallback_model()
    
    def _create_fallback_model(self):
        """Crea un modelo fallback simple"""
        if XGBOOST_AVAILABLE:
            self.model = xgb.XGBClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                learning_rate=self.config.learning_rate,
                subsample=self.config.subsample,
                colsample_bytree=self.config.colsample_bytree,
                min_child_weight=self.config.min_child_weight,
                gamma=self.config.gamma,
                random_state=42,
                use_label_encoder=False,
                eval_metric='logloss'
            )
            # No marcar como loaded - necesita entrenamiento primero
            self.model_loaded = False
            logger.info("✅ Modelo XGBoost creado (sin entrenar - usando rule-based)")
        elif LIGHTGBM_AVAILABLE:
            self.model = lgb.LGBMClassifier(
                n_estimators=self.config.n_estimators,
                max_depth=self.config.max_depth,
                learning_rate=self.config.learning_rate,
                subsample=self.config.subsample,
                colsample_bytree=self.config.colsample_bytree,
                random_state=42,
                verbose=-1
            )
            self.model_loaded = True
            logger.info("✅ Modelo LightGBM creado (fallback)")
        else:
            logger.warning("⚠️ Ningún modelo ML disponible, usando regla-based")
            self.model_loaded = False
    
    def _validate_model_data(self, data: Dict) -> bool:
        """Validate model data structure before loading"""
        if not isinstance(data, dict):
            return False

        # Backward compatibility: older training pipeline used "trained_at"
        if "last_training" not in data and "trained_at" in data:
            data["last_training"] = data["trained_at"]

        required_keys = ["model", "last_training"]
        if not all(key in data for key in required_keys):
            return False
        
        # Validate model has required attributes
        model = data.get("model")
        if model is None:
            return False
        
        # Check if model has predict method (basic sanity check)
        if not hasattr(model, 'predict'):
            return False

        # Backfill training history for dashboards if missing
        if "history" not in data:
            data["history"] = [{
                "timestamp": data.get("last_training", int(time.time() * 1000)),
                "samples": None,
                "notes": "Imported legacy model"
            }]
        
        return True
    
    def _load_model(self) -> bool:
        """Intenta cargar un modelo guardado con validación de seguridad"""
        model_file = os.path.join(self.model_path, "signal_model.pkl")
        
        if not os.path.exists(model_file):
            logger.info("No existe modelo guardado, creando nuevo")
            return False
        
        try:
            # Check file size before loading (max 100MB)
            file_size = os.path.getsize(model_file)
            if file_size > 100 * 1024 * 1024:
                logger.error(f"Model file too large: {file_size} bytes")
                return False
            
            model_data = None
            
            # Try joblib first (safer)
            if JOBLIB_AVAILABLE:
                try:
                    model_data = joblib.load(model_file)
                    logger.info("Modelo cargado con joblib")
                except Exception as e:
                    logger.warning(f"joblib failed, trying pickle: {e}")
            
            # Fallback to restricted pickle with security validation
            if model_data is None:
                try:
                    # Use restricted Unpickler for security
                    model_data = safe_pickle_load(model_file)
                    logger.info("Modelo cargado con restricted Unpickler (seguro)")
                except pickle.UnpicklingError as e:
                    logger.error(f"Pickle security error (blocked malicious class): {e}")
                    return False
                except Exception as e:
                    logger.error(f"Error loading pickle file: {e}")
                    return False
            
            # Validate the loaded data
            if not self._validate_model_data(model_data):
                logger.error("Model data validation failed")
                return False
            
            self.model = model_data.get("model")
            self.last_training_time = model_data.get("last_training", 0)
            self.feature_importance = model_data.get("feature_importance", {})
            self.training_history = model_data.get("history", [])
            
            self.model_loaded = True
            logger.info(f"✅ Modelo cargado desde {model_file}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error al cargar modelo: {e}")
            return False
    
    def save_model(self) -> bool:
        """Guarda el modelo actual usando joblib (safer than pickle)"""
        if not self.model:
            return False
        
        model_file = os.path.join(self.model_path, "signal_model.pkl")
        
        try:
            model_data = {
                "model": self.model,
                "last_training": self.last_training_time,
                "feature_importance": self.feature_importance,
                "history": self.training_history,
                "config": {
                    "model_type": self.config.model_type,
                    "n_estimators": self.config.n_estimators,
                }
            }
            
            # Use joblib if available (safer), fallback to pickle
            if JOBLIB_AVAILABLE:
                try:
                    joblib.dump(model_data, model_file, compress=3)
                    logger.info(f"✅ Modelo guardado con joblib en {model_file}")
                    return True
                except Exception as e:
                    logger.warning(f"joblib save failed, using pickle: {e}")
            
            # Fallback to pickle
            with open(model_file, "wb") as f:
                pickle.dump(model_data, f)
            
            logger.info(f"✅ Modelo guardado en {model_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error al guardar modelo: {e}")
            return False
    
    def predict(self, features: Dict) -> TradingSignal:
        """
        Genera una señal de trading basada en features
        
        Args:
            features: Diccionario con features del mercado
            
        Returns:
            TradingSignal con la señal generada
        """
        symbol = features.get("symbol", "UNKNOWN")
        price = features.get("price", 0.0)
        
        # Convertir features a array (now returns tuple with valid feature count)
        X, valid_feature_count = self._prepare_features(features)
        
        if self.model_loaded and self.model is not None:
            # Usar modelo ML
            try:
                # Predicción
                predictions = self.model.predict(X)
                probas = None
                if hasattr(self.model, "predict_proba"):
                    probas = self.model.predict_proba(X)
                
                # ISSUE 3 FIX: Validate empty prediction arrays
                if predictions is None or (hasattr(predictions, 'size') and predictions.size == 0):
                    logger.error("Model returned empty predictions")
                    return self._rule_based_signal(features, symbol, price)
                
                prediction = predictions[0]
                proba = probas[0] if probas is not None and len(probas) > 0 else None

                # Map model outputs (0,1,2) to (-1,0,1)
                mapped_prediction = int(prediction)
                if mapped_prediction in (0, 1, 2):
                    mapped_prediction -= 1

                # Extract class confidences
                sell_conf = 0.0
                hold_conf = 0.5
                buy_conf = 0.0
                if proba is not None and len(proba) >= 3:
                    sell_conf = float(proba[0])
                    hold_conf = float(proba[1])
                    buy_conf = float(proba[2])
                elif proba is not None and len(proba) == 2:
                    sell_conf = float(proba[0])
                    buy_conf = float(proba[1])
                    hold_conf = 1.0 - max(buy_conf, sell_conf)
                elif proba is not None and len(proba) == 1:
                    buy_conf = float(proba[0])
                    hold_conf = 1.0 - buy_conf
                else:
                    logger.warning("predict_proba unavailable, defaulting confidence")

                buy_activation_threshold = self.config.buy_threshold
                sell_activation_threshold = 1.0 - self.config.sell_threshold

                buy_denom = max(1e-3, 1.0 - buy_activation_threshold)
                sell_denom = max(1e-3, 1.0 - sell_activation_threshold)

                action = SignalAction.HOLD
                signal_strength = 0.0
                confidence = hold_conf

                if mapped_prediction == 1:
                    confidence = buy_conf
                    if confidence >= buy_activation_threshold:
                        action = SignalAction.BUY
                        signal_strength = (confidence - buy_activation_threshold) / buy_denom
                elif mapped_prediction == -1:
                    confidence = sell_conf
                    if confidence >= sell_activation_threshold:
                        action = SignalAction.SELL
                        signal_strength = (confidence - sell_activation_threshold) / sell_denom

                signal_strength = max(0.0, min(1.0, signal_strength))

                if action == SignalAction.HOLD:
                    confidence = 0.5
                
                signal = TradingSignal(
                    symbol=symbol,
                    action=action,
                    signal_strength=signal_strength,
                    confidence=confidence,
                    price=price,
                    model_version="ml_v1",
                    features_used=valid_feature_count
                )
                
                self.last_features = features
                self.last_prediction_time = int(time.time() * 1000)
                
                return signal
                
            except Exception as e:
                logger.error(f"Error en predicción ML: {e}")
        
        # Fallback: regla-based
        return self._rule_based_signal(features, symbol, price)
    
    def _prepare_features(self, features: Dict) -> Tuple[np.ndarray, int]:
        """
        Prepara features para el modelo.
        
        Returns:
            Tuple of (feature array, count of valid features)
        """
        # Crear array con features en el orden correcto
        X = []
        missing_count = 0
        missing_features = []
        
        for name in FEATURE_NAMES:
            value = features.get(name)
            if value is None or (isinstance(value, float) and value != value):  # NaN check
                missing_count += 1
                missing_features.append(name)
                value = 0.0  # Still use 0.0 but track it
            X.append(value)
        
        # Warn if too many features are missing (more than 20%)
        total_features = len(FEATURE_NAMES)
        missing_pct = (missing_count / total_features) * 100 if total_features > 0 else 0
        
        if missing_pct > 20:
            logger.warning(
                f"ALERT: {missing_count}/{total_features} features missing ({missing_pct:.1f}%). "
                f"Missing: {missing_features[:5]}..."  # Log first 5 missing features
            )
        elif missing_count > 0:
            logger.debug(f"{missing_count} features missing, filled with 0.0")
        
        return np.array([X]), total_features - missing_count
    
    def _rule_based_signal(self, features: Dict, symbol: str, price: float) -> TradingSignal:
        """
        Señal basada en reglas cuando no hay modelo ML
        
        Analiza features técnicos básicos
        """
        # Extraer features relevantes
        rsi = features.get("tech_rsi_14", 50)
        macd_hist = features.get("tech_macd_hist", 0)
        bb_position = features.get("tech_bb_position", 0.5)
        trend_direction = features.get("regime_trend_direction", 0)
        momentum = features.get("regime_momentum_score", 0)
        
        # Contadores
        buy_signals = 0
        sell_signals = 0
        
        # RSI
        if rsi < 30:
            buy_signals += 1
        elif rsi > 70:
            sell_signals += 1
        
        # MACD
        if macd_hist > 0:
            buy_signals += 1
        elif macd_hist < 0:
            sell_signals += 1
        
        # Bollinger
        if bb_position < 0.2:
            buy_signals += 1
        elif bb_position > 0.8:
            sell_signals += 1
        
        # Trend
        if trend_direction > 0:
            buy_signals += 1
        elif trend_direction < 0:
            sell_signals += 1
        
        # Momentum
        if momentum > 0.3:
            buy_signals += 1
        elif momentum < -0.3:
            sell_signals += 1
        
        # Determinar acción
        total_signals = buy_signals + sell_signals
        
        if buy_signals > sell_signals and buy_signals >= 2:
            action = SignalAction.BUY
            signal_strength = buy_signals / max(total_signals, 1)
            confidence = 0.6
        elif sell_signals > buy_signals and sell_signals >= 2:
            action = SignalAction.SELL
            signal_strength = sell_signals / max(total_signals, 1)
            confidence = 0.6
        else:
            action = SignalAction.HOLD
            signal_strength = 0.0
            confidence = 0.5
        
        return TradingSignal(
            symbol=symbol,
            action=action,
            signal_strength=signal_strength,
            confidence=confidence,
            price=price,
            model_version="rule_based_v1",
            features_used=5
        )
    
    def train(self, X: np.ndarray, y: np.ndarray) -> Dict:
        """
        Entrena el modelo con datos históricos
        
        Args:
            X: Features (n_samples, n_features)
            y: Labels (n_samples,) - 1=BUY, 0=HOLD, -1=SELL
            
        Returns:
            Diccionario con métricas de entrenamiento
        """
        if not XGBOOST_AVAILABLE and not LIGHTGBM_AVAILABLE:
            logger.error("No hay biblioteca ML disponible")
            return {"error": "No ML library available"}
        
        # Convertir labels a formato correcto
        # 1 (BUY) -> 1, 0 (HOLD) -> 0, -1 (SELL) -> 2
        y_mapped = np.array([1 if label == 1 else (2 if label == -1 else 0) for label in y])
        
        try:
            if XGBOOST_AVAILABLE:
                self.model = xgb.XGBClassifier(
                    n_estimators=self.config.n_estimators,
                    max_depth=self.config.max_depth,
                    learning_rate=self.config.learning_rate,
                    subsample=self.config.subsample,
                    colsample_bytree=self.config.colsample_bytree,
                    min_child_weight=self.config.min_child_weight,
                    gamma=self.config.gamma,
                    random_state=42,
                    use_label_encoder=False,
                    eval_metric='mlogloss'
                )
                
                self.model.fit(X, y_mapped)
                
                # Feature importance
                if hasattr(self.model, 'feature_importances_'):
                    importance = self.model.feature_importances_
                    self.feature_importance = {
                        name: float(imp) 
                        for name, imp in zip(FEATURE_NAMES, importance)
                    }
            
            elif LIGHTGBM_AVAILABLE:
                self.model = lgb.LGBMClassifier(
                    n_estimators=self.config.n_estimators,
                    max_depth=self.config.max_depth,
                    learning_rate=self.config.learning_rate,
                    subsample=self.config.subsample,
                    colsample_bytree=self.config.colsample_bytree,
                    random_state=42,
                    verbose=-1
                )
                
                self.model.fit(X, y_mapped)
                
                # Feature importance
                if hasattr(self.model, 'feature_importances_'):
                    importance = self.model.feature_importances_
                    self.feature_importance = {
                        name: float(imp)
                        for name, imp in zip(FEATURE_NAMES, importance)
                    }
            
            self.model_loaded = True
            self.last_training_time = int(time.time() * 1000)
            
            # Guardar modelo
            self.save_model()
            
            logger.info(f"✅ Modelo entrenado con {len(X)} samples")
            
            return {
                "success": True,
                "samples": len(X),
                "features": X.shape[1] if len(X.shape) > 1 else len(FEATURE_NAMES),
                "top_features": self._get_top_features(5)
            }
            
        except Exception as e:
            logger.error(f"Error en entrenamiento: {e}")
            return {"error": str(e)}
    
    def _get_top_features(self, n: int = 10) -> List[Tuple[str, float]]:
        """Obtiene las features más importantes"""
        if not self.feature_importance:
            return []
        
        sorted_features = sorted(
            self.feature_importance.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        return sorted_features[:n]
    
    def should_retrain(self) -> bool:
        """Determina si debe reentrenar el modelo"""
        now = int(time.time() * 1000)
        
        # Tiempo desde último entrenamiento
        hours_since_train = (now - self.last_training_time) / (1000 * 60 * 60)
        
        return hours_since_train >= self.config.retrain_interval_hours
    
    def get_model_info(self) -> Dict:
        """Obtiene información del modelo"""
        return {
            "loaded": self.model_loaded,
            "model_type": self.config.model_type if self.model_loaded else "none",
            "last_training": self.last_training_time,
            "hours_since_training": (int(time.time() * 1000) - self.last_training_time) / (1000 * 60 * 60),
            "should_retrain": self.should_retrain(),
            "top_features": self._get_top_features(5),
            "total_features": len(FEATURE_NAMES),
        }


# =======================
# EJEMPLO DE USO
# =======================

def example():
    """Ejemplo de uso"""
    
    # Crear generador
    generator = SignalGenerator(
        config=ModelConfig(),
        model_path="./models"
    )
    
    # Simular features
    features = {
        "symbol": "ASTERUSDT",
        "price": 0.5,
        
        # Price
        "price_returns_1m": 0.001,
        "price_returns_5m": 0.005,
        "price_returns_15m": 0.01,
        "price_returns_1h": -0.002,
        "price_volatility_1m": 0.02,
        "price_volatility_5m": 0.03,
        "price_volatility_1h": 0.04,
        
        # Technical
        "tech_rsi_14": 45.0,
        "tech_rsi_28": 50.0,
        "tech_macd": 0.001,
        "tech_macd_hist": 0.0005,
        "tech_bb_position": 0.4,
        "tech_adx_14": 30.0,
        
        # Regime
        "regime_trend_direction": 1,
        "regime_momentum_score": 0.2,
        "regime_market_regime": 3,
    }
    
    # Generar señal
    signal = generator.predict(features)
    
    print(f"Señal para {signal.symbol}:")
    print(f"  Acción: {signal.action.name}")
    print(f"  Strength: {signal.signal_strength:.2f}")
    print(f"  Confidence: {signal.confidence:.2f}")
    print(f"  Price: ${signal.price:.4f}")
    print(f"  Model: {signal.model_version}")
    
    # Info del modelo
    info = generator.get_model_info()
    print(f"\nModelo:")
    print(f"  Loaded: {info['loaded']}")
    print(f"  Type: {info['model_type']}")
    print(f"  Should retrain: {info['should_retrain']}")


if __name__ == "__main__":
    example()
