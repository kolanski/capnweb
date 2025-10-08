"""
RPC session management for Cap'n Web Python.

This module implements the Cap'n Web RPC protocol with proper push/pull semantics,
expression evaluation, and capability management.
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable, Awaitable, Union
from .core import RpcTarget, RpcStub, RpcPromise, StubHook, PropertyPath, RpcPayload
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

    @abstractmethod
    def abort(self, reason: Any) -> None:
        """Abort the transport due to an error."""
        pass


class RpcSessionOptions:
    """Configuration options for RPC sessions."""

    def __init__(self,
                 debug: bool = False,
                 max_message_size: int = 1024 * 1024,  # 1MB
                 call_timeout: float = 30.0,
                 connect_timeout: float = 10.0,
                 on_send_error: Optional[Callable[[Exception], Optional[Exception]]] = None,
                 on_connect: Optional[Callable[[], Awaitable[None]]] = None,
                 on_disconnect: Optional[Callable[[Exception], Awaitable[None]]] = None,
                 auth_handler: Optional[Callable[[], Awaitable[Dict[str, str]]]] = None,
                 reconnect_enabled: bool = True):
        """
        Initialize RPC session options.

        Args:
            debug: Enable debug logging
            max_message_size: Maximum message size in bytes
            call_timeout: Timeout for individual RPC calls
            connect_timeout: Timeout for initial connection
            on_send_error: Callback for error serialization/redaction
            on_connect: Callback called when connection is established
            on_disconnect: Callback called when connection is lost
            auth_handler: Async function that returns authentication headers
            reconnect_enabled: Enable automatic reconnection
        """
        self.debug = debug
        self.max_message_size = max_message_size
        self.call_timeout = call_timeout
        self.connect_timeout = connect_timeout
        self.on_send_error = on_send_error
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.auth_handler = auth_handler
        self.reconnect_enabled = reconnect_enabled


# Entry on the exports table
class ExportTableEntry:
    def __init__(self, hook: StubHook, refcount: int = 1):
        self.hook = hook
        self.refcount = refcount
        self.pull: Optional[asyncio.Task] = None


# Entry on the imports table
class ImportTableEntry:
    def __init__(self, session: 'RpcSessionImpl', import_id: int, pulling: bool = False):
        self.session = session
        self.import_id = import_id
        self.local_refcount: int = 0
        self.remote_refcount: int = 1
        self.active_pull: Optional[asyncio.Future] = None
        self.resolution: Optional[StubHook] = None
        self.on_broken_registrations: Optional[List[int]] = None

        if pulling:
            self.active_pull = asyncio.Future()

    def resolve(self, resolution: StubHook) -> None:
        """Resolve this import with the given hook."""
        if self.local_refcount == 0:
            # Already disposed, ignore resolution
            resolution.dispose()
            return

        self.resolution = resolution
        self.send_release()

        # Move onBroken callbacks to the resolution
        if self.on_broken_registrations:
            for callback_index in self.on_broken_registrations:
                if callback_index < len(self.session.on_broken_callbacks):
                    callback = self.session.on_broken_callbacks[callback_index]
                    resolution.on_broken(callback)
            self.on_broken_registrations = None

        if self.active_pull and not self.active_pull.done():
            self.active_pull.set_result(None)
            self.active_pull = None

    async def await_resolution(self) -> RpcPayload:
        """Wait for resolution and return the payload."""
        if not self.active_pull:
            self.session.send_pull(self.import_id)
            self.active_pull = asyncio.Future()

        await self.active_pull
        if not self.resolution:
            raise RuntimeError("Import resolved without resolution hook")

        return await self.resolution.pull()

    def dispose(self) -> None:
        """Dispose of this import entry."""
        if self.resolution:
            self.resolution.dispose()
        else:
            # Create error hook for unresolved promise
            error_hook = ErrorStubHook(Exception("RPC promise was disposed before resolution"))
            self.resolution = error_hook
            self.send_release()

    def abort(self, error: Any) -> None:
        """Abort this import with an error."""
        if not self.resolution:
            self.resolution = ErrorStubHook(error)
            if self.active_pull and not self.active_pull.done():
                self.active_pull.set_exception(error)
                self.active_pull = None

    def on_broken(self, callback: Callable[[Any], None]) -> None:
        """Register callback for when the connection breaks."""
        if self.resolution:
            self.resolution.on_broken(callback)
        else:
            if not self.on_broken_registrations:
                self.on_broken_registrations = []
            callback_index = len(self.session.on_broken_callbacks)
            self.session.on_broken_callbacks.append(callback)
            self.on_broken_registrations.append(callback_index)

    def send_release(self) -> None:
        """Send release message if needed."""
        if self.remote_refcount > 0:
            self.session.send_release(self.import_id, self.remote_refcount)
            self.remote_refcount = 0


class RpcImportHook(StubHook):
    """Hook for imported capabilities."""

    def __init__(self, is_promise: bool, entry: ImportTableEntry):
        super().__init__()
        self.is_promise = is_promise
        self.entry = entry
        entry.local_refcount += 1

    def call(self, path: PropertyPath, args: RpcPayload) -> StubHook:
        """Make a call through this import."""
        entry = self.get_entry()
        if entry.resolution:
            return entry.resolution.call(path, args)
        else:
            return entry.session.send_call(entry.import_id, path, args)

    def map(self, path: PropertyPath, captures: List[StubHook], instructions: List[Any]) -> StubHook:
        """Apply a map operation through this import."""
        entry = self.get_entry()
        if entry.resolution:
            return entry.resolution.map(path, captures, instructions)
        else:
            return entry.session.send_map(entry.import_id, path, captures, instructions)

    def get(self, path: PropertyPath) -> StubHook:
        """Get a property through this import."""
        entry = self.get_entry()
        if entry.resolution:
            return entry.resolution.get(path)
        else:
            return entry.session.send_call(entry.import_id, path)

    def dup(self) -> 'RpcImportHook':
        """Duplicate this hook."""
        return RpcImportHook(False, self.get_entry())

    def pull(self) -> Union[RpcPayload, Awaitable[RpcPayload]]:
        """Pull the payload for this promise."""
        if not self.is_promise:
            raise RuntimeError("Cannot pull a non-promise hook")

        entry = self.get_entry()
        if entry.resolution:
            return entry.resolution.pull()

        return entry.await_resolution()

    def ignore_unhandled_rejections(self) -> None:
        """Ignore unhandled rejections for this hook."""
        # For imports, this is handled at the session level
        pass

    def dispose(self) -> None:
        """Dispose of this hook."""
        entry = self.entry
        self.entry = None
        if entry and entry.local_refcount > 0:
            entry.local_refcount -= 1
            if entry.local_refcount == 0:
                entry.dispose()

    def on_broken(self, callback: Callable[[Any], None]) -> None:
        """Register broken callback."""
        if self.entry:
            self.entry.on_broken(callback)

    def get_entry(self) -> ImportTableEntry:
        """Get the import table entry."""
        if not self.entry:
            raise RuntimeError("Import hook has been disposed")
        return self.entry


class RpcMainHook(RpcImportHook):
    """Hook for the main interface."""

    def __init__(self, entry: ImportTableEntry):
        super().__init__(False, entry)
        self.session = entry.session

    def dispose(self) -> None:
        """Dispose and shut down the session."""
        if self.session:
            session = self.session
            self.session = None
            session.shutdown()


class ErrorStubHook(StubHook):
    """Hook that always returns an error."""

    def __init__(self, error: Any):
        self.error = error

    def call(self, path: PropertyPath, args: RpcPayload) -> StubHook:
        return self

    def map(self, path: PropertyPath, captures: List[StubHook], instructions: List[Any]) -> StubHook:
        return self

    def get(self, path: PropertyPath) -> StubHook:
        return self

    def dup(self) -> 'ErrorStubHook':
        return ErrorStubHook(self.error)

    def pull(self) -> Union[RpcPayload, Awaitable[RpcPayload]]:
        return asyncio.create_future().exception(self.error)

    def ignore_unhandled_rejections(self) -> None:
        pass

    def dispose(self) -> None:
        pass

    def on_broken(self, callback: Callable[[Any], None]) -> None:
        try:
            callback(self.error)
        except Exception:
            # Treat as unhandled exception
            asyncio.create_future().set_exception(self.error)


class RpcSessionImpl(Importer, Exporter):
    """Internal implementation of RPC session."""

    def __init__(self, transport: RpcTransport, local_main: Any = None,
                 options: RpcSessionOptions = None):
        self.transport = transport
        self.options = options or RpcSessionOptions()

        # Export/import tables
        self.exports: Dict[int, ExportTableEntry] = {}
        self.reverse_exports: Dict[StubHook, int] = {}
        self.imports: Dict[int, ImportTableEntry] = {}

        # ID management
        self.next_export_id = -1  # Negative IDs for exports
        self.next_import_id = 1   # Positive IDs for imports (client-side)

        # Session state
        self.abort_reason: Optional[Any] = None
        self.on_broken_callbacks: List[Callable[[Any], None]] = []
        self.read_task: Optional[asyncio.Task] = None

        # Promise resolution tracking
        self.pull_count = 0
        self.on_batch_done: Optional[asyncio.Future] = None

        # Export main object at ID 0
        if local_main is not None:
            main_hook = LocalStubHook(local_main)
        else:
            main_hook = ErrorStubHook(Exception("No main object available"))

        self.exports[0] = ExportTableEntry(main_hook, refcount=1)
        self.reverse_exports[main_hook] = 0

        # Import remote main at ID 0
        self.imports[0] = ImportTableEntry(self, 0, pulling=False)

        # Start message reading loop
        self.read_task = asyncio.create_task(self._read_loop())

    def get_main_import(self) -> RpcMainHook:
        """Get a hook for the remote main interface."""
        return RpcMainHook(self.imports[0])

    def shutdown(self) -> None:
        """Shut down the RPC session."""
        self.abort(Exception("RPC session was shut down"), try_send_abort_message=False)

    # Exporter interface
    def export_stub(self, hook: StubHook) -> int:
        """Export a stub and return its export ID."""
        if self.abort_reason:
            raise self.abort_reason

        existing_id = self.reverse_exports.get(hook)
        if existing_id is not None:
            self.exports[existing_id].refcount += 1
            return existing_id

        export_id = self.next_export_id
        self.next_export_id -= 1

        self.exports[export_id] = ExportTableEntry(hook, refcount=1)
        self.reverse_exports[hook] = export_id
        return export_id

    def export_promise(self, hook: StubHook) -> int:
        """Export a promise and return its export ID."""
        if self.abort_reason:
            raise self.abort_reason

        # Promises always get a new ID
        export_id = self.next_export_id
        self.next_export_id -= 1

        self.exports[export_id] = ExportTableEntry(hook, refcount=1)
        self.reverse_exports[hook] = export_id

        # Start resolving the promise
        self._ensure_resolving_export(export_id)
        return export_id

    def unexport(self, ids: List[int]) -> None:
        """Unexport the given export IDs."""
        for export_id in ids:
            self._release_export(export_id, 1)

    def on_send_error(self, error: Exception) -> Optional[Exception]:
        """Handle error serialization."""
        if self.options.on_send_error:
            return self.options.on_send_error(error)
        return None

    # Importer interface
    def import_stub(self, import_id: int) -> StubHook:
        """Import a stub by its import ID."""
        if self.abort_reason:
            raise self.abort_reason

        if import_id not in self.imports:
            self.imports[import_id] = ImportTableEntry(self, import_id, pulling=False)

        return RpcImportHook(False, self.imports[import_id])

    def import_promise(self, import_id: int) -> StubHook:
        """Import a promise by its import ID."""
        if self.abort_reason:
            raise self.abort_reason

        if import_id in self.imports:
            # Can't reuse existing ID for a promise
            return ErrorStubHook(Exception("Peer reused promise import ID"))

        entry = ImportTableEntry(self, import_id, pulling=True)
        self.imports[import_id] = entry
        return RpcImportHook(True, entry)

    def get_export(self, export_id: int) -> Optional[StubHook]:
        """Get an export by its ID."""
        if export_id in self.exports:
            return self.exports[export_id].hook
        return None

    def get_import(self, hook: StubHook) -> Optional[int]:
        """Get import ID for a hook."""
        if isinstance(hook, RpcImportHook) and hook.entry and hook.entry.session is self:
            return hook.entry.import_id
        return None

    # Message sending methods
    def send(self, message: Any) -> None:
        """Send a message to the remote peer."""
        if self.abort_reason is not None:
            return

        try:
            message_text = json.dumps(message)
            asyncio.create_task(self._send_safe(message_text))
        except Exception as e:
            self.abort(e, try_send_abort_message=True)

    async def _send_safe(self, message_text: str) -> None:
        """Send a message safely with error handling."""
        try:
            await self.transport.send(message_text)
        except Exception as e:
            self.abort(e, try_send_abort_message=False)

    def send_call(self, import_id: int, path: PropertyPath, args: Optional[RpcPayload] = None) -> RpcImportHook:
        """Send a call expression."""
        if self.abort_reason:
            raise self.abort_reason

        # Build the call expression
        expression = ["pipeline", import_id, path]
        if args:
            devalued_args = Devaluator.devaluate(args.value, None, self, args)
            # Handle array wrapping for arguments
            if isinstance(devalued_args, list) and len(devalued_args) == 1 and isinstance(devalued_args[0], list):
                expression.append(devalued_args[0])
            else:
                expression.append(devalued_args if isinstance(devalued_args, list) else [devalued_args])

        self.send(["push", expression])

        # Create import entry for the result
        import_entry = ImportTableEntry(self, self.next_import_id, pulling=False)
        import_id = self.next_import_id
        self.next_import_id += 1
        self.imports[import_id] = import_entry

        return RpcImportHook(True, import_entry)

    def send_map(self, import_id: int, path: PropertyPath, captures: List[StubHook], instructions: List[Any]) -> RpcImportHook:
        """Send a map operation."""
        if self.abort_reason:
            # Dispose captures on error
            for capture in captures:
                capture.dispose()
            raise self.abort_reason

        # Convert captures to devalued form
        devalued_captures = []
        for capture in captures:
            capture_import_id = self.get_import(capture)
            if capture_import_id is not None:
                devalued_captures.append(["import", capture_import_id])
            else:
                export_id = self.export_stub(capture)
                devalued_captures.append(["export", export_id])

        expression = ["remap", import_id, path, devalued_captures, instructions]
        self.send(["push", expression])

        # Create import entry for the result
        import_entry = ImportTableEntry(self, self.next_import_id, pulling=False)
        import_id = self.next_import_id
        self.next_import_id += 1
        self.imports[import_id] = import_entry

        return RpcImportHook(True, import_entry)

    def send_pull(self, import_id: int) -> None:
        """Send a pull message."""
        if self.abort_reason:
            return
        self.send(["pull", import_id])

    def send_release(self, import_id: int, refcount: int) -> None:
        """Send a release message."""
        if self.abort_reason:
            return
        self.send(["release", import_id, refcount])
        if import_id in self.imports:
            del self.imports[import_id]

    def abort(self, error: Any, try_send_abort_message: bool = True) -> None:
        """Abort the session due to an error."""
        if self.abort_reason is not None:
            return

        self.abort_reason = error

        # Cancel read task
        if self.read_task and not self.read_task.done():
            self.read_task.cancel()

        # Try to send abort message
        if try_send_abort_message:
            try:
                abort_message = ["abort", Devaluator.devaluate(error, None, self)]
                message_text = json.dumps(abort_message)
                asyncio.create_task(self.transport.send(message_text))
            except Exception:
                pass  # Ignore errors during abort

        # Call transport's abort method
        try:
            self.transport.abort(error)
        except Exception:
            pass

        # Notify all broken callbacks
        for callback in self.on_broken_callbacks:
            try:
                callback(error)
            except Exception:
                pass

        # Abort all imports
        for import_entry in self.imports.values():
            import_entry.abort(error)

        # Dispose all exports
        for export_entry in self.exports.values():
            export_entry.hook.dispose()

    async def _read_loop(self) -> None:
        """Main message reading loop."""
        while not self.abort_reason:
            try:
                message_text = await self.transport.receive()
                message = json.loads(message_text)
                await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.abort_reason:
                    self.abort(e)
                break

    async def _handle_message(self, message: Any) -> None:
        """Handle an incoming message."""
        if not isinstance(message, list) or len(message) == 0:
            self.abort(Exception(f"Invalid RPC message: {message}"))
            return

        message_type = message[0]

        if message_type == "push":
            await self._handle_push(message)
        elif message_type == "pull":
            await self._handle_pull(message)
        elif message_type == "resolve":
            await self._handle_resolve(message)
        elif message_type == "reject":
            await self._handle_reject(message)
        elif message_type == "release":
            await self._handle_release(message)
        elif message_type == "abort":
            await self._handle_abort(message)
        else:
            self.abort(Exception(f"Unknown message type: {message_type}"))

    async def _handle_push(self, message: List[Any]) -> None:
        """Handle a push message."""
        if len(message) < 2:
            self.abort(Exception("Push message too short"))
            return

        expression = message[1]
        payload = Evaluator.evaluate(expression, self)
        hook = PayloadStubHook(payload)
        hook.ignore_unhandled_rejections()

        # Create new export for the pushed value
        export_id = self.next_export_id
        self.next_export_id -= 1
        self.exports[export_id] = ExportTableEntry(hook, refcount=1)
        self.reverse_exports[hook] = export_id

    async def _handle_pull(self, message: List[Any]) -> None:
        """Handle a pull message."""
        if len(message) < 2 or not isinstance(message[1], int):
            self.abort(Exception("Invalid pull message"))
            return

        export_id = message[1]
        self._ensure_resolving_export(export_id)

    async def _handle_resolve(self, message: List[Any]) -> None:
        """Handle a resolve message."""
        if len(message) < 3 or not isinstance(message[1], int):
            self.abort(Exception("Invalid resolve message"))
            return

        import_id = message[1]
        expression = message[2]

        if import_id in self.imports:
            payload = Evaluator.evaluate(expression, self)
            hook = PayloadStubHook(payload)
            self.imports[import_id].resolve(hook)
        else:
            # Import not found, dispose the payload
            payload = Evaluator.evaluate(expression, self)
            payload.dispose()

    async def _handle_reject(self, message: List[Any]) -> None:
        """Handle a reject message."""
        if len(message) < 3 or not isinstance(message[1], int):
            self.abort(Exception("Invalid reject message"))
            return

        import_id = message[1]
        expression = message[2]

        if import_id in self.imports:
            payload = Evaluator.evaluate(expression, self)
            error_value = payload.value
            payload.dispose()
            error_hook = ErrorStubHook(error_value)
            self.imports[import_id].resolve(error_hook)
        else:
            # Import not found, still need to evaluate to clean up
            payload = Evaluator.evaluate(expression, self)
            payload.dispose()

    async def _handle_release(self, message: List[Any]) -> None:
        """Handle a release message."""
        if len(message) < 3 or not isinstance(message[1], int) or not isinstance(message[2], int):
            self.abort(Exception("Invalid release message"))
            return

        export_id = message[1]
        refcount = message[2]
        self._release_export(export_id, refcount)

    async def _handle_abort(self, message: List[Any]) -> None:
        """Handle an abort message."""
        if len(message) < 2:
            self.abort(Exception("Invalid abort message"))
            return

        expression = message[1]
        payload = Evaluator.evaluate(expression, self)
        error = payload.value
        payload.dispose()
        self.abort(error, try_send_abort_message=False)

    def _ensure_resolving_export(self, export_id: int) -> None:
        """Ensure that an export is being resolved."""
        if export_id not in self.exports:
            self.abort(Exception(f"Export ID {export_id} not found"))
            return

        export_entry = self.exports[export_id]
        if export_entry.pull is None:
            export_entry.pull = asyncio.create_task(self._resolve_export(export_id))

    async def _resolve_export(self, export_id: int) -> None:
        """Resolve an export by pulling its payload."""
        if export_id not in self.exports:
            return

        export_entry = self.exports[export_id]
        hook = export_entry.hook

        try:
            # Pull the payload from the hook
            payload = await hook.pull()
            if isinstance(payload, RpcPayload):
                # Send resolve message
                devalued = Devaluator.devaluate(payload.value, None, self, payload)
                self.send(["resolve", export_id, devalued])
            else:
                # Hook returned a promise, await it
                payload = await payload
                devalued = Devaluator.devaluate(payload.value, None, self, payload)
                self.send(["resolve", export_id, devalued])
        except Exception as error:
            try:
                # Send reject message
                devalued_error = Devaluator.devaluate(error, None, self)
                self.send(["reject", export_id, devalued_error])
            except Exception:
                # If serialization fails, abort
                self.abort(error)
        finally:
            self.pull_count -= 1
            if self.pull_count == 0 and self.on_batch_done:
                self.on_batch_done.set_result(None)
                self.on_batch_done = None

    def _release_export(self, export_id: int, refcount: int) -> None:
        """Release an export, decrementing its refcount."""
        if export_id not in self.exports:
            raise Exception(f"Export ID {export_id} not found")

        export_entry = self.exports[export_id]
        if export_entry.refcount < refcount:
            raise Exception(f"Refcount would go negative for export {export_id}")

        export_entry.refcount -= refcount
        if export_entry.refcount == 0:
            del self.exports[export_id]
            if export_entry.hook in self.reverse_exports:
                del self.reverse_exports[export_entry.hook]
            export_entry.hook.dispose()

    async def drain(self) -> None:
        """Wait for all pending operations to complete."""
        if self.abort_reason:
            raise self.abort_reason

        if self.pull_count > 0:
            self.on_batch_done = asyncio.Future()
            await self.on_batch_done

    def get_stats(self) -> Dict[str, int]:
        """Get session statistics."""
        return {
            "imports": len(self.imports),
            "exports": len(self.exports)
        }

    async def close(self) -> None:
        """Close the session."""
        if self.abort_reason:
            return

        self.abort(Exception("Session closed"))

        if self.read_task and not self.read_task.done():
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass


class LocalStubHook(StubHook):
    """Hook for local objects."""

    def __init__(self, target: Any):
        self.target = target
        self.disposed = False

    def call(self, path: PropertyPath, args: RpcPayload) -> StubHook:
        """Call a method on the local target."""
        if self.disposed:
            raise RuntimeError("Cannot call disposed local object")

        # Navigate to the target method
        current = self.target
        for part in path:
            if hasattr(current, part):
                current = getattr(current, part)
            else:
                raise AttributeError(f"'{type(current).__name__}' object has no attribute '{part}'")

        if not callable(current):
            raise TypeError(f"'{path[-1]}' is not callable")

        # Call the method
        try:
            result = current(*args.value)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            return PayloadStubHook(RpcPayload.from_app_return(result))
        except Exception as e:
            return ErrorStubHook(e)

    def map(self, path: PropertyPath, captures: List[StubHook], instructions: List[Any]) -> StubHook:
        """Map operation not supported on local hooks."""
        # For now, dispose captures and return error
        for capture in captures:
            capture.dispose()
        return ErrorStubHook(NotImplementedError("Map operations not supported on local hooks"))

    def get(self, path: PropertyPath) -> StubHook:
        """Get a property from the local target."""
        if self.disposed:
            raise RuntimeError("Cannot access property of disposed object")

        current = self.target
        for part in path:
            if hasattr(current, part):
                current = getattr(current, part)
            else:
                return ErrorStubHook(AttributeError(f"'{type(current).__name__}' object has no attribute '{part}'"))

        return PayloadStubHook(RpcPayload.from_app_return(current))

    def dup(self) -> 'LocalStubHook':
        """Duplicate this hook."""
        return LocalStubHook(self.target)

    def pull(self) -> Union[RpcPayload, Awaitable[RpcPayload]]:
        """Pull the payload."""
        return RpcPayload.from_app_return(self.target)

    def ignore_unhandled_rejections(self) -> None:
        """Nothing to ignore for local objects."""
        pass

    def dispose(self) -> None:
        """Dispose of this hook."""
        if not self.disposed:
            self.disposed = True
            if hasattr(self.target, '__exit__'):
                try:
                    self.target.__exit__(None, None, None)
                except Exception:
                    pass

    def on_broken(self, callback: Callable[[Any], None]) -> None:
        """Local objects don't break."""
        pass


