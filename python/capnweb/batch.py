"""
HTTP batch transport for Cap'n Web Python.

This module implements HTTP batch RPC transport, allowing multiple
RPC calls to be sent in a single HTTP request for improved performance
through pipelining.
"""

import asyncio
from typing import Any, Callable, Awaitable, List, Optional
from .rpc import RpcTransport, RpcSession, RpcSessionOptions
from .core import RpcStub


class BatchClientTransport(RpcTransport):
    """
    Client-side batch transport that batches multiple RPC messages
    into a single HTTP request.
    """
    
    def __init__(self, send_batch: Callable[[List[str]], Awaitable[List[str]]]):
        """
        Initialize the batch client transport.
        
        Args:
            send_batch: Async function that sends a batch of messages and returns responses
        """
        self._send_batch = send_batch
        self._batch_to_send: Optional[List[str]] = []
        self._batch_to_receive: Optional[List[str]] = None
        self._aborted: Optional[Any] = None
        self._promise = asyncio.create_task(self._schedule_batch())
    
    async def send(self, message: str) -> None:
        """Send a message (adds it to the batch)."""
        # If the batch was already sent, we just ignore the message, because throwing may cause the
        # RPC system to abort prematurely. Once the last receive() is done then we'll throw an error
        # that aborts the RPC system at the right time and will propagate to all other requests.
        if self._batch_to_send is not None:
            self._batch_to_send.append(message)
    
    async def receive(self) -> str:
        """Receive a message from the batch response."""
        if self._batch_to_receive is None:
            await self._promise
        
        if self._batch_to_receive and len(self._batch_to_receive) > 0:
            return self._batch_to_receive.pop(0)
        else:
            # No more messages. An error thrown here will propagate out of any calls that are still
            # open.
            raise Exception("Batch RPC request ended.")
    
    async def close(self) -> None:
        """Close the transport."""
        self._aborted = Exception("Transport closed")
    
    def abort(self, reason: Any) -> None:
        """Abort the transport with an error."""
        self._aborted = reason
    
    async def _schedule_batch(self) -> None:
        """
        Wait for microtask queue to clear before sending a batch.
        
        This gives the application a chance to queue up all the RPC calls
        that should be batched together.
        """
        # Wait for the event loop to clear pending tasks
        # This is similar to setTimeout(resolve, 0) in JavaScript
        await asyncio.sleep(0)
        
        if self._aborted is not None:
            raise self._aborted
        
        batch = self._batch_to_send
        self._batch_to_send = None
        self._batch_to_receive = await self._send_batch(batch)


class BatchServerTransport(RpcTransport):
    """
    Server-side batch transport that processes a batch of RPC messages
    and collects responses to send back.
    """
    
    def __init__(self, batch: List[str]):
        """
        Initialize the batch server transport.
        
        Args:
            batch: List of messages received from the client
        """
        self._batch_to_send: List[str] = []
        self._batch_to_receive: List[str] = batch.copy()
        self._all_received_future: asyncio.Future = asyncio.Future()
    
    async def send(self, message: str) -> None:
        """Send a message (adds it to the response batch)."""
        self._batch_to_send.append(message)
    
    async def receive(self) -> str:
        """Receive a message from the request batch."""
        if len(self._batch_to_receive) > 0:
            return self._batch_to_receive.pop(0)
        else:
            # No more messages.
            if not self._all_received_future.done():
                self._all_received_future.set_result(None)
            # Return a future that never resolves (equivalent to new Promise(r => {}))
            return await asyncio.Future()
    
    async def close(self) -> None:
        """Close the transport."""
        if not self._all_received_future.done():
            self._all_received_future.set_result(None)
    
    def abort(self, reason: Any) -> None:
        """Abort the transport with an error."""
        if not self._all_received_future.done():
            self._all_received_future.set_exception(reason if isinstance(reason, Exception) else Exception(str(reason)))
    
    async def when_all_received(self) -> None:
        """Wait until all messages from the client have been received."""
        await self._all_received_future
    
    def get_response_body(self) -> str:
        """Get the response body containing all outgoing messages."""
        return "\n".join(self._batch_to_send)


async def new_http_batch_rpc_session(
    url: str,
    options: Optional[RpcSessionOptions] = None
) -> RpcStub:
    """
    Create a client-side HTTP batch RPC session.
    
    This function creates an RPC session that batches multiple calls into a
    single HTTP POST request, enabling efficient pipelining.
    
    Args:
        url: The HTTP(S) URL of the RPC endpoint
        options: Optional RPC session configuration
    
    Returns:
        RpcStub: A stub for calling the remote API
    
    Example:
        ```python
        api = await new_http_batch_rpc_session("http://localhost:3000/rpc")
        
        # These calls are pipelined in a single HTTP request
        user = api.authenticate("cookie-123")
        profile = api.get_user_profile(user.id)
        notifications = api.get_notifications(user.id)
        
        # All results come back together
        u, p, n = await asyncio.gather(user, profile, notifications)
        ```
    """
    import aiohttp
    
    async def send_batch(batch: List[str]) -> List[str]:
        """Send a batch of messages via HTTP POST and return the responses."""
        async with aiohttp.ClientSession() as session:
            body = "\n".join(batch)
            async with session.post(url, data=body) as response:
                if response.status != 200:
                    raise Exception(f"RPC request failed: {response.status} {response.reason}")
                
                text = await response.text()
                return text.split("\n") if text else []
    
    transport = BatchClientTransport(send_batch)
    rpc = RpcSession(transport, None, options)
    return rpc.get_remote_main()


async def new_http_batch_rpc_response(
    request_body: str,
    local_main: Any,
    options: Optional[RpcSessionOptions] = None
) -> str:
    """
    Create a server-side HTTP batch RPC response.
    
    This function processes a batch RPC request and returns the response body
    to send back to the client.
    
    Args:
        request_body: The request body containing batched RPC messages
        local_main: The main RPC target to expose to the client
        options: Optional RPC session configuration
    
    Returns:
        str: The response body containing batched RPC responses
    
    Example:
        ```python
        from aiohttp import web
        from capnweb import RpcTarget, new_http_batch_rpc_response
        
        class Api(RpcTarget):
            async def hello(self, name: str) -> str:
                return f"Hello, {name}!"
        
        async def handle_rpc(request):
            body = await request.text()
            response_body = await new_http_batch_rpc_response(body, Api())
            return web.Response(text=response_body)
        
        app = web.Application()
        app.router.add_post('/rpc', handle_rpc)
        ```
    """
    batch = request_body.split("\n") if request_body else []
    
    transport = BatchServerTransport(batch)
    rpc = RpcSession(transport, local_main, options)
    
    # Wait for all messages to be received and processed
    await transport.when_all_received()
    await rpc.drain()
    
    return transport.get_response_body()
