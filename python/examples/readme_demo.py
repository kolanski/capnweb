#!/usr/bin/env python3
"""
Example showing Cap'n Web Python usage similar to the README.

This demonstrates the same patterns as the JavaScript version,
adapted for Python with async/await.
"""

import asyncio
import sys
import os

# Add the parent directory to the path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget


class MyApiServer(RpcTarget):
    """Example API server implementation - equivalent to JavaScript version."""
    
    async def hello(self, name: str) -> str:
        """Simple greeting method."""
        return f"Hello, {name}!"
    
    async def square(self, number: int) -> int:
        """Square a number."""
        return number * number
    
    async def make_counter(self, initial_value: int = 0) -> 'Counter':
        """Create and return a Counter object (passed by reference)."""
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


class Counter(RpcTarget):
    """A counter that can be passed by reference - equivalent to JavaScript version."""
    
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


async def demonstrate_api_usage():
    """Demonstrate using the API locally (simulating what would happen over RPC)."""
    print("=== Cap'n Web Python API Demo ===")
    print("(This simulates the same calls that would happen over RPC)")
    print()
    
    # Create the API server instance
    api = MyApiServer()
    
    # Simple method call
    greeting = await api.hello("World")
    print(f"api.hello('World') -> '{greeting}'")
    
    # Math operation
    result = await api.square(7)
    print(f"api.square(7) -> {result}")
    
    # Generate Fibonacci sequence
    fibonacci = await api.generate_fibonacci(10)
    print(f"api.generate_fibonacci(10) -> {fibonacci}")
    
    # Create a counter object (would be passed by reference over RPC)
    counter = await api.make_counter(5)
    print(f"counter = api.make_counter(5)")
    print(f"counter.value -> {counter.value}")
    
    # Increment the counter through the API
    new_value = await api.increment_counter(counter, 3)
    print(f"api.increment_counter(counter, 3) -> {new_value}")
    print(f"counter.value -> {counter.value}")
    
    # Direct counter operations
    incremented = await counter.increment(2)
    print(f"counter.increment(2) -> {incremented}")
    
    print()


def show_equivalent_patterns():
    """Show the equivalent patterns between JavaScript and Python."""
    print("=== JavaScript vs Python Patterns ===")
    print()
    
    print("JavaScript (from README):")
    print("```javascript")
    print('import { newWebSocketRpcSession } from "capnweb";')
    print("")
    print("// One-line setup")
    print('let api = newWebSocketRpcSession("wss://example.com/api");')
    print("")
    print("// Call a method on the server!")
    print('let result = await api.hello("World");')
    print("```")
    print()
    
    print("Python equivalent:")
    print("```python")
    print("from capnweb import new_websocket_rpc_session")
    print("")
    print("# One-line setup")
    print('api = await new_websocket_rpc_session("wss://example.com/api")')
    print("")
    print("# Call a method on the server!")
    print('result = await api.hello("World")')
    print("```")
    print()
    
    print("Server comparison:")
    print()
    print("JavaScript:")
    print("```javascript")
    print("class MyApiServer extends RpcTarget {")
    print("  hello(name) {")
    print('    return `Hello, ${name}!`;')
    print("  }")
    print("}")
    print("```")
    print()
    
    print("Python:")
    print("```python")
    print("class MyApiServer(RpcTarget):")
    print("    async def hello(self, name: str) -> str:")
    print('        return f"Hello, {name}!"')
    print("```")
    print()


def show_key_features():
    """Show the key features of Cap'n Web Python."""
    print("=== Key Features of Cap'n Web Python ===")
    print()
    
    features = [
        "✓ Object-capability RPC: Pass objects and functions by reference",
        "✓ Promise pipelining: Chain RPC calls in single network round trip", 
        "✓ Bidirectional calls: Both client and server can call each other",
        "✓ WebSocket transport: Built-in WebSocket support",
        "✓ Type hints: Full typing support for better development experience",
        "✓ Context managers: Automatic resource cleanup with 'async with'",
        "✓ Async/await: Native Python async support instead of Promises",
        "✓ Pythonic naming: snake_case instead of camelCase conventions"
    ]
    
    for feature in features:
        print(f"  {feature}")
    
    print()


async def main():
    """Main demonstration."""
    print("Cap'n Web Python - Usage Examples")
    print("=" * 40)
    print()
    
    await demonstrate_api_usage()
    show_equivalent_patterns()
    show_key_features()
    
    print("This demonstrates the core Cap'n Web concepts in Python.")
    print("The same patterns work over WebSocket, HTTP, or any transport.")
    print()
    print("Next steps:")
    print("- Set up a WebSocket server: new_websocket_rpc_server(host, port, api)")
    print("- Connect a client: new_websocket_rpc_session(uri)")
    print("- Use context managers: async with websocket_rpc_session(uri) as api:")


if __name__ == "__main__":
    asyncio.run(main())