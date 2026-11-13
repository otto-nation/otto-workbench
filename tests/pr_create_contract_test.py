"""`pr create`'s contract: it pushes, then opens a PR."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_create  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_create_says_it_pushes_and_opens_a_pr():
    effects = {e.trigger: e for e in cli.pr_create.CONTRACT.remote_effects}
    assert "--set-upstream" in effects[""].how and "gh pr create" in effects[""].how


def test_create_documents_the_existing_pr_refusal_and_the_gate():
    text = " ".join(o.meaning for o in cli.pr_create.CONTRACT.exits)
    assert "already exists" in text and "self-review" in text


def test_create_exit_codes():
    assert [int(o.code) for o in cli.pr_create.CONTRACT.exits] == [0, 1, 2, 75]
    assert cli.pr_create.CONTRACT.exits[0].code is ExitCode.OK


def test_exit_one_declares_no_unconditional_next_steps():
    failure = next(o for o in cli.pr_create.CONTRACT.exits if o.code is ExitCode.FAILURE)
    assert not failure.next_steps
