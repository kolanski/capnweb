#!/usr/bin/env python3
"""
Simple example demonstrating Cap'n Web Python usage.

This example shows a basic client-server interaction using WebSocket transport.
"""

import asyncio
import sys
import logging
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, new_websocket_rpc_session, new_websocket_rpc_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Counter(RpcTarget):
    """A simple counter object that can be passed by reference."""
    
    def __init__(self, initial_value: int = 0):
        super().__init__()
        self._value = initial_value
    
    async def increment(self, amount: int = 1) -> int:
        """Increment the counter and return the new value."""
        self._value += amount
        return self._value
    
    @property
    def value(self) -> int:
        """Get the current counter value."""
        return self._value


class MyApiServer(RpcTarget):
    """Example server implementation."""
    
    async def hello(self, name: str) -> str:
        """Simple greeting method."""
        return f"Hello, {name}!"
    
    async def square(self, number: int) -> int:
        """Square a number."""
        return number * number
    
    async def call_square(self, stub, number: int) -> dict:
        """Call the square method on a stub (for testing pipelining)."""
        result = await stub.square(number)
        return {"result": result}
    
    async def make_counter(self, initial_value: int = 0) -> Counter:
        """Create and return a Counter object."""
        return Counter(initial_value)
    
    async def increment_counter(self, counter, amount: int = 1) -> int:
        """Increment a counter passed by reference."""
        return await counter.increment(amount)
    
    async def generate_fibonacci(self, length: int) -> list:
        """Generate a Fibonacci sequence."""
        if length <= 0:
            return []
        elif length == 1:
            return [0]
        elif length == 2:
            return [0, 1]
        
        result = [0, 1]
        while len(result) < length:
            next_val = result[-1] + result[-2]
            result.append(next_val)
        
        return result


async def run_server():
    """Run the example server."""
    server_api = MyApiServer()
    server = await new_websocket_rpc_server("localhost", 8080, server_api)
    
    logger.info("Server started on ws://localhost:8080")
    logger.info("Press Ctrl+C to stop the server")
    
    try:
        await server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopping...")
        await server.stop()


async def run_client():
    """Run the example client."""
    logger.info("Connecting to server...")
    
    # Connect to the server
    api = await new_websocket_rpc_session("ws://localhost:8080")
    
    try:
        # Simple method call
        greeting = await api.hello("World")
        logger.info(f"Server says: {greeting}")
        
        # Math operation
        result = await api.square(7)
        logger.info(f"7 squared is: {result}")
        
        # Create a counter object (passed by reference)
        counter = await api.make_counter(10)
        logger.info(f"Created counter with initial value: {await counter.value}")
        
        # Increment the counter
        new_value = await api.increment_counter(counter, 5)
        logger.info(f"Counter after increment: {new_value}")
        
        # Generate Fibonacci sequence
        fibonacci = await api.generate_fibonacci(10)
        logger.info(f"Fibonacci sequence: {fibonacci}")
        
        # Demonstrate pipelining (calling method on a promise)
        # This would normally require multiple round trips, but with pipelining
        # it can be done in a single request
        counter2 = await api.make_counter(0)
        incremented = await counter2.increment(42)
        logger.info(f"Pipelined counter result: {incremented}")
        
    finally:
        # Clean up
        if hasattr(api, 'dispose'):
            api.dispose()


async def main():
    """Main function that demonstrates both server and client."""
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "server":
            await run_server()
        elif sys.argv[1] == "client":
            await run_client()
        else:
            print("Usage: python example_basic.py [server|client]")
            sys.exit(1)
    else:
        print("Choose mode:")
        print("  python example_basic.py server  - Run the server")
        print("  python example_basic.py client  - Run the client")


if __name__ == "__main__":
    asyncio.run(main())