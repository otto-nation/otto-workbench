"""`pr describe`'s contract: --post edits the PR via gh pr edit."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.pr_describe  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_describe_help_names_the_command_as_typed():
    assert cli.pr.help_parser("describe").prog == "pr describe"


def test_describe_post_edits_via_gh_pr_edit():
    effects = {e.trigger: e for e in cli.pr_describe.CONTRACT.remote_effects}
    assert "gh pr edit" in effects["--post"].how


def test_describe_exit_codes():
    assert [int(o.code) for o in cli.pr_describe.CONTRACT.exits] == [0, 1, 2, 75]
    assert cli.pr_describe.CONTRACT.exits[0].code is ExitCode.OK
