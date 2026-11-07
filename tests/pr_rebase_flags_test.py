"""Parser checks for `pr rebase --stop-on-conflict`."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402


def test_fix_with_stop_on_conflict_is_parser_error():
    with pytest.raises(SystemExit) as exc:
        cli.pr_rebase._parse_args(["--fix", "--stop-on-conflict"])
    assert exc.value.code == 2
