"""Tests for gh.shell_lex, the shell tokenizer the owner boundary builds on."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh.shell_lex


def test_segments_record_which_statement_pipes_into_the_next():
    found = gh.shell_lex.segments("a | b; c |& d && e")
    assert [segment.words for segment in found] == [("a",), ("b",), ("c",), ("d",), ("e",)]
    assert [segment.pipes_out for segment in found] == [True, False, True, False, False]


def test_split_statements_is_segments_without_the_links():
    command = "cd x && gh pr comment 1 --body 'a; b' | cat\nls"
    assert gh.shell_lex.split_statements(command) == [
        list(segment.words) for segment in gh.shell_lex.segments(command)
    ]
    assert gh.shell_lex.split_statements(command) == [
        ["cd", "x"], ["gh", "pr", "comment", "1", "--body", "a; b"], ["cat"], ["ls"],
    ]
