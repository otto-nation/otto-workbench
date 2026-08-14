"""Tests for generic serialization/deserialization."""

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import get_args, get_origin, get_type_hints
from unittest.mock import MagicMock

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from pr_state import PRState
from review_preflight import PipelineState
from serde import from_dict, load_file, to_dict, write_json


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


@dataclass
class Scalars:
    """One field per scalar hint, so a mismatch can be aimed at each."""
    count: int = 0
    ratio: float = 0.0
    name: str = ""
    enabled: bool = False


@dataclass
class Bare:
    """Container hints with no element type — shape is all there is to check."""
    mapping: dict = field(default_factory=dict)
    sequence: list = field(default_factory=list)


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


class TestWrongTypedValue:
    """A value that does not match its hint is restored or omitted, never kept.

    Passing one through leaves it behind a type hint that says otherwise, and
    the reader that trips over it does so three call frames from the file that
    caused it — the same defect the null rule closes, for non-null values.
    """

    @pytest.mark.parametrize(("raw", "expected"), [
        ("3", 3),
        (3.0, 3),
    ], ids=["str-digits", "whole-float"])
    def test_a_recoverable_int_is_restored(self, raw, expected):
        assert from_dict(Scalars, {"count": raw}).count == expected

    @pytest.mark.parametrize("raw", ["many", "3.7", 3.7, [], {}], ids=[
        "word", "fractional-str", "fractional-float", "list", "dict",
    ])
    def test_an_unrecoverable_int_falls_back_to_the_default(self, raw):
        assert from_dict(Scalars, {"count": raw}).count == 0

    def test_an_int_is_restored_for_a_float_hint(self):
        obj = from_dict(Scalars, {"ratio": 3})
        assert obj.ratio == 3.0
        assert isinstance(obj.ratio, float)

    def test_a_number_is_restored_for_a_str_hint(self):
        assert from_dict(Scalars, {"name": 3}).name == "3"

    def test_a_container_never_becomes_a_str(self):
        """`str({"a": 1})` succeeds, which is the whole reason to refuse it —
        it yields a corrupt field wearing a valid type."""
        assert from_dict(Scalars, {"name": {"a": 1}}).name == ""

    @pytest.mark.parametrize(("fname", "raw"), [
        ("count", True),
        ("ratio", True),
        ("name", True),
    ], ids=["int", "float", "str"])
    def test_a_bool_satisfies_no_other_scalar_hint(self, fname, raw):
        """`isinstance(True, int)` is True and `str(True)` is "True" where JSON
        wrote "true" — both would record a wrong value as a confident one."""
        assert getattr(from_dict(Scalars, {fname: raw}), fname) == getattr(Scalars(), fname)

    @pytest.mark.parametrize("raw", ["true", 1, "", 0], ids=["str", "int", "empty-str", "zero"])
    def test_only_a_real_bool_satisfies_a_bool_hint(self, raw):
        """`bool("false")` is True, so truthiness is not a recovery."""
        assert from_dict(Scalars, {"enabled": raw}).enabled is False

    def test_a_real_bool_still_round_trips(self):
        assert from_dict(Scalars, {"enabled": True}).enabled is True

    @pytest.mark.parametrize(("fname", "raw"), [
        ("mapping", "x"),
        ("sequence", "abc"),
    ], ids=["dict-hint", "list-hint"])
    def test_a_bare_container_hint_rejects_a_wrong_shape(self, fname, raw):
        """A str is iterable, so passing one through a bare `list` hint yields
        a field that loops over characters instead of failing."""
        assert getattr(from_dict(Bare, {fname: raw}), fname) == getattr(Bare(), fname)

    def test_a_non_dict_for_a_nested_dataclass_falls_back_to_the_default(self):
        """Otherwise the field holds a bare list and the first attribute access
        on it raises AttributeError, far from the file that caused it."""
        assert from_dict(Outer, {"inner": []}).inner == Inner()

    def test_a_wrong_typed_value_with_no_default_still_raises(self):
        with pytest.raises(TypeError):
            from_dict(Required, {"name": {"a": 1}})


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


