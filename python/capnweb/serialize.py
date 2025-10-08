"""
Serialization and deserialization for Cap'n Web Python.

This module handles converting Python objects to/from JSON-serializable
representations for transmission over RPC, following the Cap'n Web protocol.
"""

import base64
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, Protocol
from .core import RpcTarget, RpcStub, RpcPromise, type_for_rpc, unwrap_stub_and_path


class Exporter(Protocol):
    """Protocol for exporting capabilities."""

    def export_stub(self, hook) -> int:
        """Export a stub and return its export ID."""
        ...

    def export_promise(self, hook) -> int:
        """Export a promise and return its export ID."""

    def get_import(self, hook) -> Optional[int]:
        """Get import ID for a hook."""
        ...

    def unexport(self, ids: List[int]) -> None:
        """Unexport the given IDs."""
        ...

    def on_send_error(self, error: Exception) -> Optional[Exception]:
        """Handle error serialization."""
        ...


class Importer(Protocol):
    """Protocol for importing capabilities."""

    def import_stub(self, import_id: int):
        """Import a stub by its import ID."""
        ...

    def import_promise(self, import_id: int):
        """Import a promise by its import ID."""
        ...

    def get_export(self, export_id: int):
        """Get an export by its ID."""
        ...


class NullExporter:
    """Null exporter that raises errors when trying to export."""

    def export_stub(self, hook) -> int:
        raise RuntimeError("Cannot export stubs without an exporter")

    def export_promise(self, hook) -> int:
        raise RuntimeError("Cannot export promises without an exporter")

    def get_import(self, hook) -> Optional[int]:
        return None

    def unexport(self, ids: List[int]) -> None:
        pass

    def on_send_error(self, error: Exception) -> Optional[Exception]:
        return None


class NullImporter:
    """Null importer that raises errors when trying to import."""

    def import_stub(self, import_id: int):
        raise RuntimeError("Cannot import stubs without an importer")

    def import_promise(self, import_id: int):
        raise RuntimeError("Cannot import promises without an importer")

    def get_export(self, export_id: int):
        return None


NULL_EXPORTER = NullExporter()
NULL_IMPORTER = NullImporter()

# Error types for deserialization
ERROR_TYPES = {
    'Error': Exception,
    'ValueError': ValueError,
    'TypeError': TypeError,
    'KeyError': KeyError,
    'IndexError': IndexError,
    'AttributeError': AttributeError,
    'ImportError': ImportError,
    'ModuleNotFoundError': ModuleNotFoundError,
    'FileNotFoundError': FileNotFoundError,
    'PermissionError': PermissionError,
    'IsADirectoryError': IsADirectoryError,
    'NotADirectoryError': NotADirectoryError,
    'EOFError': EOFError,
    'RuntimeError': RuntimeError,
    'RecursionError': RecursionError,
    'NotImplementedError': NotImplementedError,
    'SyntaxError': SyntaxError,
    'IndentationError': IndentationError,
    'TabError': TabError,
    'ReferenceError': ReferenceError,
    'LookupError': LookupError,
    'AssertionError': AssertionError,
    'ArithmeticError': ArithmeticError,
    'FloatingPointError': FloatingPointError,
    'OverflowError': OverflowError,
    'ZeroDivisionError': ZeroDivisionError,
    'SystemError': SystemError,
    'OSError': OSError,
    'BlockingIOError': BlockingIOError,
    'ChildProcessError': ChildProcessError,
    'ConnectionError': ConnectionError,
    'BrokenPipeError': BrokenPipeError,
    'ConnectionAbortedError': ConnectionAbortedError,
    'ConnectionRefusedError': ConnectionRefusedError,
    'ConnectionResetError': ConnectionResetError,
    'TimeoutError': TimeoutError,
    'StopAsyncIteration': StopAsyncIteration,
    'StopIteration': StopIteration,
    'GeneratorExit': GeneratorExit,
    'Warning': Warning,
    'UserWarning': UserWarning,
    'DeprecationWarning': DeprecationWarning,
    'PendingDeprecationWarning': PendingDeprecationWarning,
    'SyntaxWarning': SyntaxWarning,
    'RuntimeWarning': RuntimeWarning,
    'FutureWarning': FutureWarning,
    'ImportWarning': ImportWarning,
    'UnicodeWarning': UnicodeWarning,
    'BytesWarning': BytesWarning,
    'ResourceWarning': ResourceWarning,
}


