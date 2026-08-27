"""Tests for schema_gen — JSON Schema generation from dataclasses."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import schema_gen
import serde
from schema_gen import dataclass_to_schema
from serde import HintKind


# ── Test fixtures ──────────────────────────────────────────────────────────


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


@dataclass
class Address:
    street: str
    city: str
    zip_code: str = ""


@dataclass
class Person:
    name: str
    age: int
    address: Address
    email: str = ""
    score: float = 0.0
    active: bool = True
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, int] = field(default_factory=dict)
    favorite_color: Color = Color.RED
    nickname: str | None = None


@dataclass
class Team:
    name: str
    members: list[Person] = field(default_factory=list)


@dataclass
class Palette:
    swatches: dict[Color, str] = field(default_factory=dict)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_primitives():
    @dataclass
    class Simple:
        s: str
        i: int
        f: float
        b: bool

    schema = dataclass_to_schema(Simple)
    assert schema["properties"]["s"] == {"type": "string"}
    assert schema["properties"]["i"] == {"type": "integer"}
    assert schema["properties"]["f"] == {"type": "number"}
    assert schema["properties"]["b"] == {"type": "boolean"}
    assert set(schema["required"]) == {"s", "i", "f", "b"}


def test_required_vs_optional():
    schema = dataclass_to_schema(Person)
    assert "name" in schema["required"]
    assert "age" in schema["required"]
    assert "address" in schema["required"]
    assert "email" not in schema["required"]
    assert "tags" not in schema["required"]


def test_enum():
    schema = dataclass_to_schema(Person)
    color_schema = schema["properties"]["favorite_color"]
    assert color_schema == {"type": "string", "enum": ["red", "green", "blue"]}


def test_nullable():
    schema = dataclass_to_schema(Person)
    nickname_schema = schema["properties"]["nickname"]
    assert nickname_schema == {"oneOf": [{"type": "string"}, {"type": "null"}]}


def test_nested_dataclass():
    schema = dataclass_to_schema(Person)
    addr = schema["properties"]["address"]
    assert addr["type"] == "object"
    assert "street" in addr["properties"]
    assert "city" in addr["properties"]


def test_list():
    schema = dataclass_to_schema(Person)
    tags = schema["properties"]["tags"]
    assert tags == {"type": "array", "items": {"type": "string"}}


def test_dict():
    schema = dataclass_to_schema(Person)
    meta = schema["properties"]["metadata"]
    assert meta == {"type": "object", "additionalProperties": {"type": "integer"}}


def test_enum_keyed_dict_constrains_property_names():
    """An enum key type is a closed set, so the schema names which keys exist."""
    swatches = dataclass_to_schema(Palette)["properties"]["swatches"]
    assert swatches["propertyNames"] == {"enum": ["red", "green", "blue"]}
    assert swatches["additionalProperties"] == {"type": "string"}


def test_str_keyed_dict_has_no_property_names():
    schema = dataclass_to_schema(Person)
    assert "propertyNames" not in schema["properties"]["metadata"]


def test_list_of_dataclasses():
    schema = dataclass_to_schema(Team)
    members = schema["properties"]["members"]
    assert members["type"] == "array"
    assert members["items"]["type"] == "object"
    assert "name" in members["items"]["properties"]


def test_not_a_dataclass():
    with pytest.raises(TypeError, match="is not a dataclass"):
        dataclass_to_schema(str)


def test_pr_state_models():
    """Verify schema generation works on the actual pr_state dataclasses."""
    from pr_domains import (
        CIDomain,
        CommentsSummary,
        RebaseSummary,
        ReviewSummary,
    )
    from pr_state import PRIdentity, PRState

    for cls in [PRIdentity, CIDomain, ReviewSummary, CommentsSummary, RebaseSummary, PRState]:
        schema = dataclass_to_schema(cls)
        assert schema["type"] == "object"
        assert "properties" in schema


def test_pr_state_structure():
    """PRState schema should reference nested domain schemas."""
    from pr_state import PRState

    schema = dataclass_to_schema(PRState)
    assert schema["properties"]["ci"]["type"] == "object"
    assert "failure_count" in schema["properties"]["ci"]["properties"]
    assert schema["properties"]["review"]["type"] == "object"
    assert schema["properties"]["rebase"]["type"] == "object"


def test_pr_state_runs_schema_describes_run_history():
    """`pr --tool-schema` publishes PRState as the pr CLI's output contract.
    A bare `dict` hint on CIDomain.runs made this an empty object, so the
    contract said nothing at all about run history."""
    from pr_state import PRState

    runs = dataclass_to_schema(PRState)["properties"]["ci"]["properties"]["runs"]
    run = runs["additionalProperties"]
    assert run["properties"]["run_id"] == {"type": "integer"}
    assert "failures" in run["properties"]


def test_bare_dict():
    @dataclass
    class WithBareDict:
        data: dict = field(default_factory=dict)

    schema = dataclass_to_schema(WithBareDict)
    assert schema["properties"]["data"] == {"type": "object"}


def test_tuple():
    @dataclass
    class WithTuple:
        items: tuple[int, ...] = field(default_factory=tuple)

    schema = dataclass_to_schema(WithTuple)
    assert schema["properties"]["items"] == {"type": "array", "items": {"type": "integer"}}


def test_set_says_its_elements_are_unique():
    """`serde` writes a set as an array, so the schema does too — plus the one
    thing an array does not say, which is the whole reason the field is a set."""
    @dataclass
    class WithSet:
        phases: set[Color] = field(default_factory=set)

    assert dataclass_to_schema(WithSet)["properties"]["phases"] == {
        "type": "array",
        "items": {"type": "string", "enum": ["red", "green", "blue"]},
        "uniqueItems": True,
    }


def test_bare_list():
    """A bare `list` states its shape and nothing about its elements — which is
    also all `serde` checks for one. It used to fall through to an open schema
    that did not even say "array"."""
    @dataclass
    class WithBareList:
        items: list = field(default_factory=list)

    schema = dataclass_to_schema(WithBareList)
    assert schema["properties"]["items"] == {"type": "array"}


# ── Parity with serde ──────────────────────────────────────────────────────
#
# The defect class: `serde._coerce` learns to read a shape and the schema
# published to a model keeps describing the old one. Both modules dispatch on
# `serde.classify`, so the guard is that neither table has a hole — walked from
# the enum's own members, so a new kind is covered without editing anything here.


@pytest.mark.parametrize("kind", list(HintKind), ids=lambda k: k.value)
def test_both_walks_handle_every_hint_kind(kind):
    assert kind in serde._COERCERS, f"serde._coerce cannot read a {kind.value} hint"
    assert kind in schema_gen._EMITTERS, f"schema_gen cannot describe a {kind.value} hint"


def test_dict_with_int_keys_says_the_keys_must_parse_as_integers():
    """JSON stringifies every key and `serde` restores `dict[int, V]` with
    `int()` — a key that will not parse makes the whole file unreadable. The
    schema used to say only `additionalProperties`, so a model reading the
    contract had nothing telling it the keys were numeric."""
    @dataclass
    class WithIntKeys:
        counts: dict[int, str] = field(default_factory=dict)

    counts = dataclass_to_schema(WithIntKeys)["properties"]["counts"]
    assert counts == {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "propertyNames": {"pattern": r"^-?[0-9]+$"},
    }


def test_dict_with_str_keys_is_left_unconstrained():
    schema = dataclass_to_schema(Person)
    assert "propertyNames" not in schema["properties"]["metadata"]


class Priority(Enum):
    LOW = 1
    HIGH = 2


@dataclass
class WithIntEnum:
    priority: Priority = Priority.LOW


def test_an_int_valued_enum_is_not_described_as_a_string():
    """`serde` rebuilds an enum by calling it with the written value, whatever
    type that is. The schema used to claim `string` for every enum."""
    assert dataclass_to_schema(WithIntEnum)["properties"]["priority"] == {
        "type": "integer", "enum": [1, 2],
    }


# ── The `_from_raw` contract ───────────────────────────────────────────────


@dataclass
class Tagged:
    """A type stored in more than one shape, which owns both its reconstruction
    and the schema for what that accepts."""
    value: str = ""

    @classmethod
    def _from_raw(cls, raw):
        return cls(value=str(raw)) if not isinstance(raw, dict) else cls(**raw)

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        return {"oneOf": [object_schema, {"type": "string"}]}


@dataclass
class Untagged:
    """`_from_raw` with no published schema — an out-of-tree type."""
    value: str = ""

    @classmethod
    def _from_raw(cls, raw):
        return cls(value=str(raw))


@dataclass
class TaggedHolder:
    tagged: Tagged = field(default_factory=Tagged)


@dataclass
class UntaggedHolder:
    untagged: Untagged = field(default_factory=Untagged)


def test_a_from_raw_type_publishes_the_shapes_it_accepts():
    tagged = dataclass_to_schema(TaggedHolder)["properties"]["tagged"]
    assert tagged == {
        "oneOf": [
            {"type": "object", "properties": {"value": {"type": "string"}}},
            {"type": "string"},
        ],
    }


def test_a_from_raw_type_without_a_schema_hook_is_left_open():
    """Open, not the object form. `serde` hands the whole field to the hook, so
    an object schema would call a document invalid that the reader accepts —
    the failure mode is a model told to write a shape that is not the only
    legal one, which is worse than being told nothing."""
    assert dataclass_to_schema(UntaggedHolder)["properties"]["untagged"] == {}


def test_every_from_raw_type_in_the_tree_publishes_a_raw_schema():
    """Discovered by parsing, not listed: a class that teaches `serde` to read
    an extra shape has to teach `schema_gen` to describe it in the same edit.

    The AST is enough to find them and avoids importing every module in the
    tree just to ask.
    """
    lib = Path(__file__).resolve().parent.parent / "ai" / "lib"
    classes = [
        (source.name, node)
        for source in sorted(lib.glob("*.py"))
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.ClassDef)
    ]
    methods = {
        f"{filename}:{node.name}": {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
        for filename, node in classes
    }

    hooks = {where: names for where, names in methods.items() if "_from_raw" in names}
    missing = sorted(where for where, names in hooks.items() if "_raw_schema" not in names)

    # A scan that finds nothing passes for the wrong reason.
    assert hooks, "no _from_raw classes found — the scan has stopped looking"
    assert missing == []


def test_fix_summary_schema_accepts_the_pre_fold_shape_serde_accepts():
    """The live case. `pr --tool-schema` publishes PRState, and the comment
    domain's `_from_raw` still reads the shape that predates the fold into
    `FixRecord` — a top-level `threads` list of thread outcomes beside a
    top-level `commit_status`. The published schema described only the current
    names, so it called a state file invalid that `_from_raw` reads without
    complaint."""
    from pr_state import PRState

    fix = dataclass_to_schema(PRState)["properties"]["fix"]["properties"]
    assert fix["commit_sha"] == {"type": "string"}
    assert fix["head_sha"] == {"type": "string"}
    # A plain string rather than the record's enum — an unrun pass wrote "".
    assert fix["commit_status"] == {"type": "string"}

    outcome = fix["threads"]["items"]["properties"]
    assert outcome["thread_id"] == {"type": "string"}
    assert outcome["id"] == {"type": "string"}
    assert outcome["reviewer"] == {"type": "string"}
    assert outcome["action"] == outcome["outcome"]


def test_a_diagnosis_schema_accepts_the_legacy_string_form():
    """Both gaps in one real field: `PipelineState.groups_failed` is a
    `dict[int, Diagnosis]`, so its keys must parse as integers and its values
    are whatever `Diagnosis._from_raw` reads — an object or the rendered string
    an older run wrote."""
    from review_state import PipelineState

    failed = dataclass_to_schema(PipelineState)["properties"]["groups_failed"]
    assert failed["propertyNames"] == {"pattern": r"^-?[0-9]+$"}

    object_form, string_form = failed["additionalProperties"]["oneOf"]
    assert string_form == {"type": "string"}
    assert object_form["properties"]["kind"]["type"] == "string"
