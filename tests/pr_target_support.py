"""Saved `pr` state for one target, for the target report, session and note tests."""

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import commit_all, git_in, init_repo  # noqa: E402
import pr.ci_failures  # noqa: E402
import pr.state  # noqa: E402
import pr.target  # noqa: E402

CI_SHA = "c0ffee1234567890c0ffee1234567890c0ffee12"


def state_with(*, items=(), jobs=(), closeout=False, pr_number=7, branch="feat/x"):
    """A PRState for o/r#*pr_number* with *items* outcomes, failing *jobs*, and closeout owed or not."""
    state = pr.state.PRState(identity=pr.state.PRIdentity("o/r", branch, pr_number, "h", ""))
    state.fix.fix.items = list(items)
    if jobs:
        groups = {j: pr.ci_failures.FailureGroup(j, pr.ci_failures.FailureKind.TEST, ()) for j in jobs}
        state.ci.runs = {5: pr.ci_failures.RunState(5, 1, CI_SHA, "completed", "failure", "", groups)}
        state.ci.latest_run_id = 5
    state.fix.summary_deferred = closeout
    return state


@dataclass(frozen=True)
class Target:
    repo: Path
    target_dir: Path
    branch: str


def make_target(tmp_path, **state_kw) -> Target:
    """A checkout of feat/x with a GitHub origin, and *state_kw*'s state saved in its target."""
    repo = init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("a\n")
    commit_all(repo, "init")
    git_in(repo, "remote", "add", "origin", "git@github.com:o/r.git")
    git_in(repo, "switch", "-q", "-c", "feat/x")
    target = pr.target.target_dir(pr.target.repo_key_from_origin(str(repo)), "feat/x")
    pr.state.save_state(target, state_with(**state_kw))
    return Target(repo, target, "feat/x")
