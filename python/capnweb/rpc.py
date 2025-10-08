"""
RPC session management for Cap'n Web Python.

This module handles the core RPC session logic, message routing,
and capability management.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable, Awaitable
from .core import RpcTarget, RpcStub, StubHook, PropertyPath
from .serialize import Devaluator, Evaluator, Exporter, Importer

logger = logging.getLogger(__name__)


class RpcTransport(ABC):
    """
    Abstract base class for RPC transports.
    
    A transport provides a bidirectional message stream for RPC communication.
    """
    
    @abstractmethod
    async def send(self, message: str) -> None:
        """Send a message to the remote peer."""
        pass
    
    @abstractmethod
    async def receive(self) -> str:
        """Receive a message from the remote peer."""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close the transport."""
        pass


class RpcSessionOptions:
    """Configuration options for RPC sessions."""
    
    def __init__(self, 
                 debug: bool = False,
                 max_message_size: int = 1024 * 1024,  # 1MB
                 call_timeout: float = 30.0,
                 connect_timeout: float = 10.0,
                 reconnect_enabled: bool = False,
                 reconnect_max_attempts: int = 5,
                 reconnect_delay: float = 1.0,
                 reconnect_backoff_factor: float = 2.0,
                 reconnect_max_delay: float = 60.0,
                 on_send_error: Optional[Callable[[Exception], Optional[Exception]]] = None,
                 on_connect: Optional[Callable[[], Awaitable[None]]] = None,
                 on_disconnect: Optional[Callable[[Exception], Awaitable[None]]] = None,
                 auth_handler: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None):
        """
        Initialize RPC session options.
        
        Args:
            debug: Enable debug logging
            max_message_size: Maximum message size in bytes
            call_timeout: Timeout for individual RPC calls
            connect_timeout: Timeout for initial connection
            reconnect_enabled: Enable automatic reconnection on disconnection
            reconnect_max_attempts: Maximum number of reconnection attempts
            reconnect_delay: Initial delay between reconnection attempts
            reconnect_backoff_factor: Exponential backoff factor for reconnection delays
            reconnect_max_delay: Maximum delay between reconnection attempts
            on_send_error: Callback for error serialization/redaction
            on_connect: Callback called when connection is established
            on_disconnect: Callback called when connection is lost
            auth_handler: Callback to provide authentication data
        """
        self.debug = debug
        self.max_message_size = max_message_size
        self.call_timeout = call_timeout
        self.connect_timeout = connect_timeout
        self.reconnect_enabled = reconnect_enabled
        self.reconnect_max_attempts = reconnect_max_attempts
        self.reconnect_delay = reconnect_delay
        self.reconnect_backoff_factor = reconnect_backoff_factor
        self.reconnect_max_delay = reconnect_max_delay
        self.on_send_error = on_send_error
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.auth_handler = auth_handler


class ExportTableEntry:
    """Entry in the exports table."""
    
    def __init__(self, hook: StubHook, refcount: int = 1):
        self.hook = hook
        self.refcount = refcount


class ImportTableEntry:
    """Entry in the imports table."""
    
    def __init__(self, session: 'RpcSessionImpl', import_id: int):
        self.session = session
        self.import_id = import_id
        self.refcount = 1


class LocalStubHook(StubHook):
    """Hook for local objects that can be called directly."""
    
    def __init__(self, target: Any):
        self._target = target
        self._disposed = False
    
    async def call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Call a method on the local target."""
        if self._disposed:
            raise RuntimeError("Cannot call disposed object")
        
        if not hasattr(self._target, method_name):
            raise AttributeError(f"'{type(self._target).__name__}' object has no attribute '{method_name}'")
        
        method = getattr(self._target, method_name)
        if not callable(method):
            raise TypeError(f"'{method_name}' is not callable")
        
        if asyncio.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        else:
            return method(*args, **kwargs)
    
    async def get_property(self, property_name: str) -> Any:
        """Get a property from the local target."""
        if self._disposed:
            raise RuntimeError("Cannot access property of disposed object")
        
        # First try to get the attribute directly
        if hasattr(self._target, property_name):
            attr = getattr(self._target, property_name)
            # If it's a property or not callable, return it directly
            if not callable(attr) or isinstance(attr, property):
                return attr
        
        # If no such attribute exists, raise AttributeError
        raise AttributeError(f"'{type(self._target).__name__}' object has no attribute '{property_name}'")
    
    def dispose(self) -> None:
        """Dispose of this hook."""
        self._disposed = True
        if hasattr(self._target, '__exit__'):
            try:
                self._target.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error disposing target: {e}")


class RemoteStubHook(StubHook):
    """Hook for remote objects accessible via RPC."""
    
    def __init__(self, session: 'RpcSessionImpl', import_id: int):
        self._session = session
        self._import_id = import_id
        self._disposed = False
    
    async def call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Call a method on the remote object."""
        if self._disposed:
            raise RuntimeError("Cannot call disposed object")
        
        return await self._session._call_remote(self._import_id, method_name, args, kwargs)
    
    async def get_property(self, property_name: str) -> Any:
        """Get a property from the remote object."""
        if self._disposed:
            raise RuntimeError("Cannot access property of disposed object")
        
        return await self._session._get_remote_property(self._import_id, property_name)
    
    def dispose(self) -> None:
        """Dispose of this hook."""
        if not self._disposed:
            self._disposed = True
            self._session._release_import(self._import_id)