class TestWriteJson:
    """The one atomic write every state file goes through."""

    def test_it_round_trips_through_load_file(self, tmp_path):
        path = tmp_path / "inner.json"

        write_json(path, to_dict(Inner(name="x", color=Color.BLUE)))

        assert load_file(Inner, path) == Inner(name="x", color=Color.BLUE)

    def test_it_creates_parent_directories(self, tmp_path):
        path = tmp_path / "a" / "b" / "state.json"

        write_json(path, {"k": "v"})

        assert json.loads(path.read_text()) == {"k": "v"}

    def test_it_does_not_coerce_a_path_from_what_it_is_handed(self, tmp_path, monkeypatch):
        """A `MagicMock` satisfies `os.PathLike`, so a `Path(path)` here would
        turn a test's stubbed state directory into real directories under the
        working directory — `ci_check` reaches this through a mocked context and
        swallows the failure, so the only symptom was junk in the repo."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(Exception):
            write_json(MagicMock() / "state.json", {"k": "v"})

        assert list(tmp_path.iterdir()) == []

    def test_a_failed_write_leaves_the_previous_file_intact(self, tmp_path):
        """The defect that motivates the temp file: `open(path, "w")` truncates
        before the first byte is written, so a value that cannot be encoded — or
        a crash partway through — destroys the state that was already there."""
        path = tmp_path / "state.json"
        write_json(path, {"good": True})

        with pytest.raises(TypeError):
            write_json(path, {"bad": object()})

        assert json.loads(path.read_text()) == {"good": True}

    def test_a_failed_write_leaves_no_temp_file_behind(self, tmp_path):
        path = tmp_path / "state.json"

        with pytest.raises(TypeError):
            write_json(path, {"bad": object()})

        assert list(tmp_path.iterdir()) == []

    def test_a_successful_write_leaves_no_temp_file_behind(self, tmp_path):
        path = tmp_path / "state.json"

        write_json(path, {"k": "v"})

        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_a_reader_never_observes_a_partial_file(self, tmp_path, monkeypatch):
        """A reader running between the truncate and the last byte is what makes
        a non-atomic write corrupt a state file. Standing in for that reader at
        the one moment it matters — mid-serialization — the destination still
        holds the whole previous document."""
        path = tmp_path / "state.json"
        write_json(path, {"generation": 1})
        seen = []

        class Peeking(dict):
            def items(self):
                seen.append(json.loads(path.read_text()))
                return super().items()

        write_json(path, Peeking({"generation": 2}))

        assert seen == [{"generation": 1}]
        assert json.loads(path.read_text()) == {"generation": 2}


def test_serde_owns_the_only_atomic_rename():
    """No second copy of the write-temp-then-rename dance.

    Four hand-rolled copies is what this replaced, and one of them had drifted
    into not being atomic at all. A fifth starts the same way: `os.replace` in
    a module that is not this one.
    """
    ai = Path(__file__).resolve().parent.parent / "ai"
    # The extensionless `ai/claude/bin/*` commands are Python too, so match on
    # the shebang rather than the suffix — a copy lands wherever it is written.
    sources = (
        p for p in ai.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    offenders = sorted(
        p.relative_to(ai).as_posix()
        for p in sources
        for text in [p.read_text(errors="ignore")]
        if (p.suffix == ".py" or text.startswith("#!/usr/bin/env python"))
        and "os.replace(" in text
    )

    assert offenders == ["lib/serde.py"]


def test_load_file_returns_none_when_a_required_field_is_absent(tmp_path):
    """PRState.identity has no default, so serde raises TypeError from
    cls(**kwargs) — the case the old exception tuple had to be checked for."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"created_at": "2026-08-12T00:00:00+00:00"}))

    assert load_file(PRState, path) is None
