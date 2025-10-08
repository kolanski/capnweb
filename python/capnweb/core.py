"""
Core RPC classes and types for Cap'n Web Python.

This module contains the fundamental building blocks for RPC communication,
including RpcTarget, RpcStub, RpcPromise, and RpcPayload.
"""

import asyncio
import inspect
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


type PropertyPath = List[Union[str, int]]


class StubHook(ABC):
    """Abstract base for stub hooks that handle RPC operations."""

    @abstractmethod
    def call(self, path: PropertyPath, args: 'RpcPayload') -> 'StubHook':
        """Call a method on the remote object."""
        pass

    @abstractmethod
    def map(self, path: PropertyPath, captures: List['StubHook'], instructions: List[Any]) -> 'StubHook':
        """Apply a map operation."""
        pass

    @abstractmethod
    def get(self, path: PropertyPath) -> 'StubHook':
        """Get a property from the remote object."""
        pass

    @abstractmethod
    def dup(self) -> 'StubHook':
        """Create a clone of this StubHook."""
        pass

    @abstractmethod
    def pull(self) -> Union['RpcPayload', asyncio.Future]:
        """Request resolution of a StubHook that represents a promise."""
        pass

    @abstractmethod
    def ignore_unhandled_rejections(self) -> None:
        """Prevent unhandled rejection events."""
        pass

    @abstractmethod
    def dispose(self) -> None:
        """Dispose of this stub hook."""
        pass

    @abstractmethod
    def on_broken(self, callback: Callable[[Any], None]) -> None:
        """Register callback for when the connection breaks."""
        pass


class LocatedPromise:
    """Represents a promise located in a payload."""

    def __init__(self, promise: 'RpcPromise', parent: Any, property: Union[str, int]):
        self.promise = promise
        self.parent = parent
        self.property = property


class RpcPayload:
    """
    Represents the parameters to an RPC call, or the resolution of an RPC promise.

    This is a linear type - ownership is transferred when passed around.
    The payload owns all the stubs within it. Disposing the payload disposes
    the stubs.
    """

    def __init__(self, value: Any, source: str, stubs: Optional[List['RpcStub']] = None,
                 promises: Optional[List[LocatedPromise]] = None):
        self.value = value
        self.source = source  # "params", "return", or "owned"
        self.stubs = stubs or []
        self.promises = promises or []

    @classmethod
    def from_app_params(cls, value: Any) -> 'RpcPayload':
        """Create a payload from app parameters."""
        return cls(value, "params")

    @classmethod
    def from_app_return(cls, value: Any) -> 'RpcPayload':
        """Create a payload from app return value."""
        return cls(value, "return")

    @classmethod
    def from_array(cls, payloads: List['RpcPayload']) -> 'RpcPayload':
        """Combine multiple payloads into an array payload."""
        all_stubs = []
        all_promises = []
        result_array = []

        for payload in payloads:
            # Ensure deep copy
            payload.ensure_deep_copied()

            all_stubs.extend(payload.stubs)

            for promise in payload.promises:
                if promise.parent == payload:
                    # Reparent to the array
                    new_promise = LocatedPromise(
                        promise.promise,
                        result_array,
                        len(result_array)
                    )
                    all_promises.append(new_promise)
                else:
                    all_promises.append(promise)

            result_array.append(payload.value)

        return cls(result_array, "owned", all_stubs, all_promises)

    @classmethod
    def for_evaluation(cls, stubs: List['RpcStub'], promises: List[LocatedPromise]) -> 'RpcPayload':
        """Create payload for evaluation during deserialization."""
        return cls(None, "owned", stubs, promises)

    def ensure_deep_copied(self) -> None:
        """Ensure the payload value is deep copied if it came from app."""
        if self.source != "owned":
            dup_stubs = self.source == "params"
            self.stubs = []
            self.promises = []

            # Deep copy would go here - simplified for now
            # In a full implementation, this would recursively copy and handle stubs
            self.source = "owned"

    def deliver_call(self, func: Callable, this_arg: Any) -> asyncio.Future:
        """Deliver the payload to a function call."""
        async def _deliver():
            self.ensure_deep_copied()

            # Resolve promises in payload
            promises = []
            self._resolve_promises(promises)

            if promises:
                await asyncio.gather(*promises)

            # Call the function
            result = func(*self.value) if this_arg is None else func.call(this_arg, *self.value)

            if isinstance(result, RpcPromise):
                return RpcPayload.from_app_return(result)
            else:
                return RpcPayload.from_app_return(result)

        future = asyncio.create_task(_deliver())
        return future

    def _resolve_promises(self, promises: List[asyncio.Future]) -> None:
        """Resolve all promises in the payload."""
        # Simplified - full implementation would handle complex promise resolution
        for located_promise in self.promises:
            # This would resolve each promise and substitute it in the payload
            pass

    async def deliver_resolve(self) -> Any:
        """Deliver the payload as a resolved result."""
        try:
            self.ensure_deep_copied()

            promises = []
            self._resolve_promises(promises)

            if promises:
                await asyncio.gather(*promises)

            # Add dispose method to result if it's an object
            if isinstance(self.value, object) and not hasattr(self.value, 'dispose'):
                def dispose_func():
                    self.dispose()
                # Monkey patch dispose method
                self.value.dispose = dispose_func

            return self.value
        except Exception:
            self.dispose()
            raise

    def dispose(self) -> None:
        """Dispose of the payload and all contained stubs."""
        if self.source == "owned":
            for stub in self.stubs:
                if hasattr(stub, 'dispose'):
                    stub.dispose()
            for located_promise in self.promises:
                if hasattr(located_promise.promise, 'dispose'):
                    located_promise.promise.dispose()

        # Make disposal idempotent
        self.source = "owned"
        self.stubs = []
        self.promises = []

    def ignore_unhandled_rejections(self) -> None:
        """Ignore unhandled rejections in all promises."""
        if self.stubs:
            for stub in self.stubs:
                if hasattr(stub, '_hook') and hasattr(stub._hook, 'ignore_unhandled_rejections'):
                    stub._hook.ignore_unhandled_rejections()

        for located_promise in self.promises:
            if hasattr(located_promise.promise, '_hook') and hasattr(located_promise.promise._hook, 'ignore_unhandled_rejections'):
                located_promise.promise._hook.ignore_unhandled_rejections()

    @staticmethod
    def deep_copy_from(value: Any, old_parent: Any, owner: Optional['RpcPayload']) -> 'RpcPayload':
        """Create a deep copy of a value, handling stubs appropriately."""
        # Simplified implementation
        # Full implementation would recursively copy and handle stub lifecycle
        return RpcPayload(value, "owned")


