"""`pr fix`'s contract: --post publishes review, CI, and the PR description."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.pr_commands  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_fix_help_prints_post_and_describe(capsys):
    assert cli.pr.main(["fix", "--help"], bin_dir=REPO_ROOT / "ai" / "bin") == 0
    out = capsys.readouterr().out
    assert "--post" in out
    assert "describe" in out


def test_fix_exit_codes():
    assert [int(o.code) for o in cli.pr_commands.FIX_CONTRACT.exits] == [
        0, 1, 2, 4, 75]
    assert cli.pr_commands.FIX_CONTRACT.exits[0].code is ExitCode.OK
    assert cli.pr_commands.FIX_CONTRACT.exits[3].code is ExitCode.REFUSED


def test_post_effect_names_review_ci_and_description():
    effects = {e.trigger: e for e in cli.pr_commands.FIX_CONTRACT.remote_effects}
    effect = effects["--post"]
    text = " ".join((effect.writes, effect.how, effect.on_refusal))
    assert "review fix commit" in text
    assert "CI fix" in text
    assert "PR description" in text
