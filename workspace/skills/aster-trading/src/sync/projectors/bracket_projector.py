#!/usr/bin/env python3
"""
BracketProjector - Projects bracket/stop order events to the database.

Projects bracket order events including take-profit, stop-loss, and trailing
stops to the bracket_state_log table for audit and tracking.
"""

import json
import logging
import time

from src.state.db import get_connection

logger = logging.getLogger(__name__)


class BracketProjector:
    """Projects bracket order events to the bracket_state_log table."""
    
    def __init__(self):
        self._db = get_connection
    
    def project(self, event: dict):
        """Project a bracket event to the database.
        
        Args:
            event: Event dictionary with event_type and bracket data
        """
        event_type = event.get("event_type", "")
        
        # Handle various bracket event types
        if "TP1" in event_type or "TAKE_PROFIT_1" in event_type:
            self._project_take_profit(event, "TAKE_PROFIT_1")
        elif "TP2" in event_type or "TAKE_PROFIT_2" in event_type:
            self._project_take_profit(event, "TAKE_PROFIT_2")
        elif "SL" in event_type or "STOP_LOSS" in event_type:
            self._project_stop_loss(event)
        elif "BRACKET" in event_type:
            self._project_bracket(event)
        elif "TRAILING" in event_type:
            self._project_trailing(event)
    
    def _project_take_profit(self, event: dict, bracket_type: str = "TAKE_PROFIT_1"):
        """Handle take profit event - record TP trigger in bracket_state_log."""
        position_uuid = event.get("position_uuid", "")
        logger.info(f"Projecting {bracket_type} trigger for position: {position_uuid}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Get previous state
            old_state = "ACTIVE"
            new_state = "FILLED"
            
            # Determine trigger reason
            trigger_reason = "TP1_HIT" if bracket_type == "TAKE_PROFIT_1" else "TP2_HIT"
            
            # Record state transition
            cur.execute(
                """INSERT INTO bracket_state_log (
                    bracket_uuid, position_uuid, bracket_type, old_state, new_state,
                    trigger_reason, price_trigger, quantity, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("bracket_uuid", f"{position_uuid}_{bracket_type}"),
                    position_uuid,
                    bracket_type,
                    old_state,
                    new_state,
                    trigger_reason,
                    payload.get("trigger_price", payload.get("price", 0)),
                    payload.get("quantity", 0),
                    int(time.time() * 1000)
                )
            )
            
            # Update position record for TP hit
            tp_field = "tp1_hit" if bracket_type == "TAKE_PROFIT_1" else "tp2_hit"
            cur.execute(
                f"""UPDATE positions_v3 
                    SET {tp_field} = 1, version = version + 1, updated_at = ?
                    WHERE position_uuid = ?""",
                (int(time.time() * 1000), position_uuid)
            )
            
            conn.commit()
        
        logger.debug(f"{bracket_type} trigger recorded for {position_uuid}")
    
    def _project_stop_loss(self, event: dict):
        """Handle stop loss event - record SL trigger in bracket_state_log."""
        position_uuid = event.get("position_uuid", "")
        logger.info(f"Projecting STOP_LOSS trigger for position: {position_uuid}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Record state transition
            cur.execute(
                """INSERT INTO bracket_state_log (
                    bracket_uuid, position_uuid, bracket_type, old_state, new_state,
                    trigger_reason, price_trigger, quantity, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("bracket_uuid", f"{position_uuid}_STOP_LOSS"),
                    position_uuid,
                    "STOP_LOSS",
                    "ACTIVE",
                    "FILLED",
                    payload.get("trigger_reason", "PRICE_HIT"),
                    payload.get("trigger_price", payload.get("stop_price", 0)),
                    payload.get("quantity", 0),
                    int(time.time() * 1000)
                )
            )
            
            conn.commit()
        
        logger.debug(f"STOP_LOSS trigger recorded for {position_uuid}")
    
    def _project_bracket(self, event: dict):
        """Handle bracket creation/update event."""
        position_uuid = event.get("position_uuid", "")
        logger.info(f"Projecting BRACKET event for position: {position_uuid}")
        
        # Parse payload with bracket details
        payload = json.loads(event.get("payload_json", "{}"))
        
        bracket_type = payload.get("bracket_type", "STOP_LOSS")
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Record bracket creation
            cur.execute(
                """INSERT INTO bracket_state_log (
                    bracket_uuid, position_uuid, bracket_type, old_state, new_state,
                    trigger_reason, price_trigger, quantity, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("bracket_uuid", f"{position_uuid}_{bracket_type}"),
                    position_uuid,
                    bracket_type,
                    "PENDING",
                    "ACTIVE",
                    "MANUAL",
                    payload.get("price", 0),
                    payload.get("quantity", 0),
                    int(time.time() * 1000)
                )
            )
            
            conn.commit()
        
        logger.debug(f"BRACKET event recorded for {position_uuid}")
    
    def _project_trailing(self, event: dict):
        """Handle trailing stop event - record trailing stop updates."""
        position_uuid = event.get("position_uuid", "")
        logger.info(f"Projecting TRAILING_STOP event for position: {position_uuid}")
        
        # Parse payload with trailing stop details
        payload = json.loads(event.get("payload_json", "{}"))
        
        event_type = event.get("event_type", "")
        is_activation = "ACTIVATED" in event_type
        
        with self._db() as conn:
            cur = conn.cursor()
            
            if is_activation:
                # Record trailing stop activation
                cur.execute(
                    """INSERT INTO bracket_state_log (
                        bracket_uuid, position_uuid, bracket_type, old_state, new_state,
                        trigger_reason, price_trigger, quantity, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.get("bracket_uuid", f"{position_uuid}_TRAILING_STOP"),
                        position_uuid,
                        "TRAILING_STOP",
                        "PENDING",
                        "ACTIVE",
                        "MANUAL",
                        payload.get("activation_price", 0),
                        payload.get("quantity", 0),
                        int(time.time() * 1000)
                    )
                )
                
                # Update position with trailing stop info
                cur.execute(
                    """UPDATE positions_v3 
                       SET trailing_active = 1, 
                           trailing_activation_price = ?,
                           trailing_callback_pct = ?,
                           version = version + 1, updated_at = ?
                       WHERE position_uuid = ?""",
                    (
                        payload.get("activation_price", 0),
                        payload.get("callback_rate", 0.8),
                        int(time.time() * 1000),
                        position_uuid
                    )
                )
            else:
                # Record trailing stop update
                cur.execute(
                    """INSERT INTO bracket_state_log (
                        bracket_uuid, position_uuid, bracket_type, old_state, new_state,
                        trigger_reason, price_trigger, quantity, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.get("bracket_uuid", f"{position_uuid}_TRAILING_STOP"),
                        position_uuid,
                        "TRAILING_STOP",
                        "ACTIVE",
                        "ACTIVE",
                        "TRAILING_CALLBACK",
                        payload.get("new_stop_price", 0),
                        payload.get("quantity", 0),
                        int(time.time() * 1000)
                    )
                )
                
                # Update position current stop price
                cur.execute(
                    """UPDATE positions_v3 
                       SET current_sl_price = ?, updated_at = ?
                       WHERE position_uuid = ?""",
                    (payload.get("new_stop_price", 0), int(time.time() * 1000), position_uuid)
                )
            
            conn.commit()
        
        logger.debug(f"TRAILING_STOP event recorded for {position_uuid}")
