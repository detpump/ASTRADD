"""Dashboard snapshot cache writer/reader.

This module provides a simple JSON snapshot that the trading system writes
periodically (fed by the websocket-driven runtime state) and that the
dashboard can read without calling the exchange REST API.  The goal is to
avoid bans (HTTP 418) while still exposing near real-time positions/orders.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# Cache file disabled: DB is source of truth; keep constants for compatibility
BASE_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = BASE_DIR / "logs"
CACHE_FILE = LOGS_DIR / "dashboard_cache.json"

_lock = threading.RLock()


def _ensure_logs_dir() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def update_dashboard_snapshot(snapshot: Dict[str, Any]) -> None:
    """Deprecated: dashboard should read live data from DB."""
    return


def load_dashboard_snapshot() -> Dict[str, Any]:
    """Deprecated: dashboard should read live data from DB."""
    return {}


__all__ = ["update_dashboard_snapshot", "load_dashboard_snapshot"]
