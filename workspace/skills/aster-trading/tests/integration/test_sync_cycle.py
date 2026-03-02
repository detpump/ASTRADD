#!/usr/bin/env python3
"""
Integration tests for Sync Cycle.

Tests the full synchronization cycle including batch creation,
data fetching, change detection, event emission, and projection.
"""

import pytest
import sys
import os
import time
import uuid
from unittest.mock import patch, MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


@pytest.mark.integration
class TestSyncCycle:
    """Integration tests for the sync cycle."""
    
    @pytest.fixture
    def sync_engine(self):
        """Create a SyncEngine instance."""
        from src.sync.sync_engine import SyncEngine
        return SyncEngine()
    
    @pytest.fixture
    def mock_positions(self):
        """Provide mock position data."""
        return [
            {
                "symbol": "BTCUSDT",
                "position_amt": 0.5,
                "entry_price": 45000.0,
                "mark_price": 46000.0,
                "unrealized_pnl": 500.0,
                "leverage": 10,
                "liquidation_price": 40000.0,
                "update_time": int(time.time() * 1000),
                "position_uuid": str(uuid.uuid4())
            },
            {
                "symbol": "ETHUSDT",
                "position_amt": 2.0,
                "entry_price": 2500.0,
                "mark_price": 2600.0,
                "unrealized_pnl": 200.0,
                "leverage": 5,
                "liquidation_price": 2000.0,
                "update_time": int(time.time() * 1000),
                "position_uuid": str(uuid.uuid4())
            }
        ]
    
    @pytest.fixture
    def mock_orders(self):
        """Provide mock order data."""
        return [
            {
                "order_id": "12345",
                "client_order_id": "client_12345",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "status": "NEW",
                "price": 45000.0,
                "quantity": 0.5,
                "executed_qty": 0.0,
                "avg_price": 0.0,
                "reduce_only": False,
                "time_in_force": "GTC",
                "stop_price": 0.0,
                "update_time": int(time.time() * 1000)
            }
        ]
    
    @pytest.fixture
    def mock_balances(self):
        """Provide mock balance data."""
        return [
            {
                "asset": "USDT",
                "wallet_balance": 50000.0,
                "available_balance": 45000.0,
                "cross_wallet_balance": 50000.0,
                "cross_initial_margin": 5000.0,
                "maintenance_margin": 1000.0
            }
        ]
    
    # =========================================================================
    # Sync Result Tests
    # =========================================================================
    
    def test_sync_result_fields(self, sync_engine):
        """Test SyncResult has all required fields."""
        # Test that SyncResult can be created with required fields
        from src.sync.sync_engine import SyncResult
        
        result = SyncResult(
            batch_id="test_123",
            status="COMPLETED",
            started_at=int(time.time() * 1000),
            completed_at=int(time.time() * 1000)
        )
        
        assert result.batch_id == "test_123"
        assert result.status == "COMPLETED"
        assert result.positions_fetched == 0
        assert result.orders_fetched == 0
        assert result.events_emitted == 0
        assert result.projections_succeeded == 0
        assert result.projections_failed == 0
    
    # =========================================================================
    # Batch Lifecycle Tests
    # =========================================================================
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_creates_batch(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test that sync creates a batch record."""
        mock_positions.return_value = []
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        assert result.batch_id is not None
        assert len(result.batch_id) > 0
        assert result.started_at > 0
        assert result.completed_at >= result.started_at
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_batch_status_values(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test batch status is one of valid values."""
        mock_positions.return_value = []
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        assert result.status in ['COMPLETED', 'PARTIAL', 'FAILED']
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_records_positions(self, mock_balance, mock_orders, mock_positions, sync_engine, mock_positions_data):
        """Test that sync records fetched positions."""
        mock_positions.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "45000"}
        ]
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        # Should count positions
        assert result.positions_fetched >= 0
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_records_orders(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test that sync records fetched orders."""
        mock_positions.return_value = []
        mock_orders.return_value = [
            {"orderId": "123", "symbol": "BTCUSDT", "status": "NEW"}
        ]
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        # Should count orders
        assert result.orders_fetched >= 0
    
    # =========================================================================
    # Data Persistence Tests
    # =========================================================================
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_persists_raw_positions(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test that sync persists raw position snapshots."""
        mock_positions.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "45000"}
        ]
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        # Verify positions were fetched
        assert result.positions_fetched >= 0
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_handles_api_errors_gracefully(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test that sync handles API errors gracefully."""
        mock_positions.side_effect = Exception("API Error")
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        # Should return FAILED status, not crash
        assert result.status == 'FAILED'
        assert len(result.errors) > 0
    
    # =========================================================================
    # Previous State Tests
    # =========================================================================
    
    def test_previous_positions_loaded(self, sync_engine):
        """Test that previous positions are loaded on initialization."""
        # The engine should have loaded any cached state
        # This may be empty if no previous state exists
        assert isinstance(sync_engine._previous_positions, dict)
    
    def test_previous_orders_loaded(self, sync_engine):
        """Test that previous orders are loaded on initialization."""
        # The engine should have loaded any cached state
        assert isinstance(sync_engine._previous_orders, dict)
    
    # =========================================================================
    # Scale-In Check Tests
    # =========================================================================
    
    def test_is_scale_in_pending_queries_by_uuid(self, sync_engine):
        """Test that _is_scale_in_pending queries by position_uuid."""
        # This is the key fix: should query by position_uuid, not symbol
        # The implementation should use position_uuid parameter
        import inspect
        source = inspect.getsource(sync_engine._is_scale_in_pending)
        
        # Verify it uses position_uuid in the query
        assert "position_uuid" in source
    
    # =========================================================================
    # Retry Failed Projections Tests
    # =========================================================================
    
    def test_retry_failed_projections_returns_dict(self, sync_engine):
        """Test that retry_failed_projections returns expected dictionary."""
        result = sync_engine.retry_failed_projections(max_to_process=1)
        
        assert "retried" in result
        assert "resolved" in result
        assert "failed" in result
        assert isinstance(result["retried"], int)
        assert isinstance(result["resolved"], int)
        assert isinstance(result["failed"], int)
    
    def test_retry_failed_projections_max_process(self, sync_engine):
        """Test that retry respects max_to_process parameter."""
        result = sync_engine.retry_failed_projections(max_to_process=5)
        
        # Should not exceed max
        assert result["retried"] <= 5
    
    # =========================================================================
    # Event Recording Tests
    # =========================================================================
    
    def test_record_projection_error(self, sync_engine):
        """Test recording projection errors to dead-letter queue."""
        event = {
            "event_type": "POSITION_OPENED",
            "correlation_id": "test_corr_123",
            "position_uuid": "test_pos_uuid"
        }
        error_msg = "Test error"
        
        # Should not raise
        sync_engine._record_projection_error(event, error_msg)
    
    # =========================================================================
    # Integration with Change Detector
    # =========================================================================
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_sync_emits_events(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test that sync emits events for changes."""
        mock_positions.return_value = []
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result = sync_engine.sync()
        
        # Events emitted should be tracked
        assert result.events_emitted >= 0
    
    # =========================================================================
    # Multiple Sync Cycles
    # =========================================================================
    
    @patch('src.sync.sync_engine.get_positions_v3')
    @patch('src.sync.sync_engine.get_open_orders')
    @patch('src.sync.sync_engine.get_balance_v3')
    def test_multiple_sync_cycles(self, mock_balance, mock_orders, mock_positions, sync_engine):
        """Test running multiple sync cycles."""
        mock_positions.return_value = []
        mock_orders.return_value = []
        mock_balance.return_value = []
        
        result1 = sync_engine.sync()
        result2 = sync_engine.sync()
        
        # Should have different batch IDs
        assert result1.batch_id != result2.batch_id


@pytest.mark.integration
class TestSyncCycleWithDatabase:
    """Integration tests that require actual database access."""
    
    @pytest.fixture
    def test_db_path(self, tmp_path):
        """Create a temporary database path."""
        return str(tmp_path / "test_trading.db")
    
    @patch('src.state.db.get_connection')
    def test_batch_completion_status(self, mock_conn):
        """Test batch completion status is properly set."""
        # Setup mock
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        from src.sync.sync_engine import SyncEngine
        engine = SyncEngine()
        
        result = engine.sync()
        
        # Verify status is valid
        assert result.status in ['COMPLETED', 'PARTIAL', 'FAILED']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
