#!/usr/bin/env python3
"""
EventEmitter - Emits events to event handlers.
"""

import logging
from typing import Dict, List, Callable, Any

logger = logging.getLogger(__name__)


class EventEmitter:
    """
    Simple event emitter for sync events.
    """
    
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
    
    def on(self, event_type: str, handler: Callable):
        """Register an event handler."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
    
    def emit(self, event: Dict[str, Any]):
        """Emit an event to registered handlers."""
        event_type = event.get("event_type", "UNKNOWN")
        
        if event_type in self._handlers:
            for handler in self._handlers[event_type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.exception(f"Event handler failed for {event_type}: {e}")
        else:
            logger.debug(f"No handlers registered for event type: {event_type}")
    
    def off(self, event_type: str, handler: Callable):
        """Unregister an event handler."""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass
