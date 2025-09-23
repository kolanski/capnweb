"""
Core RPC classes and types for Cap'n Web Python.

This module contains the fundamental building blocks for RPC communication,
including RpcTarget, RpcStub, and RpcPromise.
"""

import asyncio
import inspect
import weakref
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union
from types import TracebackType

T = TypeVar('T')


class RpcTarget:
    """
    Base class for objects that can be passed by reference over RPC.
    
    Classes extending RpcTarget will be passed by reference rather than by value
    when used in RPC calls. Method calls on stubs pointing to these objects
    will result in RPC calls back to the original object.
    """
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type: Optional[Type[BaseException]], 
                 exc_val: Optional[BaseException], 
                 exc_tb: Optional[TracebackType]) -> None:
        """Resource cleanup when used as context manager."""
        if hasattr(self, 'dispose'):
            self.dispose()


class StubHook(ABC):
    """Abstract base for stub hooks that handle RPC operations."""
    
    @abstractmethod
    async def call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Call a method on the remote object."""
        pass
    
    @abstractmethod
    async def get_property(self, property_name: str) -> Any:
        """Get a property from the remote object."""
        pass
    
    @abstractmethod
    def dispose(self) -> None:
        """Dispose of this stub hook."""
        pass


class PropertyPath:
    """Represents a path to a property through nested object access."""
    
    def __init__(self, path: List[Union[str, int]] = None):
        self.path = path or []
    
    def append(self, component: Union[str, int]) -> 'PropertyPath':
        """Return a new PropertyPath with the component appended."""
        return PropertyPath(self.path + [component])
    
    def __str__(self) -> str:
        return '.'.join(str(p) for p in self.path)


class RpcPromise:
    """
    Represents the result of an RPC call that may not have completed yet.
    
    Like JavaScript Promises, RpcPromise supports pipelining - you can call
    methods on the promise before it resolves, and those calls will be
    queued up.
    """
    
    def __init__(self, hook: StubHook, path: PropertyPath = None):
        self._hook = hook
        self._path = path or PropertyPath()
        self._future: Optional[asyncio.Future] = None
        self._resolved_value: Any = None
        self._is_resolved = False
    
    def __await__(self):
        """Allow awaiting the promise to get the resolved value."""
        if self._is_resolved:
            async def _return_resolved():
                return self._resolved_value
            return _return_resolved().__await__()
        
        if self._future is None:
            self._future = asyncio.create_task(self._resolve())
        
        return self._future.__await__()
    
    async def _resolve(self) -> Any:
        """Resolve the promise by getting the property value."""
        if self._is_resolved:
            return self._resolved_value
        
        if len(self._path.path) == 0:
            # This is a direct result, not a property access
            raise RuntimeError("Cannot resolve a promise without a property path")
        
        # Get the property from the remote object
        property_name = self._path.path[-1]
        self._resolved_value = await self._hook.get_property(str(property_name))
        self._is_resolved = True
        return self._resolved_value
    
    def __getattr__(self, name: str) -> 'RpcPromise':
        """Access a property of the promised value."""
        new_path = self._path.append(name)
        return RpcPromise(self._hook, new_path)
    
    def __call__(self, *args, **kwargs) -> 'RpcPromise':
        """Call the promised value as a function."""
        if len(self._path.path) == 0:
            raise RuntimeError("Cannot call a promise without a method name")
        
        method_name = str(self._path.path[-1])
        
        # Create a new promise for the result
        result_hook = CallResultHook(self._hook, method_name, args, kwargs)
        return RpcPromise(result_hook, PropertyPath())


class CallResultHook(StubHook):
    """Hook that represents the result of a method call."""
    
    def __init__(self, parent_hook: StubHook, method_name: str, args: List[Any], kwargs: Dict[str, Any]):
        self._parent_hook = parent_hook
        self._method_name = method_name
        self._args = args
        self._kwargs = kwargs
        self._result: Optional[Any] = None
        self._is_resolved = False
    
    async def call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Call a method on the result of the original call."""
        result = await self._get_result()
        if hasattr(result, method_name):
            method = getattr(result, method_name)
            if asyncio.iscoroutinefunction(method):
                return await method(*args, **kwargs)
            else:
                return method(*args, **kwargs)
        else:
            raise AttributeError(f"'{type(result).__name__}' object has no attribute '{method_name}'")
    
    async def get_property(self, property_name: str) -> Any:
        """Get a property from the result of the original call."""
        result = await self._get_result()
        return getattr(result, property_name)
    
    async def _get_result(self) -> Any:
        """Get the result of the original method call."""
        if self._is_resolved:
            return self._result
        
        self._result = await self._parent_hook.call(self._method_name, self._args, self._kwargs)
        self._is_resolved = True
        return self._result
    
    def dispose(self) -> None:
        """Dispose of this hook."""
        pass


class RpcStub:
    """
    A proxy object that represents a remote object accessible via RPC.
    
    Method calls and property access on an RpcStub are translated into
    RPC calls to the remote object.
    """
    
    def __init__(self, hook: StubHook):
        object.__setattr__(self, '_hook', hook)
        object.__setattr__(self, '_disposed', False)
    
    def __getattr__(self, name: str) -> RpcPromise:
        """Access a property or method on the remote object."""
        if self._disposed:
            raise RuntimeError("Cannot use disposed stub")
        
        # Return a promise for the property/method
        path = PropertyPath([name])
        return RpcPromise(self._hook, path)
    
    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent setting attributes on stubs."""
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            raise AttributeError("Cannot set attributes on RPC stubs")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type: Optional[Type[BaseException]], 
                 exc_val: Optional[BaseException], 
                 exc_tb: Optional[TracebackType]) -> None:
        """Auto-dispose when used as context manager."""
        self.dispose()
    
    def dispose(self) -> None:
        """Dispose of this stub, releasing remote resources."""
        if not self._disposed:
            self._hook.dispose()
            object.__setattr__(self, '_disposed', True)


def type_for_rpc(value: Any) -> str:
    """Determine the RPC type category for a value."""
    if value is None:
        return "primitive"
    
    if isinstance(value, (bool, int, float, str)):
        return "primitive"
    
    if isinstance(value, RpcTarget):
        return "rpc-target"
    
    if isinstance(value, (RpcStub, RpcPromise)):
        return "stub"
    
    if callable(value):
        return "function"
    
    if isinstance(value, list):
        return "array"
    
    if isinstance(value, dict):
        return "object"
    
    if isinstance(value, bytes):
        return "bytes"
    
    if hasattr(value, '__await__'):
        return "rpc-promise"
    
    if isinstance(value, Exception):
        return "error"
    
    return "unsupported"


def unwrap_stub_and_path(value: Any) -> tuple[Optional[StubHook], PropertyPath]:
    """
    Extract the stub hook and property path from a value.
    
    Returns (hook, path) where hook is None if the value is not a stub/promise.
    """
    if isinstance(value, RpcStub):
        return (value._hook, PropertyPath())
    elif isinstance(value, RpcPromise):
        return (value._hook, value._path)
    else:
        return (None, PropertyPath())