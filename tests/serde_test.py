"""Tests for generic serialization/deserialization."""

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from pr_state import PRState
from review_preflight import PipelineState
from serde import from_dict, to_dict


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Inner:
    name: str = ""
    color: Color = Color.RED


@dataclass
class Outer:
    label: str = ""
    inner: Inner = field(default_factory=Inner)
    items: list[str] = field(default_factory=list)


@dataclass
class Container:
    entries: dict[str, Inner] = field(default_factory=dict)
    colors: list[Color] = field(default_factory=list)


@dataclass
class Tagged:
    """A type stored in more than one shape, which owns its own hydration."""

    value: str = ""

    @classmethod
    def _from_raw(cls, raw):
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return from_dict(cls, raw)
        return cls(value=str(raw))


@dataclass
class Keyed:
    counts: dict[int, str] = field(default_factory=dict)
    tagged: Tagged = field(default_factory=Tagged)
    tags: dict[int, Tagged] = field(default_factory=dict)


@dataclass
class OuterOptional:
    """A nested dataclass field that is genuinely optional, not just defaulted."""
    inner: Inner | None = None


@dataclass
class Required:
    """A dataclass with no default for its only field."""
    name: str


@dataclass
class HasRequired:
    inner: Required = field(default_factory=lambda: Required(name="x"))


class TestToDict:
    def test_simple_dataclass(self):
        d = to_dict(Inner(name="x", color=Color.BLUE))
        assert d == {"name": "x", "color": "blue"}

    def test_nested_dataclass(self):
        d = to_dict(Outer(label="a", inner=Inner(name="b")))
        assert d["label"] == "a"
        assert d["inner"] == {"name": "b", "color": "red"}

    def test_enum_serialized_as_value(self):
        d = to_dict(Inner(color=Color.BLUE))
        assert d["color"] == "blue"


class TestFromDict:
    def test_simple_reconstruction(self):
        obj = from_dict(Inner, {"name": "x", "color": "blue"})
        assert obj.name == "x"
        assert obj.color == Color.BLUE

    def test_missing_fields_use_defaults(self):
        obj = from_dict(Inner, {})
        assert obj.name == ""
        assert obj.color == Color.RED

    def test_nested_reconstruction(self):
        obj = from_dict(Outer, {"label": "a", "inner": {"name": "b", "color": "blue"}})
        assert obj.label == "a"
        assert obj.inner.name == "b"
        assert obj.inner.color == Color.BLUE

    def test_extra_keys_ignored(self):
        obj = from_dict(Inner, {"name": "x", "unknown_field": 99})
        assert obj.name == "x"

    def test_roundtrip(self):
        original = Outer(label="test", inner=Inner(name="n", color=Color.BLUE), items=["a", "b"])
        restored = from_dict(Outer, to_dict(original))
        assert restored.label == original.label
        assert restored.inner.name == original.inner.name
        assert restored.inner.color == original.inner.color
        assert restored.items == original.items

    def test_dict_with_dataclass_values(self):
        obj = from_dict(Container, {
            "entries": {"a": {"name": "x", "color": "blue"}, "b": {"name": "y"}},
            "colors": ["red", "blue"],
        })
        assert isinstance(obj.entries["a"], Inner)
        assert obj.entries["a"].color == Color.BLUE
        assert obj.entries["b"].name == "y"
        assert obj.colors == [Color.RED, Color.BLUE]

    def test_dict_with_dataclass_values_roundtrip(self):
        original = Container(
            entries={"a": Inner(name="x", color=Color.BLUE)},
            colors=[Color.RED],
        )
        restored = from_dict(Container, to_dict(original))
        assert isinstance(restored.entries["a"], Inner)
        assert restored.entries["a"].name == "x"
        assert restored.entries["a"].color == Color.BLUE
        assert restored.colors == [Color.RED]


class TestIntDictKeys:
    """JSON stringifies every key, so `dict[int, V]` needs them coerced back."""

    def test_int_keys_restored_from_strings(self):
        obj = from_dict(Keyed, {"counts": {"1": "a", "2": "b"}})
        assert obj.counts == {1: "a", 2: "b"}

    def test_int_keys_survive_a_json_hop(self):
        original = Keyed(counts={3: "c"})
        restored = from_dict(Keyed, json.loads(json.dumps(to_dict(original))))
        assert restored == original

    def test_str_keys_are_left_alone(self):
        obj = from_dict(Container, {"entries": {"7": {"name": "x"}}})
        assert list(obj.entries) == ["7"]


