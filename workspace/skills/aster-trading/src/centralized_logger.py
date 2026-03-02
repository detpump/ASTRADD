#!/usr/bin/env python3
"""
Centralized Logger for OpenClaw Integration
Writes trading data to JSONL files that OpenClaw expects:
- /logs/history/heartbeat.jsonl - heartbeat/status updates
- /logs/history/equity.jsonl - equity tracking
- /logs/history/trades.jsonl - trade execution logs  
- /logs/history/risk.jsonl - risk checks

Autor: Aster Trading V2
Fecha: 2026-02-25

FIX: Added deduplication mechanism to prevent duplicate logging.
"""

import json
import os
import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional

# Deduplication tracking
_logged_trades: Dict[str, float] = {}  # key -> timestamp
_log_dedup_lock = threading.RLock()
_DEDUP_WINDOW_SECONDS = 5.0  # Ignore duplicates within 5 seconds
_LAST_CLEANUP_TS = 0.0
_WRITE_COUNT = 0


def _cleanup_logged_trades(current_time: float) -> None:
    """Remove expired dedup entries with minimal locking overhead."""
    global _LAST_CLEANUP_TS
    # Rate limit cleanup to avoid per-write O(n) cost unless needed
    should_cleanup = (
        current_time - _LAST_CLEANUP_TS > 0.5
        or len(_logged_trades) > 100
    )
    if not should_cleanup:
        return
    keys_to_remove = [
        k for k, v in _logged_trades.items()
        if current_time - v > _DEDUP_WINDOW_SECONDS
    ]
    for k in keys_to_remove:
        _logged_trades.pop(k, None)
    _LAST_CLEANUP_TS = current_time

# Use centralized paths - single point of change for server migration
try:
    from paths import OPENCLAW_LOGS_DIR
    OPENCLAW_LOG_PATH = OPENCLAW_LOGS_DIR
except ImportError:
    # Fallback para cuando paths.py no está disponible
    OPENCLAW_LOG_PATH = "/Users/FIRMAS/.openclaw/logs/history"

logger = logging.getLogger(__name__)


class CentralizedLogger:
    """
    Centralized logger that writes to OpenClaw-compatible JSONL files.
    """
    
    def __init__(self, log_path: str = None):
        self.log_path = log_path or OPENCLAW_LOG_PATH
        
        # Ensure log directory exists
        os.makedirs(self.log_path, exist_ok=True)
        
        # File paths
        self.heartbeat_file = os.path.join(self.log_path, "heartbeat.jsonl")
        self.equity_file = os.path.join(self.log_path, "equity.jsonl")
        self.trades_file = os.path.join(self.log_path, "trades.jsonl")
        self.risk_file = os.path.join(self.log_path, "risk.jsonl")
        
        logger.info(f"CentralizedLogger initialized at: {self.log_path}")
    
    def _is_duplicate(self, key: str) -> bool:
        """Check if this log entry is a duplicate within the dedup window."""
        global _logged_trades
        current_time = time.time()
        
        with _log_dedup_lock:
            if key in _logged_trades:
                last_time = _logged_trades[key]
                if current_time - last_time < _DEDUP_WINDOW_SECONDS:
                    logger.debug(f"Duplicate log entry detected: {key}")
                    return True
            
            # Update timestamp
            _logged_trades[key] = current_time
            _cleanup_logged_trades(current_time)
            # Force cleanup for small test datasets
            if len(_logged_trades) <= 100:
                keys_to_remove = [
                    k for k, v in _logged_trades.items()
                    if current_time - v > _DEDUP_WINDOW_SECONDS
                ]
                for k in keys_to_remove:
                    _logged_trades.pop(k, None)
            
            return False
    
    def _write_jsonl(self, filepath: str, data: Dict[str, Any], dedup_key: str = None) -> bool:
        """Write a single JSON line to the specified file."""
        # Check for duplicate if dedup_key provided
        if dedup_key and self._is_duplicate(dedup_key):
            return False
        
        try:
            with open(filepath, "a") as f:
                f.write(json.dumps(data) + "\n")
            return True
        except Exception as e:
            logger.error(f"Error writing to {filepath}: {e}")
            return False
    
    def log_heartbeat(
        self,
        running: bool,
        equity: float,
        positions: Dict[str, Any],
        signals: Dict[str, Any],
        market: Dict[str, float],
        loop_count: int = 0,
        trades_executed: int = 0,
        risk_level: str = "NORMAL"
    ) -> bool:
        """
        Log heartbeat/status update to heartbeat.jsonl
        
        Args:
            running: Whether system is running
            equity: Current account equity
            positions: Current open positions
            signals: Current trading signals
            market: Current market prices
            loop_count: Current loop iteration
            trades_executed: Total trades executed
            risk_level: Current risk level
        """
        heartbeat_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "running": running,
            "equity": equity,
            "loop_count": loop_count,
            "trades_executed": trades_executed,
            "risk_level": risk_level,
            "positions": positions,
            "signals": signals,
            "market": market
        }
        
        return self._write_jsonl(self.heartbeat_file, heartbeat_data)
    
    def log_equity(
        self,
        equity: float,
        daily_pnl: float = 0.0,
        drawdown_pct: float = 0.0,
        positions_pnl: Dict[str, float] = None
    ) -> bool:
        """
        Log equity update to equity.jsonl
        
        Args:
            equity: Current total equity
            daily_pnl: Profit/loss for the day
            drawdown_pct: Current drawdown percentage
            positions_pnl: P&L for each open position
        """
        equity_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "equity": equity,
            "daily_pnl": daily_pnl,
            "drawdown_pct": drawdown_pct,
            "positions_pnl": positions_pnl or {}
        }
        
        return self._write_jsonl(self.equity_file, equity_data)
    
    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        notional: float,
        status: str = "PENDING",
        order_id: str = None,
        pnl: float = None,
        error: str = None,
        cycle_id: str = None
    ) -> bool:
        """
        Log trade execution to trades.jsonl
        
        Args:
            symbol: Trading symbol (e.g., 'ASTERUSDT')
            side: 'BUY' or 'SELL'
            quantity: Trade quantity
            price: Execution price
            notional: Notional value in USDT
            status: Trade status ('PENDING', 'FILLED', 'REJECTED', 'ERROR')
            order_id: Exchange order ID (if filled)
            pnl: Profit/loss (if closed)
            error: Error message (if failed)
            cycle_id: Optional cycle ID for deduplication
        """
        trade_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_id": order_id,
            "pnl": pnl,
            "status": status,
            "error": error,
            "cycle_id": cycle_id
        }
        
        # Create dedup key from cycle_id or order_id + symbol + status
        dedup_key = None
        if cycle_id:
            dedup_key = f"trade_{cycle_id}"
        elif order_id:
            dedup_key = f"trade_{symbol}_{order_id}_{status}"
        
        return self._write_jsonl(self.trades_file, trade_data, dedup_key=dedup_key)
    
    # Legacy version without dedup support
    def log_trade_legacy(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        notional: float,
        status: str = "PENDING",
        order_id: str = None,
        pnl: float = None,
        error: str = None
    ) -> bool:
        """Legacy log_trade without deduplication - for backward compatibility."""
        trade_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "notional": notional,
            "status": status,
            "order_id": order_id,
            "pnl": pnl,
            "error": error
        }
        
        return self._write_jsonl(self.trades_file, trade_data)
    
    def log_risk(
        self,
        risk_level: str,
        equity: float,
        max_position_pct: float,
        current_exposure_pct: float,
        daily_loss_pct: float,
        drawdown_pct: float,
        open_positions_count: int,
        trades_today: int,
        can_trade: bool,
        reason: str = None
    ) -> bool:
        """
        Log risk check to risk.jsonl
        
        Args:
            risk_level: Current risk level ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
            equity: Current equity
            max_position_pct: Maximum position percentage allowed
            current_exposure_pct: Current exposure percentage
            daily_loss_pct: Daily loss percentage
            drawdown_pct: Current drawdown percentage
            open_positions_count: Number of open positions
            trades_today: Number of trades today
            can_trade: Whether trading is allowed
            reason: Reason if trading is blocked
        """
        risk_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "risk_level": risk_level,
            "equity": equity,
            "max_position_pct": max_position_pct,
            "current_exposure_pct": current_exposure_pct,
            "daily_loss_pct": daily_loss_pct,
            "drawdown_pct": drawdown_pct,
            "open_positions_count": open_positions_count,
            "trades_today": trades_today,
            "can_trade": can_trade,
            "reason": reason
        }
        
        return self._write_jsonl(self.risk_file, risk_data)


