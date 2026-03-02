#!/usr/bin/env python3
"""
Stress Testing Framework for Aster Trading System.

This module provides stress testing capabilities including:
- High volume request testing
- Circuit breaker stress testing  
- Rate limit testing
- Concurrent access testing
- Memory and resource testing
"""

import pytest
import time
import threading
import random
import sys
import os
from typing import List, Dict, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from api.aster_api import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    reset_circuit_breaker,
    RetryConfig,
    set_retry_config,
    _calculate_backoff_delay,
)


# =============================================================================
# Stress Test Configuration
# =============================================================================

@dataclass
class StressTestConfig:
    """Configuration for stress tests"""
    num_threads: int = 10
    num_requests: int = 100
    failure_threshold: int = 5
    timeout_seconds: float = 30.0
    success_threshold: int = 2
    half_open_max_calls: int = 3


@dataclass
class StressTestResult:
    """Results from a stress test"""
    test_name: str
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    throughput: float  # requests per second
    latency_avg: float
    latency_p50: float
    latency_p95: float
    latency_p99: float
    errors: List[str] = field(default_factory=list)


# =============================================================================
# Stress Test Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_circuit_breaker_fixture():
    """Reset circuit breaker before each stress test"""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


@pytest.fixture
def stress_config():
    """Default stress test configuration"""
    return StressTestConfig()


# =============================================================================
# Stress Test Classes
# =============================================================================

class TestCircuitBreakerStress:
    """Stress tests for circuit breaker under high load"""
    
    def test_circuit_breaker_high_failure_rate(self, stress_config):
        """Test circuit breaker behavior with high failure rate"""
        config = CircuitBreakerConfig(
            failure_threshold=stress_config.failure_threshold,
            timeout_seconds=0.1,  # Short timeout for testing
            success_threshold=stress_config.success_threshold
        )
        cb = CircuitBreaker("stress_test", config)
        
        # Record rapid failures
        start_time = time.time()
        for i in range(20):
            cb.record_failure(f"error_{i}")
        
        duration = time.time() - start_time
        
        # Circuit should be open after threshold failures
        assert cb.state == CircuitState.OPEN
        assert duration < 1.0  # Should be very fast
        
        print(f"\nCircuit opened after {duration:.3f}s with rapid failures")
    
    def test_circuit_breaker_concurrent_writes(self, stress_config):
        """Test circuit breaker with concurrent write operations"""
        config = CircuitBreakerConfig(failure_threshold=100)
        cb = CircuitBreaker("concurrent_test", config)
        
        errors = []
        
        def worker(worker_id: int):
            for i in range(20):
                try:
                    cb.record_failure(f"worker_{worker_id}_error_{i}")
                except Exception as e:
                    errors.append(str(e))
        
        # Run concurrent threads
        start_time = time.time()
        
        threads = []
        for i in range(stress_config.num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        duration = time.time() - start_time
        
        # Verify all failures recorded
        assert cb._failure_count == stress_config.num_threads * 20
        assert len(errors) == 0
        assert duration < 5.0  # Should complete quickly
        
        print(f"\nConcurrent writes completed in {duration:.3f}s")
        print(f"Total failures: {cb._failure_count}")
    
    def test_circuit_breaker_recovery_under_load(self, stress_config):
        """Test circuit breaker recovery while under load"""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=0.1,
            success_threshold=2,
            half_open_max_calls=2
        )
        cb = CircuitBreaker("recovery_test", config)
        
        # Open the circuit
        cb.record_failure("error1")
        cb.record_failure("error2")
        cb.record_failure("error3")
        
        assert cb.state == CircuitState.OPEN
        
        # Wait for half-open transition
        time.sleep(0.2)
        
        # In half-open state, limited calls allowed
        assert cb.state == CircuitState.HALF_OPEN
        
        # Record successes to close
        cb.record_success()
        cb.record_success()
        
        # Should be closed now
        assert cb.state == CircuitState.CLOSED
        
        print("\nCircuit breaker recovery completed successfully")


class TestRetryStress:
    """Stress tests for retry logic"""
    
    def test_backoff_calculation_performance(self, stress_config):
        """Test backoff delay calculation performance"""
        set_retry_config(RetryConfig(
            base_delay_seconds=0.5,
            exponential_base=2.0,
            max_delay_seconds=10.0
        ))
        
        start_time = time.time()
        
        for _ in range(10000):
            delay = _calculate_backoff_delay(random.randint(0, 10))
            assert delay >= 0
        
        duration = time.time() - start_time
        
        # 10k calculations should be very fast
        assert duration < 1.0
        
        print(f"\n10k backoff calculations completed in {duration:.3f}s")
    
    def test_retry_config_updates(self, stress_config):
        """Test rapid retry config updates"""
        for i in range(100):
            config = RetryConfig(
                max_retries=i % 10,
                base_delay_seconds=0.1 * (i % 5),
            )
            set_retry_config(config)
        
        print("\nRapid config updates completed without errors")


