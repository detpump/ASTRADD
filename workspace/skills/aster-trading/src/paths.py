#!/usr/bin/env python3
"""
Centralized Path Configuration for Aster Trading V2
====================================================

This module provides a SINGLE POINT OF CHANGE for all file paths in the system.
Change BASE_DIR here and it propagates to all modules.

For server migration:
1. Change BASE_DIR to the new location
2. All modules will automatically use the new paths

Usage:
    from paths import BASE_DIR, LOGS_DIR, CONFIG_DIR, DATA_DIR, get_state_path, get_log_path
    
    # Or use individual paths
    logs_path = LOGS_DIR
    config_path = CONFIG_DIR
"""

import os
import sys

# ============================================================
# 🎯 SINGLE POINT OF CHANGE - EDIT THIS FOR SERVER MIGRATION
# ============================================================
# Default: current working directory (auto-detected)
# For server migration, change this to: "/opt/aster-trading" or wherever you install

def _get_default_base():
    """Auto-detect base directory from this file's location"""
    # This file is in: .../workspace/skills/aster-trading/src/paths.py
    # So base is: .../workspace/skills/aster-trading/
    current_file = os.path.abspath(__file__)
    src_dir = os.path.dirname(current_file)  # .../src/
    base = os.path.dirname(src_dir)  # .../aster-trading/
    return base

# Try to load from environment variable first (for Docker/container deployments)
BASE_DIR = os.environ.get("ASTER_TRADING_DIR", _get_default_base())

# ============================================================
# Derived paths - NO HARDCODED PATHS BELOW THIS LINE
# ============================================================

# Main directories
LOGS_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(BASE_DIR, "models")
STATE_DIR = os.path.join(DATA_DIR, "state")

# Primary SQLite database (single source of truth)
DATABASE_FILE = os.path.join(LOGS_DIR, "aster.db")
# Deprecated legacy files (kept only for backward compatibility notices)
V2_STATE_FILE = os.path.join(LOGS_DIR, "v2_state.json")
RISK_STATE_FILE = os.path.join(LOGS_DIR, "risk_state.json")
V2_OUTPUT_LOG = os.path.join(LOGS_DIR, "v2_output.log")

# Config files (inside BASE_DIR/config/)
RISK_CONFIG_FILE = os.path.join(CONFIG_DIR, "risk_config.json")
RISK_ADJUSTMENT_CONFIG_FILE = os.path.join(CONFIG_DIR, "risk_adjustment_config.json")
KEYS_CONFIG_FILE = os.path.join(CONFIG_DIR, "keys.json")

# State files (inside BASE_DIR/data/state/) - legacy
TRADE_STATE_FILE = os.path.join(STATE_DIR, "trade_state.json")
OPEN_ORDERS_LOG = os.path.join(STATE_DIR, "open_orders_log.json")
MARKET_STATE_FILE = os.path.join(STATE_DIR, "market_state.json")
SCORE_PERFORMANCE_FILE = os.path.join(STATE_DIR, "score_performance.json")

# OpenClaw integration paths - use environment variables for server migration
# These can be set via OPENCLAW_BASE_DIR env var
def _get_openclaw_base():
    """Get OpenClaw base directory"""
    return os.environ.get("OPENCLAW_BASE_DIR", "/Users/FIRMAS/.openclaw")

OPENCLAW_LOGS_DIR = os.path.join(_get_openclaw_base(), "logs/history")
OPENCLAW_OPERATIONS_FILE = os.path.join(_get_openclaw_base(), "logs/operations.jsonl")

# ============================================================
# Helper functions for dynamic path resolution
# ============================================================

def get_state_path(filename: str) -> str:
    """Get path to a state file in LOGS_DIR"""
    return os.path.join(LOGS_DIR, filename)

def get_config_path(filename: str) -> str:
    """Get path to a config file in CONFIG_DIR"""
    return os.path.join(CONFIG_DIR, filename)

def get_data_path(filename: str) -> str:
    """Get path to a data file in DATA_DIR"""
    return os.path.join(DATA_DIR, filename)

def ensure_dirs():
    """Create all necessary directories if they don't exist"""
    for directory in [LOGS_DIR, CONFIG_DIR, DATA_DIR, MODELS_DIR, STATE_DIR]:
        os.makedirs(directory, exist_ok=True)

# Auto-create directories on import
ensure_dirs()

# ============================================================
# Environment info
# ============================================================

def get_env_info() -> dict:
    """Get environment information for debugging"""
    return {
        "base_dir": BASE_DIR,
        "logs_dir": LOGS_DIR,
        "config_dir": CONFIG_DIR,
        "data_dir": DATA_DIR,
        "models_dir": MODELS_DIR,
        "state_dir": STATE_DIR,
        "python_version": sys.version,
        "cwd": os.getcwd(),
    }


# Legacy compatibility - these point to the old locations
# Modules importing these will still work
DEPRECATED_TRADE_STATE = "/Users/FIRMAS/.openclaw/skills/aster-trading/data/state/trade_state.json"
DEPRECATED_BASE = "/Users/FIRMAS/.openclaw/skills/aster-trading"
