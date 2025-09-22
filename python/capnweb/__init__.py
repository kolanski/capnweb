"""
Cap'n Web Python - A capability-based RPC system

This module provides a Python implementation of Cap'n Web, bringing
object-capability RPC to Python applications.
"""

from .core import RpcTarget, RpcStub, RpcPromise
from .rpc import RpcSession, RpcTransport
from .websocket import new_websocket_rpc_session, new_websocket_rpc_server, websocket_rpc_session, WebSocketRpcServer
from .serialize import serialize, deserialize

__version__ = "0.1.0"
__all__ = [
    "RpcTarget",
    "RpcStub", 
    "RpcPromise",
    "RpcSession",
    "RpcTransport",
    "new_websocket_rpc_session",
    "new_websocket_rpc_server",
    "websocket_rpc_session",
    "WebSocketRpcServer",
    "serialize",
    "deserialize",
]