#!/usr/bin/env python3
"""
Centralized configuration loader for Aster Trading V2
Provides easy access to all magic numbers and system parameters
"""
import os
import json
import logging
from typing import Any, Dict, Optional
from functools import lru_cache

# Configure logger
logger = logging.getLogger(__name__)


class ConfigLoader:
    """Centralized configuration loader"""
    
    _instance: Optional['ConfigLoader'] = None
    _config: Optional[Dict[str, Any]] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self._load_config()
    
    def _load_config(self):
        """Load configuration from file"""
        # Find config file using relative paths from workspace
        # Get workspace root (parent of skills directory)
        workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        
        possible_paths = [
            os.path.join(os.path.dirname(__file__), '..', 'config', 'system_config.json'),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'system_config.json'),
            os.path.join(workspace_root, 'workspace', 'skills', 'aster-trading', 'config', 'system_config.json')
        ]
        
        config_path = None
        for path in possible_paths:
            if os.path.exists(path):
                config_path = path
                break
        
        if config_path:
            with open(config_path, 'r') as f:
                self._config = json.load(f)
            # Validate loaded config
            self._validate_config()
        else:
            # Use defaults
            self._config = self._get_defaults()
            logger.warning("Config file not found, using defaults")
    
    def _validate_config(self):
        """Validate required config values"""
        required_sections = ['api', 'trading', 'risk', 'system', 'logging']
        missing_sections = []
        
        for section in required_sections:
            if section not in self._config:
                missing_sections.append(section)
        
        if missing_sections:
            raise ValueError(f"Missing required config sections: {missing_sections}")
        
        # Validate specific critical values
        if self._config.get('risk', {}).get('cooldown', {}).get('min_trade_interval_seconds', 0) < 60:
            logger.warning("cooldown.min_trade_interval_seconds should be >= 60 seconds")
        
        if self._config.get('risk', {}).get('cooldown', {}).get('after_stop_loss_seconds', 0) < 60:
            logger.warning("cooldown.after_stop_loss_seconds should be >= 60 seconds")
        
        logger.info("Config validation passed")
    
    def _get_defaults(self) -> Dict[str, Any]:
        """Return default configuration"""
        return {
            "api": {
                "timeout_seconds": 10,
                "rate_limit_min_interval_ms": 100,
                "circuit_breaker": {
                    "failure_threshold": 5,
                    "success_threshold": 2,
                    "timeout_seconds": 30.0
                },
                "retry": {
                    "max_retries": 3,
                    "base_delay_seconds": 0.5,
                    "max_delay_seconds": 10.0
                }
            },
            "trading": {
                "default_leverage": 5,
                "min_notional_usdt": 5.0,
                "default_sl_pct": 1.5
            },
            "risk": {
                "max_position_pct": 0.05,
                "max_total_exposure_pct": 0.20,
                "max_daily_loss_pct": 0.05
            }
        }
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get config value using dot notation.
        Example: config.get('api.circuit_breaker.failure_threshold')
        """
        keys = key_path.split('.')
        value = self._config
        
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    # Convenience methods for common configs
    @property
    def api_timeout(self) -> int:
        return self.get('api.timeout_seconds', 10)
    
    @property
    def circuit_breaker_threshold(self) -> int:
        return self.get('api.circuit_breaker.failure_threshold', 5)
    
    @property
    def circuit_breaker_timeout(self) -> float:
        return self.get('api.circuit_breaker.timeout_seconds', 30.0)
    
    @property
    def retry_max_retries(self) -> int:
        return self.get('api.retry.max_retries', 3)
    
    @property
    def retry_base_delay(self) -> float:
        return self.get('api.retry.base_delay_seconds', 0.5)
    
    @property
    def default_leverage(self) -> int:
        return self.get('trading.default_leverage', 5)
    
    @property
    def min_notional(self) -> float:
        return self.get('trading.min_notional_usdt', 5.0)
    
    @property
    def default_sl_pct(self) -> float:
        value = self.get('trading.default_sl_pct', 1.5)
        if not (0.1 <= value <= 50):
            raise ValueError(f"Invalid default_sl_pct: {value} (must be 0.1-50)")
        return value
    
    @property
    def default_tp1_pct(self) -> float:
        value = self.get('trading.default_tp1_pct', 1.5)
        if not (0.1 <= value <= 100):
            raise ValueError(f"Invalid default_tp1_pct: {value} (must be 0.1-100)")
        return value
    
    @property
    def default_tp2_pct(self) -> float:
        value = self.get('trading.default_tp2_pct', 3.0)
        if not (0.1 <= value <= 100):
            raise ValueError(f"Invalid default_tp2_pct: {value} (must be 0.1-100)")
        return value
    
    @property
    def max_position_pct(self) -> float:
        return self.get('risk.max_position_pct', 0.05)
    
    @property
    def max_total_exposure_pct(self) -> float:
        return self.get('risk.max_total_exposure_pct', 0.20)
    
    @property
    def max_daily_loss_pct(self) -> float:
        return self.get('risk.max_daily_loss_pct', 0.05)
    
    @property
    def max_drawdown_pct(self) -> float:
        return self.get('risk.max_drawdown_pct', 0.20)
    
    @property
    def main_loop_seconds(self) -> int:
        return self.get('system.main_loop_seconds', 60)
    
    @property
    def signal_debounce_seconds(self) -> float:
        return self.get('system.debounce.signal_window_seconds', 10.0)
    
    @property
    def min_signal_strength(self) -> float:
        return self.get('ml.min_signal_strength', 0.10)
    
    @property
    def dedup_window_seconds(self) -> float:
        return self.get('logging.deduplication_window_seconds', 5.0)
    
    @property
    def cache_ttl_seconds(self) -> float:
        return self.get('logging.cache_ttl_seconds', 5.0)


# Global instance
_config_loader: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    """Get or create global config loader"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


# Convenience function for quick access
def config(key_path: str, default: Any = None) -> Any:
    """Quick access to config values"""
    return get_config().get(key_path, default)


if __name__ == "__main__":
    # Test
    cfg = get_config()
    print(f"Circuit breaker threshold: {cfg.circuit_breaker_threshold}")
    print(f"Max position %: {cfg.max_position_pct}")
    print(f"Min signal strength: {cfg.min_signal_strength}")
