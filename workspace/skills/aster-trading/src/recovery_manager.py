"""
Recovery Manager Module

Provides automated recovery procedures for connection failures and order execution errors
in the Aster Trading system. Implements exponential backoff retry logic and circuit breaker
patterns for resilient error recovery.
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

# Configure module logger
logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states for tracking system health."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing - reject requests
    HALF_OPEN = "half_open"  # Testing if recovery is possible


class RecoveryAttemptType(Enum):
    """Types of recovery attempts tracked."""
    CONNECTION_RECOVERY = "connection_recovery"
    ORDER_EXECUTION_RECOVERY = "order_execution_recovery"


class RecoveryStatus(Enum):
    """Status of a recovery attempt."""
    SUCCESS = "success"
    FAILURE = "failure"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


class RecoveryAttempt:
    """Record of a single recovery attempt."""

    def __init__(
        self,
        attempt_type: RecoveryAttemptType,
        timestamp: datetime,
        status: RecoveryStatus,
        error_details: Optional[str] = None,
        retry_count: int = 0,
        details: Optional[Dict[str, Any]] = None
    ):
        self.attempt_type = attempt_type
        self.timestamp = timestamp
        self.status = status
        self.error_details = error_details
        self.retry_count = retry_count
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert recovery attempt to dictionary for logging."""
        return {
            "attempt_type": self.attempt_type.value,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "error_details": self.error_details,
            "retry_count": self.retry_count,
            "details": self.details
        }


