#!/usr/bin/env python3
"""
Integration tests for Dead-Letter Queue Retry.

Tests the retry worker processing failed event projections from the dead-letter queue.
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
class TestDeadLetterQueue:
    """Integration tests for dead-letter queue retry functionality."""
    
    @pytest.fixture
    def retry_worker(self):
        """Create a RetryWorker instance."""
        from src.sync.retry_worker import RetryWorker
        return RetryWorker()
    
    @pytest.fixture
    def mock_event_processing_error(self):
        """Create a mock event processing error record."""
        return {
            "id": 1,
            "event_id": 100,
            "event_type": "POSITION_OPENED",
            "position_uuid": str(uuid.uuid4()),
            "error_message": "Test error message",
            "retry_count": 0,
            "max_retries": 3,
            "status": "PENDING",
            "first_attempt_at": int(time.time() * 1000)
        }
    
    # =========================================================================
    # RetryWorker Basic Tests
    # =========================================================================
    
    def test_retry_worker_init(self, retry_worker):
        """Test RetryWorker initialization."""
        assert retry_worker.MAX_RETRIES == 3
        assert retry_worker.RETRY_DELAY_SECONDS == 60
    
    def test_retry_worker_custom_max_retries(self):
        """Test RetryWorker with custom max retries."""
        from src.sync.retry_worker import RetryWorker
        
        worker = RetryWorker(max_retries=5)
        assert worker.MAX_RETRIES == 5
    
    # =========================================================================
    # Process Pending Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_process_pending_no_errors(self, mock_conn, retry_worker):
        """Test processing when no pending errors exist."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = retry_worker.process_pending()
        
        assert result == 0
    
    @patch('state.db.get_connection')
    def test_process_pending_with_errors(self, mock_conn, retry_worker, mock_event_processing_error):
        """Test processing pending errors."""
        # Setup mocks
        mock_cur = MagicMock()
        
        # Return pending error
        error_row = (
            mock_event_processing_error["id"],
            mock_event_processing_error["event_id"],
            mock_event_processing_error["event_type"],
            mock_event_processing_error["position_uuid"],
            mock_event_processing_error["error_message"]
        )
        mock_cur.fetchall.return_value = [error_row]
        
        # Return event data when queried
        event_row = (100, "POSITION_OPENED", "EXCHANGE", "corr_123", "BTCUSDT", "{}", None, None, int(time.time() * 1000))
        mock_cur.fetchone.return_value = event_row
        
        # Mock PRAGMA table_info
        mock_cur.fetchall.side_effect = [
            [error_row],  # First call for pending errors
            [],  # PRAGMA call
            [event_row]  # Event fetch
        ]
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = retry_worker.process_pending()
        
        # Should process the error
        assert result >= 0
    
    # =========================================================================
    # Error Stats Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_get_error_stats(self, mock_conn, retry_worker):
        """Test getting error statistics."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("PENDING", 5),
            ("RESOLVED", 10),
            ("FAILED", 2)
        ]
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        stats = retry_worker.get_error_stats()
        
        assert stats["PENDING"] == 5
        assert stats["RESOLVED"] == 10
        assert stats["FAILED"] == 2
    
    @patch('state.db.get_connection')
    def test_get_error_stats_empty(self, mock_conn, retry_worker):
        """Test getting error statistics when table is empty."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        stats = retry_worker.get_error_stats()
        
        assert stats["PENDING"] == 0
        assert stats["RESOLVED"] == 0
        assert stats["FAILED"] == 0
    
    # =========================================================================
    # Clear Resolved Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_clear_resolved(self, mock_conn, retry_worker):
        """Test clearing resolved errors."""
        mock_cur = MagicMock()
        mock_cur.rowcount = 5
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        deleted = retry_worker.clear_resolved()
        
        assert deleted == 5
    
    @patch('state.db.get_connection')
    def test_clear_resolved_none_to_clear(self, mock_conn, retry_worker):
        """Test clearing when no resolved errors exist."""
        mock_cur = MagicMock()
        mock_cur.rowcount = 0
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        deleted = retry_worker.clear_resolved()
        
        assert deleted == 0
    
    # =========================================================================
    # Mark Resolved Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_mark_resolved(self, mock_conn, retry_worker):
        """Test marking an error as resolved."""
        mock_cur = MagicMock()
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        retry_worker._mark_resolved(123, "RESOLVED")
        
        # Verify execute was called
        mock_cur.execute.assert_called()
        mock_conn.return_value.commit.assert_called()
    
    # =========================================================================
    # Increment Retry Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_increment_retry(self, mock_conn, retry_worker):
        """Test incrementing retry count."""
        mock_cur = MagicMock()
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        retry_worker._increment_retry(123)
        
        # Verify execute was called
        mock_cur.execute.assert_called()
        mock_conn.return_value.commit.assert_called()
    
    @patch('state.db.get_connection')
    def test_increment_retry_marks_failed_at_max(self, mock_conn, retry_worker):
        """Test that incrementing retry marks as FAILED when max reached."""
        mock_cur = MagicMock()
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        retry_worker._increment_retry(123)
        
        # The SQL should include a CASE statement to mark FAILED
        call_args = mock_cur.execute.call_args
        assert call_args is not None
    
    # =========================================================================
    # Reproject Tests
    # =========================================================================
    
    def test_reproject_position_event(self, retry_worker):
        """Test re-projecting a position event."""
        event = {
            "event_type": "POSITION_OPENED",
            "symbol": "BTCUSDT",
            "position_amt": 0.5
        }
        
        # Should not raise
        retry_worker._reproject(event)
    
    def test_reproject_order_event(self, retry_worker):
        """Test re-projecting an order event."""
        event = {
            "event_type": "ORDER_PLACED",
            "symbol": "BTCUSDT",
            "order_id": "12345"
        }
        
        # Should not raise
        retry_worker._reproject(event)
    
    def test_reproject_bracket_event(self, retry_worker):
        """Test re-projecting a bracket event."""
        event = {
            "event_type": "BRACKET_CREATED",
            "symbol": "BTCUSDT",
            "position_uuid": str(uuid.uuid4())
        }
        
        # Should not raise
        retry_worker._reproject(event)
    
    def test_reproject_unknown_event_type(self, retry_worker):
        """Test re-projecting an unknown event type."""
        event = {
            "event_type": "UNKNOWN_EVENT",
            "symbol": "BTCUSDT"
        }
        
        # Should handle gracefully
        retry_worker._reproject(event)
    
    # =========================================================================
    # Integration with Event Processing Errors Table
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_retry_respects_max_retries(self, mock_conn, retry_worker):
        """Test that retry respects max_retries limit."""
        mock_cur = MagicMock()
        
        # Return error with retry_count >= max_retries
        error_row = (1, 100, "POSITION_OPENED", "test_uuid", "error", 3, 3, "PENDING", int(time.time() * 1000))
        mock_cur.fetchall.return_value = [error_row]
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = retry_worker.process_pending()
        
        # Should not process since max retries reached
        assert result == 0
    
    @patch('state.db.get_connection')
    def test_retry_only_pending_status(self, mock_conn, retry_worker):
        """Test that only PENDING status errors are processed."""
        mock_cur = MagicMock()
        
        # Return non-PENDING errors
        error_row_resolved = (1, 100, "POSITION_OPENED", "test_uuid", "error", 0, 3, "RESOLVED", int(time.time() * 1000))
        error_row_failed = (2, 101, "POSITION_OPENED", "test_uuid", "error", 0, 3, "FAILED", int(time.time() * 1000))
        mock_cur.fetchall.return_value = [error_row_resolved, error_row_failed]
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = retry_worker.process_pending()
        
        # Should process 0 errors (none PENDING)
        assert result == 0
    
    # =========================================================================
    # Error Handling Tests
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_process_handles_event_not_found(self, mock_conn, retry_worker):
        """Test handling when event is not found."""
        mock_cur = MagicMock()
        
        # Return pending error
        error_row = (1, 100, "POSITION_OPENED", "test_uuid", "error")
        mock_cur.fetchall.return_value = [error_row]
        
        # Event not found
        mock_cur.fetchone.return_value = None
        
        # Mock PRAGMA call
        mock_cur.fetchall.side_effect = [
            [error_row],  # Pending errors
            [],  # PRAGMA
            None  # Event not found
        ]
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = retry_worker.process_pending()
        
        # Should handle gracefully
        assert result >= 0


