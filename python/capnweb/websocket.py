"""
WebSocket transport for Cap'n Web Python.

This module provides WebSocket-based RPC transport for both client and server sides
with automatic reconnection, retry logic, and connection pooling.
"""

import asyncio
import json
import logging
from typing import Optional, Union, List, Callable, Any
from urllib.parse import urlparse

try:
    import websockets
    # Use the newer API to avoid deprecation warnings
    try:
        from websockets.asyncio.server import ServerConnection as WebSocketServerProtocol
        from websockets.asyncio.client import ClientConnection as WebSocketClientProtocol
    except ImportError:
        # Fall back to legacy API if newer one isn't available
        from websockets.server import WebSocketServerProtocol
        from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None
    WebSocketServerProtocol = None
    WebSocketClientProtocol = None

from .rpc import RpcTransport, RpcSession, RpcSessionOptions
from .connection import (
    ResilientTransport, ConnectionPool, RetryPolicy, CircuitBreaker,
    ResilientRpcSession, ConnectionState
)
from .core import RpcStub

logger = logging.getLogger(__name__)


class WebSocketTransport(RpcTransport):
    """WebSocket transport implementation."""

    def __init__(self, websocket: Union[WebSocketServerProtocol, WebSocketClientProtocol]):
        if websockets is None:
            raise ImportError("websockets package is required for WebSocket transport")

        self._websocket = websocket
        self._closed = False
        self._last_ping = 0

    async def send(self, message: str) -> None:
        """Send a message over the WebSocket."""
        if self._closed:
            raise RuntimeError("Cannot send on closed transport")

        try:
            await self._websocket.send(message)

            # Update ping for health monitoring
            if json.loads(message)[0] == "ping":
                self._last_ping = asyncio.get_event_loop().time()

        except Exception as e:
            self._closed = True
            raise

    async def receive(self) -> str:
        """Receive a message from the WebSocket."""
        if self._closed:
            raise RuntimeError("Cannot receive on closed transport")

        try:
            message = await self._websocket.recv()
            if isinstance(message, bytes):
                message = message.decode('utf-8')

            # Handle pong responses
            try:
                parsed = json.loads(message)
                if isinstance(parsed, list) and parsed[0] == "pong":
                    return await self.receive()  # Skip pong messages
            except (json.JSONDecodeError, IndexError):
                pass

            return message
        except Exception as e:
            self._closed = True
            raise

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if not self._closed:
            self._closed = True
            try:
                await self._websocket.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")

    def abort(self, reason: Any) -> None:
        """Abort the WebSocket connection."""
        if not self._closed:
            self._closed = True
            try:
                asyncio.create_task(self._websocket.close())
            except Exception:
                pass


