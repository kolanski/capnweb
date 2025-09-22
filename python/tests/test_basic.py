"""
Basic tests for Cap'n Web Python implementation.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock

from capnweb.core import RpcTarget, RpcStub, type_for_rpc
from capnweb.serialize import serialize, deserialize, Devaluator, Evaluator


class TestRpcTarget:
    """Test RpcTarget functionality."""
    
    def test_rpc_target_creation(self):
        """Test that RpcTarget can be created and identified."""
        class MyTarget(RpcTarget):
            def hello(self):
                return "world"
        
        target = MyTarget()
        assert isinstance(target, RpcTarget)
        assert type_for_rpc(target) == "rpc-target"
    
    def test_context_manager(self):
        """Test RpcTarget as context manager."""
        class MyTarget(RpcTarget):
            def __init__(self):
                super().__init__()
                self.disposed = False
            
            def dispose(self):
                self.disposed = True
        
        target = MyTarget()
        with target:
            assert not target.disposed
        
        assert target.disposed


class TestSerialization:
    """Test serialization functionality."""
    
    def test_primitive_types(self):
        """Test serialization of primitive types."""
        test_cases = [
            None,
            True,
            False,
            42,
            3.14,
            "hello",
            [1, 2, 3],
            {"key": "value"}
        ]
        
        for value in test_cases:
            serialized = serialize(value)
            deserialized = deserialize(serialized)
            assert deserialized == value
    
    def test_type_detection(self):
        """Test type detection for RPC."""
        assert type_for_rpc(None) == "primitive"
        assert type_for_rpc(True) == "primitive"
        assert type_for_rpc(42) == "primitive"
        assert type_for_rpc("hello") == "primitive"
        assert type_for_rpc([1, 2, 3]) == "array"
        assert type_for_rpc({"key": "value"}) == "object"
        assert type_for_rpc(lambda x: x) == "function"
        assert type_for_rpc(b"bytes") == "bytes"
        assert type_for_rpc(Exception("error")) == "error"
    
    def test_bytes_serialization(self):
        """Test bytes serialization."""
        data = b"hello world"
        serialized, _ = Devaluator.devaluate(data)
        assert serialized[0] == "bytes"
        
        deserialized = Evaluator.evaluate(serialized)
        assert deserialized == data
    
    def test_error_serialization(self):
        """Test error serialization."""
        error = ValueError("test error")
        serialized, _ = Devaluator.devaluate(error)
        assert serialized[0] == "error"
        assert serialized[1]["name"] == "ValueError"
        assert serialized[1]["message"] == "test error"
        
        deserialized = Evaluator.evaluate(serialized)
        assert isinstance(deserialized, ValueError)
        assert str(deserialized) == "test error"


class MockTransport:
    """Mock transport for testing."""
    
    def __init__(self):
        self.sent_messages = []
        self.received_messages = []
        self.closed = False
    
    async def send(self, message: str) -> None:
        if self.closed:
            raise RuntimeError("Transport closed")
        self.sent_messages.append(message)
    
    async def receive(self) -> str:
        if self.closed:
            raise RuntimeError("Transport closed")
        if not self.received_messages:
            await asyncio.sleep(0.1)  # Simulate waiting
            raise RuntimeError("No messages available")
        return self.received_messages.pop(0)
    
    async def close(self) -> None:
        self.closed = True
    
    def add_received_message(self, message: str):
        """Add a message to be received."""
        self.received_messages.append(message)


@pytest.mark.asyncio
class TestRpcSession:
    """Test RPC session functionality."""
    
    async def test_session_creation(self):
        """Test that RPC session can be created."""
        from capnweb.rpc import RpcSession
        
        transport = MockTransport()
        
        class MyApi(RpcTarget):
            def hello(self):
                return "world"
        
        api = MyApi()
        session = RpcSession(transport, api)
        
        # Test getting remote main
        remote_main = session.get_remote_main()
        assert isinstance(remote_main, RpcStub)
        
        # Test stats
        stats = session.get_stats()
        assert "imports" in stats
        assert "exports" in stats
        
        await session.close()
    
    async def test_session_context_manager(self):
        """Test RPC session as context manager."""
        from capnweb.rpc import RpcSession
        
        transport = MockTransport()
        
        async with RpcSession(transport) as session:
            assert isinstance(session, RpcSession)
        
        assert transport.closed


if __name__ == "__main__":
    pytest.main([__file__])