#!/usr/bin/env python3
"""
PositionProjector - Projects position events to the database.

Projects position events to positions_v3 table with full V3 schema support
including correlation_id tracking, version management, and scale-in tracking.
"""

import json
import logging
import time

from src.state.db import get_connection

logger = logging.getLogger(__name__)


class PositionProjector:
    """Projects position events to the positions_v3 table."""
    
    def __init__(self):
        self._db = get_connection
    
    def project(self, event: dict):
        """Project a position event to the database.
        
        Args:
            event: Event dictionary with event_type and payload data
        """
        event_type = event.get("event_type", "")
        
        if event_type == "POSITION_OPENED":
            self._project_position_opened(event)
        elif event_type == "POSITION_SCALED_IN":
            self._project_position_scaled_in(event)
        elif event_type == "POSITION_SCALED_OUT":
            self._project_position_scaled_out(event)
        elif event_type == "POSITION_CLOSED":
            self._project_position_closed(event)
    
    def _project_position_opened(self, event: dict):
        """Handle POSITION_OPENED event - create new position in positions_v3."""
        position_uuid = event.get("position_uuid") or event.get("symbol", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting POSITION_OPENED: {position_uuid} for {symbol}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Check if position already exists
            cur.execute(
                "SELECT position_uuid, version FROM positions_v3 WHERE position_uuid = ?",
                (position_uuid,)
            )
            existing = cur.fetchone()
            
            if existing:
                # Update existing position
                self._update_position(
                    cur, position_uuid, symbol, payload, event.get("correlation_id", "")
                )
            else:
                # Insert new position
                self._insert_position(
                    cur, position_uuid, symbol, payload, event.get("correlation_id", "")
                )
            
            conn.commit()
        
        logger.debug(f"POSITION_OPENED projected successfully for {position_uuid}")
    
    def _project_position_scaled_in(self, event: dict):
        """Handle POSITION_SCALED_IN event - update position quantity."""
        position_uuid = event.get("position_uuid") or event.get("symbol", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting POSITION_SCALED_IN: {position_uuid} for {symbol}")
        
        # Parse payload with old/new quantities
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Get current position
            cur.execute(
                """SELECT quantity, entry_price, pyramid_scales, last_scale_price, last_scale_time 
                   FROM positions_v3 WHERE position_uuid = ?""",
                (position_uuid,)
            )
            row = cur.fetchone()
            
            if not row:
                logger.warning(f"Position not found for scale-in: {position_uuid}")
                return
            
            old_qty = row[0] or 0
            old_entry = row[1] or 0
            pyramid_scales = row[2] or 0
            last_scale_price = row[3]
            last_scale_time = row[4]
            
            new_qty = payload.get("new_qty", old_qty)
            new_entry = payload.get("entry_price", old_entry)
            
            # Calculate new weighted average entry price
            scale_qty = new_qty - old_qty
            if scale_qty > 0 and new_qty > 0:
                # Weighted average entry price
                new_entry = ((old_qty * old_entry) + (scale_qty * new_entry)) / new_qty
            
            # Update position with incremented pyramid scale
            cur.execute(
                """UPDATE positions_v3 
                   SET quantity = ?, entry_price = ?, current_price = ?,
                       pyramid_scales = pyramid_scales + 1,
                       last_scale_price = ?, last_scale_time = ?,
                       scale_in_pending = 0, scale_in_timestamp = NULL,
                       version = version + 1, updated_at = ?
                   WHERE position_uuid = ?""",
                (new_qty, new_entry, payload.get("current_price", new_entry),
                 new_entry, int(time.time() * 1000),
                 int(time.time() * 1000), position_uuid)
            )
            
            # Record position entry
            cur.execute(
                """INSERT INTO position_entries 
                   (position_uuid, entry_price, quantity_added, cumulative_quantity, 
                    entry_type, correlation_id, created_at)
                   VALUES (?, ?, ?, ?, 'SCALE_IN', ?, ?)""",
                (position_uuid, new_entry, scale_qty, new_qty,
                 event.get("correlation_id", ""), int(time.time() * 1000))
            )
            
            conn.commit()
        
        logger.debug(f"POSITION_SCALED_IN projected successfully for {position_uuid}")
    
    def _project_position_scaled_out(self, event: dict):
        """Handle POSITION_SCALED_OUT event - reduce position quantity."""
        position_uuid = event.get("position_uuid") or event.get("symbol", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting POSITION_SCALED_OUT: {position_uuid} for {symbol}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Update position quantity
            new_qty = payload.get("new_qty", 0)
            
            if new_qty <= 0:
                # Full scale out - close position
                cur.execute(
                    """UPDATE positions_v3 
                       SET quantity = 0, status = 'CLOSED', close_time = ?,
                           close_reason = 'SCALE_OUT', version = version + 1, updated_at = ?
                       WHERE position_uuid = ?""",
                    (int(time.time() * 1000), int(time.time() * 1000), position_uuid)
                )
            else:
                # Partial scale out
                cur.execute(
                    """UPDATE positions_v3 
                       SET quantity = ?, version = version + 1, updated_at = ?
                       WHERE position_uuid = ?""",
                    (new_qty, int(time.time() * 1000), position_uuid)
                )
            
            conn.commit()
        
        logger.debug(f"POSITION_SCALED_OUT projected successfully for {position_uuid}")
    
    def _project_position_closed(self, event: dict):
        """Handle POSITION_CLOSED event - mark position as closed."""
        position_uuid = event.get("position_uuid") or event.get("symbol", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting POSITION_CLOSED: {position_uuid} for {symbol}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Get position details for logging
            cur.execute(
                "SELECT quantity, entry_price FROM positions_v3 WHERE position_uuid = ?",
                (position_uuid,)
            )
            row = cur.fetchone()
            
            close_price = payload.get("close_price", 0)
            entry_price = row[1] if row else 0
            
            # Calculate PnL
            pnl = 0
            if row:
                qty = row[0] or 0
                side = self._get_position_side(position_uuid)
                if side == "LONG":
                    pnl = (close_price - entry_price) * qty
                elif side == "SHORT":
                    pnl = (entry_price - close_price) * qty
            
            # Update position as closed
            cur.execute(
                """UPDATE positions_v3 
                   SET quantity = 0, status = 'CLOSED', close_time = ?,
                       close_reason = ?, unrealized_pnl = ?,
                       current_price = ?, version = version + 1, updated_at = ?
                   WHERE position_uuid = ?""",
                (int(time.time() * 1000), payload.get("reason", "EXCHANGE_CLOSE"),
                 pnl, close_price, int(time.time() * 1000), position_uuid)
            )
            
            conn.commit()
        
        logger.debug(f"POSITION_CLOSED projected successfully for {position_uuid}")
    
    def _insert_position(self, cur, position_uuid: str, symbol: str, payload: dict, correlation_id: str):
        """Insert new position into positions_v3."""
        current_time = int(time.time() * 1000)
        
        cur.execute(
            """INSERT INTO positions_v3 (
                position_uuid, symbol, side, quantity, entry_price, current_price,
                unrealized_pnl, notional, leverage, open_time, cycle_id,
                correlation_id, status, scale_in_pending, version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0, 1, ?, ?)""",
            (
                position_uuid,
                symbol,
                payload.get("side", "LONG"),
                payload.get("position_amt", payload.get("quantity", 0)),
                payload.get("entry_price", 0),
                payload.get("mark_price", payload.get("entry_price", 0)),
                payload.get("unrealized_pnl", 0),
                payload.get("notional", 0),
                payload.get("leverage", 1),
                current_time,
                payload.get("cycle_id", ""),
                correlation_id,
                current_time,
                current_time
            )
        )
        
        # Record initial position entry
        cur.execute(
            """INSERT INTO position_entries 
               (position_uuid, entry_price, quantity_added, cumulative_quantity, 
                entry_type, correlation_id, created_at)
               VALUES (?, ?, ?, ?, 'OPEN', ?, ?)""",
            (position_uuid, payload.get("entry_price", 0),
             payload.get("position_amt", payload.get("quantity", 0)),
             payload.get("position_amt", payload.get("quantity", 0)),
             correlation_id, current_time)
        )
    
    def _update_position(self, cur, position_uuid: str, symbol: str, payload: dict, correlation_id: str):
        """Update existing position in positions_v3."""
        current_time = int(time.time() * 1000)
        
        cur.execute(
            """UPDATE positions_v3 
               SET symbol = ?, side = ?, quantity = ?, entry_price = ?,
                   current_price = ?, unrealized_pnl = ?, notional = ?,
                   leverage = ?, correlation_id = ?,
                   version = version + 1, updated_at = ?
               WHERE position_uuid = ?""",
            (
                symbol,
                payload.get("side", "LONG"),
                payload.get("position_amt", payload.get("quantity", 0)),
                payload.get("entry_price", 0),
                payload.get("mark_price", payload.get("entry_price", 0)),
                payload.get("unrealized_pnl", 0),
                payload.get("notional", 0),
                payload.get("leverage", 1),
                correlation_id,
                current_time,
                position_uuid
            )
        )
    
    def _get_position_side(self, position_uuid: str) -> str:
        """Get the side of a position."""
        with self._db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT side FROM positions_v3 WHERE position_uuid = ?", (position_uuid,))
            row = cur.fetchone()
            return row[0] if row else "LONG"
