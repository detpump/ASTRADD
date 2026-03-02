#!/usr/bin/env python3
"""
Monitoring Module for Aster Trading System V2
Provides Health Check Endpoint, Metrics Collection, JSON Logging, and State Recovery

Phase 2: Health Check + Metrics
Phase 3: Structured JSON Logging + State Recovery
"""

import json
import logging
import os
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict

# Add src to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)


# ============================================================================
# PHASE 2: Health Check Endpoint
# ============================================================================

class HealthCheck:
    """
    Centralized health check for the trading system.
    Provides /health endpoint data including:
    - Circuit breaker state
    - API health status
    - Last trade timestamp
    - Daily PnL
    """

    def __init__(self):
        self._last_trade_time: float = 0
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: str = ""
        self._lock = threading.Lock()

    def update_last_trade(self, timestamp: float = None):
        """Update the last trade timestamp."""
        with self._lock:
            self._last_trade_time = timestamp or time.time()

    def update_daily_pnl(self, pnl: float):
        """Update daily PnL."""
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            if self._daily_pnl_date != today:
                # Reset for new day
                self._daily_pnl = 0.0
                self._daily_pnl_date = today
            self._daily_pnl += pnl

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get comprehensive health status for /health endpoint.

        Returns:
            Dict with circuit_breaker_state, api_health, last_trade, daily_pnl
        """
        with self._lock:
            # Import circuit breaker from aster_api
            try:
                from api.aster_api import get_api_health_status, is_api_healthy, _api_circuit_breaker
                circuit_breaker_status = _api_circuit_breaker.get_status()
                api_healthy = is_api_healthy()
            except Exception as e:
                logger.warning(f"Could not get circuit breaker status: {e}")
                circuit_breaker_status = {"state": "unknown", "error": str(e)}
                api_healthy = False

            return {
                "status": "healthy" if api_healthy else "degraded",
                "timestamp": datetime.now().isoformat(),
                "circuit_breaker": circuit_breaker_status,
                "api_healthy": api_healthy,
                "last_trade_timestamp": self._last_trade_time,
                "last_trade_age_seconds": time.time() - self._last_trade_time if self._last_trade_time > 0 else None,
                "daily_pnl": self._daily_pnl,
                "daily_pnl_date": self._daily_pnl_date
            }


# Global health check instance
_health_check = HealthCheck()


def get_health_status() -> Dict[str, Any]:
    """Get health status (convenience function)."""
    return _health_check.get_health_status()


def update_last_trade(timestamp: float = None):
    """Update last trade time."""
    _health_check.update_last_trade(timestamp)


def update_daily_pnl(pnl: float):
    """Update daily PnL."""
    _health_check.update_daily_pnl(pnl)


# ============================================================================
# PHASE 2: Metrics Collection (Prometheus-style)
# ============================================================================

@dataclass
class HistogramBuckets:
    """Pre-defined histogram buckets for latency tracking."""
    # Order latency buckets (in seconds)
    ORDER_LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]


class MetricsCollector:
    """
    Prometheus-style metrics collector.
    Tracks:
    - Order latency histogram
    - Trade count counter
    - Position gauge
    """

    def __init__(self):
        self._order_latencies: List[float] = []
        self._trade_count: int = 0
        self._positions: Dict[str, float] = {}  # symbol -> position_size
        self._lock = threading.Lock()

    def record_order_latency(self, latency_seconds: float):
        """Record order execution latency."""
        with self._lock:
            self._order_latencies.append(latency_seconds)
            # Keep only last 1000 entries
            if len(self._order_latencies) > 1000:
                self._order_latencies = self._order_latencies[-1000:]

    def increment_trade_count(self, count: int = 1):
        """Increment trade counter."""
        with self._lock:
            self._trade_count += count

    def update_position(self, symbol: str, size: float):
        """Update position gauge for a symbol."""
        with self._lock:
            if size == 0:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = size

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        with self._lock:
            # Calculate latency percentiles
            latencies = sorted(self._order_latencies)
            latency_summary = {}
            if latencies:
                latency_summary = {
                    "count": len(latencies),
                    "min": latencies[0],
                    "max": latencies[-1],
                    "mean": sum(latencies) / len(latencies),
                    "p50": latencies[len(latencies) // 2],
                    "p95": latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else latencies[-1],
                    "p99": latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]
                }

            return {
                "order_latency": latency_summary,
                "trade_count": self._trade_count,
                "positions": dict(self._positions),
                "position_count": len(self._positions)
            }

    def get_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        metrics = self.get_metrics_summary()
        lines = []

        # Order latency histogram (simplified)
        latency = metrics.get("order_latency", {})
        if latency:
            lines.append(f"# HELP order_latency_seconds Order execution latency in seconds")
            lines.append(f"# TYPE order_latency_seconds summary")
            lines.append(f"order_latency_seconds_count {latency.get('count', 0)}")
            lines.append(f"order_latency_seconds_sum {latency.get('mean', 0) * latency.get('count', 0)}")

        # Trade counter
        lines.append(f"# HELP trades_total Total number of trades executed")
        lines.append(f"# TYPE trades_total counter")
        lines.append(f"trades_total {metrics.get('trade_count', 0)}")

        # Position gauge
        lines.append(f"# HELP positions_current Current open positions")
        lines.append(f"# TYPE positions_current gauge")
        for symbol, size in metrics.get("positions", {}).items():
            lines.append(f'positions_current{{symbol="{symbol}"}} {size}')
        lines.append(f"positions_current_total {metrics.get('position_count', 0)}")

        return "\n".join(lines)


# Global metrics collector
_metrics_collector = MetricsCollector()


def record_order_latency(latency_seconds: float):
    """Record order latency."""
    _metrics_collector.record_order_latency(latency_seconds)


def increment_trade_count(count: int = 1):
    """Increment trade count."""
    _metrics_collector.increment_trade_count(count)
    # Also update health check
    update_last_trade()


def update_position(symbol: str, size: float):
    """Update position tracking."""
    _metrics_collector.update_position(symbol, size)


def get_metrics_summary() -> Dict[str, Any]:
    """Get metrics summary."""
    return _metrics_collector.get_metrics_summary()


def get_prometheus_metrics() -> str:
    """Get metrics in Prometheus format."""
    return _metrics_collector.get_prometheus_format()


# ============================================================================
# PHASE 3: Structured JSON Logging
# ============================================================================

class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.
    Wraps existing logger with JSON format output.
    """

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        # Include exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Include extra fields
        if self.include_extra:
            extra_fields = {
                k: v for k, v in record.__dict__.items()
                if k not in logging.LogRecord(
                    "", 0, "", 0, "", (), None
                ).__dict__ and not k.startswith('_')
            }
            if extra_fields:
                log_data["extra"] = extra_fields

        return json.dumps(log_data, default=str)


