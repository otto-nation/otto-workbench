"""The strict reader: every way an agent's JSON can disagree with its dataclass is named."""

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.serde import HintKind
from core.strict import SchemaRejected, _CHECKS, example_json, load_strict


class Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


@dataclass(frozen=True)
class Leaf:
    name: str
    count: int = 0


@dataclass(frozen=True)
class Tree:
    title: str
    colour: Colour = Colour.RED
    ratio: float = 0.0
    flag: bool = False
    maybe: str | None = None
    leaves: tuple[Leaf, ...] = ()
    tags: list[str] = ()


def _violations(data) -> list[tuple[str, str]]:
    with pytest.raises(SchemaRejected) as exc:
        load_strict(Tree, data)
    return [(v.at, v.detail) for v in exc.value.violations]


def test_a_valid_document_loads_as_the_dataclass():
    tree = load_strict(Tree, {
        "title": "t", "colour": "blue", "ratio": 2, "flag": True, "maybe": None,
        "leaves": [{"name": "a", "count": 3}], "tags": ["x"],
    })
    assert tree == Tree("t", Colour.BLUE, 2, True, None, (Leaf("a", 3),), ["x"])


@pytest.mark.parametrize("data, at, words", [
    ({"title": "t", "colur": "red"}, "$.colur", "is not a field of Tree"),
    ({}, "$.title", "is required"),
    ({"title": 3}, "$.title", "must be a string, not a number"),
    ({"title": "t", "colour": "green"}, "$.colour", '"red" or "blue"'),
    ({"title": "t", "flag": 1}, "$.flag", "must be a boolean, not a number"),
    ({"title": "t", "ratio": True}, "$.ratio", "must be a number, not a boolean"),
    ({"title": None}, "$.title", "must not be null"),
    ({"title": "t", "leaves": {}}, "$.leaves", "must be an array, not an object"),
    ({"title": "t", "leaves": [{"name": "a", "count": "3"}]}, "$.leaves[0].count",
     "must be an integer, not a string"),
    ({"title": "t", "leaves": [{"count": 1}]}, "$.leaves[0].name", "is required"),
    ({"title": "t", "leaves": ["a"]}, "$.leaves[0]", "must be an object, not a string"),
    ([], "$", "must be an object, not an array"),
])
def test_each_disagreement_is_named_by_its_json_path(data, at, words):
    found = _violations(data)
    assert any(a == at and words in d for a, d in found), found


def test_every_violation_is_reported_not_just_the_first():
    found = _violations({"title": 1, "flag": "no", "extra": 0})
    assert {a for a, _ in found} == {"$.title", "$.flag", "$.extra"}


def test_an_integer_field_refuses_a_fraction():
    found = _violations({"title": "t", "leaves": [{"name": "a", "count": 1.5}]})
    assert found == [("$.leaves[0].count", "must be an integer, not a number")]


def test_every_hint_kind_has_a_checker():
    assert set(_CHECKS) == set(HintKind)


@pytest.mark.parametrize("hint_kind", [HintKind.SET, HintKind.DICT, HintKind.OPAQUE, HintKind.FROM_RAW])
def test_a_kind_no_artifact_uses_is_refused_loudly(hint_kind):
    with pytest.raises(TypeError, match="no strict reading"):
        _CHECKS[hint_kind](object, (), {}, "$")


def test_example_json_is_the_serde_form_indented():
    text = example_json(Tree("t", Colour.BLUE, leaves=(Leaf("a"),)))
    assert json.loads(text)["colour"] == "blue"
    assert json.loads(text)["leaves"] == [{"name": "a", "count": 0}]
    assert text.startswith("{\n  ")