class WebSocketConnectionManager:
    """
    Manages WebSocket connections with automatic reconnection and failover.
    """

    def __init__(self,
                 uris: List[str],
                 retry_policy: RetryPolicy = None,
                 circuit_breaker: CircuitBreaker = None,
                 enable_pooling: bool = False,
                 pool_size: int = 5):
        self.uris = uris
        self.retry_policy = retry_policy or RetryPolicy(
            max_attempts=5,
            initial_delay=1.0,
            max_delay=30.0,
            backoff_multiplier=2.0
        )
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=5,
            timeout=60.0
        )
        self.enable_pooling = enable_pooling
        self.pool_size = pool_size

    def create_transport_factory(self, uri: str, auth_handler: Callable = None) -> Callable:
        """Create a transport factory for the given URI."""
        async def factory():
            # Get authentication headers if auth handler is provided
            extra_headers = {}
            if auth_handler:
                try:
                    auth_data = await auth_handler()
                    if isinstance(auth_data, dict):
                        extra_headers.update(auth_data)
                except Exception as e:
                    logger.error(f"Authentication failed: {e}")
                    raise

            # Connect with timeout
            websocket = await asyncio.wait_for(
                websockets.connect(uri, extra_headers=extra_headers),
                timeout=30.0
            )
            return WebSocketTransport(websocket)

        return factory

    async def create_resilient_transport(self, uri: str, auth_handler: Callable = None) -> ResilientTransport:
        """Create a resilient transport for the given URI."""
        factory = self.create_transport_factory(uri, auth_handler)
        return ResilientTransport(factory, self.retry_policy, self.circuit_breaker)

    async def create_connection_pool(self, auth_handler: Callable = None) -> ConnectionPool:
        """Create a connection pool for all URIs."""
        factories = []
        for uri in self.uris:
            factory = self.create_transport_factory(uri, auth_handler)
            # Create multiple factories per URI for pooling
            for _ in range(max(1, self.pool_size // len(self.uris))):
                factories.append(factory)

        return ConnectionPool(
            factories,
            pool_size=self.pool_size,
            retry_policy=self.retry_policy,
            circuit_breaker=self.circuit_breaker
        )


async def new_websocket_rpc_session(
    uri: str,
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None,
    retry_policy: RetryPolicy = None,
    circuit_breaker: CircuitBreaker = None,
    enable_resilience: bool = True,
    enable_pooling: bool = False,
    pool_size: int = 5
) -> RpcStub:
    """
    Create a new WebSocket RPC session as a client with automatic reconnection.

    Args:
        uri: WebSocket URI to connect to (e.g., "ws://localhost:8080/api")
        local_main: Local object to expose to the remote peer
        options: Session configuration options
        retry_policy: Custom retry policy for reconnection attempts
        circuit_breaker: Custom circuit breaker for fault tolerance
        enable_resilience: Enable automatic reconnection and retry logic
        enable_pooling: Enable connection pooling for high performance
        pool_size: Number of connections in the pool (when pooling enabled)

    Returns:
        RpcStub representing the remote main object with automatic reconnection
    """
    if websockets is None:
        raise ImportError("websockets package is required for WebSocket transport")

    options = options or RpcSessionOptions()

    if not enable_resilience:
        # Legacy mode - no automatic reconnection
        websocket = await websockets.connect(uri)
        transport = WebSocketTransport(websocket)
        session = RpcSession(transport, local_main, options)
        return session.get_remote_main()

    # Resilient mode with automatic reconnection
    manager = WebSocketConnectionManager(
        uris=[uri],
        retry_policy=retry_policy,
        circuit_breaker=circuit_breaker,
        enable_pooling=enable_pooling,
        pool_size=pool_size
    )

    if enable_pooling:
        # Use connection pooling for high performance
        transport = await manager.create_connection_pool(options.auth_handler)
        session = ResilientRpcSession(transport, local_main, options, retry_policy, True)
    else:
        # Use single resilient connection
        transport = await manager.create_resilient_transport(uri, options.auth_handler)
        session = ResilientRpcSession(transport, local_main, options, retry_policy, True)

    return session.get_remote_main()


async def new_resilient_websocket_rpc_session(
    uris: Union[str, List[str]],  # Single URI or list for failover
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None,
    retry_policy: RetryPolicy = None,
    circuit_breaker: CircuitBreaker = None,
    pool_size: int = 5
) -> RpcStub:
    """
    Create a highly resilient WebSocket RPC session with failover and pooling.

    This is the recommended function for production use with:
    - Multiple server URIs for automatic failover
    - Connection pooling for maximum performance
    - Circuit breaker to prevent cascading failures
    - Advanced retry logic with exponential backoff

    Args:
        uris: WebSocket URI(s) for failover (string or list of strings)
        local_main: Local object to expose to the remote peer
        options: Session configuration options
        retry_policy: Custom retry policy for reconnection attempts
        circuit_breaker: Custom circuit breaker for fault tolerance
        pool_size: Number of connections in the pool

    Returns:
        RpcStub representing the remote main object with enterprise-grade resilience
    """
    if websockets is None:
        raise ImportError("websockets package is required for WebSocket transport")

    if isinstance(uris, str):
        uris = [uris]

    options = options or RpcSessionOptions()

    # Create high-performance connection manager
    manager = WebSocketConnectionManager(
        uris=uris,
        retry_policy=retry_policy or RetryPolicy(
            max_attempts=10,
            initial_delay=0.5,
            max_delay=60.0,
            backoff_multiplier=1.5
        ),
        circuit_breaker=circuit_breaker or CircuitBreaker(
            failure_threshold=3,
            timeout=30.0,
            success_threshold=2
        ),
        enable_pooling=True,
        pool_size=pool_size
    )

    # Create connection pool
    transport = await manager.create_connection_pool(options.auth_handler)

    # Create resilient session
    session = ResilientRpcSession(
        transport,
        local_main,
        options,
        manager.retry_policy,
        enable_state_recovery=True
    )

    # Store connection manager for monitoring
    session._connection_manager = manager

    return session.get_remote_main()


class WebSocketRpcServer:
    """WebSocket RPC server with connection monitoring."""

    def __init__(self,
                 host: str,
                 port: int,
                 local_main_factory: callable,
                 options: Optional[RpcSessionOptions] = None,
                 enable_resilience: bool = True):
        """
        Initialize the WebSocket RPC server.

        Args:
            host: Host to bind to
            port: Port to bind to
            local_main_factory: Function that returns a new main object for each connection
            options: Session configuration options
            enable_resilience: Enable resilient features for server connections
        """
        if websockets is None:
            raise ImportError("websockets package is required for WebSocket transport")

        self._host = host
        self._port = port
        self._local_main_factory = local_main_factory
        self._options = options or RpcSessionOptions()
        self._enable_resilience = enable_resilience
        self._server: Optional[websockets.WebSocketServer] = None
        self._sessions = set()
        self._connection_stats = {
            'total_connections': 0,
            'active_connections': 0,
            'failed_connections': 0
        }

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port
        )
        logger.info(f"WebSocket RPC server started on {self._host}:{self._port}")

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

            # Close all active sessions
            close_tasks = [session.close() for session in self._sessions]
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)

            logger.info("WebSocket RPC server stopped")

    async def serve_forever(self) -> None:
        """Start the server and serve forever."""
        await self.start()
        try:
            # Keep the server running
            await self._server.wait_closed()
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await self.stop()

    def get_stats(self) -> dict:
        """Get server statistics."""
        self._connection_stats['active_connections'] = len(self._sessions)
        return dict(self._connection_stats)

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a new WebSocket connection."""
        self._connection_stats['total_connections'] += 1
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"New WebSocket connection from {client_info}")

        try:
            # Create main object for this connection
            local_main = self._local_main_factory()

            # Create transport
            transport = WebSocketTransport(websocket)

            # Create session
            if self._enable_resilience:
                # Use resilient session for server connections too
                retry_policy = RetryPolicy(max_attempts=3, initial_delay=1.0)
                session = ResilientRpcSession(transport, local_main, self._options, retry_policy)
            else:
                session = RpcSession(transport, local_main, self._options)

            # Track the session
            self._sessions.add(session)

            try:
                # Keep the connection alive by waiting for it to close
                await websocket.wait_closed()
            finally:
                # Clean up when connection closes
                self._sessions.discard(session)
                await session.close()

        except Exception as e:
            self._connection_stats['failed_connections'] += 1
            logger.error(f"Error handling WebSocket connection from {client_info}: {e}")


async def new_websocket_rpc_server(
    host: str,
    port: int,
    local_main: object,
    options: Optional[RpcSessionOptions] = None,
    enable_resilience: bool = True
) -> WebSocketRpcServer:
    """
    Create and start a new WebSocket RPC server with resilience features.

    Args:
        host: Host to bind to
        port: Port to bind to
        local_main: Main object to expose to clients
        options: Session configuration options
        enable_resilience: Enable resilient features for server connections

    Returns:
        WebSocketRpcServer instance
    """
    # Create a factory that returns the same object for each connection
    def local_main_factory():
        return local_main

    server = WebSocketRpcServer(host, port, local_main_factory, options, enable_resilience)
    await server.start()
    return server


# Enhanced context manager with resilience
class ResilientWebSocketRpcSessionManager:
    """Context manager for resilient WebSocket RPC sessions."""

    def __init__(self,
                 uris: Union[str, List[str]],
                 local_main: Optional[object] = None,
                 options: Optional[RpcSessionOptions] = None,
                 retry_policy: RetryPolicy = None,
                 circuit_breaker: CircuitBreaker = None,
                 pool_size: int = 5):
        self._uris = uris if isinstance(uris, list) else [uris]
        self._local_main = local_main
        self._options = options
        self._retry_policy = retry_policy
        self._circuit_breaker = circuit_breaker
        self._pool_size = pool_size
        self._session: Optional[ResilientRpcSession] = None
        self._stub: Optional[RpcStub] = None

    async def __aenter__(self) -> RpcStub:
        """Enter the context and establish the resilient connection."""
        self._stub = await new_resilient_websocket_rpc_session(
            self._uris,
            self._local_main,
            self._options,
            self._retry_policy,
            self._circuit_breaker,
            self._pool_size
        )
        return self._stub

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the context and close the connection."""
        if self._session:
            await self._session.close()


def resilient_websocket_rpc_session(
    uris: Union[str, List[str]],
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None,
    retry_policy: RetryPolicy = None,
    circuit_breaker: CircuitBreaker = None,
    pool_size: int = 5
) -> ResilientWebSocketRpcSessionManager:
    """
    Create a context manager for resilient WebSocket RPC sessions.

    Usage:
        async with resilient_websocket_rpc_session([
            "ws://server1:8080/api",
            "ws://server2:8080/api"
        ], pool_size=10) as api:
            result = await api.hello("World")
    """
    return ResilientWebSocketRpcSessionManager(
        uris, local_main, options, retry_policy, circuit_breaker, pool_size
    )


# Backwards compatibility
def websocket_rpc_session(
    uri: str,
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None
) -> ResilientWebSocketRpcSessionManager:
    """
    Create a context manager for WebSocket RPC sessions (legacy).

    Usage:
        async with websocket_rpc_session("ws://localhost:8080/api") as api:
            result = await api.hello("World")
    """
    return ResilientWebSocketRpcSessionManager(uri, local_main, options)