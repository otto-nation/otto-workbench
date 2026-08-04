"""Tests for schema_gen — JSON Schema generation from dataclasses."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"))

from schema_gen import dataclass_to_schema


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
    assert nickname_schema["type"] == "string"
    assert nickname_schema["nullable"] is True


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
    from pr_state import (
        CIDomain,
        CommentsSummary,
        PRIdentity,
        PRState,
        RebaseSummary,
        ReviewSummary,
    )

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
