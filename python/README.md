# Cap'n Web Python

A Python implementation of Cap'n Web: A capability-based RPC system.

This is a pure Python port of the JavaScript Cap'n Web library, providing the same object-capability RPC functionality for Python applications.

## Installation

```bash
pip install capnweb
```

## Quick Example

### Client

```python
import asyncio
from capnweb import new_websocket_rpc_session

async def main():
    # One-line setup
    api = await new_websocket_rpc_session("wss://example.com/api")
    
    # Call a method on the server!
    result = await api.hello("World")
    
    print(result)

asyncio.run(main())
```

### Server

```python
import asyncio
from capnweb import RpcTarget, new_websocket_rpc_server

class MyApiServer(RpcTarget):
    async def hello(self, name):
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
- **HTTP batch transport**: Efficient batched HTTP requests
- **Type hints**: Full typing support for better development experience

## Key Differences from JavaScript Version

- Uses `async`/`await` instead of Promises
- Context managers for resource management instead of `Symbol.dispose`
- Python naming conventions (snake_case instead of camelCase)
- Type hints using `typing` module

## Example with Resource Management

```python
import asyncio
from capnweb import new_websocket_rpc_session

async def main():
    async with new_websocket_rpc_session("wss://example.com/api") as api:
        result = await api.hello("World")
        print(result)
    # Connection automatically closed when context exits

asyncio.run(main())
```