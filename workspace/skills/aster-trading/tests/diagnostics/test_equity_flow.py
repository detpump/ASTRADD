#!/usr/bin/env python3
"""Pytest wrapper for the diagnostic equity flow script."""

from __future__ import annotations

import importlib.util
import pathlib


def _load_diagnostic_module():
    root = pathlib.Path(__file__).resolve().parents[2]
    module_path = root / "logs" / "diagnostic_test.py"
    spec = importlib.util.spec_from_file_location("diagnostic_test", module_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader, "Failed to load diagnostic_test module"
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_equity_flow_diagnostic_runs_successfully():
    diagnostic_module = _load_diagnostic_module()
    output = diagnostic_module.run_diagnostic()
    assert "DIAGNOSTIC TEST" in output
    assert "RiskGuard status equity" in output
    assert "v2_state.json equity" in output
