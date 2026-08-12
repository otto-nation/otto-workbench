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
    - `dict[int, V]` keys are restored to ints from the strings JSON makes of them
    - A nested dataclass defining `_from_raw` reconstructs itself through it,
      which is how a type stored in more than one shape stays readable

    Raises TypeError if the data omits a field that has no default.
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


def _identity(value):
    return value


def _coerce(hint, value):
    """Coerce a raw value to match its type hint."""
    origin = get_origin(hint)
    args = get_args(hint)

    # Optional[X] / X | None — union types containing None
    if args and type(None) in args:
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

    # Nested dataclass. A class that can be stored in more than one shape owns
    # its own reconstruction through `_from_raw` — the plain path below only
    # knows how to read a dict.
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        if hasattr(hint, "_from_raw"):
            return hint._from_raw(value)
        # An explicit `null` on a nested-dataclass field means "value omitted",
        # matching from_dict's own `if not data: data = {}` guard one level up.
        # A dataclass has no null state of its own — treating None as {} here
        # lets fields with defaults reconstruct as a default instance, and
        # lets a dataclass with required fields (e.g. PRIdentity) still raise
        # TypeError, rather than smuggling a bare None past the type hint.
        if value is None:
            value = {}
        if isinstance(value, dict):
            return from_dict(hint, value)

    # list[X] — coerce elements
    if origin is list and args:
        item_type = args[0]
        if isinstance(value, list):
            return [_coerce(item_type, v) for v in value]

    # tuple[X, ...] — reconstruct from list
    if origin is tuple and args:
        item_type = args[0]
        if isinstance(value, (list, tuple)):
            return tuple(_coerce(item_type, v) for v in value)

    # dict[K, V] — coerce values, and int keys back from the strings JSON made
    # of them. Only int: every other key type survives the trip as itself.
    if origin is dict and args and len(args) >= 2:
        key_type, val_type = args[0], args[1]
        if isinstance(value, dict):
            coerce_key = int if key_type is int else _identity
            return {coerce_key(k): _coerce(val_type, v) for k, v in value.items()}

    return value
