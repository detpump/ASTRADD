# src/risk/stop_priority.py
"""
Stop Priority Manager.

This module manages stop loss priority enforcement to prevent conflicting triggers.
The priority stack ensures proper stop order execution sequence.
"""

import logging
import time
from enum import IntEnum
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class StopPriority(IntEnum):
    """
    Stop priority order (lower number = higher priority).
    
    Priority hierarchy:
    1. HARD_STOP - Daily loss >= 5% equity (emergency exit)
    2. INITIAL_SL - Price-based guard from entry
    3. BREAK_EVEN_MOVE - Move SL to entry after TP1 hit
    4. TRAILING_STOP - Trailing after TP2 or profit threshold
    5. TIME_STOP - Close stale positions (time-based)
    """
    HARD_STOP = 1
    INITIAL_SL = 2
    BREAK_EVEN_MOVE = 3
    TRAILING_STOP = 4
    TIME_STOP = 5


class StopPriorityManager:
    """
    Manages stop priority enforcement to prevent conflicting triggers.
    
    This manager ensures that higher-priority stops are executed before
    lower-priority ones, preventing issues like:
    - Trailing stop triggering before break-even move
    - Multiple stops firing simultaneously
    
    State Management:
    - ACTIVE: Stop is currently active and protecting position
    - PENDING: Stop is registered but not yet active
    - TRIGGERED: Stop has been triggered and executed
    - CANCELLED: Stop was cancelled
    """
    
    STATE_ACTIVE = "ACTIVE"
    STATE_PENDING = "PENDING"
    STATE_TRIGGERED = "TRIGGERED"
    STATE_CANCELLED = "CANCELLED"
    
    def __init__(self):
        """Initialize the stop priority manager."""
        self._active_stops: Dict[str, Dict[StopPriority, str]] = {}
    
    def register_stop(
        self,
        position_uuid: str,
        stop_type: StopPriority,
        state: str = "PENDING"
    ):
        """
        Register a stop activation.
        
        Args:
            position_uuid: Unique position identifier
            stop_type: Type of stop from StopPriority enum
            state: Initial state (default: PENDING)
        """
        if position_uuid not in self._active_stops:
            self._active_stops[position_uuid] = {}
        
        old_state = self._active_stops[position_uuid].get(stop_type, "NONE")
        self._active_stops[position_uuid][stop_type] = state
        
        self._log_state_transition(position_uuid, stop_type, old_state, state)
        logger.debug(
            f"Registered stop {stop_type.name} for position {position_uuid} "
            f"with state {state}"
        )
    
    def activate_stop(
        self,
        position_uuid: str,
        stop_type: StopPriority
    ):
        """
        Activate a stop (change state from PENDING to ACTIVE).
        
        Args:
            position_uuid: Unique position identifier
            stop_type: Type of stop to activate
        """
        self.register_stop(position_uuid, stop_type, self.STATE_ACTIVE)
    
    def deactivate_stop(
        self,
        position_uuid: str,
        stop_type: StopPriority
    ):
        """
        Deactivate a stop (mark as CANCELLED).
        
        Args:
            position_uuid: Unique position identifier
            stop_type: Type of stop to deactivate
        """
        self.register_stop(position_uuid, stop_type, self.STATE_CANCELLED)
    
    def can_activate(self, position_uuid: str, requested_stop: StopPriority) -> bool:
        """
        Check if requested stop can be activated based on priority.
        
        A stop can be activated if:
        1. No higher-priority stop is currently ACTIVE
        2. OR the position has no active stops
        
        Args:
            position_uuid: Unique position identifier
            requested_stop: The stop type to check
        
        Returns:
            True if stop can be activated, False otherwise
        """
        if position_uuid not in self._active_stops:
            return True
        
        active = self._active_stops[position_uuid]
        
        # Check if any higher-priority stop is active
        for stop_type, state in active.items():
            if stop_type < requested_stop and state == self.STATE_ACTIVE:
                logger.debug(
                    f"Cannot activate {requested_stop.name}: "
                    f"{stop_type.name} is ACTIVE"
                )
                return False
        
        return True
    
    def get_active_stop(self, position_uuid: str) -> Optional[StopPriority]:
        """
        Get the highest-priority active stop for a position.
        
        Args:
            position_uuid: Unique position identifier
        
        Returns:
            Highest priority StopPriority that's ACTIVE, or None
        """
        if position_uuid not in self._active_stops:
            return None
        
        active = self._active_stops[position_uuid]
        active_stops = [
            stop_type for stop_type, state in active.items()
            if state == self.STATE_ACTIVE
        ]
        
        if not active_stops:
            return None
        
        return min(active_stops)
    
    def get_all_stops(self, position_uuid: str) -> Dict[StopPriority, str]:
        """
        Get all stops for a position.
        
        Args:
            position_uuid: Unique position identifier
        
        Returns:
            Dictionary of stop_type -> state
        """
        return self._active_stops.get(position_uuid, {})
    
    def has_active_stops(self, position_uuid: str) -> bool:
        """
        Check if position has any active stops.
        
        Args:
            position_uuid: Unique position identifier
        
        Returns:
            True if position has any ACTIVE stops
        """
        active_stop = self.get_active_stop(position_uuid)
        return active_stop is not None
    
    def clear_position(self, position_uuid: str):
        """
        Clear all stops for a position.
        
        Args:
            position_uuid: Unique position identifier
        """
        if position_uuid in self._active_stops:
            del self._active_stops[position_uuid]
            logger.debug(f"Cleared all stops for position {position_uuid}")
    
    def _log_state_transition(
        self,
        position_uuid: str,
        stop_type: StopPriority,
        old_state: str,
        new_state: str
    ):
        """
        Log bracket state transition for audit trail.
        
        Args:
            position_uuid: Unique position identifier
            stop_type: Type of stop
            old_state: Previous state
            new_state: New state
        """
        # Import here to avoid circular imports
        try:
            from state.db import get_connection
            
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO bracket_state_log 
                       (bracket_uuid, position_uuid, bracket_type, old_state, new_state, trigger_reason, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"{position_uuid}_{stop_type.name}",
                        position_uuid,
                        stop_type.name,
                        old_state,
                        new_state,
                        "PRICE_HIT" if new_state == self.STATE_ACTIVE else "STATE_CHANGE",
                        int(time.time() * 1000)
                    )
                )
                conn.commit()
        except Exception as e:
            # Log warning but don't fail the operation
            logger.warning(f"Failed to log state transition: {e}")
    
    def get_stop_hierarchy(self, position_uuid: str) -> list:
        """
        Get the full stop hierarchy for a position with their states.
        
        Useful for debugging and UI display.
        
        Args:
            position_uuid: Unique position identifier
        
        Returns:
            List of tuples (stop_priority, stop_name, state)
        """
        result = []
        all_stops = self.get_all_stops(position_uuid)
        
        for priority in StopPriority:
            state = all_stops.get(priority, "NOT_REGISTERED")
            result.append((priority, priority.name, state))
        
        return result
