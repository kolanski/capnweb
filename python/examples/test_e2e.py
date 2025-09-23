#!/usr/bin/env python3
"""
End-to-end test for Cap'n Web Python WebSocket functionality.
"""

import asyncio
import sys
import os

# Add the parent directory to the path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, new_websocket_rpc_session, WebSocketRpcServer
from capnweb.rpc import RpcSessionOptions


class TestApi(RpcTarget):
    """Simple test API."""
    
    async def hello(self, name: str) -> str:
        return f"Hello, {name}!"
    
    async def square(self, x: int) -> int:
        return x * x
    
    async def echo(self, data) -> any:
        return data


async def run_test():
    """Run an end-to-end test."""
    print("Starting end-to-end test...")
    
    # Create server
    def server_factory():
        return TestApi()
    
    server = WebSocketRpcServer("localhost", 8765, server_factory)
    
    try:
        # Start server
        await server.start()
        print("Server started on localhost:8765")
        
        # Give server a moment to start
        await asyncio.sleep(0.1)
        
        # Connect client
        print("Connecting client...")
        api = await new_websocket_rpc_session("ws://localhost:8765")
        
        try:
            # Test simple call
            result = await api.hello("World")
            print(f"hello('World') = {result}")
            assert result == "Hello, World!"
            
            # Test math operation
            result = await api.square(7)
            print(f"square(7) = {result}")
            assert result == 49
            
            # Test echo
            test_data = {"test": True, "numbers": [1, 2, 3]}
            result = await api.echo(test_data)
            print(f"echo({test_data}) = {result}")
            assert result == test_data
            
            print("All tests passed!")
            
        finally:
            # Clean up client
            if hasattr(api, 'dispose'):
                api.dispose()
        
    finally:
        # Stop server
        await server.stop()
        print("Server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(run_test())
        print("End-to-end test completed successfully!")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)