class PayloadStubHook(StubHook):
    """Hook wrapping an RpcPayload."""

    def __init__(self, payload: RpcPayload):
        self.payload = payload

    def call(self, path: PropertyPath, args: RpcPayload) -> StubHook:
        """Call through the payload."""
        # This would require navigating through the payload structure
        # For now, not implemented
        return ErrorStubHook(NotImplementedError("Payload call not implemented"))

    def map(self, path: PropertyPath, captures: List[StubHook], instructions: List[Any]) -> StubHook:
        """Map operation on payload."""
        # Dispose captures on error
        for capture in captures:
            capture.dispose()
        return ErrorStubHook(NotImplementedError("Payload map not implemented"))

    def get(self, path: PropertyPath) -> StubHook:
        """Get a property from the payload."""
        # Navigate through payload to get property
        # For now, not implemented
        return ErrorStubHook(NotImplementedError("Payload get not implemented"))

    def dup(self) -> 'PayloadStubHook':
        """Duplicate this hook."""
        return PayloadStubHook(RpcPayload.deep_copy_from(self.payload.value, None, self.payload))

    def pull(self) -> Union[RpcPayload, Awaitable[RpcPayload]]:
        """Pull the payload."""
        return self.payload

    def ignore_unhandled_rejections(self) -> None:
        """Ignore unhandled rejections in the payload."""
        self.payload.ignore_unhandled_rejections()

    def dispose(self) -> None:
        """Dispose of this hook."""
        self.payload.dispose()

    def on_broken(self, callback: Callable[[Any], None]) -> None:
        """Register broken callback."""
        # Payloads don't break on their own
        pass


class RpcSession:
    """
    Public RPC session interface.

    This wraps RpcSessionImpl and provides a clean public API.
    """

    def __init__(self, transport: RpcTransport, local_main: Any = None,
                 options: RpcSessionOptions = None):
        self._impl = RpcSessionImpl(transport, local_main, options)
        self._main_stub = None

    def get_remote_main(self) -> RpcStub:
        """Get a stub for the remote main object."""
        if self._main_stub is None:
            hook = self._impl.get_main_import()
            self._main_stub = RpcStub(hook)
        return self._main_stub

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