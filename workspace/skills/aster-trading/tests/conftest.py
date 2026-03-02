#!/usr/bin/env python3
"""
Pytest configuration and fixtures for Aster Trading System tests.

This module provides:
- Test environment configuration
- Mock API server lifecycle management
- TestSuite fixture for trade execution tests
- Common test utilities

Usage:
    Run tests with: pytest tests/
    Run with mock server: pytest tests/ --mock-api
    Skip stress tests: pytest tests/ -m "not stress"
"""

import os
import sys
import pytest
import logging
import threading
import time
from typing import Generator, Dict, Any

# =============================================================================
# Test Configuration
# =============================================================================

# Test environment settings
TEST_ENV = "testnet"
MOCK_API_BASE_URL = os.getenv("MOCK_API_BASE_URL", "http://localhost:8899")
USE_TESTNET = True
ENABLE_STRESS_TESTS = os.getenv("STRESS_TESTS", "false").lower() == "true"

# Setup path for imports
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SKILL_DIR, "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Configure logging for tests
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Import TestSuite from test_trade_execution_suite
# =============================================================================

def _import_test_suite():
    """Import TestSuite and TestResult from test_trade_execution_suite."""
    try:
        # Add tests directory to path
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        
        from test_trade_execution_suite import TestSuite, TestResult
        return TestSuite, TestResult
    except ImportError as e:
        logger.warning(f"Could not import TestSuite: {e}")
        return None, None


# =============================================================================
# Session-Scoped Mock API Server
# =============================================================================

class MockAPIServer:
    """Manages the mock API server lifecycle."""
    
    def __init__(self, port: int = 8899):
        self.port = port
        self.server = None
        self.thread = None
        self._server_module = None
    
    def start(self):
        """Start the mock API server in a background thread."""
        if self.server is not None:
            logger.info(f"Mock API server already running on port {self.port}")
            return
        
        try:
            # Import the mock server module
            mock_server_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "mocks", "mock_api_server.py"
            )
            if mock_server_path not in sys.path:
                sys.path.insert(0, os.path.dirname(mock_server_path))
            
            from mocks.mock_api_server import start_server, MockAPIServerState
            
            # Reset state and start server
            state = MockAPIServerState()
            state.reset()
            
            self.server = start_server(self.port)
            logger.info(f"Mock API server started on port {self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start mock API server: {e}")
            # Don't fail tests if mock server can't start
            # Some tests may not need it
            pass
    
    def stop(self):
        """Stop the mock API server."""
        if self.server is not None:
            try:
                from mocks.mock_api_server import stop_server
                stop_server()
                logger.info("Mock API server stopped")
            except Exception as e:
                logger.warning(f"Error stopping mock server: {e}")
            finally:
                self.server = None
    
    def reset_state(self):
        """Reset the mock server state."""
        try:
            from mocks.mock_api_server import reset_state
            reset_state()
        except Exception as e:
            logger.warning(f"Could not reset mock state: {e}")


# Global mock server instance
_mock_server: MockAPIServer = None


@pytest.fixture(scope="session")
def mock_api_server() -> Generator[MockAPIServer, None, None]:
    """
    Session-scoped fixture that provides and manages the mock API server.
    
    Usage:
        def test_something(mock_api_server):
            # Mock server is running
            ...
    """
    global _mock_server
    
    # Check if we should start mock server
    start_mock = os.getenv("USE_MOCK_API", "true").lower() == "true"
    
    if start_mock:
        _mock_server = MockAPIServer(port=8899)
        _mock_server.start()
        yield _mock_server
        _mock_server.stop()
    else:
        # Yield None if mock server disabled
        yield None


@pytest.fixture(scope="session")
def mock_api_state(mock_api_server):
    """
    Provides access to mock API server state for test manipulation.
    
    Usage:
        def test_something(mock_api_state):
            # Access and modify mock state
            state = mock_api_state.get_state()
            state.balances["USDT"].balance = 50000.0
    """
    if mock_api_server is None:
        pytest.skip("Mock API server not available")
    return mock_api_server


