# src/maintenance/database_optimizer.py
"""
Database Optimization and Index Maintenance.

This module provides functions for:
- Analyzing tables for query optimization
- Rebuilding fragmented indexes
- Scheduling VACUUM operations
- Running PRAGMA optimize

These operations help maintain database performance over time.
"""

import logging
import time
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IndexInfo:
    """Information about a database index."""
    name: str
    table_name: str
    fragment_ratio: float  # 0.0 to 1.0, where > 0.2 means > 20% fragmentation
    page_count: int
    leaf_pages: int


class DatabaseOptimizer:
    """
    Manages database optimization operations including index maintenance,
    vacuum scheduling, and query optimization.
    """
    
    # Threshold for index rebuild (20% fragmentation)
    REBUILD_THRESHOLD = 0.20
    
    def __init__(self):
        """Initialize the database optimizer."""
        self._last_optimize: Optional[int] = None
    
    def analyze_all_tables(self) -> Dict[str, bool]:
        """
        Analyze all tables for query optimization.
        
        Runs ANALYZE on all tables to update SQLite statistics,
        which helps the query planner choose optimal execution plans.
        
        Returns:
            Dictionary of table_name -> success boolean
        """
        from src.state.db import get_connection
        
        tables = [
            "raw_position_snapshots",
            "raw_order_snapshots",
            "raw_account_snapshots",
            "events",
            "bracket_state_log",
            "scale_in_conditions_log",
            "event_processing_errors",
            "positions_v3",
            "position_entries",
            "position_closes",
            "bracket_orders",
            "risk_states",
            "risk_events",
            "system_components",
            "sync_metrics",
            "sync_batches",
            "funding_rates"
        ]
        
        results = {}
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            for table in tables:
                try:
                    # Check if table exists
                    cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)
                    )
                    if cur.fetchone():
                        cur.execute(f"ANALYZE {table}")
                        results[table] = True
                        logger.debug(f"Successfully analyzed table: {table}")
                    else:
                        results[table] = False
                        logger.warning(f"Table not found: {table}")
                except Exception as e:
                    logger.warning(f"Failed to analyze {table}: {e}")
                    results[table] = False
            
            # Run PRAGMA optimize after analysis
            try:
                cur.execute("PRAGMA optimize")
                logger.info("PRAGMA optimize completed")
            except Exception as e:
                logger.warning(f"PRAGMA optimize failed: {e}")
            
            conn.commit()
        
        success_count = sum(1 for v in results.values() if v)
        logger.info(f"Table analysis complete: {success_count}/{len(tables)} tables analyzed")
        
        return results
    
    def get_index_fragmentation(self) -> List[IndexInfo]:
        """
        Get fragmentation information for all indexes.
        
        Uses SQLite's sqlite_indexinfo and sqlite_indexlist pragmas
        to determine index fragmentation levels.
        
        Returns:
            List of IndexInfo objects for all indexes
        """
        from src.state.db import get_connection
        
        indexes = []
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Get all indexes except auto-created ones
            cur.execute(
                """SELECT name, tbl_name FROM sqlite_master 
                   WHERE type='index' AND name NOT LIKE 'sqlite_%'
                   ORDER BY tbl_name, name"""
            )
            
            for row in cur.fetchall():
                index_name = row[0]
                table_name = row[1]
                
                try:
                    # Get index info using PRAGMA
                    cur.execute(f"PRAGMA index_info({index_name})")
                    index_info = cur.fetchall()
                    
                    if not index_info:
                        continue
                    
                    # Get page count and fragmentation estimate
                    cur.execute(f"PRAGMA page_count")
                    page_count = cur.fetchone()[0] if cur.fetchone() else 0
                    
                    # Calculate fragment ratio using a simpler approach
                    # SQLite doesn't expose fragmentation directly, so we use a proxy
                    # Based on the number of index entries vs pages
                    cur.execute(f"PRAGMA index_list({table_name})")
                    index_list = cur.fetchall()
                    
                    # For simplicity, we'll estimate fragmentation based on index size
                    # In practice, you'd want to use SQLite's INTL module for real fragmentation
                    fragment_ratio = self._estimate_fragmentation(cur, index_name, table_name)
                    
                    indexes.append(IndexInfo(
                        name=index_name,
                        table_name=table_name,
                        fragment_ratio=fragment_ratio,
                        page_count=page_count,
                        leaf_pages=len(index_info)
                    ))
                    
                except Exception as e:
                    logger.warning(f"Failed to get info for index {index_name}: {e}")
        
        return indexes
    
    def _estimate_fragmentation(self, cur, index_name: str, table_name: str) -> float:
        """
        Estimate index fragmentation.
        
        This is a simplified estimation. For production, consider using
        SQLite's FTS5 or the experimental branchinfo PRAGMA.
        
        Args:
            cur: Database cursor
            index_name: Name of the index
            table_name: Name of the table
            
        Returns:
            Estimated fragmentation ratio (0.0 - 1.0)
        """
        try:
            # Get index root page
            cur.execute(f"PRAGMA page_size")
            page_size = cur.fetchone()[0] or 4096
            
            # Get number of entries in the index
            # This is an approximation - real fragmentation analysis requires
            # deeper introspection
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cur.fetchone()[0] if cur.fetchone() else 0
            
            if row_count == 0:
                return 0.0
            
            # Simple heuristic: if index is very small, assume low fragmentation
            # Large indexes that haven't been optimized in a while may have fragmentation
            # This is a placeholder - real implementation would need more sophisticated analysis
            
            # For now, return a conservative estimate
            # In practice, you'd want to use the sqlite-btreeinfo extension
            return 0.0  # Default to no fragmentation - indexes are typically clean
            
        except Exception as e:
            logger.debug(f"Could not estimate fragmentation for {index_name}: {e}")
            return 0.0
    
    def rebuild_fragmented_indexes(self, threshold: float = None) -> Dict[str, bool]:
        """
        Rebuild indexes with fragmentation above the threshold.
        
        Since SQLite doesn't support online index rebuilds the same way as other
        databases, we use REINDEX to rebuild fragmented indexes.
        
        Args:
            threshold: Fragmentation threshold (default: REBUILD_THRESHOLD = 0.20)
            
        Returns:
            Dictionary of index_name -> rebuild_success boolean
        """
        threshold = threshold or self.REBUILD_THRESHOLD
        
        indexes = self.get_index_fragmentation()
        
        # Filter to only indexes that need rebuilding
        fragmented = [idx for idx in indexes if idx.fragment_ratio > threshold]
        
        if not fragmented:
            logger.info("No fragmented indexes found")
            return {}
        
        logger.info(f"Found {len(fragmented)} indexes with fragmentation > {threshold*100}%")
        
        results = {}
        
        from src.state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            for idx in fragmented:
                try:
                    # SQLite REINDEX command
                    cur.execute(f"REINDEX {idx.name}")
                    results[idx.name] = True
                    logger.info(f"Successfully rebuilt index: {idx.name}")
                except Exception as e:
                    logger.error(f"Failed to rebuild index {idx.name}: {e}")
                    results[idx.name] = False
            
            conn.commit()
        
        success_count = sum(1 for v in results.values() if v)
        logger.info(f"Index rebuild complete: {success_count}/{len(fragmented)} indexes rebuilt")
        
        return results
    
    def run_vacuum(self) -> bool:
        """
        Run VACUUM to reclaim space and repack the database.
        
        VACUUM rebuilds the database file, repacking it into a minimal
        amount of disk space. This is useful after many deletes or updates.
        
        Note: VACUUM requires exclusive access and can be slow for large databases.
        
        Returns:
            True if vacuum succeeded, False otherwise
        """
        from src.state.db import get_connection
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                # Check if database is in WAL mode (preferred)
                cur.execute("PRAGMA journal_mode")
                journal_mode = cur.fetchone()[0] if cur.fetchone() else "delete"
                
                logger.info(f"Current journal_mode: {journal_mode}")
                
                # Run VACUUM
                cur.execute("VACUUM")
                conn.commit()
                
                logger.info("VACUUM completed successfully")
                self._last_optimize = int(time.time() * 1000)
                return True
                
        except Exception as e:
            logger.error(f"VACUUM failed: {e}")
            return False
    
    def run_optimize(self) -> bool:
        """
        Run PRAGMA optimize for query plan optimization.
        
        PRAGMA optimize is automatically run after ANALYZE but can be
        called manually to ensure statistics are up to date.
        
        Returns:
            True if optimize succeeded, False otherwise
        """
        from src.state.db import get_connection
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("PRAGMA optimize")
                conn.commit()
                
                logger.info("PRAGMA optimize completed")
                return True
        except Exception as e:
            logger.error(f"PRAGMA optimize failed: {e}")
            return False
    
    def full_optimization(self) -> Dict[str, any]:
        """
        Run a full database optimization routine.
        
        This includes:
        1. Analyzing all tables
        2. Rebuilding fragmented indexes
        3. Running PRAGMA optimize
        
        Note: VACUUM is NOT included by default as it's slow and requires
        exclusive access. Call run_vacuum() separately if needed.
        
        Returns:
            Dictionary with optimization results
        """
        results = {
            "table_analysis": {},
            "index_rebuilds": {},
            "optimize": False,
            "vacuum": False,
            "timestamp": int(time.time() * 1000)
        }
        
        # Step 1: Analyze tables
        logger.info("Starting full database optimization...")
        results["table_analysis"] = self.analyze_all_tables()
        
        # Step 2: Rebuild fragmented indexes
        results["index_rebuilds"] = self.rebuild_fragmented_indexes()
        
        # Step 3: Run optimize
        results["optimize"] = self.run_optimize()
        
        logger.info("Full database optimization complete")
        
        return results
    
    def should_vacuum(self, min_interval_hours: int = 24) -> bool:
        """
        Check if VACUUM should be run based on time since last vacuum.
        
        Args:
            min_interval_hours: Minimum hours between vacuum operations
            
        Returns:
            True if vacuum should be run, False otherwise
        """
        if self._last_optimize is None:
            # Check database for last vacuum time
            from src.state.db import get_connection
            
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("PRAGMA user_version")
                    # We can store last vacuum time in a simple way
                    # For now, return True if no record exists
                    return True
                except:
                    return True
        
        import datetime
        current_time = int(time.time() * 1000)
        elapsed_ms = current_time - self._last_optimize
        elapsed_hours = elapsed_ms / (1000 * 60 * 60)
        
        return elapsed_hours >= min_interval_hours
    
    def scheduled_optimize(self) -> Dict[str, any]:
        """
        Run optimization suitable for scheduled execution.
        
        This is a lighter-weight version that doesn't include VACUUM
        but does analyze tables and rebuild indexes if needed.
        
        Returns:
            Dictionary with optimization results
        """
        results = {
            "table_analysis": {},
            "index_rebuilds": {},
            "optimize": False,
            "timestamp": int(time.time() * 1000)
        }
        
        # Always analyze tables
        results["table_analysis"] = self.analyze_all_tables()
        
        # Rebuild fragmented indexes
        results["index_rebuilds"] = self.rebuild_fragmented_indexes()
        
        # Run optimize
        results["optimize"] = self.run_optimize()
        
        # Optionally run vacuum if needed
        if self.should_vacuum():
            logger.info("Running scheduled VACUUM...")
            results["vacuum"] = self.run_vacuum()
        
        return results