class Devaluator:
    """
    Converts fully-hydrated Python objects into JSON-serializable representations.

    This follows the Cap'n Web protocol specification for serialization.
    """

    def __init__(self, exporter: Exporter = NULL_EXPORTER, source=None):
        self.exporter = exporter
        self.source = source
        self.exports: List[int] = []

    @classmethod
    def devaluate(cls, value: Any, parent=None, exporter: Exporter = NULL_EXPORTER, source=None) -> Any:
        """
        Devaluate a value for transmission.

        Returns the serialized value.
        """
        devaluator = cls(exporter, source)
        try:
            return devaluator._devaluate_impl(value, parent, 0)
        except Exception:
            if devaluator.exports:
                try:
                    exporter.unexport(devaluator.exports)
                except Exception:
                    pass  # Ignore cleanup errors
            raise

    def _devaluate_impl(self, value: Any, parent: Any, depth: int) -> Any:
        """Internal implementation of devaluation."""
        if depth >= 64:
            raise RuntimeError("Serialization exceeded maximum allowed depth")

        rpc_type = type_for_rpc(value)

        if rpc_type == "unsupported":
            raise TypeError(f"Cannot serialize value of type {type(value)}")

        elif rpc_type == "primitive":
            return value

        elif rpc_type == "undefined":
            return ["undefined"]

        elif rpc_type == "object":
            if isinstance(value, dict):
                result = {}
                for key, val in value.items():
                    result[key] = self._devaluate_impl(val, value, depth + 1)
                return result
            else:
                # Convert object to dict
                result = {}
                for attr in dir(value):
                    if not attr.startswith('_') and not callable(getattr(value, attr)):
                        try:
                            attr_value = getattr(value, attr)
                            result[attr] = self._devaluate_impl(attr_value, value, depth + 1)
                        except Exception:
                            pass  # Skip inaccessible attributes
                return result

        elif rpc_type == "array":
            array_result = []
            for item in value:
                array_result.append(self._devaluate_impl(item, value, depth + 1))
            # Escape arrays by wrapping in another array
            return [array_result]

        elif rpc_type == "bigint":
            # Python doesn't have built-in bigint, but we can handle large ints
            return ["bigint", str(value)]

        elif rpc_type == "date":
            return ["date", int(value.timestamp() * 1000)]  # milliseconds

        elif rpc_type == "bytes":
            # Use base64 without padding
            b64 = base64.b64encode(value).decode('ascii').rstrip('=')
            return ["bytes", b64]

        elif rpc_type == "error":
            error = value
            # Process error through callback if provided
            processed_error = self.exporter.on_send_error(error) if hasattr(self.exporter, 'on_send_error') else error
            if processed_error:
                error = processed_error

            result = ["error", type(error).__name__, str(error)]
            # Add stack trace if available
            if hasattr(error, '__traceback__') and error.__traceback__:
                import traceback
                result.append(''.join(traceback.format_exception(type(error), error, error.__traceback__)))
            return result

        elif rpc_type in ("stub", "rpc-promise"):
            if not self.source:
                raise RuntimeError("Cannot serialize RPC stubs without session context")

            hook, path_if_promise = unwrap_stub_and_path(value)
            if hook is None:
                raise RuntimeError(f"Failed to unwrap {rpc_type}")

            import_id = self.exporter.get_import(hook)
            if import_id is not None:
                if path_if_promise:
                    # Pipelining back to peer
                    if len(path_if_promise) > 0:
                        return ["pipeline", import_id, path_if_promise]
                    else:
                        return ["pipeline", import_id]
                else:
                    return ["import", import_id]

            # Need to export a new capability
            if path_if_promise:
                # Get the property as a promise
                hook = hook.get(path_if_promise)
                return self._devaluate_hook("promise", hook)
            else:
                hook = hook.dup()
                return self._devaluate_hook("export", hook)

        elif rpc_type in ("function", "rpc-target"):
            if not self.source:
                raise RuntimeError("Cannot serialize functions without session context")

            # For now, we don't support serializing functions directly
            raise RuntimeError("Cannot serialize functions without proper session context")

        else:
            raise RuntimeError(f"Unsupported type for RPC: {rpc_type}")

    def _devaluate_hook(self, hook_type: str, hook) -> List[Any]:
        """Devaluate a stub or promise hook."""
        if hook_type == "promise":
            export_id = self.exporter.export_promise(hook)
        else:
            export_id = self.exporter.export_stub(hook)

        self.exports.append(export_id)
        return [hook_type, export_id]


