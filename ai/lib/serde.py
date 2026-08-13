"""Generic dataclass serialization with enum support.

Replaces hand-written _to_dict/_from_dict pairs. Uses dataclasses.asdict()
for serialization and type-hint-driven reconstruction for deserialization.
"""

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import log


_SCALARS = (bool, int, float, str)


class _Omitted(Exception):
    """Raised by `_coerce` for a value that cannot stand in for its hint.

    Never escapes `from_dict`, which is `_coerce`'s only caller.
    """


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
    - An explicit `null` on a field with no null form of its own (enum, scalar,
      list, tuple, dict) is treated as a missing key, falling back to the
      field's default. A `null` nested inside a list or dict element propagates
      the same way, dropping the whole field to its default rather than keeping
      a `None` alongside real values
    - A value whose type does not match its hint is restored where the
      conversion recovers the written value (`"3"` for an `int`, `3` for a
      `str`) and treated as a missing key where it would invent one (`"many"`
      for an `int`, a list for a nested dataclass). `bool` converts to and
      from nothing

    Raises TypeError if the data omits a field that has no default, or if the
    top-level data is not a dict (`None` excepted — that means "nothing
    recorded" and reconstructs as if every field were omitted).
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} needs a dict, got {type(data).__name__}")
    hints = get_type_hints(cls)
    fields = dataclasses.fields(cls)
    kwargs = {}
    for f in fields:
        if f.name not in data:
            continue
        try:
            kwargs[f.name] = _coerce(hints[f.name], data[f.name])
        except _Omitted:
            continue
    return cls(**kwargs)


def load_file(cls, path: Path):
    """Reconstruct a dataclass from a JSON file, or None if there isn't a usable one.

    A missing file and an unreadable one both come back as None. Every caller
    owns a regenerable cache — nothing in these files is authoritative — so
    discarding one that will not parse is always a correct recovery, and the
    warning is what keeps that from being silent.

    ValueError covers JSONDecodeError and UnicodeDecodeError, which subclass it,
    along with an unknown enum value and a non-numeric key under a dict[int, V].
    TypeError covers a field with no dataclass default that the file omits.
    """
    if not path.is_file():
        return None
    try:
        return from_dict(cls, json.loads(path.read_text()))
    except (OSError, TypeError, ValueError):
        log.warn(f"{path} is unreadable — discarding it")
        return None


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

    # Nested dataclass. A class that can be stored in more than one shape owns
    # its own reconstruction through `_from_raw` — the plain path below only
    # knows how to read a dict.
    is_dataclass_hint = isinstance(hint, type) and dataclasses.is_dataclass(hint)
    if is_dataclass_hint and hasattr(hint, "_from_raw"):
        # `_from_raw` owns every shape this type is stored in, but no type
        # has a null form. Route a null to the same place a missing key
        # goes rather than into a hook that will mis-handle it: a field
        # with a default gets that default, one without still raises
        # TypeError from `cls(**kwargs)`.
        if value is None:
            raise _Omitted
        return hint._from_raw(value)
    if is_dataclass_hint:
        # An explicit `null` on a nested-dataclass field means "value omitted",
        # matching from_dict's own `if data is None: data = {}` guard one level up.
        # A dataclass has no null state of its own — treating None as {} here
        # lets fields with defaults reconstruct as a default instance, and
        # lets a dataclass with required fields (e.g. PRIdentity) still raise
        # TypeError, rather than smuggling a bare None past the type hint.
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise _Omitted
        return from_dict(hint, value)

    # Every hint below this line — enum, scalar, list, tuple, dict — has no
    # null form of its own. An explicit `null` written for one means what a
    # missing key means: use the field's default. Only a hand-edited or
    # partially-written file produces one, since `to_dict` emits real values;
    # the alternative is a `None` sitting behind an `int` hint until a reader
    # does arithmetic on it. A field with no default still raises TypeError
    # from `cls(**kwargs)`, exactly as an absent key does.
    if value is None:
        raise _Omitted

    # Enum
    if isinstance(hint, type) and issubclass(hint, Enum):
        if isinstance(value, hint):
            return value
        return hint(value)

    # Scalar — bool, int, float, str. See _coerce_scalar for the rules.
    if hint in _SCALARS:
        return _coerce_scalar(hint, value)

    # A value that cannot be the container its hint names is an omitted field,
    # not a value to pass through. Returning it verbatim would plant a str
    # behind a `list[X]` hint for the first loop over it to trip on. A bare
    # `list`/`tuple`/`dict` hint carries no element type, so shape is all
    # there is to check.
    if origin is list or hint is list:
        if not isinstance(value, list):
            raise _Omitted
        return [_coerce(args[0], v) for v in value] if args else value

    if origin is tuple or hint is tuple:
        if not isinstance(value, (list, tuple)):
            raise _Omitted
        return tuple(_coerce(args[0], v) for v in value) if args else tuple(value)

    # dict[K, V] — coerce values, and int keys back from the strings JSON made
    # of them. Only int: every other key type survives the trip as itself.
    if origin is dict or hint is dict:
        if not isinstance(value, dict):
            raise _Omitted
        if len(args) < 2:
            return value
        coerce_key = int if args[0] is int else _identity
        return {coerce_key(k): _coerce(args[1], v) for k, v in value.items()}

    return value


def _coerce_scalar(hint, value):
    """A scalar as its hinted type, or `_Omitted` when it cannot become one.

    JSON loses the distinction often enough that a mismatch is worth restoring
    rather than discarding: a model writes `"3"` for an int field, a hand-edit
    writes `3` for a str one. Conversion is refused wherever it would invent a
    value instead of recovering one — the field's default is a better answer
    than a confidently wrong number.
    """
    if hint is bool or isinstance(value, bool):
        # bool is isolated from every conversion below. `isinstance(True, int)`
        # is True, `bool("false")` is True, and `str(True)` is "True" where JSON
        # wrote "true" — each yields a wrong value rather than a restored one.
        # So only a real bool satisfies a bool hint, and a bool satisfies
        # nothing else.
        if hint is bool and isinstance(value, bool):
            return value
        raise _Omitted
    if isinstance(value, hint):
        return value
    if not isinstance(value, (int, float, str)):
        # A list or dict. `str(value)` renders either into a plausible-looking
        # string, which is a corrupt field wearing a valid type.
        raise _Omitted
    if hint is int and isinstance(value, float) and not value.is_integer():
        # 3.0 is an int that JSON wrote as a float; 3.7 is not an int at all.
        raise _Omitted
    try:
        return hint(value)
    except (TypeError, ValueError):
        raise _Omitted from None
