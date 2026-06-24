"""Generic dataclass serialization with enum support.

Replaces hand-written _to_dict/_from_dict pairs. Uses dataclasses.asdict()
for serialization and type-hint-driven reconstruction for deserialization.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import get_args, get_origin, get_type_hints


def to_dict(obj) -> dict:
    """Serialize a dataclass to a dict, converting enums to their values."""
    def factory(pairs):
        return {k: v.value if isinstance(v, Enum) else v for k, v in pairs}
    return dataclasses.asdict(obj, dict_factory=factory)


def from_dict(cls, data: dict):
    """Reconstruct a dataclass from a dict, handling enums and nested dataclasses.

    - Missing keys use field defaults
    - Extra keys are ignored
    - Enum fields are reconstructed from their string values
    - Nested dataclass fields are recursively reconstructed
    """
    if not data:
        data = {}
    hints = get_type_hints(cls)
    fields = dataclasses.fields(cls)
    kwargs = {}
    for f in fields:
        if f.name not in data:
            continue
        raw = data[f.name]
        kwargs[f.name] = _coerce(hints[f.name], raw)
    return cls(**kwargs)


def _coerce(hint, value):
    """Coerce a raw value to match its type hint."""
    origin = get_origin(hint)
    args = get_args(hint)

    # Optional[X] / X | None — union types
    if origin is type(int | None) or (args and type(None) in args):
        non_none = [a for a in args if a is not type(None)]
        if value is None:
            return None
        if non_none:
            return _coerce(non_none[0], value)
        return value

    # Enum
    if isinstance(hint, type) and issubclass(hint, Enum):
        if isinstance(value, hint):
            return value
        return hint(value)

    # Nested dataclass
    if isinstance(hint, type) and dataclasses.is_dataclass(hint) and isinstance(value, dict):
        return from_dict(hint, value)

    # tuple[X, ...] — reconstruct from list
    if origin is tuple and args:
        item_type = args[0]
        if isinstance(value, (list, tuple)):
            return tuple(_coerce(item_type, v) for v in value)

    return value
