"""The line `git.push.push` prints before git runs: what is about to happen."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.push  # noqa: E402

from conftest import git_in  # noqa: E402
# `pushable` is a fixture: imported so pytest finds it here, never called by name.
from push_support import _commit, pushable  # noqa: E402,F401


def test_a_leased_push_names_both_tips_the_lease_and_the_count(pushable):
    wt, _ = pushable
    remote_tip = git.client.head_sha(cwd=wt)
    _commit(wt, "one")
    _commit(wt, "two")
    local = git.client.head_sha(cwd=wt)
    args = (f"--force-with-lease=refs/heads/main:{remote_tip}",)
    line = git.push.plan_line(wt, sha=local, branch="main", remote="origin", args=args)
    a = git.client.abbrev
    assert line == (f"Will force-push main to origin: remote {a(remote_tip)} -> local "
                    f"{a(local)}, lease on {a(remote_tip)}, 2 commits, pre-push hook runs")


def test_a_plain_push_says_push_and_a_skipped_hook_says_so(pushable):
    wt, _ = pushable
    _commit(wt, "one")
    local = git.client.head_sha(cwd=wt)
    line = git.push.plan_line(wt, sha=local, branch="main", remote="origin",
                              args=("--no-verify",))
    assert line.startswith("Will push main to origin: remote ")
    assert line.endswith("1 commit, pre-push hook skipped (--no-verify)")
    assert "lease" not in line


def test_a_new_branch_has_no_remote_tip(pushable):
    wt, _ = pushable
    git_in(wt, "checkout", "-q", "-b", "feat")
    _commit(wt, "one")
    line = git.push.plan_line(wt, sha=git.client.head_sha(cwd=wt), branch="feat",
                              remote="origin", args=("--force-with-lease=refs/heads/feat:",))
    assert "remote (none) ->" in line and "lease on (absent ref)" in line
    assert "1 commit" in line


def test_push_prints_the_plan_before_git_runs(pushable, capsys):
    wt, _ = pushable
    _commit(wt, "one")
    result = git.push.push(wt, gated=False, owner_check=None)
    assert result.ok
    err = capsys.readouterr().err
    assert "Will push main to origin" in err
