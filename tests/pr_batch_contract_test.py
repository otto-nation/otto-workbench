"""`pr batch` contract: drafted by default; auto-publish is a fast-forward."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_batch_help_keeps_the_docstring():
    text = cli.pr.help_parser("batch").format_help()
    assert "A default run never opens a decision" in text


def test_run_auto_publish_is_a_fast_forward_never_a_force_push():
    effects = {e.trigger: e for e in cli.pr_batch.CONTRACT.remote_effects}
    effect = effects["run --auto-publish"]
    text = " ".join((effect.writes, effect.how, effect.on_refusal))
    assert "fast-forward" in text
    assert "never a force push" in text.lower()


def test_run_no_verify_is_described():
    effects = {e.trigger: e for e in cli.pr_batch.CONTRACT.remote_effects}
    assert "run --no-verify" in effects
    assert "pre-push" in effects["run --no-verify"].how


def test_batch_exit_codes():
    assert [int(o.code) for o in cli.pr_batch.CONTRACT.exits] == [0, 1, 2]
    assert cli.pr_batch.CONTRACT.outcome(ExitCode.TEMPFAIL) is None
