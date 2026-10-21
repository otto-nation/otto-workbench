"""Strict reading of a dataclass from JSON someone else wrote.

`core.serde.from_dict` is lenient on purpose: the files it reads are caches
and state this workbench wrote itself, and a value that will not coerce is
better dropped to its default than allowed to fail a run. A document an agent
wrote is the opposite case. Dropping what does not fit is a silent repair, and
the reader has to say exactly what is wrong so the agent can fix it.

`load_strict` checks a parsed JSON value against a dataclass and reports every
disagreement by JSON path (`$.findings[2].id`). Only once there are none does
it build the instance, through the same `from_dict`. Both readers dispatch on
`core.serde.classify`, so the two cannot disagree about what a hint means.

`example_json` renders an instance the way `to_dict` writes it. A prompt that
shows an agent the shape to write shows it with this, so the example and the
reader are one schema.
"""

# doc-group: platform

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TypeVar, get_type_hints

import core.serde
from core.serde import HintKind, classify

T = TypeVar("T")


@dataclass(frozen=True)
class SchemaViolation:
    """One place a document disagrees with its dataclass: where, and how."""

    at: str
    detail: str


class SchemaRejected(Exception):
    """The document does not match its dataclass; `violations` names each disagreement."""

    def __init__(self, violations: tuple[SchemaViolation, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(f"{v.at}: {v.detail}" for v in violations))


def load_strict(cls: type[T], data: object) -> T:
    """`data` as a `cls`, or `SchemaRejected` naming every disagreement.

    Unknown keys, missing required keys, a value of the wrong JSON type, an
    enum value outside the enum and a `null` where the hint has no null form are
    all violations. Raises TypeError for a hint kind no artifact may use.
    """
    violations = _check(cls, data, "$")
    if violations:
        raise SchemaRejected(tuple(violations))
    return core.serde.from_dict(cls, data)


def example_json(instance) -> str:
    """`instance` as the JSON an agent is shown — `to_dict`'s form, indented."""
    return json.dumps(core.serde.to_dict(instance), indent=2)


_JSON_TYPES = (
    (bool, "a boolean"), (int, "a number"), (float, "a number"),
    (str, "a string"), (list, "an array"), (dict, "an object"),
)
_SCALAR_NAMES = {bool: "a boolean", int: "an integer", float: "a number", str: "a string"}


def _json_type(value: object) -> str:
    return next((name for kind, name in _JSON_TYPES if isinstance(value, kind)), type(value).__name__)


def _check(hint, value, at: str) -> list[SchemaViolation]:
    kind, args = classify(hint)
    if value is None:
        if kind is HintKind.OPTIONAL:
            return []
        return [SchemaViolation(at, "must not be null")]
    return _CHECKS[kind](hint, args, value, at)


def _check_optional(hint, args, value, at):
    if len(args) != 1:
        raise TypeError(f"{hint!r} has no strict reading: a union of more than one type")
    return _check(args[0], value, at)


def _check_dataclass(hint, args, value, at):
    if not isinstance(value, dict):
        return [SchemaViolation(at, f"must be an object, not {_json_type(value)}")]
    hints = get_type_hints(hint)
    fields = {f.name: f for f in dataclasses.fields(hint)}
    names = ", ".join(fields)
    out = [
        SchemaViolation(f"{at}.{key}", f"is not a field of {hint.__name__} (fields: {names})")
        for key in value if key not in fields
    ]
    for name, f in fields.items():
        required = f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        if name in value:
            out += _check(hints[name], value[name], f"{at}.{name}")
        elif required:
            out.append(SchemaViolation(f"{at}.{name}", "is required"))
    return out


def _check_enum(hint, args, value, at):
    allowed = [m.value for m in hint]
    if not isinstance(value, bool) and value in allowed:
        return []
    listed = " or ".join(json.dumps(v) for v in allowed)
    return [SchemaViolation(at, f"must be {listed}, not {json.dumps(value)}")]


def _scalar_ok(hint, value) -> bool:
    if hint is bool or isinstance(value, bool):
        return hint is bool and isinstance(value, bool)
    if hint is float:
        return isinstance(value, (int, float))
    return isinstance(value, hint)


def _check_scalar(hint, args, value, at):
    if _scalar_ok(hint, value):
        return []
    if hint is int and isinstance(value, float):
        return [SchemaViolation(at, "must be an integer, not a fractional or float number")]
    return [SchemaViolation(at, f"must be {_SCALAR_NAMES[hint]}, not {_json_type(value)}")]


def _check_sequence(hint, args, value, at):
    if not isinstance(value, list):
        return [SchemaViolation(at, f"must be an array, not {_json_type(value)}")]
    if not args:
        return []
    out: list[SchemaViolation] = []
    for i, item in enumerate(value):
        out += _check(args[0], item, f"{at}[{i}]")
    return out


def _unsupported(hint, args, value, at):
    raise TypeError(f"{hint!r} has no strict reading: no agent artifact may use this kind")


# Keyed on every `HintKind`, so a kind added to serde is a failing test here
# rather than a silent fallthrough. The four an artifact never needs refuse
# loudly: a reader that half-supported them would be lenient by accident.
_CHECKS = {
    HintKind.OPTIONAL: _check_optional,
    HintKind.DATACLASS: _check_dataclass,
    HintKind.ENUM: _check_enum,
    HintKind.SCALAR: _check_scalar,
    HintKind.LIST: _check_sequence,
    HintKind.TUPLE: _check_sequence,
    HintKind.SET: _unsupported,
    HintKind.DICT: _unsupported,
    HintKind.FROM_RAW: _unsupported,
    HintKind.OPAQUE: _unsupported,
}
