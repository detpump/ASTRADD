#!/usr/bin/env python3
"""
Bracket projector - projects bracket order events to the database.
"""

import logging

logger = logging.getLogger(__name__)


class BracketProjector:
    """Projects bracket order events to the brackets table."""
    
    def project(self, event: dict):
        """Project a bracket event to the database."""
        event_type = event.get("event_type", "")
        
        # Handle various bracket event types
        if "TP" in event_type:
            self._project_take_profit(event)
        elif "SL" in event_type:
            self._project_stop_loss(event)
        elif "BRACKET" in event_type:
            self._project_bracket(event)
        elif "TRAILING" in event_type:
            self._project_trailing(event)
    
    def _project_take_profit(self, event: dict):
        """Handle take profit event."""
        logger.debug(f"Projecting take profit: {event.get('position_uuid')}")
    
    def _project_stop_loss(self, event: dict):
        """Handle stop loss event."""
        logger.debug(f"Projecting stop loss: {event.get('position_uuid')}")
    
    def _project_bracket(self, event: dict):
        """Handle bracket event."""
        logger.debug(f"Projecting bracket: {event.get('position_uuid')}")
    
    def _project_trailing(self, event: dict):
        """Handle trailing stop event."""
        logger.debug(f"Projecting trailing stop: {event.get('position_uuid')}")
