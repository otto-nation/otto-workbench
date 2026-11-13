"""`checked_exit` refuses a code the command did not declare."""

import sys
from pathlib import Path

import pytest

from conftest import command_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402


def test_declared_exit_passes():
    spec = command_spec()
    assert cli.pr.checked_exit(spec, 0) == 0
    assert cli.pr.checked_exit(spec, 1) == 1
    assert cli.pr.checked_exit(spec, 2) == 2


def test_undeclared_exit_raises_under_pytest():
    spec = command_spec()
    with pytest.raises(AssertionError, match="pr probe returned undeclared exit 3"):
        cli.pr.checked_exit(spec, 3)


def test_undeclared_exit_warns_without_pytest(monkeypatch, capsys):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    spec = command_spec()
    assert cli.pr.checked_exit(spec, 3) == 3
    assert "pr probe returned undeclared exit 3" in capsys.readouterr().err


def test_no_contract_skips_the_check():
    spec = command_spec(contract="")
    assert cli.pr.checked_exit(spec, 99) == 99
