#!/usr/bin/env python3
"""
Simple test for batch transport functionality.
"""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, new_http_batch_rpc_response, new_http_batch_rpc_session


class TestApi(RpcTarget):
    """Simple test API."""
    
    async def add(self, a: int, b: int) -> int:
        return a + b
    
    async def multiply(self, a: int, b: int) -> int:
        return a * b
    
    async def get_user(self, user_id: str) -> dict:
        return {"id": user_id, "name": f"User {user_id}"}


async def test_batch_transport():
    """Test that batch transport can serialize and deserialize messages."""
    print("Testing batch transport...")
    
    # Simulate a batch request
    # First, let's create messages manually to test the server side
    
    # Create a simple batch request
    request_messages = [
        '{"type":"call","exportId":0,"method":"add","args":[2,3],"callId":1}',
        '{"type":"call","exportId":0,"method":"multiply","args":[4,5],"callId":2}',
    ]
    request_body = "\n".join(request_messages)
    
    print(f"Request body:\n{request_body}\n")
    
    # Process the batch on the server side
    response_body = await new_http_batch_rpc_response(request_body, TestApi())
    
    print(f"Response body:\n{response_body}\n")
    
    # Check that we got responses
    response_messages = response_body.split("\n") if response_body else []
    print(f"Got {len(response_messages)} response messages")
    
    if len(response_messages) >= 2:
        print("✓ Batch transport test passed!")
        return True
    else:
        print("✗ Batch transport test failed - expected 2+ response messages")
        return False


async def test_batch_client_mock():
    """Test batch client with a mock send function."""
    print("\nTesting batch client with mock...")
    
    from capnweb.batch import BatchClientTransport
    
    # Mock send_batch function
    async def mock_send_batch(batch):
        print(f"Mock received batch with {len(batch)} messages")
        # Simulate server responses
        responses = []
        for msg in batch:
            # Simple echo response
            responses.append(f'{{"type":"return","value":"ok","callId":1}}')
        return responses
    
    transport = BatchClientTransport(mock_send_batch)
    
    # Send some messages
    await transport.send('{"type":"call","method":"test"}')
    await transport.send('{"type":"call","method":"test2"}')
    
    # Receive responses
    try:
        msg1 = await transport.receive()
        print(f"Received: {msg1}")
        print("✓ Batch client test passed!")
        return True
    except Exception as e:
        print(f"✗ Batch client test failed: {e}")
        return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Batch Transport Tests")
    print("=" * 60)
    
    test1 = await test_batch_transport()
    test2 = await test_batch_client_mock()
    
    print("\n" + "=" * 60)
    if test1 and test2:
        print("All tests passed! ✓")
        return 0
    else:
        print("Some tests failed ✗")
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
