# Cap'n Web Python

A Python implementation of Cap'n Web: A capability-based RPC system.

This is a pure Python port of the JavaScript Cap'n Web library, providing the same object-capability RPC functionality for Python applications with native async/await support.

## Installation

```bash
pip install websockets aiohttp  # Install dependencies
# Then copy the capnweb package to your project
```

## Quick Example

### Client

```python
import asyncio
from capnweb import new_websocket_rpc_session

async def main():
    # One-line setup
    api = await new_websocket_rpc_session("ws://localhost:8080/api")
    
    # Call a method on the server!
    result = await api.hello("World")
    print(result)  # "Hello, World!"

asyncio.run(main())
```

### Server

```python
import asyncio
from capnweb import RpcTarget, new_websocket_rpc_server

class MyApiServer(RpcTarget):
    async def hello(self, name: str) -> str:
        return f"Hello, {name}!"

async def main():
    server = await new_websocket_rpc_server("localhost", 8080, MyApiServer())
    await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
```

## Features

- **Object-capability RPC**: Pass objects and functions by reference
- **Promise pipelining**: Chain RPC calls in a single network round trip  
- **Bidirectional calls**: Both client and server can call each other
- **WebSocket transport**: Built-in WebSocket support
- **Type hints**: Full typing support for better development experience
- **Context managers**: Automatic resource cleanup
- **Async/await**: Native Python async support

## Advanced Features

### HTTP Batch Transport and Pipelining

The HTTP batch transport allows multiple RPC calls to be sent in a single HTTP request, dramatically reducing latency for dependent calls:

```python
import asyncio
from capnweb import new_http_batch_rpc_session

async def main():
    # Create a batch session
    api = await new_http_batch_rpc_session("http://localhost:3000/rpc")
    
    # These calls are pipelined - they all go in ONE HTTP request
    user = api.authenticate("session-token")
    profile = api.get_user_profile(user.id)  # Uses result from authenticate
    notifications = api.get_notifications(user.id)  # Also uses result from authenticate
    
    # All results come back together in a single round trip
    u, p, n = await asyncio.gather(user, profile, notifications)
    print(f"User: {u}")
    print(f"Profile: {p}")
    print(f"Notifications: {n}")

asyncio.run(main())
```

Server-side batch handling:

```python
from aiohttp import web
from capnweb import RpcTarget, new_http_batch_rpc_response

class Api(RpcTarget):
    async def authenticate(self, session_token: str) -> dict:
        # Validate token and return user info
        return {"id": "u_1", "name": "Alice"}
    
    async def get_user_profile(self, user_id: str) -> dict:
        return {"id": user_id, "bio": "Developer"}
    
    async def get_notifications(self, user_id: str) -> list:
        return ["Welcome!", "You have 3 new messages"]

async def handle_rpc(request):
    """Handle batch RPC requests."""
    body = await request.text()
    response_body = await new_http_batch_rpc_response(body, Api())
    return web.Response(text=response_body)

app = web.Application()
app.router.add_post('/rpc', handle_rpc)
web.run_app(app, port=3000)
```

### Reconnection and Error Handling

```python
import asyncio
from capnweb import new_websocket_rpc_session, RpcSessionOptions

async def auth_handler():
    """Provide authentication headers for WebSocket connection."""
    return {"Authorization": "Bearer your-token-here"}

async def on_connect():
    """Called when connection is established."""
    print("Connected to server!")

async def on_disconnect(error):
    """Called when connection is lost."""
    print(f"Disconnected: {error}")

def error_handler(error):
    """Process outgoing errors (server-side)."""
    # Redact sensitive information
    if "password" in str(error).lower():
        return Exception("Sensitive information redacted")
    return error

# Configure enhanced session options
options = RpcSessionOptions(
    # Connection settings
    connect_timeout=10.0,
    call_timeout=5.0,
    
    # Reconnection settings
    reconnect_enabled=True,
    reconnect_max_attempts=5,
    reconnect_delay=1.0,
    reconnect_backoff_factor=2.0,
    reconnect_max_delay=60.0,
    
    # Callbacks
    auth_handler=auth_handler,
    on_connect=on_connect,
    on_disconnect=on_disconnect,
    on_send_error=error_handler,
    
    # Debugging
    debug=True
)

async def main():
    # Client with enhanced features
    api = await new_websocket_rpc_session("ws://localhost:8080/api", options=options)
    
    try:
        result = await api.hello("World")
        print(result)
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
```

### Authentication

The Python implementation supports custom authentication through headers:

```python
async def auth_handler():
    """Return authentication headers for WebSocket connection."""
    return {
        "Authorization": "Bearer your-jwt-token",
        "X-API-Key": "your-api-key",
        "X-Client-Version": "1.0.0"
    }

options = RpcSessionOptions(auth_handler=auth_handler)
api = await new_websocket_rpc_session("wss://secure-api.com/rpc", options=options)
```

