"""`pr board` and `pr stack` contracts: JSON documents, no remote writes."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_board  # noqa: E402
import cli.pr_stack  # noqa: E402
from board.model import SCHEMA_VERSIONS  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_board_exit_2_names_schema_version():
    meaning = cli.pr_board.CONTRACT.outcome(ExitCode.USAGE).meaning
    assert "--schema-version" in meaning


def test_board_examples_all_pass_schema_version():
    assert cli.pr_board.CONTRACT.examples
    for example in cli.pr_board.CONTRACT.examples:
        assert "--schema-version" in example.command


def test_board_summary_is_built_from_schema_versions():
    for version in SCHEMA_VERSIONS:
        assert str(version) in cli.pr_board.CONTRACT.summary


def test_stack_exit_codes():
    assert [int(o.code) for o in cli.pr_stack.CONTRACT.exits] == [0, 1, 2]
