"""`pr batch` contract: drafted by default; auto-publish is a fast-forward."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


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


@pytest.mark.parametrize("argv", [
    ["batch", "resume"],
    ["batch", "session", "r1"],
    ["batch", "note", "r1", "1", "--finding", "ci_failing:ci",
     "--verdict", "deferred", "--reason", "x"],
    ["batch", "release", "r1", "1"],
])
def test_dropped_batch_subcommands_fail_usage(argv, capsys):
    assert _main(argv) == 2
    err = capsys.readouterr().err.lower()
    assert "invalid choice" in err or "unrecognized" in err or "invalid" in err


def test_contract_does_not_list_session_note_release_or_resume():
    flags = " ".join(f for g in cli.pr_batch.CONTRACT.groups for f in g.flags)
    titles = " ".join(g.title for g in cli.pr_batch.CONTRACT.groups)
    examples = " ".join(e.command for e in cli.pr_batch.CONTRACT.examples)
    blob = f"{flags}\n{titles}\n{examples}"
    for word in ("resume", "session", "note", "release", "with-review"):
        assert word not in blob, blob