def type_for_rpc(value: Any) -> str:
    """Determine the RPC type category for a value."""
    if value is None:
        return "primitive"

    if isinstance(value, (bool, int, float, str)):
        return "primitive"

    if isinstance(value, RpcTarget):
        return "rpc-target"

    # Check for specific types first
    from datetime import datetime
    if isinstance(value, datetime):
        return "date"

    # Check for RpcStub and RpcPromise if they're already defined
    # This avoids recursion by checking before monkey patching
    if 'RpcStub' in globals() and isinstance(value, RpcStub):
        return "stub"

    if 'RpcPromise' in globals() and isinstance(value, RpcPromise):
        return "rpc-promise"

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

    # Check for undefined-like behavior
    if value is ...:  # Ellipsis used as undefined placeholder
        return "undefined"

    return "unsupported"


def unwrap_stub_and_path(value: Any) -> tuple[Optional[StubHook], Optional[PropertyPath]]:
    """
    Extract the stub hook and property path from a value.

    Returns (hook, path) where hook is None if the value is not a stub/promise.
    """
    # Check for RpcStub and RpcPromise if they're already defined
    if 'RpcStub' in globals() and isinstance(value, RpcStub):
        return (value._hook, [])
    elif 'RpcPromise' in globals() and isinstance(value, RpcPromise):
        return (value._hook, value._path)
    elif hasattr(value, '_hook'):
        if hasattr(value, '_path'):
            return (value._hook, value._path)
        else:
            return (value._hook, [])
    else:
        return (None, None)


class RpcPromise:
    """
    Represents the result of an RPC call that may not have completed yet.

    Like JavaScript Promises, RpcPromise supports pipelining - you can call
    methods on the promise before it resolves, and those calls will be
    queued up.
    """

    def __init__(self, hook: StubHook, path: PropertyPath = None):
        self._hook = hook
        self._path = path or []
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

        if len(self._path) == 0:
            raise RuntimeError("Cannot resolve a promise without a property path")

        # Get the property from the remote object
        property_name = str(self._path[-1])
        # This would need to be implemented based on the hook's get method
        # For now, simplified
        result = await self._hook.pull()
        self._resolved_value = result
        self._is_resolved = True
        return self._resolved_value

    def __getattr__(self, name: str) -> 'RpcPromise':
        """Access a property of the promised value."""
        new_path = self._path + [name]
        return RpcPromise(self._hook, new_path)

    def __call__(self, *args, **kwargs) -> 'RpcPromise':
        """Call the promised value as a function."""
        if len(self._path) == 0:
            raise RuntimeError("Cannot call a promise without a method name")

        method_name = str(self._path[-1])

        # Create a new promise for the result
        # This would need proper implementation
        return RpcPromise(self._hook, self._path)

    def then(self, on_fulfilled=None, on_rejected=None):
        """JavaScript-like then method."""
        async def _then():
            try:
                result = await self
                if on_fulfilled:
                    return on_fulfilled(result)
                return result
            except Exception as e:
                if on_rejected:
                    return on_rejected(e)
                raise

        return asyncio.create_task(_then())

    def catch(self, on_rejected):
        """JavaScript-like catch method."""
        return self.then(None, on_rejected)

    def finally_(self, on_finally):
        """JavaScript-like finally method."""
        async def _finally():
            try:
                return await self
            finally:
                on_finally()

        return asyncio.create_task(_finally())


class RpcStub(RpcTarget):
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
        path = [name]
        return RpcPromise(self._hook, path)

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent setting attributes on stubs."""
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            raise AttributeError("Cannot set attributes on RPC stubs")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Auto-dispose when used as context manager."""
        self.dispose()

    def dispose(self) -> None:
        """Dispose of this stub, releasing remote resources."""
        if not self._disposed:
            self._hook.dispose()
            object.__setattr__(self, '_disposed', True)

    def dup(self) -> 'RpcStub':
        """Create a duplicate of this stub."""
        if self._disposed:
            raise RuntimeError("Cannot duplicate disposed stub")

        new_hook = self._hook.dup()
        return RpcStub(new_hook)

    def on_rpc_broken(self, callback: Callable[[Any], None]) -> None:
        """Register callback for when the RPC connection breaks."""
        self._hook.on_broken(callback)


# No need for monkey patching - the functions already check for the classes