"""step_argv(ai=False) and Run.uses_ai for a v1 state file.

Revert: restore --fix for ai=False; add --track-all to the comments argv;
default with_ai to [].
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.steps  # noqa: E402
import core.serde  # noqa: E402
from batch.model import Run, RunStatus, Step  # noqa: E402


def test_ai_false_rebase_and_ci_have_no_fix():
    rebase = batch.steps.step_argv(Step.REBASE, "pr", "/wt", ai=False)
    assert rebase == ["pr", "rebase", "--no-push", "--repo-dir", "/wt"]
    ci = batch.steps.step_argv(Step.CI, "pr", "/wt", remote_sha="abc", ai=False)
    assert ci == ["ci-check", "--no-rebase", "--head-sha", "abc", "--repo-dir", "/wt"]
    assert "--fix" not in rebase and "--fix" not in ci


@pytest.mark.parametrize("step", list(Step))
@pytest.mark.parametrize("ai", [True, False])
@pytest.mark.parametrize("fork_point,wait", [("", False), ("abc", True)])
def test_no_step_argv_carries_track(step, ai, fork_point, wait):
    argv = batch.steps.step_argv(step, "pr", "/wt", remote_sha="h", wait=wait,
                                 fork_point=fork_point, ai=ai)
    assert "--track" not in argv and "--track-all" not in argv


def test_v1_state_without_with_ai_uses_ai_for_every_step():
    run = core.serde.from_dict(Run, {
        "id": "r1", "started_at": "t", "steps": ["rebase"], "pool": 1,
        "auto_publish": [], "status": RunStatus.RUNNING.value,
    })
    assert run.with_ai is None
    assert all(run.uses_ai(s) for s in Step)
