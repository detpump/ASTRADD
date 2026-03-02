#!/usr/bin/env python3
"""
ChangeDetector - Detects changes between current and previous state.

FIXED: Uses position_uuid instead of symbol to correctly identify positions
when multiple positions exist for the same symbol.
"""

import json
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class ChangeDetector:
    """
    Detects changes between current and previous state and generates events.
    
    FIXED: Now uses position_uuid as the primary identifier to correctly
    handle multiple positions per symbol.
    """
    
    def detect_position_changes(
        self, 
        current: List[dict], 
        previous: Dict[str, dict], 
        correlation_id: str
    ) -> List[dict]:
        """Detect position changes and emit events.
        
        Events now include position_uuid to enable correct identification of positions
        when multiple positions exist for the same symbol.
        
        Args:
            current: List of current position dictionaries
            previous: Dict of previous positions keyed by position_uuid
            correlation_id: Correlation ID for the batch
            
        Returns:
            List of event dictionaries
        """
        events = []
        
        # FIXED: Use position_uuid as key instead of symbol to handle multiple positions per symbol
        current_by_uuid = {}
        for p in current:
            # Prefer position_uuid, fall back to symbol for backward compatibility
            uuid_key = p.get("position_uuid") or p.get("symbol", "")
            if uuid_key:
                current_by_uuid[uuid_key] = p
        
        # Build previous by uuid (handle both uuid and symbol keys)
        previous_by_uuid = {}
        for key, val in previous.items():
            if isinstance(val, dict):
                uuid_key = val.get("position_uuid") or val.get("symbol", key)
                if uuid_key:
                    previous_by_uuid[uuid_key] = val
        
        # Detect new and modified positions
        for position_uuid, pos in current_by_uuid.items():
            if position_uuid not in previous_by_uuid:
                # New position opened
                events.append({
                    "event_type": "POSITION_OPENED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "position_uuid": position_uuid,  # FIXED: Include position_uuid
                    "symbol": pos.get("symbol", ""),
                    "payload_json": self._encode_payload(pos),
                    "position_amt": pos.get("position_amt", 0),
                    "entry_price": pos.get("entry_price", 0),
                    "side": pos.get("side", "")
                })
            else:
                # Position exists - check for changes
                prev = previous_by_uuid[position_uuid]
                if abs(pos.get("position_amt", 0) - prev.get("position_amt", 0)) > 0.0001:
                    if pos.get("position_amt", 0) > prev.get("position_amt", 0):
                        # Scale in
                        events.append({
                            "event_type": "POSITION_SCALED_IN",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "position_uuid": position_uuid,  # FIXED: Include position_uuid
                            "symbol": pos.get("symbol", ""),
                            "payload_json": self._encode_payload({
                                "old_qty": prev.get("position_amt", 0),
                                "new_qty": pos.get("position_amt", 0),
                                "entry_price": pos.get("entry_price", 0),
                                "side": pos.get("side", "")
                            })
                        })
                    else:
                        # Scale out
                        events.append({
                            "event_type": "POSITION_SCALED_OUT",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "position_uuid": position_uuid,  # FIXED: Include position_uuid
                            "symbol": pos.get("symbol", ""),
                            "payload_json": self._encode_payload({
                                "old_qty": prev.get("position_amt", 0),
                                "new_qty": pos.get("position_amt", 0)
                            })
                        })
        
        # Detect closed positions
        for position_uuid, prev in previous_by_uuid.items():
            if position_uuid not in current_by_uuid:
                events.append({
                    "event_type": "POSITION_CLOSED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "position_uuid": position_uuid,  # FIXED: Include position_uuid
                    "symbol": prev.get("symbol", ""),
                    "payload_json": self._encode_payload({
                        "old_qty": prev.get("position_amt", 0),
                        "close_price": prev.get("mark_price", 0)
                    })
                })
        
        return events
    
    def detect_order_changes(
        self, 
        current: List[dict], 
        previous: Dict[str, dict], 
        correlation_id: str
    ) -> List[dict]:
        """Detect order changes and emit events.
        
        Args:
            current: List of current order dictionaries
            previous: Dict of previous orders keyed by order_id
            correlation_id: Correlation ID for the batch
            
        Returns:
            List of event dictionaries
        """
        events = []
        current_by_id = {o.get("order_id", ""): o for o in current if o.get("order_id")}
        
        # Detect new orders
        for order_id, order in current_by_id.items():
            if order_id not in previous:
                events.append({
                    "event_type": "ORDER_PLACED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "order_id": order_id,
                    "symbol": order.get("symbol", ""),
                    "side": order.get("side", ""),
                    "type": order.get("type", ""),
                    "price": order.get("price", 0),
                    "quantity": order.get("quantity", 0),
                    "status": order.get("status", ""),
                    "payload_json": self._encode_payload(order)
                })
            else:
                # Check for status changes
                prev_order = previous[order_id]
                prev_status = prev_order.get("status", "")
                curr_status = order.get("status", "")
                
                if curr_status != prev_status:
                    if curr_status == "FILLED":
                        events.append({
                            "event_type": "ORDER_FILLED",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "order_id": order_id,
                            "symbol": order.get("symbol", ""),
                            "side": order.get("side", ""),
                            "executed_qty": order.get("executed_qty", 0),
                            "avg_price": order.get("avg_price", 0),
                            "payload_json": self._encode_payload(order)
                        })
                    elif curr_status == "CANCELLED":
                        events.append({
                            "event_type": "ORDER_CANCELLED",
                            "event_source": "EXCHANGE",
                            "correlation_id": correlation_id,
                            "order_id": order_id,
                            "symbol": order.get("symbol", ""),
                            "payload_json": self._encode_payload(order)
                        })
        
        # Detect cancelled orders (in previous but not in current)
        for order_id, prev_order in previous.items():
            if order_id not in current_by_id and prev_order.get("status") != "CANCELLED":
                events.append({
                    "event_type": "ORDER_CANCELLED",
                    "event_source": "EXCHANGE",
                    "correlation_id": correlation_id,
                    "order_id": order_id,
                    "symbol": prev_order.get("symbol", ""),
                    "payload_json": self._encode_payload(prev_order)
                })
        
        return events
    
    def _encode_payload(self, data: dict) -> str:
        """Encode payload to JSON string."""
        try:
            return json.dumps(data)
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to encode payload: {e}")
            return "{}"
