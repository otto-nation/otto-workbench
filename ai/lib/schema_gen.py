"""JSON Schema generation from Python dataclasses.

Produces JSON Schema from dataclass definitions, describing the documents
`serde` will accept for them. `serde.classify` owns what a type hint means;
this module only decides how each kind is written down, so the schema a model
reads and the reader that accepts the model's answer cannot disagree about
which shapes are legal. Both dispatch on `classify`'s one answer, so a new
`HintKind` fails a test in every module that has to handle it.

One case needs the dataclass's help. A class that reads more than one stored
shape through `_from_raw` — a legacy string, a renamed key — is the only thing
that knows what those shapes are, so it also defines
`_raw_schema(object_schema)`, returning the widened fragment. Without it the
published schema would call a document invalid that `serde` reads without
complaint; a test fails any `_from_raw` class in `ai/lib/` that does not define
one.

This is what fills the output schema half of a tool's `--tool-schema` contract —
see `tool_parser`.
"""

# doc-group: platform

from __future__ import annotations

import dataclasses
import logging
from enum import Enum
from typing import get_type_hints

from serde import HintKind, classify

logger = logging.getLogger(__name__)

_SCALAR_SCHEMAS = {
    bool: {"type": "boolean"},
    int: {"type": "integer"},
    float: {"type": "number"},
    str: {"type": "string"},
}

# JSON has string keys only, so `dict[int, V]` is written with its keys
# stringified and `serde` restores them with `int()`. A key that will not parse
# makes the whole file unreadable, which is a constraint worth stating.
_INT_KEY_PATTERN = r"^-?[0-9]+$"


def dataclass_to_schema(cls) -> dict:
    """Generate a JSON Schema object from a dataclass.

    The object form specifically. A class that also reads other shapes through
    `_from_raw` says so in `_raw_schema`, which is handed this and widens it —
    see `_from_raw_schema`.
    """
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
    kind, args = classify(hint)
    return _EMITTERS[kind](hint, args)


def _optional_schema(hint, args) -> dict:
    # `serde` coerces a union to its first non-None member and ignores the
    # rest, so the schema names the shape that will actually be reconstructed.
    return {"oneOf": [_hint_to_schema(args[0]), {"type": "null"}]}


def _from_raw_schema(hint, args) -> dict:
    """The shapes a `_from_raw` type accepts, which only that type knows.

    `serde` hands the whole field to the hook, so the object schema alone
    describes a document `serde` would happily read as something else — a
    legacy string, a renamed key. `_raw_schema(object_schema)` is where the
    type widens its own contract; a test fails any reachable `_from_raw` type
    that does not define one, so the open schema below is what an out-of-tree
    type gets rather than a gap this repo can grow.
    """
    object_schema = dataclass_to_schema(hint)
    if not hasattr(hint, "_raw_schema"):
        logger.debug("%r reads raw values but publishes no schema for them", hint)
        return {}
    return hint._raw_schema(object_schema)


def _enum_schema(hint, args) -> dict:
    """`serde` rebuilds an enum by calling it with the written value, so the
    schema names the member values and the type they are written as. A mixed
    enum states its values and leaves the type open rather than misreporting it.
    """
    values = [m.value for m in hint]
    types = {_SCALAR_SCHEMAS[type(v)]["type"] for v in values if type(v) in _SCALAR_SCHEMAS}
    if len(types) == 1:
        return {"type": types.pop(), "enum": values}
    return {"enum": values}


def _array_schema(hint, args) -> dict:
    # A bare `list`/`tuple` states its shape and nothing about its elements,
    # which is all `serde` checks for one.
    if not args:
        return {"type": "array"}
    return {"type": "array", "items": _hint_to_schema(args[0])}


def _dict_schema(hint, args) -> dict:
    if not args:
        return {"type": "object"}
    schema = {"type": "object", "additionalProperties": _hint_to_schema(args[1])}
    if args[0] is int:
        schema["propertyNames"] = {"pattern": _INT_KEY_PATTERN}
    # An enum key type is a closed set, so it constrains which property names
    # are valid, not only what their values look like.
    elif isinstance(args[0], type) and issubclass(args[0], Enum):
        schema["propertyNames"] = {"enum": [m.value for m in args[0]]}
    return schema


def _opaque_schema(hint, args) -> dict:
    logger.debug("Unrecognized type hint %r — emitting open schema", hint)
    return {}


_EMITTERS = {
    HintKind.OPTIONAL: _optional_schema,
    HintKind.FROM_RAW: _from_raw_schema,
    HintKind.DATACLASS: lambda hint, args: dataclass_to_schema(hint),
    HintKind.ENUM: _enum_schema,
    HintKind.SCALAR: lambda hint, args: dict(_SCALAR_SCHEMAS[hint]),
    HintKind.LIST: _array_schema,
    HintKind.TUPLE: _array_schema,
    HintKind.DICT: _dict_schema,
    HintKind.OPAQUE: _opaque_schema,
}
