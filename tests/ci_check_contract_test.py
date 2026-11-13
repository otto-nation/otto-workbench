"""`pr ci`'s contract: --fix --post force-pushes a rebase then the fix commit."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import cli.pr  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_ci_help_names_the_command_as_typed():
    assert cli.pr.help_parser("ci").prog == "pr ci"


def test_ci_fix_post_force_pushes_then_pushes_the_fix():
    effects = {e.trigger: e for e in cli.ci_check.CONTRACT.remote_effects}
    how = effects["--fix --post"].how
    assert "--force-with-lease" in how and "--no-rebase" in how


def test_ci_no_verify_skips_the_verify_gate_not_the_pre_push_hook():
    effects = {e.trigger: e for e in cli.ci_check.CONTRACT.remote_effects}
    how = effects["--no-verify"].how
    assert "verify gate" in how
    assert "not git's pre-push hook" in how


def test_ci_exit_codes():
    assert [int(o.code) for o in cli.ci_check.CONTRACT.exits] == [0, 1, 2, 75]
    assert cli.ci_check.CONTRACT.exits[0].code is ExitCode.OK