# =============================================================================
# TestSuite Fixture for Trade Execution Tests
# =============================================================================

@pytest.fixture
def test_suite() -> Generator[Any, None, None]:
    """
    Fixture providing a TestSuite instance for trade execution tests.
    
    This fixture:
    - Creates a TestSuite with dry_run=True (default for pytest)
    - Yields it to each test function
    - Prints summary after test completes
    
    Usage:
        def test_api_credentials_loaded(test_suite):
            # Use test_suite to add results
            result = TestResult(name="test", passed=True, message="ok")
            test_suite.add_result(result)
    """
    TestSuite, TestResult = _import_test_suite()
    
    if TestSuite is None:
        # Fallback: create a simple dict-based suite
        logger.warning("Using fallback test suite (dict-based)")
        
        class FallbackTestSuite:
            def __init__(self):
                self.results = []
                self.dry_run = True
            
            def add_result(self, result):
                self.results.append(result)
                logger.info(f"Result: {result}")
            
            def print_summary(self):
                passed = sum(1 for r in self.results if r.get("passed", False))
                failed = len(self.results) - passed
                logger.info(f"Results: {passed} passed, {failed} failed")
        
        suite = FallbackTestSuite()
        yield suite
        suite.print_summary()
        return
    
    # Create proper TestSuite instance
    suite = TestSuite(dry_run=True)
    
    # Also expose TestResult for direct use
    yield suite
    
    # Print summary after test
    suite.print_summary()


# =============================================================================
# Test Environment Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def test_env() -> Dict[str, Any]:
    """Provide test environment configuration."""
    return {
        "env": TEST_ENV,
        "mock_api_url": MOCK_API_BASE_URL,
        "use_testnet": USE_TESTNET,
        "stress_tests_enabled": ENABLE_STRESS_TESTS,
    }


@pytest.fixture
def mock_equity() -> float:
    """Provide a baseline equity value used across tests."""
    return 10000.0


@pytest.fixture
def equity(mock_equity) -> float:
    """Provide equity value for trade execution tests.
    
    This fixture is used by test functions in test_trade_execution_suite.py
    that need an equity value to calculate position sizes and test risk limits.
    """
    return mock_equity


@pytest.fixture
def dry_run() -> bool:
    """Provide dry_run flag for trade execution tests.
    
    When True, tests will not place actual orders on the exchange.
    Default is True for pytest runs.
    """
    return True


@pytest.fixture
def reset_risk_state():
    """Reset risk guard state before each test."""
    try:
        from src.services.risk_service import reset_risk_guard_for_tests
        reset_risk_guard_for_tests()
    except ImportError:
        try:
            from services.risk_service import reset_risk_guard_for_tests
            reset_risk_guard_for_tests()
        except ImportError:
            logger.warning("Could not import risk reset function")
    
    yield
    
    # Cleanup after test
    try:
        reset_risk_guard_for_tests()
    except:
        pass


# =============================================================================
# Circuit Breaker Reset Fixture
# =============================================================================

@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Automatically reset circuit breaker before each test."""
    try:
        from api.aster_api import reset_circuit_breaker
        reset_circuit_breaker()
    except ImportError:
        try:
            from src.api.aster_api import reset_circuit_breaker
            reset_circuit_breaker()
        except ImportError:
            logger.warning("Could not import circuit breaker reset")
    
    yield


# =============================================================================
# Pytest Configuration Hooks
# =============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "stress: mark test as stress test (requires STRESS_TESTS=true)"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as unit test"
    )


def pytest_collection_modifyitems(config, items):
    """
    Modify test collection to handle markers and skip conditions.
    
    - Skip stress tests unless STRESS_TESTS env var is set
    - Skip tests requiring mock server if it's disabled
    """
    skip_stress = pytest.mark.skip(reason="Stress tests disabled (set STRESS_TESTS=true to enable)")
    
    for item in items:
        # Add markers based on test location
        if "stress" in item.nodeid:
            item.add_marker(skip_stress)
        
        # Auto-mark integration tests
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--fixtures"])
