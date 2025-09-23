#!/usr/bin/env python3
"""
Advanced example demonstrating enhanced Cap'n Web Python features:
- Reconnection with exponential backoff
- Timeout handling
- Error management with callbacks
- Authentication
"""

import asyncio
import sys
import os
import logging
from typing import Dict, Any, Optional

# Add the parent directory to the path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, new_websocket_rpc_session, WebSocketRpcServer
from capnweb.rpc import RpcSessionOptions

# Configure logging to see reconnection attempts
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SecureApiServer(RpcTarget):
    """API server with authentication and error handling."""
    
    def __init__(self):
        super().__init__()
        self._authenticated_clients = set()
        self._call_count = 0
    
    async def authenticate(self, token: str) -> Dict[str, Any]:
        """Authenticate a client with a token."""
        if token == "valid-token-123":
            client_id = f"client-{len(self._authenticated_clients) + 1}"
            self._authenticated_clients.add(client_id)
            return {
                "success": True,
                "client_id": client_id,
                "permissions": ["read", "write"]
            }
        else:
            raise PermissionError("Invalid authentication token")
    
    async def hello(self, name: str) -> str:
        """Simple greeting that requires authentication."""
        self._call_count += 1
        
        # Simulate occasional server errors for testing error handling
        if self._call_count % 5 == 0:
            raise RuntimeError(f"Simulated server error (call #{self._call_count})")
        
        return f"Hello, {name}! This is call #{self._call_count}"
    
    async def get_server_status(self) -> Dict[str, Any]:
        """Get server status information."""
        return {
            "authenticated_clients": len(self._authenticated_clients),
            "total_calls": self._call_count,
            "server_status": "healthy"
        }


async def auth_handler() -> Dict[str, str]:
    """Authentication handler that provides headers for WebSocket connection."""
    # In a real application, this might read from a config file or environment
    return {
        "Authorization": "Bearer valid-token-123"
    }


def error_handler(error: Exception) -> Optional[Exception]:
    """Error handler for outgoing errors (server-side)."""
    logger.info(f"Processing outgoing error: {type(error).__name__}: {error}")
    
    # Redact sensitive information from error messages
    if "password" in str(error).lower() or "secret" in str(error).lower():
        return Exception("Sensitive information redacted")
    
    # Return the original error (could also return a modified version)
    return error


async def on_connect():
    """Called when connection is established."""
    logger.info("🔗 Connected to server!")


async def on_disconnect(error: Exception):
    """Called when connection is lost."""
    logger.warning(f"💔 Disconnected from server: {error}")


async def run_server():
    """Run the enhanced API server."""
    def server_factory():
        return SecureApiServer()
    
    # Configure server options with error handling
    server_options = RpcSessionOptions(
        debug=True,
        on_send_error=error_handler,
        call_timeout=10.0
    )
    
    server = WebSocketRpcServer("localhost", 8765, server_factory, server_options)
    
    logger.info("🚀 Starting secure server on ws://localhost:8765")
    logger.info("Press Ctrl+C to stop the server")
    
    try:
        await server.serve_forever()
    except KeyboardInterrupt:
        logger.info("🛑 Server stopping...")
        await server.stop()


async def run_client():
    """Run the enhanced client with reconnection and auth."""
    logger.info("🔄 Starting enhanced client...")
    
    # Configure client options with reconnection and callbacks
    client_options = RpcSessionOptions(
        debug=True,
        call_timeout=5.0,
        connect_timeout=5.0,
        reconnect_enabled=True,
        reconnect_max_attempts=3,
        reconnect_delay=1.0,
        reconnect_backoff_factor=2.0,
        reconnect_max_delay=10.0,
        auth_handler=auth_handler,
        on_connect=on_connect,
        on_disconnect=on_disconnect
    )
    
    try:
        # Connect to the server with enhanced options
        api = await new_websocket_rpc_session("ws://localhost:8765", options=client_options)
        
        try:
            # Test authentication
            auth_result = await api.authenticate("valid-token-123")
            logger.info(f"✅ Authentication successful: {auth_result}")
            
            # Test normal operation
            for i in range(8):
                try:
                    greeting = await api.hello(f"User {i+1}")
                    logger.info(f"📝 Server response: {greeting}")
                    
                    # Get server status occasionally
                    if i % 3 == 0:
                        status = await api.get_server_status()
                        logger.info(f"📊 Server status: {status}")
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"❌ RPC call failed: {e}")
                    await asyncio.sleep(0.5)
            
            logger.info("✅ Client demonstration completed successfully")
            
        finally:
            # Clean up
            if hasattr(api, 'dispose'):
                api.dispose()
                
    except Exception as e:
        logger.error(f"❌ Client failed: {e}")


async def run_client_with_failures():
    """Demonstrate reconnection by simulating connection failures."""
    logger.info("🔄 Testing reconnection behavior...")
    
    # More aggressive reconnection settings for testing
    client_options = RpcSessionOptions(
        debug=True,
        call_timeout=2.0,
        connect_timeout=3.0,
        reconnect_enabled=True,
        reconnect_max_attempts=5,
        reconnect_delay=0.5,
        reconnect_backoff_factor=1.5,
        reconnect_max_delay=5.0,
        auth_handler=auth_handler,
        on_connect=on_connect,
        on_disconnect=on_disconnect
    )
    
    try:
        api = await new_websocket_rpc_session("ws://localhost:8765", options=client_options)
        
        # Keep making calls - if server is stopped and restarted, should reconnect
        for i in range(20):
            try:
                greeting = await api.hello(f"Persistent User {i+1}")
                logger.info(f"📝 [{i+1}] {greeting}")
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"❌ Call {i+1} failed: {e}")
                await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"❌ Persistent client failed: {e}")


async def main():
    """Main function that demonstrates the enhanced features."""
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "server":
            await run_server()
        elif sys.argv[1] == "client":
            await run_client()
        elif sys.argv[1] == "persistent":
            await run_client_with_failures()
        else:
            print("Usage: python example_enhanced.py [server|client|persistent]")
            sys.exit(1)
    else:
        print("Cap'n Web Python - Enhanced Features Demo")
        print("=" * 45)
        print()
        print("Choose mode:")
        print("  python example_enhanced.py server     - Run the secure server")
        print("  python example_enhanced.py client     - Run the enhanced client")
        print("  python example_enhanced.py persistent - Test reconnection behavior")
        print()
        print("Enhanced features demonstrated:")
        print("  ✓ Authentication with custom headers")
        print("  ✓ Automatic reconnection with exponential backoff")
        print("  ✓ Connection/disconnection event callbacks")
        print("  ✓ Error handling and redaction")
        print("  ✓ Configurable timeouts")
        print("  ✓ Debug logging for troubleshooting")


if __name__ == "__main__":
    asyncio.run(main())