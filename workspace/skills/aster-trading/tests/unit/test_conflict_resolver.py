#!/usr/bin/env python3
"""
Unit tests for ConflictResolver.

Tests conflict resolution between real-time WebSocket events and batch sync data.
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.sync.conflict_resolver import ConflictResolver


class TestConflictResolver:
    """Test cases for ConflictResolver class."""
    
    @pytest.fixture
    def resolver(self):
        """Create a ConflictResolver instance."""
        return ConflictResolver()
    
    # =========================================================================
    # Position Conflict Resolution Tests
    # =========================================================================
    
    def test_resolve_position_batch_only(self, resolver):
        """Test resolution when only batch data exists."""
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 45000.0,
            "update_time": 1000
        }
        
        result, source = resolver.resolve_position_conflict(
            websocket_position={},
            batch_position=batch_position
        )
        
        assert result == batch_position
        assert source == "BATCH_ONLY"
    
    def test_resolve_position_websocket_only(self, resolver):
        """Test resolution when only WebSocket data exists."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.6,
            "entry_price": 46000.0,
            "update_time": 2000
        }
        
        result, source = resolver.resolve_position_conflict(
            websocket_position=websocket_position,
            batch_position={}
        )
        
        assert result == websocket_position
        assert source == "WEBSOCKET_ONLY"
    
    def test_resolve_position_websocket_qty_change(self, resolver):
        """Test resolution when WebSocket has quantity change (likely a fill)."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.6,
            "entry_price": 46000.0,
            "update_time": 2000
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 45000.0,
            "update_time": 1000
        }
        
        result, source = resolver.resolve_position_conflict(
            websocket_position=websocket_position,
            batch_position=batch_position
        )
        
        assert result["position_amt"] == 0.6
        assert source == "WEBSOCKET_QTY_CHANGE"
    
    def test_resolve_position_batch_qty_match(self, resolver):
        """Test resolution when quantities match (trust batch for more context)."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 45000.0,
            "update_time": 2000
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 45000.0,
            "update_time": 1000
        }
        
        result, source = resolver.resolve_position_conflict(
            websocket_position=websocket_position,
            batch_position=batch_position
        )
        
        assert result["position_amt"] == 0.5
        assert source == "BATCH_QTY_MATCH"
    
    def test_resolve_position_batch_more_recent(self, resolver):
        """Test resolution when batch data is more recent."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 45000.0,
            "update_time": 1000
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "entry_price": 46000.0,
            "update_time": 2000
        }
        
        result, source = resolver.resolve_position_conflict(
            websocket_position=websocket_position,
            batch_position=batch_position
        )
        
        assert result == batch_position
        assert source == "BATCH_MORE_RECENT"
    
    # =========================================================================
    # Order Conflict Resolution Tests
    # =========================================================================
    
    def test_resolve_order_batch_only(self, resolver):
        """Test order resolution when only batch data exists."""
        batch_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "NEW",
            "update_time": 1000
        }
        
        result, source = resolver.resolve_order_conflict(
            websocket_order={},
            batch_order=batch_order
        )
        
        assert result == batch_order
        assert source == "BATCH_ONLY"
    
    def test_resolve_order_websocket_only(self, resolver):
        """Test order resolution when only WebSocket data exists."""
        websocket_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "FILLED",
            "update_time": 2000
        }
        
        result, source = resolver.resolve_order_conflict(
            websocket_order=websocket_order,
            batch_order={}
        )
        
        assert result == websocket_order
        assert source == "WEBSOCKET_ONLY"
    
    def test_resolve_order_websocket_filled(self, resolver):
        """Test order resolution - WebSocket FILLED > batch NEW."""
        websocket_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "FILLED",
            "update_time": 2000
        }
        batch_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "NEW",
            "update_time": 1000
        }
        
        result, source = resolver.resolve_order_conflict(
            websocket_order=websocket_order,
            batch_order=batch_order
        )
        
        assert result["status"] == "FILLED"
        assert source == "WEBSOCKET_STATUS"
    
    def test_resolve_order_batch_status_higher(self, resolver):
        """Test order resolution - batch has higher priority status."""
        websocket_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "NEW",
            "update_time": 2000
        }
        batch_order = {
            "order_id": "12345",
            "symbol": "ETHUSDT",
            "status": "FILLED",
            "update_time": 1000
        }
        
        result, source = resolver.resolve_order_conflict(
            websocket_order=websocket_order,
            batch_order=batch_order
        )
        
        assert result["status"] == "FILLED"
        assert source == "BATCH_STATUS"
    
    def test_resolve_order_status_priority(self, resolver):
        """Test order status priority ordering."""
        # Verify priority order
        assert resolver.STATUS_PRIORITY["FILLED"] > resolver.STATUS_PRIORITY["PARTIALLY_FILLED"]
        assert resolver.STATUS_PRIORITY["PARTIALLY_FILLED"] > resolver.STATUS_PRIORITY["CANCELLED"]
        assert resolver.STATUS_PRIORITY["CANCELLED"] > resolver.STATUS_PRIORITY["NEW"]
        assert resolver.STATUS_PRIORITY["NEW"] > resolver.STATUS_PRIORITY["PENDING"]
    
    # =========================================================================
    # Position Conflict Detection Tests
    # =========================================================================
    
    def test_detect_position_conflict_qty_mismatch(self, resolver):
        """Test position conflict detection - quantity mismatch."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.6
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5
        }
        
        assert resolver.detect_position_conflict(websocket_position, batch_position) is True
    
    def test_detect_position_conflict_no_conflict(self, resolver):
        """Test position conflict detection - no conflict."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5
        }
        
        assert resolver.detect_position_conflict(websocket_position, batch_position) is False
    
    def test_detect_position_conflict_side_mismatch(self, resolver):
        """Test position conflict detection - opposite sides."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5  # Long
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": -0.5  # Short
        }
        
        assert resolver.detect_position_conflict(websocket_position, batch_position) is True
    
    def test_detect_position_conflict_missing_data(self, resolver):
        """Test position conflict detection with missing data."""
        assert resolver.detect_position_conflict({}, {"position_amt": 0.5}) is False
        assert resolver.detect_position_conflict({"position_amt": 0.5}, {}) is False
        assert resolver.detect_position_conflict({}, {}) is False
    
    # =========================================================================
    # Order Conflict Detection Tests
    # =========================================================================
    
    def test_detect_order_conflict_status_differs(self, resolver):
        """Test order conflict detection - different status."""
        websocket_order = {"status": "FILLED"}
        batch_order = {"status": "NEW"}
        
        assert resolver.detect_order_conflict(websocket_order, batch_order) is True
    
    def test_detect_order_conflict_no_conflict(self, resolver):
        """Test order conflict detection - no conflict."""
        websocket_order = {"status": "NEW"}
        batch_order = {"status": "NEW"}
        
        assert resolver.detect_order_conflict(websocket_order, batch_order) is False
    
    def test_detect_order_conflict_missing_data(self, resolver):
        """Test order conflict detection with missing data."""
        assert resolver.detect_order_conflict({}, {"status": "NEW"}) is False
        assert resolver.detect_order_conflict({"status": "NEW"}, {}) is False
    
    # =========================================================================
    # Anomaly Detection Tests
    # =========================================================================
    
    def test_detect_anomaly_large_qty_diff(self, resolver):
        """Test anomaly detection - large quantity discrepancy (>10%)."""
        websocket_position = {
            "position_amt": 1.0  # 100% different
        }
        batch_position = {
            "position_amt": 0.1  # from 0.1 to 1.0 = 900% diff
        }
        
        anomaly = resolver.detect_anomaly(websocket_position, batch_position)
        
        assert anomaly is not None
        assert "LARGE_QTY_DIFF" in anomaly
    
    def test_detect_anomaly_side_mismatch(self, resolver):
        """Test anomaly detection - opposite sides."""
        websocket_position = {
            "position_amt": 0.5  # Long
        }
        batch_position = {
            "position_amt": -0.5  # Short
        }
        
        anomaly = resolver.detect_anomaly(websocket_position, batch_position)
        
        assert anomaly is not None
        assert "SIDE_MISMATCH" in anomaly
    
    def test_detect_anomaly_no_anomaly(self, resolver):
        """Test anomaly detection - no anomaly."""
        websocket_position = {
            "position_amt": 0.5
        }
        batch_position = {
            "position_amt": 0.48  # Small diff
        }
        
        anomaly = resolver.detect_anomaly(websocket_position, batch_position)
        
        assert anomaly is None
    
    def test_detect_anomaly_missing_data(self, resolver):
        """Test anomaly detection with missing data."""
        assert resolver.detect_anomaly({}, {"position_amt": 0.5}) is None
        assert resolver.detect_anomaly({"position_amt": 0.5}, {}) is None
    
    # =========================================================================
    # Resolve with Anomaly Check Tests
    # =========================================================================
    
    def test_resolve_with_anomaly_check_no_anomaly(self, resolver):
        """Test resolve with anomaly check - no anomaly."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.5,
            "update_time": 2000
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.48,
            "update_time": 1000
        }
        
        resolved, source, anomaly = resolver.resolve_with_anomaly_check(
            websocket_position, batch_position
        )
        
        assert anomaly is None
        assert source == "WEBSOCKET_QTY_CHANGE"
    
    def test_resolve_with_anomaly_check_with_anomaly(self, resolver):
        """Test resolve with anomaly check - with anomaly."""
        websocket_position = {
            "symbol": "BTCUSDT",
            "position_amt": 1.0,
            "update_time": 2000
        }
        batch_position = {
            "symbol": "BTCUSDT",
            "position_amt": 0.1,
            "update_time": 1000
        }
        
        resolved, source, anomaly = resolver.resolve_with_anomaly_check(
            websocket_position, batch_position
        )
        
        assert anomaly is not None
        assert "LARGE_QTY_DIFF" in anomaly
    
    # =========================================================================
    # Edge Cases
    # =========================================================================
    
    def test_resolve_position_none_websocket(self, resolver):
        """Test resolution with None websocket data."""
        result, source = resolver.resolve_position_conflict(None, {"position_amt": 0.5})
        
        assert result == {"position_amt": 0.5}
        assert source == "BATCH_ONLY"
    
    def test_resolve_position_none_batch(self, resolver):
        """Test resolution with None batch data."""
        result, source = resolver.resolve_position_conflict({"position_amt": 0.5}, None)
        
        assert result == {"position_amt": 0.5}
        assert source == "WEBSOCKET_ONLY"
    
    def test_resolve_order_none_websocket(self, resolver):
        """Test order resolution with None websocket data."""
        result, source = resolver.resolve_order_conflict(None, {"status": "NEW"})
        
        assert result == {"status": "NEW"}
        assert source == "BATCH_ONLY"
    
    def test_detect_position_conflict_zero_qty(self, resolver):
        """Test position conflict detection with zero quantity."""
        websocket_position = {"position_amt": 0}
        batch_position = {"position_amt": 0}
        
        # Should not raise and should return False (no conflict)
        assert resolver.detect_position_conflict(websocket_position, batch_position) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
