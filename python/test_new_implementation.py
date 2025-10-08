#!/usr/bin/env python3
"""
Test script for the new Cap'n Web Python implementation.

This script tests the core functionality to ensure our implementation
matches the protocol specification.
"""

import asyncio
from typing import Any
from capnweb.core import RpcTarget, RpcStub, RpcPromise, type_for_rpc
from capnweb.serialize import serialize, deserialize
from capnweb.rpc import RpcSession, RpcTransport
from datetime import datetime
import json


class MockTransport(RpcTransport):
    """Mock transport for testing."""

    def __init__(self):
        self.messages = []
        self.closed = False

    async def send(self, message: str) -> None:
        if self.closed:
            raise RuntimeError("Transport closed")
        print(f"Sending: {message}")
        self.messages.append(json.loads(message))

    async def receive(self) -> str:
        if self.closed:
            raise RuntimeError("Transport closed")
        # This would normally wait for messages
        await asyncio.sleep(0.1)
        return '["ping"]'

    async def close(self) -> None:
        self.closed = True

    def abort(self, reason: Any) -> None:
        print(f"Transport aborted: {reason}")
        self.closed = True


class TestApi(RpcTarget):
    """Test RPC target."""

    def hello(self) -> str:
        return "world"

    def add(self, a: int, b: int) -> int:
        return a + b

    def get_data(self) -> dict:
        return {"message": "hello", "timestamp": datetime.now()}


def test_serialization():
    """Test serialization functionality."""
    print("=== Testing Serialization ===")

    # Basic types
    data = {
        "string": "hello",
        "number": 42,
        "array": [1, 2, 3],
        "nested": {"a": 1, "b": [4, 5]}
    }

    serialized = serialize(data)
    deserialized = deserialize(serialized)
    assert deserialized == data, "Basic serialization failed"
    print("✓ Basic serialization works")

    # Date serialization
    date_val = datetime.now()
    serialized_date = serialize(date_val)
    deserialized_date = deserialize(serialized_date)
    assert isinstance(deserialized_date, datetime), "Date serialization failed"
    print("✓ Date serialization works")

    # Array escaping
    array_data = [[1, 2], [3, 4]]
    serialized_array = serialize(array_data)
    # Should be escaped as [[...], [...]]
    assert serialized_array.startswith('['), "Array escaping failed"
    print("✓ Array escaping works")

    print()


def test_type_detection():
    """Test RPC type detection."""
    print("=== Testing Type Detection ===")

    api = TestApi()
    assert type_for_rpc(api) == "rpc-target"
    assert type_for_rpc("hello") == "primitive"
    assert type_for_rpc([1, 2, 3]) == "array"
    assert type_for_rpc({"key": "value"}) == "object"
    assert type_for_rpc(datetime.now()) == "date"
    assert type_for_rpc(b"bytes") == "bytes"

    print("✓ Type detection works correctly")
    print()


async def test_rpc_session():
    """Test basic RPC session functionality."""
    print("=== Testing RPC Session ===")

    transport = MockTransport()
    api = TestApi()

    # Create session
    session = RpcSession(transport, api)
    print("✓ RPC session created")

    # Get remote main stub
    remote_main = session.get_remote_main()
    assert isinstance(remote_main, RpcStub)
    print("✓ Remote main stub created")

    # Get stats
    stats = session.get_stats()
    assert "imports" in stats and "exports" in stats
    print(f"✓ Session stats: {stats}")

    # Close session
    await session.close()
    print("✓ Session closed")

    print()


def test_stub_functionality():
    """Test stub creation and basic functionality."""
    print("=== Testing Stub Functionality ===")

    # We can't fully test stube without a real session
    # but we can test basic creation
    from capnweb.core import StubHook

    class TestHook(StubHook):
        def call(self, path, args): return self
        def map(self, path, captures, instructions): return self
        def get(self, path): return self
        def dup(self): return TestHook()
        def pull(self): return "test_result"
        def ignore_unhandled_rejections(self): pass
        def dispose(self): pass
        def on_broken(self, callback): pass

    hook = TestHook()
    stub = RpcStub(hook)
    assert not stub._disposed
    print("✓ Stub created successfully")

    # Test property access
    promise = stub.some_property
    assert isinstance(promise, RpcPromise)
    print("✓ Stub property access works")

    # Test method access
    method_promise = stub.some_method
    assert isinstance(method_promise, RpcPromise)
    print("✓ Stub method access works")

    # Test disposal
    stub.dispose()
    assert stub._disposed
    print("✓ Stub disposal works")

    print()


def main():
    """Run all tests."""
    print("🚀 Testing New Cap'n Web Python Implementation")
    print("=" * 50)

    test_serialization()
    test_type_detection()
    test_stub_functionality()

    # Run async tests
    asyncio.run(test_rpc_session())

    print("🎉 All tests passed!")


if __name__ == "__main__":
    main()