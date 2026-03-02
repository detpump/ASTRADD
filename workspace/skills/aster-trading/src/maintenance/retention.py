# src/maintenance/retention.py
"""
Data Retention Policy Management.

This module implements data retention policies to manage database size
and comply with data storage requirements.

Retention Periods:
- Raw snapshots: 7 days
- Events: 30 days
- Scale-in logs: 90 days
- Sync batches: 90 days
"""

import logging
import time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


# Retention periods in milliseconds
RETENTION_PERIODS = {
    "raw_snapshots": 7 * 24 * 60 * 60 * 1000,    # 7 days
    "events": 30 * 24 * 60 * 60 * 1000,           # 30 days
    "scale_in_logs": 90 * 24 * 60 * 60 * 1000,    # 90 days
    "sync_batches": 90 * 24 * 60 * 60 * 1000,     # 90 days
    "event_errors": 30 * 24 * 60 * 60 * 1000,     # 30 days
}


class RetentionManager:
    """
    Manages data retention policies for the trading system.
    
    Provides methods to clean up old data based on retention periods.
    """
    
    def __init__(self):
        """Initialize the retention manager."""
        self._last_cleanup: Dict[str, int] = {}
    
    def cleanup_all(self, dry_run: bool = False) -> Dict[str, Tuple[int, int]]:
        """
        Run all retention cleanup operations.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Dictionary of table_name -> (deleted_count, remaining_count)
        """
        results = {}
        
        results["raw_position_snapshots"] = self.cleanup_raw_snapshots(dry_run)
        results["raw_order_snapshots"] = self.cleanup_raw_order_snapshots(dry_run)
        results["events"] = self.cleanup_events(dry_run)
        results["scale_in_conditions_log"] = self.cleanup_scale_in_logs(dry_run)
        results["sync_batches"] = self.cleanup_sync_batches(dry_run)
        results["event_processing_errors"] = self.cleanup_event_errors(dry_run)
        
        # Run database optimization
        if not dry_run:
            self._optimize_database()
        
        return results
    
    def cleanup_raw_snapshots(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up raw position snapshots older than 7 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        return self._cleanup_table(
            "raw_position_snapshots",
            "created_at",
            RETENTION_PERIODS["raw_snapshots"],
            dry_run
        )
    
    def cleanup_raw_order_snapshots(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up raw order snapshots older than 7 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        return self._cleanup_table(
            "raw_order_snapshots",
            "created_at",
            RETENTION_PERIODS["raw_snapshots"],
            dry_run
        )
    
    def cleanup_events(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up events older than 30 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        return self._cleanup_table(
            "events",
            "created_at",
            RETENTION_PERIODS["events"],
            dry_run
        )
    
    def cleanup_scale_in_logs(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up scale-in logs older than 90 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        return self._cleanup_table(
            "scale_in_conditions_log",
            "created_at",
            RETENTION_PERIODS["scale_in_logs"],
            dry_run
        )
    
    def cleanup_sync_batches(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up sync batches older than 90 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        return self._cleanup_table(
            "sync_batches",
            "created_at",
            RETENTION_PERIODS["sync_batches"],
            dry_run
        )
    
    def cleanup_event_errors(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up resolved event processing errors older than 30 days.
        
        Args:
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Count records to delete
            cutoff_time = int(time.time() * 1000) - RETENTION_PERIODS["event_errors"]
            cur.execute(
                """SELECT COUNT(*) FROM event_processing_errors 
                   WHERE status IN ('RESOLVED', 'FAILED') 
                   AND resolved_at < ?""",
                (cutoff_time,)
            )
            to_delete = cur.fetchone()[0]
            
            if dry_run:
                cur.execute("SELECT COUNT(*) FROM event_processing_errors")
                remaining = cur.fetchone()[0]
                return to_delete, remaining
            
            # Delete old resolved/failed errors
            cur.execute(
                """DELETE FROM event_processing_errors 
                   WHERE status IN ('RESOLVED', 'FAILED') 
                   AND resolved_at < ?""",
                (cutoff_time,)
            )
            
            cur.execute("SELECT COUNT(*) FROM event_processing_errors")
            remaining = cur.fetchone()[0]
            
            conn.commit()
            
            logger.info(
                f"Cleaned up {to_delete} event processing errors, "
                f"{remaining} remaining"
            )
            
            return to_delete, remaining
    
    def _cleanup_table(
        self,
        table_name: str,
        timestamp_column: str,
        retention_ms: int,
        dry_run: bool = False
    ) -> Tuple[int, int]:
        """
        Generic cleanup method for a table.
        
        Args:
            table_name: Name of the table to clean
            timestamp_column: Name of the timestamp column
            retention_ms: Retention period in milliseconds
            dry_run: If True, only count records without deleting
        
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            cutoff_time = int(time.time() * 1000) - retention_ms
            
            # Count records to delete
            cur.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE {timestamp_column} < ?",
                (cutoff_time,)
            )
            to_delete = cur.fetchone()[0]
            
            if dry_run:
                cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                remaining = cur.fetchone()[0]
                return to_delete, remaining
            
            # Delete old records
            cur.execute(
                f"DELETE FROM {table_name} WHERE {timestamp_column} < ?",
                (cutoff_time,)
            )
            
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            remaining = cur.fetchone()[0]
            
            conn.commit()
            
            logger.info(
                f"Cleaned up {to_delete} records from {table_name}, "
                f"{remaining} remaining"
            )
            
            return to_delete, remaining
    
    def _optimize_database(self):
        """Run database optimization after cleanup."""
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Analyze tables for query optimization (V3 schema tables)
            tables = [
                "raw_position_snapshots",
                "raw_order_snapshots",
                "raw_account_snapshots",
                "events",
                "positions_v3",
                "position_entries",
                "position_closes",
                "bracket_orders",
                "risk_states",
                "risk_events",
                "sync_metrics",
                "sync_batches"
            ]
            
            for table in tables:
                try:
                    cur.execute(f"ANALYZE {table}")
                except Exception as e:
                    logger.warning(f"Failed to analyze {table}: {e}")
            
            # Run SQLite optimize pragma
            cur.execute("PRAGMA optimize")
            
            conn.commit()
            logger.info("Database optimization completed")
    
    def get_table_sizes(self) -> Dict[str, int]:
        """
        Get current row counts for all retention-managed tables.
        
        Returns:
            Dictionary of table_name -> row_count
        """
        from state.db import get_connection
        
        tables = [
            "raw_position_snapshots",
            "raw_order_snapshots",
            "events",
            "scale_in_conditions_log",
            "sync_batches",
            "event_processing_errors"
        ]
        
        sizes = {}
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            for table in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    sizes[table] = cur.fetchone()[0]
                except Exception as e:
                    logger.warning(f"Failed to get size for {table}: {e}")
                    sizes[table] = -1
        
        return sizes


def run_retention_cleanup(dry_run: bool = False) -> Dict[str, Tuple[int, int]]:
    """
    Run the retention cleanup process.
    
    This is the main entry point for scheduled retention cleanup.
    
    Args:
        dry_run: If True, only count records without deleting
    
    Returns:
        Dictionary of table_name -> (deleted_count, remaining_count)
    """
    logger.info(f"Starting retention cleanup (dry_run={dry_run})")
    
    manager = RetentionManager()
    results = manager.cleanup_all(dry_run)
    
    total_deleted = sum(deleted for deleted, _ in results.values())
    logger.info(f"Retention cleanup complete: {total_deleted} total records processed")
    
    return results


if __name__ == "__main__":
    # Run retention cleanup when executed directly
    import sys
    
    dry_run = "--dry-run" in sys.argv
    results = run_retention_cleanup(dry_run)
    
    print("\nRetention Cleanup Results:")
    print("-" * 50)
    for table, (deleted, remaining) in results.items():
        print(f"{table}: {deleted} deleted, {remaining} remaining")
