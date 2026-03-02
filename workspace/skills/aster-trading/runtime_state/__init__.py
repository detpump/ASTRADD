"""Runtime shim so `import state.state_service` resolves to source package.

Because the production runtime stores SQLite artifacts under ``state/`` at the
repo root, Python was treating that directory as a namespace-less package and
ignoring the actual implementation that lives in ``src/state``.  This shim
adds the source path to ``sys.path`` and re-exports the expected symbols so the
dashboard and background services can import them reliably.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Get the repo root (parent of state/)
_repo_root = Path(__file__).resolve().parent
_src_path = _repo_root / "src"

# Directly load the modules from src/state/ without going through package imports
# This avoids the circular reference issue

# Load state_service module
_state_service_spec = importlib.util.spec_from_file_location(
    "state_service", _src_path / "state" / "state_service.py"
)
_state_service_module = importlib.util.module_from_spec(_state_service_spec)
# We need to set up the module's imports first
sys.modules['state.state_service'] = _state_service_module

# Load models module first (since state_service depends on it)
_models_spec = importlib.util.spec_from_file_location(
    "models", _src_path / "state" / "models.py"
)
_models_module = importlib.util.module_from_spec(_models_spec)
sys.modules['state.models'] = _models_module
_models_spec.loader.exec_module(_models_module)

# Now load db and repositories
_db_spec = importlib.util.spec_from_file_location(
    "db", _src_path / "state" / "db.py"
)
_db_module = importlib.util.module_from_spec(_db_spec)
sys.modules['state.db'] = _db_module
_db_spec.loader.exec_module(_db_module)

_repos_spec = importlib.util.spec_from_file_location(
    "repositories", _src_path / "state" / "repositories.py"
)
_repos_module = importlib.util.module_from_spec(_repos_spec)
sys.modules['state.repositories'] = _repos_module
_repos_spec.loader.exec_module(_repos_module)

# Finally load state_service
_state_service_spec.loader.exec_module(_state_service_module)

# Re-export the expected symbols
state_service = _state_service_module.state_service

from state.models import (
    SystemComponent,
    SystemActivity,
    SystemState,
    Position,
    Order,
    RiskState,
    ExecutionTracker,
    LastSignal,
    MarketPrice,
    AuditEvent,
)

__all__ = [
    "state_service",
    "SystemComponent",
    "SystemActivity",
    "SystemState",
    "Position",
    "Order",
    "RiskState",
    "ExecutionTracker",
    "LastSignal",
    "MarketPrice",
    "AuditEvent",
]