# Singleton instance for easy import
_centralized_logger: Optional[CentralizedLogger] = None


def get_logger() -> CentralizedLogger:
    """Get the singleton centralized logger instance."""
    global _centralized_logger
    if _centralized_logger is None:
        _centralized_logger = CentralizedLogger()
    return _centralized_logger


def log_heartbeat(**kwargs) -> bool:
    """Convenience function to log heartbeat."""
    return get_logger().log_heartbeat(**kwargs)


def log_equity(**kwargs) -> bool:
    """Convenience function to log equity."""
    return get_logger().log_equity(**kwargs)


def log_trade(**kwargs) -> bool:
    """Convenience function to log trade."""
    return get_logger().log_trade(**kwargs)


def log_risk(**kwargs) -> bool:
    """Convenience function to log risk."""
    return get_logger().log_risk(**kwargs)


# Legacy functions from backup - wrapper for backward compatibility
# These are used by risk_guard.py and other legacy scripts
def log_operation(event: dict) -> None:
    """
    Registra una operación en el log principal de operaciones.
    Legacy wrapper for backward compatibility.
    """
    # Use log_trade as fallback or log to operations file
    from datetime import datetime, UTC
    import json
    import os

    ts = datetime.now(UTC).isoformat()
    event = dict(event)
    event["ts"] = ts

    # Log to operations file
    try:
        from paths import OPENCLAW_OPERATIONS_FILE
        ops_path = OPENCLAW_OPERATIONS_FILE
    except ImportError:
        ops_path = "/Users/FIRMAS/.openclaw/logs/operations.jsonl"
    os.makedirs(os.path.dirname(ops_path), exist_ok=True)
    with open(ops_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_risk_check(data: dict) -> None:
    """
    Registra una verificación de reglas de riesgo.
    Legacy wrapper for backward compatibility.
    """
    from datetime import datetime, UTC
    import json
    import os

    ts = datetime.now(UTC).isoformat()
    data = dict(data)
    data["ts"] = ts

    # Log to risk file - use centralized path
    risk_path = os.path.join(OPENCLAW_LOG_PATH, "risk.jsonl")
    os.makedirs(os.path.dirname(risk_path), exist_ok=True)
    with open(risk_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
