"""
Enterprise-grade connection management for Cap'n Web Python.

This module provides automatic reconnection, retry logic, connection pooling,
and circuit breaker functionality for production-scale deployments.
"""

import asyncio
import logging
import time
import random
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union
from .rpc import RpcTransport, RpcSessionImpl, RpcSessionOptions

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection states for monitoring."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ConnectionMetrics:
    """Metrics for connection monitoring."""
    connect_attempts: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    total_bytes_sent: int = 0
    total_bytes_received: int = 0
    last_connection_time: Optional[float] = None
    last_error: Optional[Exception] = None
    avg_response_time: float = 0.0
    response_times: deque = field(default_factory=lambda: deque(maxlen=100))


class CircuitBreaker:
    """
    Circuit breaker for fault tolerance.

    Automatically opens when failure rate exceeds threshold,
    preventing cascading failures.
    """

    def __init__(self,
                 failure_threshold: int = 5,
                 timeout: float = 60.0,
                 success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.success_threshold = success_threshold

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = ConnectionState.CONNECTED  # CLOSED state

    def call(self, func: Callable, *args, **kwargs):
        """Execute function through circuit breaker."""
        if self.state == ConnectionState.CIRCUIT_OPEN:
            if time.time() - (self.last_failure_time or 0) > self.timeout:
                self.state = ConnectionState.CONNECTING  # HALF_OPEN
                self.success_count = 0
            else:
                raise ConnectionError("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _on_success(self):
        """Handle successful operation."""
        self.failure_count = 0
        if self.state == ConnectionState.CONNECTING:  # HALF_OPEN
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = ConnectionState.CONNECTED
                self.success_count = 0

    def _on_failure(self):
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = ConnectionState.CIRCUIT_OPEN

    def reset(self):
        """Reset circuit breaker to closed state."""
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.state = ConnectionState.CONNECTED


class RetryPolicy:
    """Retry policy with exponential backoff and jitter."""

    def __init__(self,
                 max_attempts: int = 3,
                 initial_delay: float = 1.0,
                 max_delay: float = 30.0,
                 backoff_multiplier: float = 2.0,
                 jitter: bool = True):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number."""
        delay = min(
            self.initial_delay * (self.backoff_multiplier ** (attempt - 1)),
            self.max_delay
        )

        if self.jitter:
            # Add ±25% jitter
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)

        return max(0, delay)

    def should_retry(self, exception: Exception) -> bool:
        """Determine if exception should be retried."""
        # Don't retry certain exceptions
        non_retryable = (
            TypeError,
            ValueError,
            PermissionError,
            AttributeError
        )
        return not isinstance(exception, non_retryable)


class ResilientTransport(RpcTransport):
    """
    Transport wrapper with automatic reconnection and retry logic.
    """

    def __init__(self,
                 transport_factory: Callable[[], RpcTransport],
                 retry_policy: RetryPolicy = None,
                 circuit_breaker: CircuitBreaker = None):
        self.transport_factory = transport_factory
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        self._transport: Optional[RpcTransport] = None
        self._connection_state = ConnectionState.DISCONNECTED
        self._reconnect_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._metrics = ConnectionMetrics()
        self._on_connect_callbacks: List[Callable] = []
        self._on_disconnect_callbacks: List[Callable] = []
        self._message_queue = asyncio.Queue()
        self._shutdown = False

    async def connect(self) -> None:
        """Establish connection with retry logic."""
        if self._connection_state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            return

        await self._connect_with_retry()

    async def _connect_with_retry(self) -> None:
        """Connect with exponential backoff retry."""
        self._connection_state = ConnectionState.CONNECTING

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                self._metrics.connect_attempts += 1
                logger.info(f"Connection attempt {attempt}/{self.retry_policy.max_attempts}")

                self._transport = self.transport_factory()
                await self._transport.send('["ping"]')  # Test connection

                self._connection_state = ConnectionState.CONNECTED
                self._metrics.successful_connections += 1
                self._metrics.last_connection_time = time.time()

                # Start background tasks
                self._start_background_tasks()

                # Notify callbacks
                for callback in self._on_connect_callbacks:
                    try:
                        await callback()
                    except Exception as e:
                        logger.error(f"Error in connect callback: {e}")

                logger.info("Connection established successfully")
                return

            except Exception as e:
                self._metrics.failed_connections += 1
                self._metrics.last_error = e

                if attempt == self.retry_policy.max_attempts:
                    self._connection_state = ConnectionState.FAILED
                    logger.error(f"Failed to connect after {attempt} attempts: {e}")
                    raise ConnectionError(f"Connection failed after {attempt} attempts") from e

                if not self.retry_policy.should_retry(e):
                    self._connection_state = ConnectionState.FAILED
                    raise e

                delay = self.retry_policy.get_delay(attempt)
                logger.warning(f"Connection attempt {attempt} failed: {e}. Retrying in {delay:.2f}s")
                await asyncio.sleep(delay)

    def _start_background_tasks(self):
        """Start background monitoring tasks."""
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()

        self._ping_task = asyncio.create_task(self._ping_loop())

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        self._reconnect_task = asyncio.create_task(self._monitor_connection())

    async def _ping_loop(self):
        """Send periodic ping messages to monitor connection health."""
        while not self._shutdown and self._connection_state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(30)  # Ping every 30 seconds
                if self._connection_state == ConnectionState.CONNECTED:
                    await self._transport.send('["ping"]')
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Ping failed: {e}")
                await self._handle_connection_loss(e)
                break

    async def _monitor_connection(self):
        """Monitor connection health and handle reconnection."""
        while not self._shutdown:
            await asyncio.sleep(5)

            if self._connection_state == ConnectionState.FAILED and not self._shutdown:
                logger.info("Attempting automatic reconnection...")
                try:
                    await self._connect_with_retry()
                except Exception as e:
                    logger.error(f"Reconnection failed: {e}")

    async def _handle_connection_loss(self, error: Exception):
        """Handle unexpected connection loss."""
        if self._connection_state == ConnectionState.CONNECTED:
            logger.warning(f"Connection lost: {error}")
            self._connection_state = ConnectionState.RECONNECTING

            # Notify callbacks
            for callback in self._on_disconnect_callbacks:
                try:
                    await callback(error)
                except Exception as e:
                    logger.error(f"Error in disconnect callback: {e}")

            # Trigger reconnection
            if not self._shutdown:
                asyncio.create_task(self._attempt_reconnection())

    async def _attempt_reconnection(self):
        """Attempt to reconnect with exponential backoff."""
        if self._connection_state == ConnectionState.CONNECTED:
            return

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                logger.info(f"Reconnection attempt {attempt}/{self.retry_policy.max_attempts}")
                await self._connect_with_retry()
                return

            except Exception as e:
                if attempt == self.retry_policy.max_attempts:
                    self._connection_state = ConnectionState.FAILED
                    logger.error(f"Reconnection failed after {attempt} attempts: {e}")
                else:
                    delay = self.retry_policy.get_delay(attempt)
                    logger.warning(f"Reconnection attempt {attempt} failed: {e}. Retrying in {delay:.2f}s")
                    await asyncio.sleep(delay)

    async def send(self, message: str) -> None:
        """Send message through circuit breaker with retry logic."""
        if self._connection_state != ConnectionState.CONNECTED:
            if self._connection_state == ConnectionState.DISCONNECTED:
                await self.connect()
            elif self._connection_state in (ConnectionState.FAILED, ConnectionState.RECONNECTING):
                raise ConnectionError("Not connected")

        async def _send():
            if not self._transport:
                raise ConnectionError("No transport available")

            start_time = time.time()
            try:
                await self._transport.send(message)

                # Record metrics
                response_time = time.time() - start_time
                self._metrics.response_times.append(response_time)
                self._metrics.avg_response_time = sum(self._metrics.response_times) / len(self._metrics.response_times)
                self._metrics.total_bytes_sent += len(message.encode())

            except Exception as e:
                await self._handle_connection_loss(e)
                raise

        # Send through circuit breaker
        self.circuit_breaker.call(_send)

    async def receive(self) -> str:
        """Receive message with error handling."""
        if not self._transport:
            raise ConnectionError("No transport available")

        try:
            message = await self._transport.receive()
            self._metrics.total_bytes_received += len(message.encode())
            return message
        except Exception as e:
            await self._handle_connection_loss(e)
            raise

    async def close(self) -> None:
        """Close transport and cleanup."""
        self._shutdown = True

        # Cancel background tasks
        for task in [self._ping_task, self._reconnect_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close transport
        if self._transport:
            await self._transport.close()
            self._transport = None

        self._connection_state = ConnectionState.DISCONNECTED

    def abort(self, reason: Any) -> None:
        """Abort connection."""
        if self._transport:
            self._transport.abort(reason)

        self._connection_state = ConnectionState.FAILED
        self._metrics.last_error = reason

    def add_connect_callback(self, callback: Callable) -> None:
        """Add callback called when connection is established."""
        self._on_connect_callbacks.append(callback)

    def add_disconnect_callback(self, callback: Callable) -> None:
        """Add callback called when connection is lost."""
        self._on_disconnect_callbacks.append(callback)

    def get_metrics(self) -> ConnectionMetrics:
        """Get connection metrics."""
        return self._metrics

    def get_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._connection_state


class ConnectionPool:
    """
    High-performance connection pool for scaling RPC connections.

    Manages multiple connections with load balancing and automatic failover.
    """

    def __init__(self,
                 transport_factories: List[Callable[[], RpcTransport]],
                 pool_size: int = 10,
                 retry_policy: RetryPolicy = None,
                 circuit_breaker: CircuitBreaker = None):
        self.transport_factories = transport_factories
        self.pool_size = pool_size
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        self._connections: List[ResilientTransport] = []
        self._available_connections = asyncio.Queue()
        self._connection_lock = asyncio.Lock()
        self._initialized = False
        self._shutdown = False
        self._metrics = defaultdict(ConnectionMetrics)

    async def initialize(self) -> None:
        """Initialize connection pool."""
        if self._initialized:
            return

        async with self._connection_lock:
            if self._initialized:
                return

            for i in range(self.pool_size):
                factory = self.transport_factories[i % len(self.transport_factories)]
                transport = ResilientTransport(factory, self.retry_policy, self.circuit_breaker)
                self._connections.append(transport)
                await self._available_connections.put(transport)

            self._initialized = True
            logger.info(f"Connection pool initialized with {self.pool_size} connections")

    async def get_connection(self) -> ResilientTransport:
        """Get a connection from the pool."""
        if not self._initialized:
            await self.initialize()

        if self._shutdown:
            raise RuntimeError("Connection pool is shutdown")

        # Get connection from pool (wait if necessary)
        transport = await self._available_connections.get()

        # Ensure connection is healthy
        if transport.get_state() != ConnectionState.CONNECTED:
            try:
                await transport.connect()
            except Exception:
                # Put back and try another connection
                await self._available_connections.put(transport)
                # Try to get a different connection
                transport = await self._available_connections.get()
                if transport.get_state() != ConnectionState.CONNECTED:
                    await transport.connect()

        return transport

    async def return_connection(self, transport: ResilientTransport) -> None:
        """Return connection to pool."""
        if not self._shutdown and transport:
            await self._available_connections.put(transport)

    async def execute(self, operation: Callable) -> Any:
        """Execute operation using pooled connection."""
        transport = await self.get_connection()
        try:
            return await operation(transport)
        finally:
            await self.return_connection(transport)

    async def get_all_metrics(self) -> Dict[int, ConnectionMetrics]:
        """Get metrics for all connections."""
        metrics = {}
        for i, transport in enumerate(self._connections):
            metrics[i] = transport.get_metrics()
        return metrics

    async def close(self) -> None:
        """Close all connections in pool."""
        self._shutdown = True

        # Close all connections
        close_tasks = []
        for transport in self._connections:
            close_tasks.append(transport.close())

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        self._connections.clear()

        # Clear queue
        while not self._available_connections.empty():
            try:
                self._available_connections.get_nowait()
            except asyncio.QueueEmpty:
                break

        logger.info("Connection pool closed")

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        states = [conn.get_state() for conn in self._connections]
        state_counts = defaultdict(int)
        for state in states:
            state_counts[state.value] += 1

        return {
            "pool_size": self.pool_size,
            "active_connections": len(self._connections),
            "available_connections": self._available_connections.qsize(),
            "connection_states": dict(state_counts)
        }


class ResilientRpcSession(RpcSessionImpl):
    """
    Enhanced RPC session with automatic reconnection and state recovery.
    """

    def __init__(self,
                 transport: Union[RpcTransport, ConnectionPool],
                 local_main: Any = None,
                 options: RpcSessionOptions = None,
                 retry_policy: RetryPolicy = None,
                 enable_state_recovery: bool = True):

        # Wrap transport if needed
        if not isinstance(transport, (ResilientTransport, ConnectionPool)):
            transport = ResilientTransport(lambda: transport, retry_policy)

        self.base_transport = transport
        self.retry_policy = retry_policy or RetryPolicy()
        self.enable_state_recovery = enable_state_recovery

        # State recovery
        self._pending_calls: Dict[int, Any] = {}
        self._exported_objects: Dict[int, Any] = {}
        self._session_context: Dict[str, Any] = {}

        # Initialize with wrapped transport
        if isinstance(transport, ConnectionPool):
            # For pooled connections, we need to handle transport per operation
            super().__init__(None, local_main, options)
            self._is_pooled = True
        else:
            super().__init__(transport, local_main, options)
            self._is_pooled = False

            # Add connection monitoring
            transport.add_disconnect_callback(self._handle_disconnect)
            transport.add_connect_callback(self._handle_reconnect)

    def get_remote_main(self):
        """Get a stub for the remote main interface."""
        from .core import RpcMainHook
        return RpcMainHook(self.imports[0])

    async def _handle_disconnect(self, error: Exception):
        """Handle connection disconnection."""
        logger.warning(f"Session disconnected: {error}")

        if self.enable_state_recovery:
            await self._save_session_state()

    async def _handle_reconnect(self):
        """Handle successful reconnection."""
        logger.info("Session reconnected")

        if self.enable_state_recovery:
            await self._restore_session_state()

    async def _save_session_state(self):
        """Save current session state for recovery."""
        self._exported_objects = dict(self.exports)
        self._pending_calls = {k: v for k, v in self.imports.items() if v.active_pull}

        # Save session context
        self._session_context.update({
            'next_export_id': self.next_export_id,
            'next_import_id': self.next_import_id,
        })

    async def _restore_session_state(self):
        """Restore session state after reconnection."""
        try:
            # Restore export IDs
            self.next_export_id = self._session_context.get('next_export_id', -1)
            self.next_import_id = self._session_context.get('next_import_id', 1)

            # Re-export objects (simplified)
            for export_id, export_entry in self._exported_objects.items():
                if export_id < 0:  # Only restore negative IDs
                    self.exports[export_id] = export_entry

            # Re-establish pending calls (simplified)
            for import_id, import_entry in self._pending_calls.items():
                if import_id > 0:  # Only restore positive IDs
                    self.imports[import_id] = import_entry

            logger.info("Session state restored successfully")

        except Exception as e:
            logger.error(f"Failed to restore session state: {e}")

    async def _execute_with_connection(self, operation: Callable) -> Any:
        """Execute operation with automatic connection management."""
        if self._is_pooled:
            return await self.base_transport.execute(operation)
        else:
            return await operation(self.base_transport)

    async def send_with_retry(self, message: Any) -> None:
        """Send message with retry logic."""
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                async def _send(transport):
                    if self._is_pooled:
                        transport.send(json.dumps(message))
                    else:
                        self.send(message)

                await self._execute_with_connection(_send)
                return

            except Exception as e:
                if attempt == self.retry_policy.max_attempts:
                    logger.error(f"Failed to send message after {attempt} attempts: {e}")
                    raise

                if not self.retry_policy.should_retry(e):
                    raise

                delay = self.retry_policy.get_delay(attempt)
                logger.warning(f"Send attempt {attempt} failed: {e}. Retrying in {delay:.2f}s")
                await asyncio.sleep(delay)

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        if isinstance(self.base_transport, ConnectionPool):
            return self.base_transport.get_pool_stats()
        else:
            metrics = self.base_transport.get_metrics()
            return {
                "state": self.base_transport.get_state().value,
                "metrics": {
                    "connect_attempts": metrics.connect_attempts,
                    "successful_connections": metrics.successful_connections,
                    "avg_response_time": metrics.avg_response_time,
                }
            }