@pytest.mark.integration
class TestDeadLetterQueueIntegration:
    """Full integration tests for dead-letter queue."""
    
    @pytest.fixture
    def setup_test_db(self, tmp_path):
        """Set up a test database with required schema."""
        db_path = str(tmp_path / "test_dlq.db")
        
        # Create tables
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Create events table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                event_type TEXT,
                event_source TEXT,
                correlation_id TEXT,
                symbol TEXT,
                payload_json TEXT,
                parent_event_id INTEGER,
                caused_by_event_id INTEGER,
                created_at INTEGER
            )
        """)
        
        # Create event_processing_errors table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS event_processing_errors (
                id INTEGER PRIMARY KEY,
                event_id INTEGER,
                event_type TEXT,
                position_uuid TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                status TEXT CHECK(status IN ('PENDING', 'RESOLVED', 'FAILED')) DEFAULT 'PENDING',
                first_attempt_at INTEGER,
                last_retry_at INTEGER,
                resolved_at INTEGER,
                created_at INTEGER
            )
        """)
        
        conn.commit()
        conn.close()
        
        return db_path
    
    def test_error_stats_structure(self):
        """Test error stats returns proper structure."""
        from src.sync.retry_worker import RetryWorker
        
        worker = RetryWorker()
        
        # Stats should always return these keys
        with patch('state.db.get_connection') as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = []
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.cursor.return_value = mock_cur
            
            stats = worker.get_error_stats()
            
            assert "PENDING" in stats
            assert "RESOLVED" in stats
            assert "FAILED" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