def setup_json_logging(logger_name: str = None, level: int = logging.INFO):
    """
    Setup JSON logging for a specific logger or root logger.

    Args:
        logger_name: Name of logger to configure. If None, configures root logger.
        level: Logging level to set.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    handler.setLevel(level)

    if logger_name:
        log = logging.getLogger(logger_name)
    else:
        log = logging.getLogger()

    log.addHandler(handler)
    log.setLevel(level)

    return log


def get_json_logger(name: str) -> logging.Logger:
    """Get a logger configured with JSON formatting."""
    log = logging.getLogger(name)
    # Check if already has JSON handler
    has_json_handler = any(
        isinstance(h, JSONFormatter) or
        (hasattr(h, 'formatter') and isinstance(h.formatter, JSONFormatter))
        for h in log.handlers
    )
    if not has_json_handler:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        log.addHandler(handler)
    return log


# ============================================================================
# PHASE 3: State Recovery
# ============================================================================

class StateRecovery:
    """
    State persistence for crash recovery.
    Saves and restores:
    - Trade state
    - Position state
    - Risk state
    - Metrics state
    """

    def __init__(self, state_dir: str = None):
        # Use centralized paths
        try:
            from paths import STATE_DIR
            self._state_dir = state_dir or STATE_DIR
        except ImportError:
            BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._state_dir = state_dir or os.path.join(BASE, "data/state")

        self._lock = threading.Lock()
        self._recovery_data: Dict[str, Any] = {}
        self._last_save_time: float = 0

    def save_state(self, state_type: str, data: Dict[str, Any]):
        """
        Save state for recovery.

        Args:
            state_type: Type of state (trade, position, risk, metrics)
            data: State data to persist
        """
        with self._lock:
            self._recovery_data[state_type] = {
                "data": data,
                "timestamp": time.time()
            }
            self._last_save_time = time.time()

    def load_state(self, state_type: str) -> Optional[Dict[str, Any]]:
        """
        Load state for recovery.

        Args:
            state_type: Type of state to load

        Returns:
            State data or None if not found
        """
        with self._lock:
            state = self._recovery_data.get(state_type)
            if state:
                return state.get("data")
            return None

    def persist_to_disk(self, filename: str = "recovery_state.json"):
        """
        Persist all state to disk for crash recovery.

        Args:
            filename: Name of recovery file
        """
        with self._lock:
            filepath = os.path.join(self._state_dir, filename)
            os.makedirs(self._state_dir, exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "saved_at": time.time(),
                    "states": self._recovery_data
                }, f, indent=2, default=str)

            logger.info(f"State persisted to {filepath}")

    def load_from_disk(self, filename: str = "recovery_state.json") -> bool:
        """
        Load state from disk on startup.

        Args:
            filename: Name of recovery file

        Returns:
            True if state was loaded successfully
        """
        filepath = os.path.join(self._state_dir, filename)

        if not os.path.exists(filepath):
            logger.info(f"No recovery file found at {filepath}")
            return False

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                recovery = json.load(f)

            states = recovery.get("states", {})
            with self._lock:
                self._recovery_data = states
                self._last_save_time = recovery.get("saved_at", 0)

            logger.info(f"State recovered from {filepath}: {list(states.keys())}")
            return True
        except Exception as e:
            logger.error(f"Failed to load recovery state: {e}")
            return False

    def auto_save(self, interval_seconds: float = 60.0, filename: str = "recovery_state.json"):
        """
        Start automatic state persistence.

        Args:
            interval_seconds: How often to save (default 60s)
            filename: Name of recovery file
        """
        def _auto_save_loop():
            while True:
                time.sleep(interval_seconds)
                try:
                    self.persist_to_disk(filename)
                except Exception as e:
                    logger.error(f"Auto-save failed: {e}")

        thread = threading.Thread(target=_auto_save_loop, daemon=True)
        thread.start()
        logger.info(f"Auto-save started (interval={interval_seconds}s)")


# Global state recovery instance
_state_recovery = StateRecovery()


def save_recovery_state(state_type: str, data: Dict[str, Any]):
    """Save state for recovery."""
    _state_recovery.save_state(state_type, data)


def load_recovery_state(state_type: str) -> Optional[Dict[str, Any]]:
    """Load state for recovery."""
    return _state_recovery.load_state(state_type)


def persist_all_state(filename: str = "recovery_state.json"):
    """Persist all state to disk."""
    _state_recovery.persist_to_disk(filename)


def recover_from_disk(filename: str = "recovery_state.json") -> bool:
    """Recover state from disk."""
    return _state_recovery.load_from_disk(filename)


# ============================================================================
# Combined Health + Metrics Endpoint
# ============================================================================

def get_system_status() -> Dict[str, Any]:
    """
    Get complete system status for monitoring endpoint.

    Returns:
        Dict with health, metrics, and state information
    """
    return {
        "health": get_health_status(),
        "metrics": get_metrics_summary(),
        "recovery": {
            "last_save_time": _state_recovery._last_save_time,
            "saved_states": list(_state_recovery._recovery_data.keys())
        }
        # Note: Don't expose full state in API response for security
    }
