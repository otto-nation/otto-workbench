"""Squash-before-replay through `lifecycle.fresh` against a real repository."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend  # noqa: E402
import git.replay  # noqa: E402
import git.topology  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.refusals  # noqa: E402
import rebase.types  # noqa: E402

from rebase_fork_point_test import _ctx  # noqa: E402

_TARGET = "main"


def _always() -> None:
    root = Path(os.environ["WORKBENCH_CONFIG_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yml").write_text("rebase:\n  squash: always\n")


def _root(tmp_path) -> Path:
    repo = init_repo(tmp_path / "repo")
    (repo / "root.txt").write_text("root\n")
    git_in(repo, "add", "root.txt")
    git_in(repo, "commit", "-q", "-m", "chore: root")
    git_in(repo, "checkout", "-q", "-b", "feat")
    return repo


def _three_on_feat(repo: Path, *, content) -> None:
    for n, body in enumerate(content, start=1):
        (repo / "f.txt").write_text(body)
        git_in(repo, "add", "f.txt")
        git_in(repo, "commit", "-q", "-m", f"feat: {n}")


def _conflicting(tmp_path) -> tuple[Path, str]:
    """Three commits on feat all edit f.txt; main edited it too."""
    repo = _root(tmp_path)
    _three_on_feat(repo, content=("one\n", "two\n", "three\n"))
    git_in(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n")
    git_in(repo, "add", "f.txt")
    git_in(repo, "commit", "-q", "-m", "chore: main")
    git_in(repo, "checkout", "-q", "feat")
    tip = git_out(repo, "rev-parse", "HEAD").strip()
    return repo, tip


def _clean_three(tmp_path) -> tuple[Path, list[str]]:
    """Three commits on feat; main grew an unrelated file so the replay is real."""
    repo = _root(tmp_path)
    _three_on_feat(repo, content=("one\n", "two\n", "three\n"))
    originals = [
        s for s in git_out(repo, "log", "--reverse", "--format=%H", "main..HEAD").splitlines()
        if s
    ]
    git_in(repo, "checkout", "-q", "main")
    (repo / "other.txt").write_text("other\n")
    git_in(repo, "add", "other.txt")
    git_in(repo, "commit", "-q", "-m", "chore: other")
    git_in(repo, "checkout", "-q", "feat")
    return repo, originals


def _fresh(
    repo: Path, *, force: bool = False, pause_on_conflict: bool = False,
    trail=None, mode: rebase.types.RunMode | None = None,
) -> int:
    """`lifecycle.fresh` with everything outside the replay stubbed out.

    Same harness as `rebase_fork_point_test.TestForkPointReplay._fresh`.
    """
    mode = mode or rebase.types.RunMode.REBASE_ONLY
    with mock.patch.object(git.topology, "default_branch", return_value="main"), \
         mock.patch.object(rebase.lifecycle.rebase_lease, "remembered_tip",
                           return_value=""), \
         mock.patch.object(rebase.lifecycle.rebase_lease, "resolve",
                           return_value=None), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: None), \
         mock.patch.object(rebase.refusals, "tracker_landed_check", return_value=None), \
         mock.patch.object(rebase.refusals, "git_landed_check", return_value=None), \
         mock.patch.object(rebase.refusals, "unrelated_history_check", return_value=None):
        return rebase.lifecycle.fresh(
            str(repo), _ctx(), mode,
            force=force, target_ref=_TARGET, fork_point="",
            pause_on_conflict=pause_on_conflict, trail=trail,
        )


def _subjects(repo: Path) -> list[str]:
    return [
        s for s in git_out(repo, "log", "--reverse", "--format=%s", "main..HEAD").splitlines()
        if s
    ]


def test_three_conflicting_commits_stop_once_on_the_squash(tmp_path):
    _always()
    repo, _ = _conflicting(tmp_path)

    assert _fresh(repo, pause_on_conflict=True) == rebase.types.CONFLICTS_EXIT
    _, subject = rebase.inspect.rebase_head_info(str(repo))
    assert subject == "feat: 1"
    assert rebase.inspect.remaining_rebase_commits(str(repo)) == 0


def test_an_abort_to_clean_restores_the_unsquashed_branch(tmp_path):
    """Moving the branch to S before rebase would make abort restore S.

    git records orig-head from the tip the rebase started at. A
    `reset` / `update-ref` onto the squash commit makes that tip S, so
    `abort_clean`'s `git rebase --abort` would leave one squash commit
    instead of the three originals this asserts.
    """
    _always()
    repo, tip = _conflicting(tmp_path)

    assert _fresh(repo) == rebase.types.CONFLICTS_EXIT
    assert git_out(repo, "rev-parse", "HEAD").strip() == tip
    assert _subjects(repo) == ["feat: 1", "feat: 2", "feat: 3"]
    assert not rebase.inspect.rebase_in_progress(str(repo))


def test_over_budget_refusal_restores_the_unsquashed_branch(tmp_path):
    """`refuse_over_budget` aborts to orig-head, which is the unsquashed tip.

    A move-the-ref implementation would restore the squash commit the same
    way `test_an_abort_to_clean_restores_the_unsquashed_branch` would.
    """
    _always()
    repo = _root(tmp_path)
    n = rebase.types.CONFLICT_FILE_BUDGET + 1
    git_in(repo, "checkout", "-q", "main")
    for i in range(n):
        (repo / f"f{i}.txt").write_text("base\n")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "chore: files")
    git_in(repo, "checkout", "-q", "feat")
    for label in ("one", "two"):
        for i in range(n):
            (repo / f"f{i}.txt").write_text(f"{label}\n")
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-q", "-m", f"feat: {label}")
    git_in(repo, "checkout", "-q", "main")
    for i in range(n):
        (repo / f"f{i}.txt").write_text("main\n")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "chore: main")
    git_in(repo, "checkout", "-q", "feat")
    tip = git_out(repo, "rev-parse", "HEAD").strip()

    with mock.patch.object(agent.backend, "is_available", return_value=True):
        rc = _fresh(repo, mode=rebase.types.RunMode.FIX)

    assert rc == rebase.types.REFUSAL_EXIT
    assert git_out(repo, "rev-parse", "HEAD").strip() == tip
    assert _subjects(repo) == ["feat: one", "feat: two"]


def test_a_repo_with_missing_commits_check_error_still_squashes(tmp_path):
    _always()
    repo, _ = _clean_three(tmp_path)
    git_in(repo, "config", "rebase.missingCommitsCheck", "error")

    assert _fresh(repo) == 0
    assert len(_subjects(repo)) == 1


def test_original_commits_resolve_to_the_rebased_squash(tmp_path, record_rewrites):
    _always()
    repo, originals = _clean_three(tmp_path)
    record_rewrites(repo)

    assert _fresh(repo) == 0
    finder = git.replay.ReplayFinder(repo)
    for sha in originals:
        assert finder.find(sha).status is git.replay.ReplayStatus.FOUND


def test_conflict_report_pre_rebase_head_is_the_unsquashed_tip(tmp_path, capsys):
    _always()
    repo, tip = _conflicting(tmp_path)

    assert _fresh(repo, pause_on_conflict=True) == rebase.types.CONFLICTS_EXIT
    report = json.loads(capsys.readouterr().out)
    assert report["pre_rebase_head"] == tip
