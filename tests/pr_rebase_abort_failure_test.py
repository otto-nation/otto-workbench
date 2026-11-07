"""A failed ``git rebase --abort`` must not be recorded as a clean abort."""

import json
import sys
from pathlib import Path
from unittest import mock

from conftest import commit_all, git_out, init_repo, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc  # noqa: E402
import git.client  # noqa: E402
import pr.domains  # noqa: E402
import rebase.conflict_stop  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402


def _conflicted(tmp_path) -> tuple[Path, str]:
    """A feat-onto-main rebase paused on a handwritten conflict."""
    repo = init_repo(tmp_path / "repo")
    (repo / "a.go").write_text("package base\n")
    commit_all(repo, "base")
    git_out(repo, "checkout", "-b", "feat")
    (repo / "a.go").write_text("package feat\n")
    commit_all(repo, "feat")
    pre = git_out(repo, "rev-parse", "HEAD").strip()
    git_out(repo, "checkout", "main")
    (repo / "a.go").write_text("package main\n")
    commit_all(repo, "main2")
    git_out(repo, "checkout", "feat")
    r = git.client.run("rebase", "main", cwd=str(repo))
    assert not r.ok
    return repo, pre


def test_failed_abort_reports_paused_and_does_not_mark_aborted(tmp_path, capsys):
    """Revert target: abort_clean ignoring a failed ``git rebase --abort``.

    If abort_clean saved ABORTED / tree=clean regardless of the abort result,
    this assertion fails. The patch is the abort only — every other git call
    is real, so the paused tree is still there to report.
    """
    repo, pre = _conflicted(tmp_path)
    ctx = make_ctx(worktree_root=repo, branch="feat", current_branch="feat")
    files = rebase.inspect.detect_conflicts(str(repo))
    classes = rebase.conflict_stop.classify_stop(str(repo), files)
    tally = rebase.types.ResolutionTally(pre_rebase_head=pre)
    real_run = git.client.run

    def run(*args, **kwargs):
        if args[:2] == ("rebase", "--abort"):
            return core.proc.CmdResult(returncode=128, stderr="fatal: cannot abort")
        return real_run(*args, **kwargs)

    with mock.patch.object(git.client, "run", side_effect=run):
        rc = rebase.conflict_stop.abort_clean(
            str(repo), ctx, target_ref="main", classes=classes, tally=tally,
        )

    report = json.loads(capsys.readouterr().out)
    state = rebase.types.load_or_init(ctx).rebase
    assert rc == rebase.types.CONFLICTS_EXIT
    assert report["tree"] == "paused"
    assert report["aborted"] is False
    assert state.status != pr.domains.RebaseStatus.ABORTED.value
    assert state.aborted is False
    assert rebase.inspect.rebase_in_progress(str(repo))
