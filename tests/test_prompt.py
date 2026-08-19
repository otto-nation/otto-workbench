"""Tests for the shared prompt helpers."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import prompt


def test_confirm_accepts_bare_enter_as_yes():
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value=""):
        assert prompt.confirm("Proceed?") is True


def test_confirm_accepts_y():
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="Y"):
        assert prompt.confirm("Proceed?") is True


def test_confirm_rejects_n():
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"):
        assert prompt.confirm("Proceed?") is False


def test_confirm_is_false_when_there_is_no_tty():
    """No terminal and no /dev/tty means no answer, which is not consent."""
    with patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.open", side_effect=OSError):
        assert prompt.confirm("Proceed?") is False


def test_confirm_is_false_when_input_raises_eof_at_a_tty():
    """Ctrl-D at a real prompt is not consent either, and never falls through to /dev/tty."""
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=EOFError):
        assert prompt.confirm("Proceed?") is False


def test_ask_returns_the_typed_answer():
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="github"):
        assert prompt.ask("Provider: ") == "github"


def test_ask_is_empty_when_there_is_no_tty():
    with patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.open", side_effect=OSError):
        assert prompt.ask("Provider: ") == ""


def test_ask_is_empty_when_input_raises_eof_at_a_tty():
    """Ctrl-D at a real prompt is the empty answer, and never falls through to /dev/tty."""
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=EOFError):
        assert prompt.ask("Provider: ") == ""


def test_interactive_is_false_without_a_tty():
    with patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.open", side_effect=OSError):
        assert prompt.interactive() is False
