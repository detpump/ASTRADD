#!/usr/bin/env python3
"""
OrderProjector - Projects order events to the database.

Projects order events to the orders table with full event tracking
including correlation_id, fill information, and status history.
"""

import json
import logging
import time

from src.state.db import get_connection

logger = logging.getLogger(__name__)


class OrderProjector:
    """Projects order events to the orders table."""
    
    def __init__(self):
        self._db = get_connection
    
    def project(self, event: dict):
        """Project an order event to the database.
        
        Args:
            event: Event dictionary with event_type and order data
        """
        event_type = event.get("event_type", "")
        
        if event_type == "ORDER_PLACED":
            self._project_order_placed(event)
        elif event_type == "ORDER_FILLED":
            self._project_order_filled(event)
        elif event_type == "ORDER_CANCELLED":
            self._project_order_cancelled(event)
    
    def _project_order_placed(self, event: dict):
        """Handle ORDER_PLACED event - insert or update order record."""
        order_id = event.get("order_id", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting ORDER_PLACED: {order_id} for {symbol}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Check if order already exists
            cur.execute(
                "SELECT id, status FROM orders WHERE order_id = ?",
                (order_id,)
            )
            existing = cur.fetchone()
            
            if existing:
                # Update existing order
                cur.execute(
                    """UPDATE orders 
                       SET status = ?, price = ?, quantity = ?, executed_qty = ?,
                           avg_price = ?, update_time = ?, metadata = ?,
                           cycle_id = COALESCE(cycle_id, ?)
                       WHERE order_id = ?""",
                    (
                        payload.get("status", "NEW"),
                        payload.get("price", 0),
                        payload.get("quantity", 0),
                        payload.get("executed_qty", 0),
                        payload.get("avg_price", 0),
                        int(time.time() * 1000),
                        json.dumps(payload),
                        event.get("correlation_id", ""),
                        order_id
                    )
                )
            else:
                # Insert new order
                cur.execute(
                    """INSERT INTO orders (
                        order_id, client_order_id, symbol, side, type, status,
                        price, quantity, executed_qty, avg_price, reduce_only,
                        time_in_force, position_side, update_time, cycle_id,
                        bracket_label, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        order_id,
                        payload.get("client_order_id", ""),
                        symbol,
                        payload.get("side", ""),
                        payload.get("type", "LIMIT"),
                        payload.get("status", "NEW"),
                        payload.get("price", 0),
                        payload.get("quantity", 0),
                        payload.get("executed_qty", 0),
                        payload.get("avg_price", 0),
                        1 if payload.get("reduce_only") else 0,
                        payload.get("time_in_force", "GTC"),
                        payload.get("position_side", "BOTH"),
                        int(time.time() * 1000),
                        event.get("correlation_id", ""),
                        payload.get("bracket_label", ""),
                        json.dumps(payload),
                        int(time.time() * 1000)
                    )
                )
            
            conn.commit()
        
        logger.debug(f"ORDER_PLACED projected successfully for {order_id}")
    
    def _project_order_filled(self, event: dict):
        """Handle ORDER_FILLED event - update order with fill information."""
        order_id = event.get("order_id", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting ORDER_FILLED: {order_id} for {symbol}")
        
        # Parse payload with fill details
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Update order as filled
            cur.execute(
                """UPDATE orders 
                   SET status = 'FILLED', 
                       executed_qty = COALESCE(?, executed_qty),
                       avg_price = COALESCE(?, avg_price),
                       update_time = ?,
                       metadata = ?
                   WHERE order_id = ?""",
                (
                    payload.get("executed_qty", 0),
                    payload.get("avg_price", 0),
                    int(time.time() * 1000),
                    json.dumps(payload),
                    order_id
                )
            )
            
            conn.commit()
        
        logger.debug(f"ORDER_FILLED projected successfully for {order_id}")
    
    def _project_order_cancelled(self, event: dict):
        """Handle ORDER_CANCELLED event - mark order as cancelled."""
        order_id = event.get("order_id", "")
        symbol = event.get("symbol", "")
        
        logger.info(f"Projecting ORDER_CANCELLED: {order_id} for {symbol}")
        
        # Parse payload
        payload = json.loads(event.get("payload_json", "{}"))
        
        with self._db() as conn:
            cur = conn.cursor()
            
            # Update order as cancelled
            cur.execute(
                """UPDATE orders 
                   SET status = 'CANCELLED', 
                       update_time = ?,
                       metadata = ?
                   WHERE order_id = ?""",
                (
                    int(time.time() * 1000),
                    json.dumps(payload),
                    order_id
                )
            )
            
            conn.commit()
        
        logger.debug(f"ORDER_CANCELLED projected successfully for {order_id}")
