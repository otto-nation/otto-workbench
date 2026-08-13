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
from serde import from_dict, load_file, to_dict


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

    def test_a_null_direct_field_yields_the_field_default_not_the_hook(self):
        """`_from_raw` has no null form of its own: `Tagged._from_raw(None)`
        would happily coerce it to `Tagged(value="None")` via `str(raw)`, a
        wrong value with no warning. `None` must be routed to the field
        default before it reaches the hook, exactly like a missing key."""
        obj = from_dict(Keyed, {"tagged": None})
        assert obj.tagged == Tagged()

    def test_a_null_under_a_dict_hint_yields_the_field_default_not_the_hook(self):
        """Same guard, reached through `dict[int, Tagged]` instead of a bare
        field — a null value must drop the whole field to its default rather
        than calling the hook on `None`."""
        obj = from_dict(Keyed, {"tags": {"1": None}})
        assert obj.tags == {}


class TestNullOnAField:
    """`None` on a field means "value omitted", not "field is None".

    Nothing in the schema has a null state of its own, so an explicit `null`
    written for a field — e.g. a hand-edited state file, or a domain reset to
    its default — must reconstruct the same way a missing key does: default
    fields fall back to their defaults, required fields still raise.
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

    def test_null_on_a_str_field_yields_the_default(self):
        obj = from_dict(Inner, {"name": None})
        assert obj.name == ""

    def test_null_on_an_enum_field_yields_the_default_not_a_value_error(self):
        obj = from_dict(Inner, {"color": None})
        assert obj.color == Color.RED

    def test_null_on_a_list_field_yields_the_default(self):
        obj = from_dict(Outer, {"items": None})
        assert obj.items == []

    def test_null_on_a_dict_field_yields_the_default(self):
        obj = from_dict(Container, {"entries": None})
        assert obj.entries == {}

    def test_null_as_a_list_element_drops_the_whole_field_to_its_default(self):
        """A `null` inside a collection propagates: the field has no way to
        hold "some real values, one hole" against its type hint, so it falls
        back the same way a top-level `null` does, rather than keeping `None`
        alongside the real entries.
        """
        obj = from_dict(Outer, {"items": ["a", None]})
        assert obj.items == []

    def test_null_on_a_field_with_no_default_still_raises(self):
        with pytest.raises(TypeError):
            from_dict(Required, {"name": None})


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


# ── The load guard ───────────────────────────────────────────────────────────

# Every persisted root must survive an unreadable file the same way. Derived
# from PERSISTED_ROOTS rather than listed, so a new state file inherits this.


@pytest.mark.parametrize("cls", PERSISTED_ROOTS, ids=lambda c: c.__name__)
def test_load_file_returns_none_for_a_truncated_file(cls, tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text('{"head_sha": "abc"')

    assert load_file(cls, path) is None
    assert "unreadable" in capsys.readouterr().err


@pytest.mark.parametrize("cls", PERSISTED_ROOTS, ids=lambda c: c.__name__)
def test_load_file_returns_none_for_a_missing_file(cls, tmp_path, capsys):
    """A first run is not a fault, so a missing file must not warn."""
    assert load_file(cls, tmp_path / "state.json") is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("cls", PERSISTED_ROOTS, ids=lambda c: c.__name__)
def test_load_file_returns_none_for_a_directory(cls, tmp_path):
    """A directory where the file belongs is not a usable file."""
    (tmp_path / "state.json").mkdir()

    assert load_file(cls, tmp_path / "state.json") is None


@pytest.mark.parametrize("cls", PERSISTED_ROOTS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("raw", ["[1, 2, 3]", '"hello"'], ids=["list", "string"])
def test_load_file_returns_none_for_a_non_dict_top_level_value(cls, raw, tmp_path, capsys):
    """A top-level JSON value that is valid but not an object used to fall
    through `from_dict`'s old `if not data: data = {}` guard and reconstruct
    a silent, fully-defaulted instance instead of being discarded — every
    field on these roots has a default, so nothing raised. That turns real
    corruption into a clean-looking empty state instead of a warned discard.
    """
    path = tmp_path / "state.json"
    path.write_text(raw)

    assert load_file(cls, path) is None
    assert "unreadable" in capsys.readouterr().err


def test_load_file_reconstructs_a_dataclass(tmp_path):
    path = tmp_path / "inner.json"
    path.write_text(json.dumps({"name": "x", "color": "blue"}))

    assert load_file(Inner, path) == Inner(name="x", color=Color.BLUE)


def test_load_file_returns_none_for_an_unknown_enum_value(tmp_path):
    path = tmp_path / "inner.json"
    path.write_text(json.dumps({"color": "chartreuse"}))

    assert load_file(Inner, path) is None


def test_load_file_recovers_a_null_behind_a_scalar_hint(tmp_path, capsys):
    """The reported crash: a `null` written for an int-typed field must not
    smuggle a `None` past the type hint and into a reader that does arithmetic
    on it — it degrades to the field's default, the same as a missing key,
    and is not a fault worth warning about."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "identity": {
            "repo": "owner/repo",
            "branch": "feat",
            "pr_number": 42,
            "head_sha": "abc",
            "worktree_root": str(tmp_path),
        },
        "ci": {"conclusion": "failure", "failure_count": None},
    }))

    state = load_file(PRState, path)

    assert state is not None
    assert state.ci.failure_count == 0
    assert capsys.readouterr().err == ""


def test_load_file_returns_none_when_a_required_field_is_absent(tmp_path):
    """PRState.identity has no default, so serde raises TypeError from
    cls(**kwargs) — the case the old exception tuple had to be checked for."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"created_at": "2026-08-12T00:00:00+00:00"}))

    assert load_file(PRState, path) is None