### Timeouts

Configure different timeout values for various operations:

```python
options = RpcSessionOptions(
    connect_timeout=10.0,  # Timeout for initial connection
    call_timeout=30.0      # Timeout for individual RPC calls
)
```

### Error Handling and Redaction

Implement custom error processing for security:

```python
def sanitize_errors(error):
    """Remove sensitive information from errors before sending to client."""
    error_msg = str(error)
    
    # Redact sensitive patterns
    if any(word in error_msg.lower() for word in ['password', 'token', 'secret']):
        return Exception("Internal server error")
    
    # Log detailed error server-side while returning generic message
    if "database" in error_msg.lower():
        logger.error(f"Database error: {error}")
        return Exception("Service temporarily unavailable")
    
    return error  # Return original error if no sensitive data

options = RpcSessionOptions(on_send_error=sanitize_errors)
```

```python
import asyncio
from capnweb import RpcTarget, new_websocket_rpc_session

class Counter(RpcTarget):
    def __init__(self, initial_value: int = 0):
        super().__init__()
        self._value = initial_value
    
    async def increment(self, amount: int = 1) -> int:
        self._value += amount
        return self._value
    
    @property
    def value(self) -> int:
        return self._value

class ApiServer(RpcTarget):
    async def hello(self, name: str) -> str:
        return f"Hello, {name}!"
    
    async def make_counter(self, initial: int = 0) -> Counter:
        return Counter(initial)  # Passed by reference!
    
    async def increment_counter(self, counter, amount: int = 1) -> int:
        return await counter.increment(amount)

# Usage
async def demo():
    api = await new_websocket_rpc_session("ws://localhost:8080")
    
    # Create a counter on the server (passed by reference)
    counter = await api.make_counter(10)
    
    # Use the counter through the API
    result = await api.increment_counter(counter, 5)
    print(f"Counter value: {result}")  # 15
    
    # Or call the counter directly (pipelined RPC)
    result = await counter.increment(3)
    print(f"Counter value: {result}")  # 18

asyncio.run(demo())
```

## Key Differences from JavaScript Version

- **Async/await**: Uses Python's `async`/`await` instead of Promises
- **Context managers**: Use `async with` for automatic resource cleanup
- **Type hints**: Full typing support using Python's `typing` module
- **Snake case**: Python naming conventions (`snake_case` instead of `camelCase`)
- **Exception handling**: Python exception types instead of JavaScript errors

## Context Manager Usage

```python
import asyncio
from capnweb import websocket_rpc_session

async def main():
    async with websocket_rpc_session("ws://localhost:8080/api") as api:
        result = await api.hello("World")
        print(result)
    # Connection automatically closed when context exits

asyncio.run(main())
```

## Core Concepts

### RpcTarget

Classes that extend `RpcTarget` are passed by reference over RPC:

```python
class MyService(RpcTarget):
    async def process_data(self, data: list) -> dict:
        return {"processed": len(data), "data": data}
```

### Object Capabilities

Pass objects and functions by reference, enabling powerful patterns:

```python
# Server can call back to client-provided functions
async def process_with_callback(self, data, callback):
    for item in data:
        result = await callback(item)  # RPC back to client
        yield result
```

### Promise Pipelining

Chain RPC calls without waiting for intermediate results:

```python
# These calls can be pipelined into a single network round trip
user = api.authenticate(token)  # Returns immediately
profile = user.get_profile()    # Doesn't wait for authenticate
posts = profile.get_posts()     # Doesn't wait for get_profile

# All three results come back together
user_data, profile_data, posts_data = await asyncio.gather(user, profile, posts)
```

## Testing

```bash
cd python
pip install -r requirements.txt
PYTHONPATH=. python -m pytest tests/ -v
```

## Examples

See the `examples/` directory for:
- `demo_core.py` - Core functionality demonstration
- `readme_demo.py` - README-style usage examples  
- `example_basic.py` - Complete client/server example
- `example_enhanced.py` - Advanced features (reconnection, auth, error handling)

## Implementation Status

- ✅ Core RPC classes (RpcTarget, RpcStub, RpcSession)
- ✅ Serialization/deserialization system
- ✅ WebSocket transport with reconnection support
- ✅ HTTP batch transport with pipelining
- ✅ Authentication via custom headers
- ✅ Timeout configuration (connect and call timeouts)
- ✅ Error handling and redaction callbacks
- ✅ Connection lifecycle callbacks (on_connect, on_disconnect)
- ✅ Automatic reconnection with exponential backoff
- ✅ Basic tests and examples

This implementation provides the same powerful object-capability RPC features as the JavaScript version, adapted for Python's async ecosystem.