#!/usr/bin/env python3
"""
Simple demonstration of Cap'n Web Python core functionality.

This example shows serialization, RpcTarget usage, and basic RPC concepts
without needing WebSocket communication.
"""

import asyncio
import sys
import os

# Add the parent directory to the path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, serialize, deserialize
from capnweb.core import type_for_rpc
from capnweb.serialize import Devaluator, Evaluator


class Calculator(RpcTarget):
    """A simple calculator that can be used as an RPC target."""
    
    def __init__(self, name: str = "Calculator"):
        super().__init__()
        self.name = name
        self.operations_count = 0
    
    async def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        self.operations_count += 1
        return a + b
    
    async def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        self.operations_count += 1
        return a * b
    
    async def get_stats(self) -> dict:
        """Get calculator statistics."""
        return {
            "name": self.name,
            "operations_performed": self.operations_count
        }


class Counter(RpcTarget):
    """A simple counter object."""
    
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


def demonstrate_serialization():
    """Demonstrate basic serialization functionality."""
    print("=== Serialization Demo ===")
    
    # Test basic types
    test_values = [
        None,
        True,
        False,
        42,
        3.14159,
        "Hello, World!",
        [1, 2, 3, 4, 5],
        {"name": "John", "age": 30, "active": True},
        b"binary data",
        ValueError("test error")
    ]
    
    for value in test_values:
        rpc_type = type_for_rpc(value)
        serialized = serialize(value)
        deserialized = deserialize(serialized)
        
        if isinstance(value, Exception):
            # Special handling for exceptions
            matches = (type(deserialized).__name__ == type(value).__name__ and 
                      str(deserialized) == str(value))
        else:
            matches = deserialized == value
        
        print(f"  {rpc_type:12} | {str(value)[:30]:30} | {'✓' if matches else '✗'}")
    
    print()


def demonstrate_rpc_targets():
    """Demonstrate RpcTarget functionality."""
    print("=== RpcTarget Demo ===")
    
    # Create RPC targets
    calc = Calculator("MyCalc")
    counter = Counter(10)
    
    # Test type detection
    print(f"Calculator type: {type_for_rpc(calc)}")
    print(f"Counter type: {type_for_rpc(counter)}")
    print(f"Regular function type: {type_for_rpc(lambda x: x)}")
    
    # Test context manager usage
    with counter as c:
        print(f"Counter value: {c.value}")
    
    print()


async def demonstrate_local_calls():
    """Demonstrate calling RpcTarget methods locally."""
    print("=== Local RPC Calls Demo ===")
    
    calc = Calculator("LocalCalc")
    
    # Call methods directly (not over RPC, but same interface)
    result1 = await calc.add(5, 7)
    print(f"5 + 7 = {result1}")
    
    result2 = await calc.multiply(3, 4)
    print(f"3 * 4 = {result2}")
    
    stats = await calc.get_stats()
    print(f"Calculator stats: {stats}")
    
    # Test counter
    counter = Counter(0)
    
    for i in range(5):
        value = await counter.increment(2)
        print(f"Counter after increment: {value}")
    
    print()


def demonstrate_advanced_serialization():
    """Demonstrate advanced serialization features."""
    print("=== Advanced Serialization Demo ===")
    
    # Test nested data structures
    complex_data = {
        "metadata": {
            "version": "1.0",
            "timestamp": "2024-01-01T00:00:00Z"
        },
        "data": [
            {"id": 1, "values": [1.1, 2.2, 3.3]},
            {"id": 2, "values": [4.4, 5.5, 6.6]},
        ],
        "binary": b"some binary data",
        "flags": [True, False, True]
    }
    
    # Serialize and deserialize
    serialized, exports = Devaluator.devaluate(complex_data)
    deserialized = Evaluator.evaluate(serialized)
    
    print(f"Original data keys: {list(complex_data.keys())}")
    print(f"Deserialized keys: {list(deserialized.keys())}")
    print(f"Data preserved: {'✓' if deserialized == complex_data else '✗'}")
    print(f"Exports found: {len(exports)}")
    
    print()


async def main():
    """Main demonstration function."""
    print("Cap'n Web Python - Core Functionality Demo")
    print("=" * 50)
    print()
    
    # Run all demonstrations
    demonstrate_serialization()
    demonstrate_rpc_targets()
    await demonstrate_local_calls()
    demonstrate_advanced_serialization()
    
    print("✓ All demonstrations completed successfully!")
    print()
    print("This shows that the core Cap'n Web Python functionality is working.")
    print("The RPC transport layer (WebSocket, HTTP) can be added on top of this foundation.")


if __name__ == "__main__":
    asyncio.run(main())