class CircuitBreaker:
    """Circuit breaker for preventing cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitBreakerState:
        """Get current circuit breaker state, checking for timeout transitions."""
        if self._state == CircuitBreakerState.OPEN:
            if self._last_failure_time:
                elapsed = (datetime.utcnow() - self._last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._half_open_calls = 0
                    logger.info("Circuit breaker transitioned to HALF_OPEN state")
        return self._state

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._state = CircuitBreakerState.CLOSED
                self._failure_count = 0
                self._half_open_calls = 0
                logger.info("Circuit breaker transitioned to CLOSED state")
        elif self._state == CircuitBreakerState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = datetime.utcnow()

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker transitioned to OPEN state after half_open failure")
        elif self._state == CircuitBreakerState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(
                    f"Circuit breaker opened after {self._failure_count} failures"
                )

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        return self.state != CircuitBreakerState.OPEN


class RecoveryManager:
    """
    Manages automated recovery procedures for connection failures and order execution errors.

    Features:
    - Exponential backoff retry logic
    - Circuit breaker pattern
    - Comprehensive logging of all recovery attempts
    - Order execution verification with exchange
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_recovery_timeout: float = 60.0
    ):
        """
        Initialize the RecoveryManager.

        Args:
            max_retries: Maximum number of retry attempts for failed operations.
            backoff_base: Base value for exponential backoff calculation.
            circuit_breaker_failure_threshold: Number of failures before opening circuit.
            circuit_breaker_recovery_timeout: Seconds to wait before attempting recovery.
        """
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self._circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_failure_threshold,
            recovery_timeout=circuit_breaker_recovery_timeout
        )

        self._recovery_log: List[RecoveryAttempt] = []
        self._stats = {
            "total_attempts": 0,
            "successful_recoveries": 0,
            "failed_recoveries": 0,
            "connection_recoveries": 0,
            "order_recoveries": 0
        }

        logger.info(
            f"RecoveryManager initialized with max_retries={max_retries}, "
            f"backoff_base={backoff_base}"
        )

    @property
    def circuit_breaker_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state."""
        return self._circuit_breaker.state

    def _calculate_backoff(self, retry_count: int) -> float:
        """
        Calculate backoff delay using exponential backoff.

        Args:
            retry_count: Current retry attempt number (0-indexed).

        Returns:
            Delay in seconds before next retry.
        """
        return self.backoff_base * (2 ** retry_count)

    def _log_recovery_attempt(self, attempt: RecoveryAttempt) -> None:
        """
        Log a recovery attempt and update statistics.

        Args:
            attempt: The RecoveryAttempt to log.
        """
        self._recovery_log.append(attempt)
        self._stats["total_attempts"] += 1

        if attempt.status == RecoveryStatus.SUCCESS:
            self._stats["successful_recoveries"] += 1
            if attempt.attempt_type == RecoveryAttemptType.CONNECTION_RECOVERY:
                self._stats["connection_recoveries"] += 1
            else:
                self._stats["order_recoveries"] += 1
            logger.info(
                f"Recovery successful: {attempt.attempt_type.value} "
                f"(retry_count={attempt.retry_count})"
            )
        elif attempt.status == RecoveryStatus.FAILURE:
            self._stats["failed_recoveries"] += 1
            logger.error(
                f"Recovery failed: {attempt.attempt_type.value} "
                f"(retry_count={attempt.retry_count}): {attempt.error_details}"
            )
        else:
            logger.warning(
                f"Recovery status {attempt.status.value}: "
                f"{attempt.attempt_type.value} - {attempt.error_details}"
            )

    async def recover_connection(
        self,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> Any:
        """
        Recover from connection failures with exponential backoff.

        Args:
            operation: The async operation to execute and retry if it fails.
            *args: Positional arguments to pass to the operation.
            **kwargs: Keyword arguments to pass to the operation.

        Returns:
            The result of the successful operation.

        Raises:
            ConnectionError: If connection fails after all retries.
            TimeoutError: If operation times out after all retries.
            Exception: Re-raises any non-recoverable errors after max retries.
        """
        # Check circuit breaker before attempting
        if not self._circuit_breaker.can_execute():
            attempt = RecoveryAttempt(
                attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
                timestamp=datetime.utcnow(),
                status=RecoveryStatus.CIRCUIT_BREAKER_OPEN,
                error_details="Circuit breaker is open, rejecting connection attempt"
            )
            self._log_recovery_attempt(attempt)
            raise ConnectionError("Circuit breaker is open, connection not allowed")

        last_exception: Optional[Exception] = None

        for retry_count in range(self.max_retries + 1):
            try:
                # Execute the operation
                if asyncio.iscoroutinefunction(operation):
                    result = await operation(*args, **kwargs)
                else:
                    result = operation(*args, **kwargs)

                # Record success
                self._circuit_breaker.record_success()

                # Log successful recovery (if this wasn't the first attempt)
                if retry_count > 0:
                    attempt = RecoveryAttempt(
                        attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
                        timestamp=datetime.utcnow(),
                        status=RecoveryStatus.SUCCESS,
                        retry_count=retry_count,
                        details={"args": str(args), "kwargs": str(kwargs)}
                    )
                    self._log_recovery_attempt(attempt)

                logger.info(
                    f"Connection operation succeeded after {retry_count} retries"
                )
                return result

            except ConnectionError as e:
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"ConnectionError: {str(e)}"
                logger.warning(
                    f"Connection failed (attempt {retry_count + 1}/{self.max_retries + 1}): "
                    f"{error_msg}"
                )

                # Log the failure
                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"operation": operation.__name__ if hasattr(operation, "__name__") else str(operation)}
                )
                self._log_recovery_attempt(attempt)

                # Check if we should retry
                if retry_count < self.max_retries:
                    backoff = self._calculate_backoff(retry_count)
                    logger.info(f"Retrying connection in {backoff:.2f} seconds...")
                    await asyncio.sleep(backoff)

            except asyncio.TimeoutError as e:
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"TimeoutError: {str(e)}"
                logger.warning(
                    f"Connection timeout (attempt {retry_count + 1}/{self.max_retries + 1})"
                )

                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"operation": operation.__name__ if hasattr(operation, "__name__") else str(operation)}
                )
                self._log_recovery_attempt(attempt)

                if retry_count < self.max_retries:
                    backoff = self._calculate_backoff(retry_count)
                    logger.info(f"Retrying after timeout in {backoff:.2f} seconds...")
                    await asyncio.sleep(backoff)

            except Exception as e:
                # Non-recoverable error - log and re-raise
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"Unexpected error: {type(e).__name__}: {str(e)}"
                logger.error(f"Non-recoverable error during connection: {error_msg}")

                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"exception_type": type(e).__name__}
                )
                self._log_recovery_attempt(attempt)

                # Only retry on certain recoverable errors
                if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
                    if retry_count < self.max_retries:
                        backoff = self._calculate_backoff(retry_count)
                        await asyncio.sleep(backoff)
                        continue

                # Re-raise non-recoverable errors
                raise

        # Max retries exceeded
        attempt = RecoveryAttempt(
            attempt_type=RecoveryAttemptType.CONNECTION_RECOVERY,
            timestamp=datetime.utcnow(),
            status=RecoveryStatus.MAX_RETRIES_EXCEEDED,
            error_details=str(last_exception) if last_exception else "Unknown error",
            retry_count=self.max_retries
        )
        self._log_recovery_attempt(attempt)

        if last_exception:
            raise last_exception
        raise ConnectionError(f"Connection failed after {self.max_retries} retries")

    async def recover_order_execution(
        self,
        order_data: Dict[str, Any],
        executor: Any
    ) -> Dict[str, Any]:
        """
        Recover from order execution failures by checking if order exists on exchange.

        Args:
            order_data: Dictionary containing order details (order_id, symbol, etc.).
            executor: The order executor object with methods to verify order status.

        Returns:
            Dictionary containing order verification result and current order state.

        Raises:
            Exception: If order verification fails after all retries.
        """
        # Check circuit breaker
        if not self._circuit_breaker.can_execute():
            attempt = RecoveryAttempt(
                attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                timestamp=datetime.utcnow(),
                status=RecoveryStatus.CIRCUIT_BREAKER_OPEN,
                error_details="Circuit breaker is open, rejecting order recovery"
            )
            self._log_recovery_attempt(attempt)
            raise ConnectionError("Circuit breaker is open, order recovery not allowed")

        order_id = order_data.get("order_id", "unknown")
        symbol = order_data.get("symbol", "unknown")

        logger.info(f"Starting order execution recovery for order {order_id} ({symbol})")

        last_exception: Optional[Exception] = None

        for retry_count in range(self.max_retries + 1):
            try:
                # Check if order exists on exchange
                if hasattr(executor, "verify_order"):
                    order_status = await executor.verify_order(order_id)
                elif hasattr(executor, "get_order"):
                    order_status = await executor.get_order(order_id)
                elif asyncio.iscoroutinefunction(executor):
                    order_status = await executor(order_id)
                else:
                    raise AttributeError(
                        "Executor must have 'verify_order' or 'get_order' method"
                    )

                # Record success
                self._circuit_breaker.record_success()

                # Check order status
                if order_status:
                    # Order exists on exchange
                    status_value = order_status.get("status", "unknown") if isinstance(order_status, dict) else "unknown"
                    logger.info(f"Order {order_id} found on exchange with status: {status_value}")

                    result = {
                        "order_exists": True,
                        "order_id": order_id,
                        "order_status": order_status,
                        "recovered": True,
                        "verification_attempts": retry_count + 1
                    }

                    if retry_count > 0:
                        attempt = RecoveryAttempt(
                            attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                            timestamp=datetime.utcnow(),
                            status=RecoveryStatus.SUCCESS,
                            retry_count=retry_count,
                            details=result
                        )
                        self._log_recovery_attempt(attempt)

                    return result
                else:
                    # Order not found - might need to resubmit
                    logger.warning(f"Order {order_id} not found on exchange")

                    if retry_count < self.max_retries:
                        # Try to resubmit the order
                        backoff = self._calculate_backoff(retry_count)
                        logger.info(f"Attempting order resubmission in {backoff:.2f} seconds...")

                        if hasattr(executor, "submit_order"):
                            await executor.submit_order(order_data)
                        elif hasattr(executor, "place_order"):
                            await executor.place_order(order_data)
                        else:
                            raise AttributeError(
                                "Executor must have 'submit_order' or 'place_order' method"
                            )

                        await asyncio.sleep(backoff)
                        continue
                    else:
                        # Order not found after all retries
                        result = {
                            "order_exists": False,
                            "order_id": order_id,
                            "order_status": None,
                            "recovered": False,
                            "verification_attempts": retry_count + 1
                        }

                        attempt = RecoveryAttempt(
                            attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                            timestamp=datetime.utcnow(),
                            status=RecoveryStatus.MAX_RETRIES_EXCEEDED,
                            error_details=f"Order {order_id} not found on exchange after {self.max_retries} retries",
                            retry_count=retry_count,
                            details=result
                        )
                        self._log_recovery_attempt(attempt)

                        return result

            except ConnectionError as e:
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"ConnectionError during order verification: {str(e)}"
                logger.warning(
                    f"Order verification failed (attempt {retry_count + 1}/{self.max_retries + 1}): "
                    f"{error_msg}"
                )

                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"order_id": order_id, "symbol": symbol}
                )
                self._log_recovery_attempt(attempt)

                if retry_count < self.max_retries:
                    backoff = self._calculate_backoff(retry_count)
                    await asyncio.sleep(backoff)

            except asyncio.TimeoutError as e:
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"TimeoutError during order verification: {str(e)}"
                logger.warning(
                    f"Order verification timeout (attempt {retry_count + 1}/{self.max_retries + 1})"
                )

                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"order_id": order_id, "symbol": symbol}
                )
                self._log_recovery_attempt(attempt)

                if retry_count < self.max_retries:
                    backoff = self._calculate_backoff(retry_count)
                    await asyncio.sleep(backoff)

            except Exception as e:
                last_exception = e
                self._circuit_breaker.record_failure()

                error_msg = f"Unexpected error during order verification: {type(e).__name__}: {str(e)}"
                logger.error(error_msg)

                attempt = RecoveryAttempt(
                    attempt_type=RecoveryAttemptType.ORDER_EXECUTION_RECOVERY,
                    timestamp=datetime.utcnow(),
                    status=RecoveryStatus.FAILURE,
                    error_details=error_msg,
                    retry_count=retry_count,
                    details={"exception_type": type(e).__name__}
                )
                self._log_recovery_attempt(attempt)

                # Only retry on recoverable errors
                if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
                    if retry_count < self.max_retries:
                        backoff = self._calculate_backoff(retry_count)
                        await asyncio.sleep(backoff)
                        continue

                raise

        # Max retries exceeded
        raise last_exception or Exception(
            f"Order verification failed after {self.max_retries} retries"
        )

    def get_recovery_log(self) -> List[Dict[str, Any]]:
        """
        Get log of all recovery attempts.

        Returns:
            List of dictionaries containing recovery attempt details.
        """
        return [attempt.to_dict() for attempt in self._recovery_log]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get recovery statistics including success/failure rates.

        Returns:
            Dictionary containing recovery statistics.
        """
        stats = self._stats.copy()

        if stats["total_attempts"] > 0:
            stats["success_rate"] = stats["successful_recoveries"] / stats["total_attempts"]
            stats["failure_rate"] = stats["failed_recoveries"] / stats["total_attempts"]
        else:
            stats["success_rate"] = 0.0
            stats["failure_rate"] = 0.0

        stats["circuit_breaker_state"] = self._circuit_breaker.state.value

        return stats

    def clear_recovery_log(self) -> None:
        """Clear the recovery log and reset statistics."""
        self._recovery_log.clear()
        self._stats = {
            "total_attempts": 0,
            "successful_recoveries": 0,
            "failed_recoveries": 0,
            "connection_recoveries": 0,
            "order_recoveries": 0
        }
        logger.info("Recovery log and statistics cleared")

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker to closed state."""
        self._circuit_breaker._state = CircuitBreakerState.CLOSED
        self._circuit_breaker._failure_count = 0
        self._circuit_breaker._half_open_calls = 0
        logger.info("Circuit breaker manually reset to CLOSED state")


# Example usage and testing
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    async def test_connection_operation():
        """Simulate a connection operation."""
        import random
        # Simulate occasional failures
        if random.random() < 0.7:
            raise ConnectionError("Simulated connection failure")
        return "Connection successful"

    async def test_order_verification():
        """Simulate order verification."""
        import random
        # Simulate occasional failures
        if random.random() < 0.5:
            raise ConnectionError("Simulated verification failure")
        return {"status": "filled", "filled_quantity": 100}

    async def main():
        # Create recovery manager
        recovery_manager = RecoveryManager(max_retries=3, backoff_base=0.5)

        print("=" * 60)
        print("Testing RecoveryManager")
        print("=" * 60)

        # Test connection recovery
        print("\n--- Testing Connection Recovery ---")
        try:
            result = await recovery_manager.recover_connection(test_connection_operation)
            print(f"Connection recovery result: {result}")
        except ConnectionError as e:
            print(f"Connection recovery failed: {e}")

        # Test order recovery
        print("\n--- Testing Order Execution Recovery ---")

        class MockExecutor:
            async def verify_order(self, order_id):
                return await test_order_verification()

            async def submit_order(self, order_data):
                print(f"Resubmitting order: {order_data}")

        executor = MockExecutor()
        order_data = {"order_id": "TEST123", "symbol": "BTC/USD", "quantity": 1.0}

        try:
            result = await recovery_manager.recover_order_execution(order_data, executor)
            print(f"Order recovery result: {result}")
        except Exception as e:
            print(f"Order recovery failed: {e}")

        # Print statistics
        print("\n--- Recovery Statistics ---")
        stats = recovery_manager.get_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")

        # Print recovery log
        print("\n--- Recovery Log ---")
        log = recovery_manager.get_recovery_log()
        for entry in log:
            print(f"  {entry}")

    # Run the test
    asyncio.run(main())
