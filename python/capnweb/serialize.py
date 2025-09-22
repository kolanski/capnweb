"""
Serialization and deserialization for Cap'n Web Python.

This module handles converting Python objects to/from JSON-serializable
representations for transmission over RPC.
"""

import json
import base64
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
        ...


class Importer(Protocol):
    """Protocol for importing capabilities."""
    
    def import_stub(self, import_id: int) -> Any:
        """Import a stub by its import ID."""
        ...
    
    def import_promise(self, import_id: int) -> Any:
        """Import a promise by its import ID."""
        ...


class NullExporter:
    """Null exporter that raises errors when trying to export."""
    
    def export_stub(self, hook) -> int:
        raise RuntimeError("Cannot export stubs without an exporter")
    
    def export_promise(self, hook) -> int:
        raise RuntimeError("Cannot export promises without an exporter")


NULL_EXPORTER = NullExporter()


class Devaluator:
    """
    Converts fully-hydrated Python objects into JSON-serializable representations.
    
    This is the opposite of Evaluator - it prepares objects for sending over the wire.
    """
    
    def __init__(self, exporter: Exporter = NULL_EXPORTER):
        self._exporter = exporter
        self._exports: List[int] = []
    
    @classmethod
    def devaluate(cls, value: Any, exporter: Exporter = NULL_EXPORTER) -> tuple[Any, List[int]]:
        """
        Devaluate a value for transmission.
        
        Returns (serialized_value, export_ids).
        """
        devaluator = cls(exporter)
        serialized = devaluator._devaluate_impl(value, None, 0)
        return serialized, devaluator._exports
    
    def _devaluate_impl(self, value: Any, parent: Optional[Any], depth: int) -> Any:
        """Internal implementation of devaluation."""
        if depth > 100:  # Prevent infinite recursion
            raise RuntimeError("Maximum recursion depth exceeded during serialization")
        
        rpc_type = type_for_rpc(value)
        
        if rpc_type == "primitive":
            return value
        
        elif rpc_type == "undefined":
            return None
        
        elif rpc_type == "array":
            return [self._devaluate_impl(item, value, depth + 1) for item in value]
        
        elif rpc_type == "object":
            if isinstance(value, dict):
                return {
                    key: self._devaluate_impl(val, value, depth + 1)
                    for key, val in value.items()
                }
            else:
                # Convert object to dict
                result = {}
                for attr in dir(value):
                    if not attr.startswith('_'):
                        try:
                            attr_value = getattr(value, attr)
                            if not callable(attr_value):
                                result[attr] = self._devaluate_impl(attr_value, value, depth + 1)
                        except AttributeError:
                            pass
                return result
        
        elif rpc_type == "bytes":
            return ["bytes", base64.b64encode(value).decode('ascii')]
        
        elif rpc_type == "error":
            return [
                "error",
                {
                    "name": type(value).__name__,
                    "message": str(value),
                }
            ]
        
        elif rpc_type in ("stub", "rpc-promise"):
            hook, path = unwrap_stub_and_path(value)
            if hook is None:
                raise RuntimeError(f"Failed to unwrap {rpc_type}")
            
            if len(path.path) == 0:
                # Direct stub
                return self._devaluate_hook("export", hook)
            else:
                # Promise for a property
                return self._devaluate_hook("promise", hook)
        
        elif rpc_type == "rpc-target":
            # For RpcTarget instances, we need to create a hook
            # This would typically be handled by the RPC session
            raise RuntimeError("Cannot serialize RpcTarget without session context")
        
        elif rpc_type == "function":
            # Functions are treated similarly to RpcTargets
            raise RuntimeError("Cannot serialize functions without session context")
        
        else:
            raise RuntimeError(f"Unsupported type for RPC: {rpc_type}")
    
    def _devaluate_hook(self, hook_type: str, hook) -> List[Union[str, int]]:
        """Devaluate a stub or promise hook."""
        if hook_type == "promise":
            export_id = self._exporter.export_promise(hook)
        else:
            export_id = self._exporter.export_stub(hook)
        
        self._exports.append(export_id)
        return [hook_type, export_id]


class Evaluator:
    """
    Converts JSON-serializable representations back into Python objects.
    
    This is the opposite of Devaluator - it reconstructs objects received over the wire.
    """
    
    def __init__(self, importer: Optional[Importer] = None):
        self._importer = importer
    
    @classmethod
    def evaluate(cls, value: Any, importer: Optional[Importer] = None) -> Any:
        """Evaluate a serialized value back into a Python object."""
        evaluator = cls(importer)
        return evaluator._evaluate_impl(value, 0)
    
    def _evaluate_impl(self, value: Any, depth: int) -> Any:
        """Internal implementation of evaluation."""
        if depth > 100:  # Prevent infinite recursion
            raise RuntimeError("Maximum recursion depth exceeded during deserialization")
        
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        
        elif isinstance(value, list):
            if len(value) >= 2 and isinstance(value[0], str):
                # Check for special encoded types
                type_tag = value[0]
                
                if type_tag == "bytes":
                    return base64.b64decode(value[1])
                
                elif type_tag == "error":
                    error_info = value[1]
                    error_name = error_info.get("name", "Exception")
                    message = error_info.get("message", "")
                    
                    # Try to get the error class from builtins
                    try:
                        # Check if __builtins__ is a dict or module
                        if isinstance(__builtins__, dict):
                            builtins_dict = __builtins__
                        else:
                            builtins_dict = __builtins__.__dict__
                        
                        if error_name in builtins_dict:
                            error_class = builtins_dict[error_name]
                            # Make sure it's actually an exception class
                            if isinstance(error_class, type) and issubclass(error_class, BaseException):
                                return error_class(message)
                    except (AttributeError, TypeError, KeyError):
                        pass
                    
                    # Fall back to Exception if the specific type isn't available
                    return Exception(message)
                
                elif type_tag == "export" and self._importer:
                    return self._importer.import_stub(value[1])
                
                elif type_tag == "promise" and self._importer:
                    return self._importer.import_promise(value[1])
                
                elif type_tag in ("export", "promise"):
                    raise RuntimeError(f"Cannot deserialize {type_tag} without importer")
            
            # Regular array
            return [self._evaluate_impl(item, depth + 1) for item in value]
        
        elif isinstance(value, dict):
            return {
                key: self._evaluate_impl(val, depth + 1)
                for key, val in value.items()
            }
        
        else:
            return value


def serialize(value: Any) -> str:
    """
    Serialize a value to a JSON string.
    
    This is a simplified version that doesn't handle stubs/promises.
    For full RPC serialization, use Devaluator within an RPC session.
    """
    serialized, _ = Devaluator.devaluate(value)
    return json.dumps(serialized)


def deserialize(data: str) -> Any:
    """
    Deserialize a JSON string back to a Python value.
    
    This is a simplified version that doesn't handle stubs/promises.
    For full RPC deserialization, use Evaluator within an RPC session.
    """
    parsed = json.loads(data)
    return Evaluator.evaluate(parsed)