class RpcSessionImpl(Exporter, Importer):
    """Internal implementation of RPC session."""
    
    def __init__(self, transport: RpcTransport, local_main: Any = None, 
                 options: RpcSessionOptions = None):
        self._transport = transport
        self._options = options or RpcSessionOptions()
        self._original_transport_factory: Optional[Callable[[], Awaitable[RpcTransport]]] = None
        
        # Export/import tables
        self._exports: List[Optional[ExportTableEntry]] = []
        self._imports: List[Optional[ImportTableEntry]] = []
        
        # Message handling
        self._next_call_id = 1
        self._pending_calls: Dict[int, asyncio.Future] = {}
        self._closed = False
        self._connected = False
        self._reconnect_attempts = 0
        
        # Store local main for reconnection
        self._local_main = local_main
        
        # Export the main object at index 0
        if local_main is not None:
            main_hook = LocalStubHook(local_main)
        else:
            main_hook = LocalStubHook(object())  # Dummy object
        
        self._exports.append(ExportTableEntry(main_hook))
        
        # Import the remote main object at index 0
        self._imports.append(ImportTableEntry(self, 0))
        
        # Start message handling
        self._read_task = asyncio.create_task(self._read_loop())
    
    def set_transport_factory(self, factory: Callable[[], Awaitable[RpcTransport]]) -> None:
        """Set a factory function for creating new transports (used for reconnection)."""
        self._original_transport_factory = factory
    
    def get_remote_main(self) -> RpcStub:
        """Get a stub for the remote main object."""
        hook = RemoteStubHook(self, 0)
        return RpcStub(hook)
    
    def get_stats(self) -> Dict[str, int]:
        """Get session statistics."""
        return {
            "imports": len([e for e in self._imports if e is not None]),
            "exports": len([e for e in self._exports if e is not None]),
            "pending_calls": len(self._pending_calls)
        }
    
    async def drain(self) -> None:
        """Wait for all pending calls to complete."""
        while self._pending_calls:
            await asyncio.sleep(0.01)
    
    async def close(self) -> None:
        """Close the session."""
        if self._closed:
            return
        
        self._closed = True
        
        # Cancel pending calls
        for future in self._pending_calls.values():
            if not future.done():
                future.cancel()
        
        # Close transport
        await self._transport.close()
        
        # Cancel read task
        if not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
    
    # Exporter interface
    def export_stub(self, hook: StubHook) -> int:
        """Export a stub and return its export ID."""
        export_id = len(self._exports)
        self._exports.append(ExportTableEntry(hook))
        return export_id
    
    def export_promise(self, hook: StubHook) -> int:
        """Export a promise and return its export ID."""
        # For now, treat promises the same as stubs
        return self.export_stub(hook)
    
    # Importer interface
    def import_stub(self, import_id: int) -> RpcStub:
        """Import a stub by its import ID."""
        hook = RemoteStubHook(self, import_id)
        return RpcStub(hook)
    
    def import_promise(self, import_id: int) -> RpcStub:
        """Import a promise by its import ID."""
        # For now, treat promises the same as stubs
        return self.import_stub(import_id)
    
    async def _call_remote(self, export_id: int, method_name: str, 
                          args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Call a method on a remote object."""
        call_id = self._next_call_id
        self._next_call_id += 1
        
        # Serialize arguments
        all_args = list(args)
        if kwargs:
            all_args.append(kwargs)
        
        serialized_args, export_ids = Devaluator.devaluate(all_args, self)
        
        message = {
            "type": "call",
            "callId": call_id,
            "exportId": export_id,
            "method": method_name,
            "args": serialized_args,
            "exports": export_ids
        }
        
        # Create future for the result
        future = asyncio.get_event_loop().create_future()
        self._pending_calls[call_id] = future
        
        try:
            await self._transport.send(json.dumps(message))
            
            # Wait for response with timeout
            return await asyncio.wait_for(future, timeout=self._options.call_timeout)
        
        except asyncio.TimeoutError:
            # Clean up on timeout
            self._pending_calls.pop(call_id, None)
            raise asyncio.TimeoutError(f"RPC call timed out after {self._options.call_timeout}s")
    
    async def _get_remote_property(self, export_id: int, property_name: str) -> Any:
        """Get a property from a remote object."""
        # Properties are accessed like method calls with no arguments
        return await self._call_remote(export_id, property_name, [], {})
    
    def _release_import(self, import_id: int) -> None:
        """Release an import, decrementing its reference count."""
        if import_id < len(self._imports) and self._imports[import_id] is not None:
            entry = self._imports[import_id]
            entry.refcount -= 1
            
            if entry.refcount <= 0:
                self._imports[import_id] = None
                
                # Send release message to peer
                message = {
                    "type": "release",
                    "importId": import_id
                }
                asyncio.create_task(self._transport.send(json.dumps(message)))
    
    async def _read_loop(self) -> None:
        """Main message reading loop with reconnection support."""
        while not self._closed:
            try:
                # Mark as connected if we're starting fresh
                if not self._connected:
                    self._connected = True
                    self._reconnect_attempts = 0
                    if self._options.on_connect:
                        try:
                            await self._options.on_connect()
                        except Exception as e:
                            logger.warning(f"Error in on_connect callback: {e}")
                
                # Message reading loop
                while not self._closed:
                    try:
                        message_str = await self._transport.receive()
                        message = json.loads(message_str)
                        await self._handle_message(message)
                    
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode message: {e}")
                    except Exception as e:
                        # For batch transport, "Batch RPC request ended" is normal
                        if not self._closed and str(e) != "Batch RPC request ended.":
                            logger.error(f"Error handling message: {e}")
                        # Break on any transport error to potentially reconnect
                        raise
                        
            except Exception as e:
                self._connected = False
                
                # Call disconnect callback
                if self._options.on_disconnect:
                    try:
                        await self._options.on_disconnect(e)
                    except Exception as callback_error:
                        logger.warning(f"Error in on_disconnect callback: {callback_error}")
                
                if self._closed:
                    break
                
                # Try to reconnect if enabled
                if (self._options.reconnect_enabled and 
                    self._original_transport_factory and
                    self._reconnect_attempts < self._options.reconnect_max_attempts):
                    
                    self._reconnect_attempts += 1
                    delay = min(
                        self._options.reconnect_delay * (self._options.reconnect_backoff_factor ** (self._reconnect_attempts - 1)),
                        self._options.reconnect_max_delay
                    )
                    
                    logger.info(f"Attempting reconnection {self._reconnect_attempts}/{self._options.reconnect_max_attempts} in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    
                    try:
                        # Close old transport
                        await self._transport.close()
                        
                        # Create new transport
                        self._transport = await self._original_transport_factory()
                        logger.info(f"Reconnection {self._reconnect_attempts} successful")
                        continue  # Continue with new transport
                        
                    except Exception as reconnect_error:
                        logger.error(f"Reconnection {self._reconnect_attempts} failed: {reconnect_error}")
                        if self._reconnect_attempts >= self._options.reconnect_max_attempts:
                            logger.error("Maximum reconnection attempts reached")
                            break
                        continue  # Try again
                else:
                    if not self._closed and str(e) != "Batch RPC request ended.":
                        logger.error(f"Read loop error (no reconnection): {e}")
                    break
        
        # Close session on exit
        if not self._closed:
            await self.close()
    
    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle an incoming message."""
        msg_type = message.get("type")
        
        if msg_type == "call":
            await self._handle_call(message)
        elif msg_type == "response":
            await self._handle_response(message)
        elif msg_type == "error":
            await self._handle_error(message)
        elif msg_type == "release":
            await self._handle_release(message)
        else:
            logger.warning(f"Unknown message type: {msg_type}")
    
    async def _handle_call(self, message: Dict[str, Any]) -> None:
        """Handle an incoming call message."""
        call_id = message["callId"]
        export_id = message["exportId"]
        method_name = message["method"]
        serialized_args = message["args"]
        
        try:
            # Get the export
            if export_id >= len(self._exports) or self._exports[export_id] is None:
                raise ValueError(f"Invalid export ID: {export_id}")
            
            export_entry = self._exports[export_id]
            
            # Deserialize arguments
            args = Evaluator.evaluate(serialized_args, self)
            
            # Separate args and kwargs
            if isinstance(args, list) and len(args) > 0 and isinstance(args[-1], dict):
                kwargs = args[-1]
                args = args[:-1]
            else:
                kwargs = {}
            
            # Determine if this is a method call or property access
            if not args and not kwargs:
                # This might be a property access
                try:
                    result = await export_entry.hook.get_property(method_name)
                except AttributeError:
                    # If property access fails, try calling as a method with no args
                    result = await export_entry.hook.call(method_name, args, kwargs)
            else:
                # This is definitely a method call
                result = await export_entry.hook.call(method_name, args, kwargs)
            
            # Serialize and send response
            serialized_result, export_ids = Devaluator.devaluate(result, self)
            
            response = {
                "type": "response",
                "callId": call_id,
                "result": serialized_result,
                "exports": export_ids
            }
            
            await self._transport.send(json.dumps(response))
        
        except Exception as e:
            # Process error through callback if provided
            processed_error = e
            if self._options.on_send_error and isinstance(e, Exception):
                try:
                    callback_result = self._options.on_send_error(e)
                    if callback_result is not None:
                        processed_error = callback_result
                except Exception as callback_error:
                    logger.warning(f"Error in on_send_error callback: {callback_error}")
            
            # Send error response
            error_response = {
                "type": "error",
                "callId": call_id,
                "error": {
                    "name": type(processed_error).__name__,
                    "message": str(processed_error)
                }
            }
            
            await self._transport.send(json.dumps(error_response))
    
    async def _handle_response(self, message: Dict[str, Any]) -> None:
        """Handle an incoming response message."""
        call_id = message["callId"]
        
        if call_id in self._pending_calls:
            future = self._pending_calls.pop(call_id)
            
            if not future.done():
                # Deserialize result
                result = Evaluator.evaluate(message["result"], self)
                future.set_result(result)
    
    async def _handle_error(self, message: Dict[str, Any]) -> None:
        """Handle an incoming error message."""
        call_id = message["callId"]
        
        if call_id in self._pending_calls:
            future = self._pending_calls.pop(call_id)
            
            if not future.done():
                error_info = message["error"]
                error_class = getattr(__builtins__, error_info.get("name", "Exception"), Exception)
                error = error_class(error_info.get("message", "Remote error"))
                future.set_exception(error)
    
    async def _handle_release(self, message: Dict[str, Any]) -> None:
        """Handle an incoming release message."""
        import_id = message["importId"]
        
        if import_id < len(self._exports) and self._exports[import_id] is not None:
            entry = self._exports[import_id]
            entry.refcount -= 1
            
            if entry.refcount <= 0:
                entry.hook.dispose()
                self._exports[import_id] = None


class RpcSession:
    """
    Public RPC session interface.
    
    This wraps RpcSessionImpl and provides a clean public API.
    """
    
    def __init__(self, transport: RpcTransport, local_main: Any = None, 
                 options: RpcSessionOptions = None):
        self._impl = RpcSessionImpl(transport, local_main, options)
    
    def get_remote_main(self) -> RpcStub:
        """Get a stub for the remote main object."""
        return self._impl.get_remote_main()
    
    def get_stats(self) -> Dict[str, int]:
        """Get session statistics."""
        return self._impl.get_stats()
    
    async def drain(self) -> None:
        """Wait for all pending calls to complete."""
        await self._impl.drain()
    
    async def close(self) -> None:
        """Close the session."""
        await self._impl.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()