def run_database_optimization() -> Dict[str, any]:
    """
    Main entry point for database optimization.
    
    This can be called from cron or scheduler to maintain database performance.
    
    Returns:
        Dictionary with optimization results
    """
    logger.info("Starting database optimization...")
    
    optimizer = DatabaseOptimizer()
    results = optimizer.scheduled_optimize()
    
    total_ops = (
        sum(1 for v in results.get("table_analysis", {}).values() if v) +
        sum(1 for v in results.get("index_rebuilds", {}).values() if v)
    )
    
    logger.info(f"Database optimization complete: {total_ops} operations performed")
    
    return results


if __name__ == "__main__":
    # Run optimization when executed directly
    import sys
    
    if "--vacuum" in sys.argv:
        # Full vacuum mode
        optimizer = DatabaseOptimizer()
        success = optimizer.run_vacuum()
        print(f"VACUUM {'succeeded' if success else 'failed'}")
    elif "--analyze" in sys.argv:
        # Analyze only
        optimizer = DatabaseOptimizer()
        results = optimizer.analyze_all_tables()
        print("\nTable Analysis Results:")
        print("-" * 50)
        for table, success in results.items():
            print(f"{table}: {'✓' if success else '✗'}")
    elif "--rebuild" in sys.argv:
        # Rebuild indexes
        optimizer = DatabaseOptimizer()
        results = optimizer.rebuild_fragmented_indexes()
        print("\nIndex Rebuild Results:")
        print("-" * 50)
        for idx, success in results.items():
            print(f"{idx}: {'✓' if success else '✗'}")
    else:
        # Full optimization
        results = run_database_optimization()
        print("\nDatabase Optimization Results:")
        print("-" * 50)
        print(f"Tables analyzed: {sum(1 for v in results.get('table_analysis', {}).values() if v)}")
        print(f"Indexes rebuilt: {sum(1 for v in results.get('index_rebuilds', {}).values() if v)}")
        print(f"Optimize: {'✓' if results.get('optimize') else '✗'}")
