"""
Cap'n Web Python - A capability-based RPC system

This module provides a Python implementation of Cap'n Web, bringing
object-capability RPC to Python applications.
"""

from .core import RpcTarget, RpcStub, RpcPromise
from .rpc import RpcSession, RpcTransport, RpcSessionOptions
from .websocket import new_websocket_rpc_session, new_websocket_rpc_server, websocket_rpc_session, WebSocketRpcServer
from .batch import new_http_batch_rpc_session, new_http_batch_rpc_response
from .serialize import serialize, deserialize

__version__ = "0.1.0"
__all__ = [
    "RpcTarget",
    "RpcStub", 
    "RpcPromise",
    "RpcSession",
    "RpcSessionOptions",
    "RpcTransport",
    "new_websocket_rpc_session",
    "new_websocket_rpc_server",
    "websocket_rpc_session",
    "WebSocketRpcServer",
    "new_http_batch_rpc_session",
    "new_http_batch_rpc_response",
    "serialize",
    "deserialize",
]