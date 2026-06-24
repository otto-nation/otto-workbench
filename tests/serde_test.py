"""Tests for generic serialization/deserialization."""

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"
sys.path.insert(0, str(LIB_DIR))

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
