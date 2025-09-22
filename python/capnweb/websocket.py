"""
WebSocket transport for Cap'n Web Python.

This module provides WebSocket-based RPC transport for both client and server sides.
"""

import asyncio
import logging
from typing import Optional, Union
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
from .core import RpcStub

logger = logging.getLogger(__name__)


class WebSocketTransport(RpcTransport):
    """WebSocket transport implementation."""
    
    def __init__(self, websocket: Union[WebSocketServerProtocol, WebSocketClientProtocol]):
        if websockets is None:
            raise ImportError("websockets package is required for WebSocket transport")
        
        self._websocket = websocket
        self._closed = False
    
    async def send(self, message: str) -> None:
        """Send a message over the WebSocket."""
        if self._closed:
            raise RuntimeError("Cannot send on closed transport")
        
        try:
            await self._websocket.send(message)
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


async def new_websocket_rpc_session(
    uri: str, 
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None
) -> RpcStub:
    """
    Create a new WebSocket RPC session as a client.
    
    Args:
        uri: WebSocket URI to connect to (e.g., "ws://localhost:8080/api")
        local_main: Local object to expose to the remote peer
        options: Session configuration options
    
    Returns:
        RpcStub representing the remote main object
    """
    if websockets is None:
        raise ImportError("websockets package is required for WebSocket transport")
    
    websocket = await websockets.connect(uri)
    transport = WebSocketTransport(websocket)
    session = RpcSession(transport, local_main, options)
    
    return session.get_remote_main()


class WebSocketRpcServer:
    """WebSocket RPC server."""
    
    def __init__(self, host: str, port: int, local_main_factory: callable,
                 options: Optional[RpcSessionOptions] = None):
        """
        Initialize the WebSocket RPC server.
        
        Args:
            host: Host to bind to
            port: Port to bind to
            local_main_factory: Function that returns a new main object for each connection
            options: Session configuration options
        """
        if websockets is None:
            raise ImportError("websockets package is required for WebSocket transport")
        
        self._host = host
        self._port = port
        self._local_main_factory = local_main_factory
        self._options = options or RpcSessionOptions()
        self._server: Optional[websockets.WebSocketServer] = None
        self._sessions = set()
    
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
    
    async def _handle_connection(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """Handle a new WebSocket connection."""
        logger.info(f"New WebSocket connection from {websocket.remote_address}")
        
        try:
            # Create main object for this connection
            local_main = self._local_main_factory()
            
            # Create transport and session
            transport = WebSocketTransport(websocket)
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
            logger.error(f"Error handling WebSocket connection: {e}")


async def new_websocket_rpc_server(
    host: str, 
    port: int, 
    local_main: object,
    options: Optional[RpcSessionOptions] = None
) -> WebSocketRpcServer:
    """
    Create and start a new WebSocket RPC server.
    
    Args:
        host: Host to bind to
        port: Port to bind to  
        local_main: Main object to expose to clients
        options: Session configuration options
    
    Returns:
        WebSocketRpcServer instance
    """
    # Create a factory that returns the same object for each connection
    def local_main_factory():
        return local_main
    
    server = WebSocketRpcServer(host, port, local_main_factory, options)
    await server.start()
    return server


# Context manager support for cleaner usage
class WebSocketRpcSessionManager:
    """Context manager for WebSocket RPC sessions."""
    
    def __init__(self, uri: str, local_main: Optional[object] = None,
                 options: Optional[RpcSessionOptions] = None):
        self._uri = uri
        self._local_main = local_main
        self._options = options
        self._session: Optional[RpcSession] = None
        self._stub: Optional[RpcStub] = None
    
    async def __aenter__(self) -> RpcStub:
        """Enter the context and establish the connection."""
        if websockets is None:
            raise ImportError("websockets package is required for WebSocket transport")
        
        websocket = await websockets.connect(self._uri)
        transport = WebSocketTransport(websocket)
        self._session = RpcSession(transport, self._local_main, self._options)
        self._stub = self._session.get_remote_main()
        return self._stub
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the context and close the connection."""
        if self._session:
            await self._session.close()


def websocket_rpc_session(
    uri: str,
    local_main: Optional[object] = None,
    options: Optional[RpcSessionOptions] = None
) -> WebSocketRpcSessionManager:
    """
    Create a context manager for WebSocket RPC sessions.
    
    Usage:
        async with websocket_rpc_session("ws://localhost:8080/api") as api:
            result = await api.hello("World")
    """
    return WebSocketRpcSessionManager(uri, local_main, options)