# src/sync/conflict_resolver.py
"""
Conflict Resolution for WebSocket vs Batch Reconciliation.

This module handles conflicts between real-time WebSocket events and batch sync data,
providing intelligent resolution strategies based on data freshness and type.
"""

import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class ConflictResolver:
    """
    Resolves conflicts between real-time WebSocket events and batch sync data.
    
    Resolution Strategy:
    - For positions: Trust WebSocket when quantity changes (likely a fill)
    - For orders: Trust highest status priority (FILLED > PARTIALLY_FILLED > CANCELLED > NEW > PENDING)
    - Detect anomalies requiring manual review (>10% quantity difference, opposite sides)
    """
    
    # Status priority for order conflict resolution
    STATUS_PRIORITY = {
        "FILLED": 4,
        "PARTIALLY_FILLED": 3,
        "CANCELLED": 2,
        "NEW": 1,
        "PENDING": 0
    }
    
    # Anomaly thresholds
    QUANTITY_DISCREPANCY_THRESHOLD = 0.10  # 10%
    
    def resolve_position_conflict(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> Tuple[dict, str]:
        """
        Resolve position conflict between WebSocket and batch data.
        
        Args:
            websocket_position: Position data from WebSocket (real-time)
            batch_position: Position data from batch sync (snapshot)
        
        Returns:
            Tuple of (resolved_position, resolution_source)
            - resolution_source: BATCH_ONLY, WEBSOCKET_ONLY, WEBSOCKET_QTY_CHANGE, 
                              BATCH_QTY_MATCH, BATCH_MORE_RECENT
        """
        # Handle missing data cases
        if not websocket_position:
            return batch_position, "BATCH_ONLY"
        
        if not batch_position:
            return websocket_position, "WEBSOCKET_ONLY"
        
        # Compare timestamps to determine which is more recent
        ws_time = websocket_position.get("update_time", 0)
        batch_time = batch_position.get("update_time", 0)
        
        if ws_time > batch_time:
            # WebSocket is more recent - check if quantity changed (likely a fill)
            ws_qty = websocket_position.get("position_amt", 0)
            batch_qty = batch_position.get("position_amt", 0)
            
            if abs(ws_qty - batch_qty) > 0.0001:
                logger.info(
                    f"Position conflict resolved: WS qty={ws_qty} vs batch qty={batch_qty}. "
                    f"Trusting WebSocket (more recent)"
                )
                return websocket_position, "WEBSOCKET_QTY_CHANGE"
            
            # Quantity same, trust batch (has more context)
            return batch_position, "BATCH_QTY_MATCH"
        
        # Batch is more recent
        return batch_position, "BATCH_MORE_RECENT"
    
    def resolve_order_conflict(
        self,
        websocket_order: dict,
        batch_order: dict
    ) -> Tuple[dict, str]:
        """
        Resolve order conflict between WebSocket and batch data.
        
        For orders, always trust the most recent status based on priority:
        FILLED > PARTIALLY_FILLED > CANCELLED > NEW > PENDING
        
        Args:
            websocket_order: Order data from WebSocket (real-time)
            batch_order: Order data from batch sync (snapshot)
        
        Returns:
            Tuple of (resolved_order, resolution_source)
            - resolution_source: BATCH_ONLY, WEBSOCKET_ONLY, WEBSOCKET_STATUS, BATCH_STATUS
        """
        # Handle missing data cases
        if not websocket_order:
            return batch_order, "BATCH_ONLY"
        
        if not batch_order:
            return websocket_order, "WEBSOCKET_ONLY"
        
        # Compare status priorities
        ws_status = websocket_order.get("status", "")
        batch_status = batch_order.get("status", "")
        
        ws_priority = self.STATUS_PRIORITY.get(ws_status, 0)
        batch_priority = self.STATUS_PRIORITY.get(batch_status, 0)
        
        if ws_priority > batch_priority:
            logger.info(f"Order conflict: WS status {ws_status} > batch {batch_status}")
            return websocket_order, "WEBSOCKET_STATUS"
        
        return batch_order, "BATCH_STATUS"
    
    def detect_position_conflict(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> bool:
        """
        Detect if there is a position conflict between WebSocket and batch data.
        
        Args:
            websocket_position: Position data from WebSocket
            batch_position: Position data from batch sync
        
        Returns:
            True if conflict detected, False otherwise
        """
        if not websocket_position or not batch_position:
            return False
        
        ws_qty = websocket_position.get("position_amt", 0)
        batch_qty = batch_position.get("position_amt", 0)
        
        # Check for quantity discrepancy
        if ws_qty != batch_qty:
            return True
        
        # Check for side mismatch
        ws_side = "LONG" if ws_qty > 0 else "SHORT"
        batch_side = "LONG" if batch_qty > 0 else "SHORT"
        
        return ws_side != batch_side
    
    def detect_order_conflict(
        self,
        websocket_order: dict,
        batch_order: dict
    ) -> bool:
        """
        Detect if there is an order conflict between WebSocket and batch data.
        
        Args:
            websocket_order: Order data from WebSocket
            batch_order: Order data from batch sync
        
        Returns:
            True if conflict detected, False otherwise
        """
        if not websocket_order or not batch_order:
            return False
        
        ws_status = websocket_order.get("status", "")
        batch_status = batch_order.get("status", "")
        
        # Different status indicates a conflict
        return ws_status != batch_status
    
    def detect_anomaly(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> Optional[str]:
        """
        Detect anomalies that require manual review.
        
        Anomalies detected:
        - Large quantity discrepancy (>10%)
        - Opposite sides (long vs short)
        
        Args:
            websocket_position: Position data from WebSocket
            batch_position: Position data from batch sync
        
        Returns:
            Anomaly description string, or None if no anomaly detected
        """
        if not websocket_position or not batch_position:
            return None
        
        ws_qty = abs(websocket_position.get("position_amt", 0))
        batch_qty = abs(batch_position.get("position_amt", 0))
        
        # Large quantity discrepancy (>10%)
        if ws_qty > 0 and batch_qty > 0:
            pct_diff = abs(ws_qty - batch_qty) / max(ws_qty, batch_qty)
            if pct_diff > self.QUANTITY_DISCREPANCY_THRESHOLD:
                return f"LARGE_QTY_DIFF: {pct_diff:.1%} difference"
        
        # Opposite sides
        ws_side = "LONG" if websocket_position.get("position_amt", 0) > 0 else "SHORT"
        batch_side = "LONG" if batch_position.get("position_amt", 0) > 0 else "SHORT"
        
        if ws_side != batch_side:
            return f"SIDE_MISMATCH: WS={ws_side} vs batch={batch_side}"
        
        return None
    
    def resolve_with_anomaly_check(
        self,
        websocket_position: dict,
        batch_position: dict
    ) -> Tuple[dict, str, Optional[str]]:
        """
        Resolve position conflict and check for anomalies in one operation.
        
        Args:
            websocket_position: Position data from WebSocket
            batch_position: Position data from batch sync
        
        Returns:
            Tuple of (resolved_position, resolution_source, anomaly)
        """
        resolved, source = self.resolve_position_conflict(websocket_position, batch_position)
        anomaly = self.detect_anomaly(websocket_position, batch_position)
        
        if anomaly:
            logger.warning(f"Position anomaly detected: {anomaly}")
        
        return resolved, source, anomaly
