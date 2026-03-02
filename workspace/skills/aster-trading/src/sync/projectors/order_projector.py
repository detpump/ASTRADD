#!/usr/bin/env python3
"""
Order projector - projects order events to the database.
"""

import logging

logger = logging.getLogger(__name__)


class OrderProjector:
    """Projects order events to the orders table."""
    
    def project(self, event: dict):
        """Project an order event to the database."""
        event_type = event.get("event_type", "")
        
        if event_type == "ORDER_PLACED":
            self._project_order_placed(event)
        elif event_type == "ORDER_FILLED":
            self._project_order_filled(event)
        elif event_type == "ORDER_CANCELLED":
            self._project_order_cancelled(event)
    
    def _project_order_placed(self, event: dict):
        """Handle ORDER_PLACED event."""
        logger.debug(f"Projecting ORDER_PLACED: {event.get('order_id')}")
    
    def _project_order_filled(self, event: dict):
        """Handle ORDER_FILLED event."""
        logger.debug(f"Projecting ORDER_FILLED: {event.get('order_id')}")
    
    def _project_order_cancelled(self, event: dict):
        """Handle ORDER_CANCELLED event."""
        logger.debug(f"Projecting ORDER_CANCELLED: {event.get('order_id')}")
