# src/sync/retry_worker.py
"""
Retry Worker for Failed Event Projections.

This module processes failed event projections from the dead-letter queue,
attempting to re-project events that failed due to transient errors.
"""

import time
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class RetryWorker:
    """
    Processes failed event projections from dead-letter queue.
    
    The worker:
    1. Queries pending errors from event_processing_errors table
    2. Re-fetches the original event data
    3. Attempts to re-project the event using appropriate projector
    4. Marks errors as resolved or increments retry count
    
    Configuration:
    - MAX_RETRIES: Maximum retry attempts before marking as FAILED
    - RETRY_DELAY_SECONDS: Delay before processing (currently not enforced)
    """
    
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 60
    
    def __init__(self, max_retries: int = None):
        """
        Initialize the retry worker.
        
        Args:
            max_retries: Maximum retry attempts (defaults to MAX_RETRIES)
        """
        self.MAX_RETRIES = max_retries or self.MAX_RETRIES
    
    def process_pending(self) -> int:
        """
        Process all pending failed events.
        
        Queries the event_processing_errors table for PENDING errors
        with retry_count < MAX_RETRIES and attempts to re-project them.
        
        Returns:
            Number of events successfully processed
        """
        from state.db import get_connection
        
        processed = 0
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Get pending errors
            cur.execute(
                """SELECT id, event_id, event_type, position_uuid, error_message
                   FROM event_processing_errors
                   WHERE status = 'PENDING' AND retry_count < ?
                   ORDER BY first_attempt_at ASC""",
                (self.MAX_RETRIES,)
            )
            
            errors = cur.fetchall()
            logger.info(f"Found {len(errors)} pending events to retry")
            
            for error in errors:
                error_id, event_id, event_type, position_uuid, error_msg = error
                
                try:
                    # Re-fetch event from events table
                    cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
                    event_row = cur.fetchone()
                    
                    if not event_row:
                        logger.error(f"Event {event_id} not found, marking as FAILED")
                        self._mark_resolved(error_id, "EVENT_MISSING")
                        continue
                    
                    # Convert row to dict (assuming column order from table schema)
                    # Get column names first
                    cur.execute("PRAGMA table_info(events)")
                    columns = [col[1] for col in cur.fetchall()]
                    event = dict(zip(columns, event_row))
                    
                    # Re-project the event
                    self._reproject(event)
                    
                    # Mark as resolved
                    self._mark_resolved(error_id, "RESOLVED")
                    processed += 1
                    logger.info(f"Successfully reprocessed event {event_id} (type: {event_type})")
                    
                except Exception as e:
                    logger.exception(f"Retry failed for error {error_id}: {e}")
                    self._increment_retry(error_id)
        
        if processed > 0:
            logger.info(f"Retry worker processed {processed} events")
        
        return processed
    
    def _reproject(self, event: Dict[str, Any]):
        """
        Re-project an event using the appropriate projector.
        
        Args:
            event: Event dictionary with event_type and data
        """
        # Import projectors dynamically to avoid circular imports
        from sync.projectors.position_projector import PositionProjector
        from sync.projectors.order_projector import OrderProjector
        from sync.projectors.bracket_projector import BracketProjector
        
        projector = None
        event_type = event.get("event_type", "")
        
        if event_type.startswith("POSITION"):
            projector = PositionProjector()
        elif event_type.startswith("ORDER"):
            projector = OrderProjector()
        elif event_type.startswith("BRACKET"):
            projector = BracketProjector()
        
        if projector:
            projector.project(event)
        else:
            logger.warning(f"No projector found for event type: {event_type}")
    
    def _mark_resolved(self, error_id: int, resolution: str):
        """
        Mark an error as resolved.
        
        Args:
            error_id: Error ID in event_processing_errors table
            resolution: Resolution type (RESOLVED, EVENT_MISSING, etc.)
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE event_processing_errors 
                   SET status = 'RESOLVED', resolved_at = ?
                   WHERE id = ?""",
                (int(time.time() * 1000), error_id)
            )
            conn.commit()
    
    def _increment_retry(self, error_id: int):
        """
        Increment retry count for an error.
        
        If retry_count + 1 >= MAX_RETRIES, marks status as FAILED.
        
        Args:
            error_id: Error ID in event_processing_errors table
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE event_processing_errors 
                   SET retry_count = retry_count + 1, 
                       last_retry_at = ?,
                       status = CASE 
                           WHEN retry_count + 1 >= ? THEN 'FAILED' 
                           ELSE 'PENDING' 
                       END
                   WHERE id = ?""",
                (int(time.time() * 1000), self.MAX_RETRIES, error_id)
            )
            conn.commit()
    
    def get_error_stats(self) -> Dict[str, int]:
        """
        Get statistics on event processing errors.
        
        Returns:
            Dictionary with counts by status
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT status, COUNT(*) as count 
                   FROM event_processing_errors 
                   GROUP BY status"""
            )
            rows = cur.fetchall()
            
            stats = {"PENDING": 0, "RESOLVED": 0, "FAILED": 0}
            for status, count in rows:
                if status in stats:
                    stats[status] = count
            
            return stats
    
    def clear_resolved(self) -> int:
        """
        Clear all resolved errors from the table.
        
        Returns:
            Number of records deleted
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM event_processing_errors WHERE status = 'RESOLVED'"
            )
            deleted = cur.rowcount
            conn.commit()
            
            logger.info(f"Cleared {deleted} resolved error records")
            return deleted
