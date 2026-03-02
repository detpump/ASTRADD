#!/usr/bin/env python3
"""
Monitoring and Debug Utilities for Aster Trading System

Provides memory leak detection, stack trace analysis, and profiling utilities.

Author: Aster Trading V2
Date: 2026-02-26
"""

import gc
import logging
import sys
import time
import traceback
import threading
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger(__name__)


@dataclass
class MemorySnapshot:
    """Snapshot of memory usage at a point in time"""
    timestamp: float
    gc_stats: Dict[str, int]
    object_counts: Dict[str, int]
    total_memory_mb: float


class MemoryMonitor:
    """
    Memory monitoring utility for detecting potential memory leaks.
    """
    
    def __init__(self, warning_threshold_mb: float = 100.0):
        self.warning_threshold_mb = warning_threshold_mb
        self._snapshots: List[MemorySnapshot] = []
        self._baseline_counts: Dict[str, int] = {}
        self._is_baseline_set = False
    
    def take_snapshot(self) -> MemorySnapshot:
        """Take a snapshot of current memory usage"""
        gc.collect()
        
        # Get object counts by type
        object_counts = {}
        for obj in gc.get_objects():
            type_name = type(obj).__name__
            object_counts[type_name] = object_counts.get(type_name, 0) + 1
        
        # Get GC stats
        gc_stats = {
            'collections': gc.get_count(),
            'threshold': gc.get_threshold(),
        }
        
        # Estimate total memory (rough approximation)
        total_memory = sum(
            sys.getsizeof(obj) for obj in gc.get_objects()
        ) / (1024 * 1024)  # Convert to MB
        
        snapshot = MemorySnapshot(
            timestamp=time.time(),
            gc_stats=gc_stats,
            object_counts=object_counts,
            total_memory_mb=total_memory
        )
        
        self._snapshots.append(snapshot)
        
        # Set baseline on first snapshot
        if not self._is_baseline_set:
            self._baseline_counts = object_counts.copy()
            self._is_baseline_set = True
        
        return snapshot
    
    def check_for_leaks(self) -> List[str]:
        """Check for potential memory leaks by comparing to baseline"""
        if not self._snapshots:
            return []
        
        warnings = []
        
        latest = self._snapshots[-1]
        
        # Check total memory
        if latest.total_memory_mb > self.warning_threshold_mb:
            warnings.append(
                f"Memory usage ({latest.total_memory_mb:.1f}MB) exceeds threshold ({self.warning_threshold_mb}MB)"
            )
        
        # Check for growing object counts
        if self._baseline_counts:
            for obj_type, baseline_count in self._baseline_counts.items():
                if obj_type not in latest.object_counts:
                    continue
                
                current_count = latest.object_counts[obj_type]
                growth = current_count - baseline_count
                
                # Warn if any type has grown significantly (>10x baseline or >1000 objects)
                if baseline_count > 0 and current_count > baseline_count * 10:
                    warnings.append(
                        f"Object count for '{obj_type}' grew significantly: "
                        f"{baseline_count} -> {current_count} ({growth:+d})"
                    )
                elif growth > 1000:
                    warnings.append(
                        f"Object count for '{obj_type}' increased by {growth}"
                    )
        
        return warnings
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of memory monitoring"""
        if not self._snapshots:
            return {"status": "no_snapshots"}
        
        latest = self._snapshots[-1]
        
        return {
            "snapshots_taken": len(self._snapshots),
            "total_memory_mb": latest.total_memory_mb,
            "warnings": self.check_for_leaks(),
            "baseline_set": self._is_baseline_set,
        }


class Profiler:
    """
    Simple profiling utility for measuring function execution time.
    """
    
    def __init__(self):
        self._timings: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
    
    def profile(self, func: Callable) -> Callable:
        """Decorator to profile function execution time"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter() - start
                with self._lock:
                    func_name = f"{func.__module__}.{func.__name__}"
                    if func_name not in self._timings:
                        self._timings[func_name] = []
                    self._timings[func_name].append(elapsed)
        
        return wrapper
    
    def get_stats(self) -> Dict[str, Dict[str, float]]:
        """Get profiling statistics"""
        stats = {}
        
        with self._lock:
            for func_name, timings in self._timings.items():
                if not timings:
                    continue
                
                stats[func_name] = {
                    "calls": len(timings),
                    "total_time": sum(timings),
                    "avg_time": sum(timings) / len(timings),
                    "min_time": min(timings),
                    "max_time": max(timings),
                }
        
        return stats
    
    def reset(self):
        """Reset all profiling data"""
        with self._lock:
            self._timings.clear()


def analyze_exception(exc: Exception) -> Dict[str, Any]:
    """
    Analyze an exception and extract useful debugging information.
    
    Args:
        exc: Exception to analyze
    
    Returns:
        Dictionary with exception analysis
    """
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "module": exc.__class__.__module__,
        "traceback": traceback.format_exc(),
        "stack_frames": [
            {
                "file": frame.filename,
                "line": frame.lineno,
                "function": frame.name,
                "code": frame.line
            }
            for frame in traceback.extract_tb(exc.__traceback__)
        ]
    }


class ErrorCollector:
    """
    Collects and aggregates errors for analysis.
    """
    
    def __init__(self, max_errors: int = 1000):
        self.max_errors = max_errors
        self._errors: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
    
    def add_error(self, error: Exception, context: Dict[str, Any] = None):
        """Add an error to the collection"""
        with self._lock:
            error_info = {
                "timestamp": time.time(),
                "error": analyze_exception(error),
                "context": context or {}
            }
            
            self._errors.append(error_info)
            
            # Trim if needed
            if len(self._errors) > self.max_errors:
                self._errors = self._errors[-self.max_errors:]
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of collected errors"""
        with self._lock:
            if not self._errors:
                return {"total_errors": 0}
            
            # Count by type
            error_types: Dict[str, int] = {}
            for e in self._errors:
                error_type = e["error"]["type"]
                error_types[error_type] = error_types.get(error_type, 0) + 1
            
            return {
                "total_errors": len(self._errors),
                "error_types": error_types,
                "recent_errors": self._errors[-10:],
            }
    
    def clear(self):
        """Clear all collected errors"""
        with self._lock:
            self._errors.clear()


def log_execution(func: Callable) -> Callable:
    """
    Decorator to log function execution details.
    
    Args:
        func: Function to decorate
    
    Returns:
        Decorated function
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        func_name = f"{func.__module__}.{func.__name__}"
        
        logger.debug(f"Entering: {func_name}")
        
        start_time = time.perf_counter()
        
        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start_time
            
            logger.debug(
                f"Exiting: {func_name} - Success ({elapsed:.4f}s)"
            )
            
            return result
            
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            
            logger.error(
                f"Exception in: {func_name} after {elapsed:.4f}s - {type(e).__name__}: {e}"
            )
            
            raise
    
    return wrapper


# Global instances
_memory_monitor = MemoryMonitor()
_profiler = Profiler()
_error_collector = ErrorCollector()


def get_memory_monitor() -> MemoryMonitor:
    """Get the global memory monitor instance"""
    return _memory_monitor


def get_profiler() -> Profiler:
    """Get the global profiler instance"""
    return _profiler


def get_error_collector() -> ErrorCollector:
    """Get the global error collector instance"""
    return _error_collector
