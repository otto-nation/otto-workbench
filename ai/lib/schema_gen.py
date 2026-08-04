"""JSON Schema generation from Python dataclasses.

Produces JSON Schema from dataclass definitions, mirroring the type
handling in serde.py for consistency.
"""

from __future__ import annotations

import dataclasses
import logging
from enum import Enum
from typing import get_args, get_origin, get_type_hints

logger = logging.getLogger(__name__)


def dataclass_to_schema(cls) -> dict:
    """Generate a JSON Schema object from a dataclass."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls} is not a dataclass")

    hints = get_type_hints(cls)
    fields = dataclasses.fields(cls)
    properties = {}
    required = []

    for f in fields:
        properties[f.name] = _hint_to_schema(hints[f.name])
        if _is_required(f):
            required.append(f.name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _is_required(f: dataclasses.Field) -> bool:
    """A field is required if it has no default and no default_factory."""
    return (
        f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    )


def _hint_to_schema(hint) -> dict:
    """Convert a type hint to a JSON Schema fragment."""
    origin = get_origin(hint)
    args = get_args(hint)

    # Optional[X] / X | None
    if args and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            inner = _hint_to_schema(non_none[0])
            return {"oneOf": [inner, {"type": "null"}]}
        return {}

    # Enum
    if isinstance(hint, type) and issubclass(hint, Enum):
        return {"type": "string", "enum": [m.value for m in hint]}

    # Nested dataclass
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return dataclass_to_schema(hint)

    # list[X]
    if origin is list and args:
        return {"type": "array", "items": _hint_to_schema(args[0])}

    # tuple[X, ...]
    if origin is tuple and args:
        return {"type": "array", "items": _hint_to_schema(args[0])}

    # dict[K, V]
    if origin is dict and args and len(args) >= 2:
        return {"type": "object", "additionalProperties": _hint_to_schema(args[1])}

    # Primitives
    if hint is int:
        return {"type": "integer"}
    if hint is float:
        return {"type": "number"}
    if hint is str:
        return {"type": "string"}
    if hint is bool:
        return {"type": "boolean"}

    # Bare dict (no type args)
    if hint is dict:
        return {"type": "object"}

    logger.debug("Unrecognized type hint %r — emitting open schema", hint)
    return {}
