"""
Tests for enhanced Cap'n Web Python features.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock

from capnweb.rpc import RpcSessionOptions, RpcSession
from capnweb.core import RpcTarget


class TestEnhancedFeatures:
    """Test enhanced RPC features."""
    
    def test_session_options_enhanced(self):
        """Test enhanced session options."""
        async def auth_handler():
            return {"Authorization": "Bearer token"}
        
        async def on_connect():
            pass
            
        async def on_disconnect(error):
            pass
        
        def error_handler(error):
            return Exception("Redacted")
        
        options = RpcSessionOptions(
            debug=True,
            call_timeout=10.0,
            connect_timeout=5.0,
            reconnect_enabled=True,
            reconnect_max_attempts=3,
            reconnect_delay=1.0,
            reconnect_backoff_factor=2.0,
            reconnect_max_delay=30.0,
            auth_handler=auth_handler,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
            on_send_error=error_handler
        )
        
        assert options.debug is True
        assert options.call_timeout == 10.0
        assert options.connect_timeout == 5.0
        assert options.reconnect_enabled is True
        assert options.reconnect_max_attempts == 3
        assert options.reconnect_delay == 1.0
        assert options.reconnect_backoff_factor == 2.0
        assert options.reconnect_max_delay == 30.0
        assert options.auth_handler is auth_handler
        assert options.on_connect is on_connect
        assert options.on_disconnect is on_disconnect
        assert options.on_send_error is error_handler
    
    def test_default_session_options(self):
        """Test default session options."""
        options = RpcSessionOptions()
        
        assert options.debug is False
        assert options.call_timeout == 30.0
        assert options.connect_timeout == 10.0
        assert options.reconnect_enabled is False
        assert options.reconnect_max_attempts == 5
        assert options.reconnect_delay == 1.0
        assert options.reconnect_backoff_factor == 2.0
        assert options.reconnect_max_delay == 60.0
        assert options.auth_handler is None
        assert options.on_connect is None
        assert options.on_disconnect is None
        assert options.on_send_error is None
    
    @pytest.mark.asyncio
    async def test_error_handler_callback(self):
        """Test error handling callback."""
        error_handler_called = False
        original_error = ValueError("sensitive password data")
        
        def error_handler(error):
            nonlocal error_handler_called
            error_handler_called = True
            assert error is original_error
            return Exception("Redacted error")
        
        options = RpcSessionOptions(on_send_error=error_handler)
        
        # Create a mock transport
        transport = AsyncMock()
        transport.send = AsyncMock()
        transport.receive = AsyncMock(side_effect=asyncio.CancelledError())
        transport.close = AsyncMock()
        
        # Create a test target that throws an error
        class ErrorTarget(RpcTarget):
            def test_method(self):
                raise original_error
        
        session = RpcSession(transport, ErrorTarget(), options)
        
        # Simulate handling a call that triggers an error
        message = {
            "type": "call",
            "callId": 1,
            "exportId": 0,
            "method": "test_method",
            "args": []
        }
        
        await session._impl._handle_call(message)
        
        # Verify error handler was called and response was sent
        assert error_handler_called
        assert transport.send.called
        
        # Check that the error response contains the redacted message
        call_args = transport.send.call_args[0][0]
        import json
        response = json.loads(call_args)
        assert response["type"] == "error"
        assert response["error"]["message"] == "Redacted error"
        
        await session.close()


if __name__ == "__main__":
    pytest.main([__file__])