class Evaluator:
    """
    Converts JSON-serializable representations back into Python objects.

    This follows the Cap'n Web protocol specification for deserialization.
    """

    def __init__(self, importer: Importer = NULL_IMPORTER):
        self.importer = importer
        self.stubs: List[RpcStub] = []
        self.promises: List[Any] = []  # LocatedPromise equivalents

    def evaluate(self, value: Any) -> 'RpcPayload':
        """Evaluate a serialized value into an RpcPayload."""
        from .core import RpcPayload
        payload = RpcPayload.for_evaluation(self.stubs, self.promises)
        try:
            payload.value = self._evaluate_impl(value, payload, "value")
            return payload
        except Exception:
            payload.dispose()
            raise

    def _evaluate_impl(self, value: Any, parent: Any, property: Union[str, int]) -> Any:
        """Internal implementation of evaluation."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        elif isinstance(value, list):
            # Check for special encoded types
            if len(value) >= 1 and isinstance(value[0], str):
                type_tag = value[0]

                if type_tag == "bigint" and len(value) >= 2:
                    if isinstance(value[1], str):
                        return int(value[1])

                elif type_tag == "date" and len(value) >= 2:
                    if isinstance(value[1], (int, float)):
                        return datetime.fromtimestamp(value[1] / 1000)

                elif type_tag == "bytes" and len(value) >= 2:
                    if isinstance(value[1], str):
                        # Add back padding if needed
                        b64 = value[1]
                        padding_needed = (4 - len(b64) % 4) % 4
                        b64 += '=' * padding_needed
                        return base64.b64decode(b64)

                elif type_tag == "error" and len(value) >= 3:
                    error_name = value[1]
                    message = value[2]
                    stack = value[3] if len(value) >= 4 else None

                    error_class = ERROR_TYPES.get(error_name, Exception)
                    error = error_class(message)

                    if stack and hasattr(error, '__traceback__'):
                        # For Python, setting the stack trace is complex
                        # We'll just store it as an attribute for now
                        error.stack_trace = stack

                    return error

                elif type_tag == "undefined" and len(value) == 1:
                    # Python doesn't have undefined, return None
                    return None

                elif type_tag in ("import", "pipeline"):
                    if len(value) < 2 or not isinstance(value[1], int):
                        raise ValueError(f"Invalid {type_tag} expression")

                    # From sender's perspective it's an import, from ours it's an export
                    export_id = value[1]
                    hook = self.importer.get_export(export_id)
                    if not hook:
                        raise ValueError(f"No such export: {export_id}")

                    is_promise = type_tag == "pipeline"

                    def add_stub(hook):
                        if is_promise:
                            from .core import RpcPromise
                            promise = RpcPromise(hook, [])
                            self.promises.append({"promise": promise, "parent": parent, "property": property})
                            return promise
                        else:
                            from .core import RpcStub
                            stub = RpcStub(hook)
                            self.stubs.append(stub)
                            return stub

                    if len(value) == 2:
                        # Just referencing the export itself
                        if is_promise:
                            return add_stub(hook.get([]))  # Get as promise
                        else:
                            return add_stub(hook.dup())

                    # Has property path
                    if len(value) >= 3:
                        path = value[2]
                        if not isinstance(path, list):
                            raise ValueError("Property path must be an array")

                        if len(value) == 3:
                            # Just property access
                            return add_stub(hook.get(path))

                        # Has call arguments
                        if len(value) >= 4:
                            args = value[3]
                            if not isinstance(args, list):
                                raise ValueError("Call arguments must be an array")

                            # Evaluate arguments in a separate context
                            sub_evaluator = Evaluator(self.importer)
                            args_payload = sub_evaluator.evaluate([args])
                            # args_payload will be disposed after use

                            return add_stub(hook.call(path, args_payload))

                elif type_tag == "remap":
                    if len(value) != 5:
                        raise ValueError("Invalid remap expression")

                    export_id = value[1]
                    path = value[2]
                    captures = value[3]
                    instructions = value[4]

                    hook = self.importer.get_export(export_id)
                    if not hook:
                        raise ValueError(f"No such export: {export_id}")

                    if not isinstance(path, list):
                        raise ValueError("Property path must be an array")

                    # Process captures
                    capture_hooks = []
                    for capture in captures:
                        if not isinstance(capture, list) or len(capture) != 2:
                            raise ValueError("Invalid capture format")

                        capture_type = capture[0]
                        capture_id = capture[1]

                        if capture_type == "export":
                            capture_hook = self.importer.import_stub(capture_id)
                        elif capture_type == "import":
                            capture_hook = self.importer.get_export(capture_id)
                            if capture_hook:
                                capture_hook = capture_hook.dup()
                            else:
                                raise ValueError(f"No such export for capture: {capture_id}")
                        else:
                            raise ValueError(f"Unknown capture type: {capture_type}")

                        capture_hooks.append(capture_hook)

                    # For now, return a promise for the mapped result
                    # Full map implementation would be more complex
                    from .core import RpcPromise
                    result_hook = hook.map(path, capture_hooks, instructions)
                    promise = RpcPromise(result_hook, [])
                    self.promises.append({"promise": promise, "parent": parent, "property": property})
                    return promise

                elif type_tag in ("export", "promise"):
                    if len(value) < 2 or not isinstance(value[1], int):
                        raise ValueError(f"Invalid {type_tag} expression")

                    import_id = value[1]
                    if type_tag == "promise":
                        hook = self.importer.import_promise(import_id)
                        from .core import RpcPromise
                        promise = RpcPromise(hook, [])
                        self.promises.append({"promise": promise, "parent": parent, "property": property})
                        return promise
                    else:
                        hook = self.importer.import_stub(import_id)
                        from .core import RpcStub
                        stub = RpcStub(hook)
                        self.stubs.append(stub)
                        return stub

            # Regular array - check for escaped array
            if len(value) == 1 and isinstance(value[0], list):
                # Escaped array [[...]] -> [...]
                return [self._evaluate_impl(item, value, i) for i, item in enumerate(value[0])]

            # Regular array
            return [self._evaluate_impl(item, value, i) for i, item in enumerate(value)]

        elif isinstance(value, dict):
            result = {}
            for key, val in value.items():
                # Skip Object.prototype properties for security
                if key in ('__proto__', 'constructor', 'prototype'):
                    self._evaluate_impl(val, result, key)  # Still evaluate to clean up
                    continue
                result[key] = self._evaluate_impl(val, result, key)
            return result

        else:
            # Unknown type, return as-is
            return value


def serialize(value: Any) -> str:
    """
    Serialize a value to a JSON string.

    This is a simplified version that doesn't handle stubs/promises.
    For full RPC serialization, use Devaluator within an RPC session.
    """
    serialized = Devaluator.devaluate(value)
    return json.dumps(serialized)


def deserialize(data: str) -> Any:
    """
    Deserialize a JSON string back to a Python value.

    This is a simplified version that doesn't handle stubs/promises.
    For full RPC deserialization, use Evaluator within an RPC session.
    """
    parsed = json.loads(data)
    evaluator = Evaluator(NULL_IMPORTER)
    payload = evaluator.evaluate(parsed)
    result = payload.value
    payload.dispose()
    return result