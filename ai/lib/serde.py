"""Generic dataclass serialization with enum support.

Replaces hand-written _to_dict/_from_dict pairs. Uses dataclasses.asdict()
for serialization and type-hint-driven reconstruction for deserialization.

`classify` is the type-hint walk itself, exported because reading a value is
not the only thing that has to know what an annotation means — `schema_gen`
describes the same hints to a model and dispatches on the same answer.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from enum import Enum, StrEnum
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


def write_json(path: Path, data) -> None:
    """Write `data` to `path` as JSON, atomically, creating parent directories.

    The single owner of "put this JSON on disk without a reader ever seeing it
    half-written". ``open(path, "w")`` truncates in place, so a concurrent
    reader can observe a zero-byte or partial file and fail with a
    JSONDecodeError; writing a temp file and renaming it means a reader sees
    either the whole previous file or the whole new one.

    The temp file is created by `mkstemp` in the destination directory: unique
    per call, so two threads or two processes writing the same path cannot land
    on the same temp name, and on the same filesystem, so `os.replace` is a
    rename rather than a copy. It inherits mkstemp's 0600 — these are per-user
    state and cache files under a worktree or `~/.local/state`, and nothing
    reads them as another user.

    Serialization runs before the rename, so a value JSON cannot encode leaves
    the existing file untouched rather than truncating it to the point of the
    failure.

    Takes a `Path`, and does not coerce one from what it is handed. `Path(x)`
    accepts anything with `__fspath__`, which a `MagicMock` has — coercing here
    turns a test's stubbed state directory into a real one under the working
    directory, silently, at the bottom of a call stack that never meant to
    touch the disk.
    """
    # ceiling: no fsync — os.replace orders the rename against a concurrent
    # reader, which is what these files need; surviving a machine crash
    # mid-write is not. Add one if a caller ever stores something unrebuildable.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class HintKind(StrEnum):
    """What a type hint is, for everything that has to walk one.

    `classify` is the single owner of the question "what does this annotation
    mean". `_coerce` answers it with a value and `schema_gen` answers it with a
    JSON Schema fragment, and neither re-derives the question — two independent
    walks over `get_origin`/`get_args` is how the two drifted apart before.

    Adding a member is adding a capability: both dispatch tables are keyed on
    this enum and a test walks its members, so a new kind is a failing test in
    every module that has to handle it rather than a silent fallthrough.
    """

    OPTIONAL = "optional"
    FROM_RAW = "from_raw"
    DATACLASS = "dataclass"
    ENUM = "enum"
    SCALAR = "scalar"
    LIST = "list"
    TUPLE = "tuple"
    DICT = "dict"
    OPAQUE = "opaque"


# The kinds that read a `None` as something other than "field omitted". Every
# other kind — enum, scalar, list, tuple, dict, and the `_from_raw` hook, which
# has no null form of its own — takes the shared rule in `_coerce`.
_NULL_AWARE = frozenset({HintKind.OPTIONAL, HintKind.DATACLASS})


def classify(hint) -> tuple[HintKind, tuple]:
    """Sort a type hint into a `HintKind` and the type arguments that kind uses.

    The returned args are what the kind's handler needs, not `get_args` verbatim:
    OPTIONAL yields the non-None members, containers yield their element types,
    and a kind that needs nothing from the annotation yields `()`. A bare
    `list`/`tuple`/`dict` is its own kind with no args — shape is all it states.
    """
    args = get_args(hint)
    origin = get_origin(hint)
    is_type = isinstance(hint, type)

    # Optional[X] / X | None. A union with no non-None member is not a hint any
    # of this can act on, so it falls through to OPAQUE with the rest.
    if args and type(None) in args:
        non_none = tuple(a for a in args if a is not type(None))
        if non_none:
            return HintKind.OPTIONAL, non_none

    if is_type and dataclasses.is_dataclass(hint):
        # A class stored in more than one shape owns its own reconstruction.
        # The plain dataclass path only knows how to read a dict.
        return (HintKind.FROM_RAW if hasattr(hint, "_from_raw") else HintKind.DATACLASS), ()

    if is_type and issubclass(hint, Enum):
        return HintKind.ENUM, ()

    if hint in _SCALARS:
        return HintKind.SCALAR, ()

    if origin is list or hint is list:
        return HintKind.LIST, args
    if origin is tuple or hint is tuple:
        return HintKind.TUPLE, args
    if origin is dict or hint is dict:
        # A dict hint carries a key type as well, and only a full pair is usable.
        return HintKind.DICT, args if len(args) >= 2 else ()

    return HintKind.OPAQUE, ()


def _identity(value):
    return value


def _coerce(hint, value):
    """Coerce a raw value to match its type hint."""
    kind, args = classify(hint)

    # Every kind but the two that own a null reads one the way it reads a
    # missing key: use the field's default. Only a hand-edited or
    # partially-written file produces one, since `to_dict` emits real values;
    # the alternative is a `None` sitting behind an `int` hint until a reader
    # does arithmetic on it. A field with no default still raises TypeError
    # from `cls(**kwargs)`, exactly as an absent key does. For a `_from_raw`
    # type that means the hook never sees a null it would have to invent a
    # value for.
    if value is None and kind not in _NULL_AWARE:
        raise _Omitted

    return _COERCERS[kind](hint, args, value)


def _coerce_optional(hint, args, value):
    return None if value is None else _coerce(args[0], value)


def _coerce_from_raw(hint, args, value):
    return hint._from_raw(value)


def _coerce_dataclass(hint, args, value):
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


def _coerce_enum(hint, args, value):
    if isinstance(value, hint):
        return value
    return hint(value)


# A value that cannot be the container its hint names is an omitted field, not
# a value to pass through. Returning it verbatim would plant a str behind a
# `list[X]` hint for the first loop over it to trip on. A bare `list`/`tuple`/
# `dict` hint carries no element type, so shape is all there is to check.
def _coerce_list(hint, args, value):
    if not isinstance(value, list):
        raise _Omitted
    return [_coerce(args[0], v) for v in value] if args else value


def _coerce_tuple(hint, args, value):
    if not isinstance(value, (list, tuple)):
        raise _Omitted
    return tuple(_coerce(args[0], v) for v in value) if args else tuple(value)


def _coerce_dict(hint, args, value):
    """dict[K, V] — coerce values, and int keys back from the strings JSON made
    of them. Only int: every other key type survives the trip as itself."""
    if not isinstance(value, dict):
        raise _Omitted
    if not args:
        return value
    coerce_key = int if args[0] is int else _identity
    return {coerce_key(k): _coerce(args[1], v) for k, v in value.items()}


def _coerce_opaque(hint, args, value):
    """A hint nothing here can act on — a bare union, an unparameterised alias.
    The written value is the best available answer."""
    return value


_COERCERS = {
    HintKind.OPTIONAL: _coerce_optional,
    HintKind.FROM_RAW: _coerce_from_raw,
    HintKind.DATACLASS: _coerce_dataclass,
    HintKind.ENUM: _coerce_enum,
    HintKind.SCALAR: lambda hint, args, value: _coerce_scalar(hint, value),
    HintKind.LIST: _coerce_list,
    HintKind.TUPLE: _coerce_tuple,
    HintKind.DICT: _coerce_dict,
    HintKind.OPAQUE: _coerce_opaque,
}


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