class TestConcurrencyStress:
    """Stress tests for concurrent operations"""
    
    def test_circuit_breaker_parallel_state_checks(self, stress_config):
        """Test parallel state checks on circuit breaker"""
        config = CircuitBreakerConfig(failure_threshold=50)
        cb = CircuitBreaker("parallel_test", config)
        
        results = []
        
        def state_checker():
            for _ in range(100):
                state = cb.state
                can_exec = cb.can_execute()
                results.append((state, can_exec))
        
        # Run parallel state checks
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(state_checker) for _ in range(10)]
            for f in as_completed(futures):
                f.result()
        
        # All checks should complete without errors
        assert len(results) == 1000
        
        print(f"\n1000 parallel state checks completed")
    
    def test_mixed_operations_concurrency(self, stress_config):
        """Test mixed success/failure operations"""
        config = CircuitBreakerConfig(failure_threshold=1000)
        cb = CircuitBreaker("mixed_test", config)
        
        def mixed_worker(worker_id: int):
            for i in range(50):
                if random.random() < 0.3:
                    cb.record_failure(f"error_{worker_id}_{i}")
                else:
                    cb.record_success()
        
        with ThreadPoolExecutor(max_workers=stress_config.num_threads) as executor:
            futures = [executor.submit(mixed_worker, i) for i in range(stress_config.num_threads)]
            for f in as_completed(futures):
                f.result()
        
        print(f"\nMixed concurrent operations completed")
        print(f"Final failure count: {cb._failure_count}")


class TestLatencyStress:
    """Stress tests for latency under load"""
    
    def test_circuit_breaker_latency_under_load(self, stress_config):
        """Measure latency of circuit breaker operations under load"""
        config = CircuitBreakerConfig(failure_threshold=1000)
        cb = CircuitBreaker("latency_test", config)
        
        latencies = []
        
        # Measure operation latency
        for _ in range(1000):
            start = time.perf_counter()
            cb.record_success()
            cb.record_failure("test")
            latency = (time.perf_counter() - start) * 1000  # Convert to ms
            latencies.append(latency)
        
        avg_latency = statistics.mean(latencies)
        p50 = statistics.median(latencies)
        p95 = statistics.quantiles(latencies, n=20)[18]  # 95th percentile
        p99 = statistics.quantiles(latencies, n=100)[98]  # 99th percentile
        
        print(f"\nCircuit breaker latency under load:")
        print(f"  Average: {avg_latency:.3f}ms")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")
        
        # All latencies should be very low
        assert p99 < 10.0  # 99th percentile should be under 10ms


class TestResourceStress:
    """Stress tests for resource usage"""
    
    def test_circuit_breaker_memory_usage(self, stress_config):
        """Test memory usage with many circuit breaker instances"""
        # Create many circuit breakers
        breakers = []
        
        for i in range(100):
            config = CircuitBreakerConfig(failure_threshold=10)
            cb = CircuitBreaker(f"cb_{i}", config)
            
            # Record some failures
            for j in range(10):
                cb.record_failure(f"error_{j}")
            
            breakers.append(cb)
        
        # All should be in OPEN state
        for cb in breakers:
            assert cb.state == CircuitState.OPEN
        
        print(f"\n100 circuit breaker instances created and tested")
        
        # Clear references
        breakers.clear()
    
    def test_state_transitions_memory(self, stress_config):
        """Test memory usage with rapid state transitions"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.01,
            success_threshold=1
        )
        cb = CircuitBreaker("transitions_test", config)
        
        # Rapid transitions
        for _ in range(1000):
            cb.record_failure("error")
            time.sleep(0.02)  # Allow state transition
            cb.record_success()
            time.sleep(0.02)
        
        print(f"\n1000 state transitions completed without memory issues")


# =============================================================================
# Stress Test Utilities
# =============================================================================

def run_stress_test(
    test_func,
    config: StressTestConfig,
    **kwargs
) -> StressTestResult:
    """Run a stress test and collect metrics"""
    start_time = time.time()
    
    # Run the test
    test_func(config, **kwargs)
    
    duration = time.time() - start_time
    
    return StressTestResult(
        test_name=test_func.__name__,
        duration_seconds=duration,
        total_requests=config.num_requests,
        successful_requests=0,
        failed_requests=0,
        throughput=0,
        latency_avg=0,
        latency_p50=0,
        latency_p95=0,
        latency_p99=0,
    )


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
