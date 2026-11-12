"""Tests for otto_queue.keys: one key per distinct paid input, the same key for the same one."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.version  # noqa: E402
import otto_queue.keys  # noqa: E402

HEAD, BASE = "a" * 40, "b" * 40
BASE_ARGS = dict(action="review", repo="otto-nation/otto-workbench", pr_number=7, head_sha=HEAD,
                 base_sha=BASE, focus="locks", effort="high", model="claude-x", tool_version="1.0.0")


def _key(**overrides):
    return otto_queue.keys.review_key(**{**BASE_ARGS, **overrides})


def test_the_same_inputs_give_the_same_fixed_length_key():
    assert _key() == _key()
    assert _key().startswith("review:") and len(_key()) == len("review:") + 64


@pytest.mark.parametrize("overrides", [
    {"repo": "Otto-Nation/Otto-Workbench"}, {"head_sha": HEAD.upper()}, {"effort": " HIGH "},
    {"model": " claude-x "}, {"action": "  review "}, {"focus": "  locks\n"},
])
def test_equivalent_spellings_give_the_same_key(overrides):
    assert _key(**overrides) == _key()


def test_absent_and_empty_flags_are_the_same():
    assert _key(focus=None, effort=None, model=None) == _key(focus="", effort="", model="")


@pytest.mark.parametrize("overrides", [
    {"action": "comments"}, {"repo": "otto-nation/other"}, {"pr_number": 8},
    {"head_sha": "c" * 40}, {"base_sha": "c" * 40}, {"focus": "races"}, {"effort": "low"},
    {"model": "grok-x"}, {"tool_version": "1.0.1"},
])
def test_every_input_changes_the_key(overrides):
    assert _key(**overrides) != _key()


def test_the_tool_version_defaults_to_the_running_tools(monkeypatch):
    monkeypatch.setattr(core.version, "tool_version", lambda: "9.9.9")
    assert _key(tool_version=None) == _key(tool_version="9.9.9")


def test_a_sha256_object_name_is_accepted():
    assert _key(head_sha="d" * 64) != _key()


@pytest.mark.parametrize("overrides,match", [
    ({"head_sha": "abc1234"}, "full commit SHA"), ({"base_sha": ""}, "full commit SHA"),
    ({"pr_number": 0}, "positive"), ({"repo": "otto-workbench"}, "owner/repo"),
    ({"action": " "}, "owner/repo"),
])
def test_ambiguous_inputs_are_refused(overrides, match):
    with pytest.raises(ValueError, match=match):
        _key(**overrides)
