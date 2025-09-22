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

## Advanced Example

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

## Implementation Status

- ✅ Core RPC classes (RpcTarget, RpcStub, RpcSession)
- ✅ Serialization/deserialization system
- ✅ WebSocket transport
- ✅ Basic tests and examples
- 🚧 HTTP batch transport (planned)
- 🚧 Advanced pipelining features (in progress)

This implementation provides the same powerful object-capability RPC features as the JavaScript version, adapted for Python's async ecosystem.