class TestFromRawHook:
    """A dataclass defining `_from_raw` reconstructs itself."""

    def test_a_dict_goes_through_the_hook(self):
        obj = from_dict(Keyed, {"tagged": {"value": "x"}})
        assert obj.tagged == Tagged(value="x")

    def test_a_bare_scalar_goes_through_the_hook(self):
        obj = from_dict(Keyed, {"tagged": "legacy"})
        assert obj.tagged == Tagged(value="legacy")

    def test_an_existing_instance_passes_through(self):
        obj = from_dict(Keyed, {"tagged": Tagged(value="x")})
        assert obj.tagged == Tagged(value="x")

    def test_the_hook_reaches_dict_values(self):
        obj = from_dict(Keyed, {"tags": {"1": "legacy", "2": {"value": "typed"}}})
        assert obj.tags == {1: Tagged(value="legacy"), 2: Tagged(value="typed")}


class TestNullOnNestedDataclass:
    """`None` on a nested-dataclass field means "value omitted", not "field is None".

    A dataclass has no null state of its own, so an explicit `null` written for
    one — e.g. a hand-edited state file, or a domain reset to its default — must
    reconstruct the same way a missing key does: default fields fall back to
    their defaults, required fields still raise.
    """

    def test_null_on_a_defaulted_field_yields_the_default_instance(self):
        obj = from_dict(Outer, {"label": "a", "inner": None})
        assert obj.inner == Inner()

    def test_null_still_reconstructs_none_for_an_optional_field(self):
        """`X | None` fields are unaffected — the union branch returns first."""
        obj = from_dict(OuterOptional, {"inner": None})
        assert obj.inner is None

    def test_null_on_a_dataclass_with_required_fields_still_raises(self):
        with pytest.raises(TypeError):
            from_dict(HasRequired, {"inner": None})


# ── The round-trip guard ─────────────────────────────────────────────────────

# The dataclasses that get written to disk and read back. Only the roots are
# named: coverage of everything nested beneath them is derived from their field
# type hints, so a new field whose type is a new dataclass is covered here
# without editing this list.
PERSISTED_ROOTS = [PipelineState, PRState]


def _sample(hint):
    """Build a non-default value for a type hint.

    Defaults round-trip whether or not `_coerce` understands their type — the
    read/write asymmetry this guards against only shows on a populated field.
    """
    # A bare `dict`/`list` annotation is its own origin. Nothing can be derived
    # about what it holds, so it gets a plain sample — parameterising the hint
    # deepens this guard's coverage for free.
    origin = get_origin(hint) or hint
    args = get_args(hint)

    if args and type(None) in args:
        return _sample(next(a for a in args if a is not type(None)))
    if origin is list:
        return [_sample(args[0])] if args else ["x"]
    if origin is tuple:
        return (_sample(args[0]),) if args else ("x",)
    if origin is dict:
        return {_sample(args[0]): _sample(args[1])} if args else {"k": "v"}
    if isinstance(hint, type) and issubclass(hint, Enum):
        # The last member, so an enum field never matches a first-member default.
        return list(hint)[-1]
    if dataclasses.is_dataclass(hint):
        return _sample_instance(hint)
    if hint in (str, int, float, bool):
        return {str: "s", int: 7, float: 1.5, bool: True}[hint]
    raise AssertionError(f"the round-trip guard has no sample for {hint!r}")


def _sample_instance(cls):
    """A fully-populated instance of a dataclass — every field set, recursively."""
    hints = get_type_hints(cls)
    return cls(**{f.name: _sample(hints[f.name]) for f in dataclasses.fields(cls)})


@pytest.mark.parametrize("cls", PERSISTED_ROOTS, ids=[c.__name__ for c in PERSISTED_ROOTS])
def test_persisted_state_survives_a_json_round_trip(cls):
    """Every persisted dataclass reads back as what was written.

    The defect class is a writer that goes through `serde` and a reader that
    hand-lists fields: the two drift, and a field is silently dropped or comes
    back as the wrong type. Asserting equality across a real JSON hop catches
    that on the day the field is added rather than on the next recovery run.
    """
    original = _sample_instance(cls)

    restored = from_dict(cls, json.loads(json.dumps(to_dict(original))))

    assert restored == original
