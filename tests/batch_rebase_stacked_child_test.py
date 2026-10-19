"""A stacked child after its parent is rebased looks partially landed.

The batch rebase step should replay only the child's unique commits, using
`--fork-point merge-base(parent_pre_rebase_head, child)` (not `--force`, which
replays the parent's old commits onto the rewritten parent). A
`rebase_refused` decision whose payload names that fork point should offer an
action that runs that argv.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from conftest import git_in, git_out, init_repo, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.requests  # noqa: E402
import batch.resolve  # noqa: E402
import batch.steps  # noqa: E402
import gh.landed  # noqa: E402
import rebase.refusals  # noqa: E402
from batch.model import Step, StepStatus  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from rebase.types import REFUSAL_EXIT  # noqa: E402
from batch_scheduler_support import (  # noqa: F401
    NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)


def _commit(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(f"{name}\n")
    git_in(repo, "add", name)
    git_in(repo, "commit", "-q", "-m", subject)
    return git_out(repo, "rev-parse", "HEAD").strip()


def _subjects(repo: Path, since: str, until: str = "HEAD") -> list[str]:
    """Commit subjects *since*..*until*, oldest first — the set a rebase replays."""
    return git_out(
        repo, "log", "--reverse", "--no-merges", "--format=%s",
        f"{since}..{until}",
    ).splitlines()


def _ok_ancestor(repo: Path, commit: str, of: str) -> bool:
    return run_checked(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, of],
        check=False,
    ).returncode == 0


def _stacked(tmp_path, *, extra_parent: bool = False) -> Path:
    """main, a two-commit parent, and a child stacked on the parent.

    Each commit owns its own file so a later rebase onto advanced main is
    clean. *extra_parent* adds a third parent commit after the child forks,
    so the parent's tip is no longer an ancestor of the child.
    """
    repo = init_repo(tmp_path / "repo")
    _commit(repo, "root.txt", "chore: root")
    git_in(repo, "checkout", "-q", "-b", "parent")
    _commit(repo, "p1.txt", "feat: parent one")
    _commit(repo, "p2.txt", "feat: parent two")
    git_in(repo, "checkout", "-q", "-b", "child")
    _commit(repo, "c1.txt", "feat: child")
    if extra_parent:
        git_in(repo, "checkout", "-q", "parent")
        _commit(repo, "p3.txt", "feat: parent three")
    return repo


def _advance_main_and_rebase_parent(repo: Path) -> str:
    """Advance main, rebase parent onto it, return the parent's pre-rebase tip."""
    git_in(repo, "checkout", "-q", "parent")
    old_parent = git_out(repo, "rev-parse", "HEAD").strip()
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "main2.txt", "chore: advance main")
    git_in(repo, "checkout", "-q", "parent")
    git_in(repo, "rebase", "main")
    return old_parent


def _refused_child(tmp_path) -> tuple[batch.model.Decision, batch.model.Item, Path]:
    """A `rebase_refused` decision for a stacked child whose payload names a fork point."""
    repo = _stacked(tmp_path)
    _advance_main_and_rebase_parent(repo)
    git_in(repo, "checkout", "-q", "child")
    ctx = SimpleNamespace(branch="child")
    report = rebase.refusals.partially_landed_check(
        str(repo), ctx, target_ref="parent",
    )
    item = batch.model.Item(
        key="o/r#2", repo="o/r", repo_dir="/r", pr=2, branch="child",
        head_sha="s", worktree=str(repo), stacked_on="o/r#1", base_ref="parent",
        steps=[batch.model.StepRecord(batch.model.Step.REBASE)],
        status=batch.model.ItemStatus.AWAITING_DECISION,
    )
    classified = batch.outcomes.classify(
        batch.model.Step.REBASE, REFUSAL_EXIT, json.dumps({
            "branch": report.branch, "signal": report.signal,
            "detail": report.detail, "status": report.status,
            "override": report.override, "remedy": report.remedy,
            "commits_ahead": report.commits_ahead,
        }),
        item=item, log_tail=[],
    )
    decision = batch.model.Decision(
        id="d1", item=item.key, step="rebase",
        kind=batch.model.DecisionKind.REBASE_REFUSED,
        payload=classified.decisions[0].payload,
    )
    return decision, item, repo


def test_rebasing_the_parent_makes_the_child_look_partially_landed(tmp_path):
    """The child's prefix is the parent's old commits; they match the rewrite."""
    repo = _stacked(tmp_path)
    old_parent = _advance_main_and_rebase_parent(repo)

    partial = gh.landed.partial_landing(str(repo), target_ref="parent", rev="child")

    assert partial is not None
    assert partial.landed == 2
    assert partial.unlanded == 1
    assert partial.fork_point == old_parent
    assert partial.fork_subject == "feat: parent two"


def test_force_replays_parent_commits_fork_point_replays_child_only(tmp_path):
    """`git rebase parent` on the child replays P1, P2, C1 — not just C1."""
    repo = _stacked(tmp_path)
    old_parent = _advance_main_and_rebase_parent(repo)

    force_set = _subjects(repo, "parent", "child")
    fork_set = _subjects(repo, old_parent, "child")

    assert force_set == ["feat: parent one", "feat: parent two", "feat: child"]
    assert fork_set == ["feat: child"]


def test_parent_grew_after_fork_so_old_tip_is_not_the_fork_point(tmp_path):
    """Parent grew after the child forked; the real fork is an earlier commit.

    The parent's pre-rebase tip is not on the child, so passing that tip to
    `--fork-point` is rejected. merge-base(parent_pre_rebase_head, child) is
    the last shared parent commit.
    """
    repo = _stacked(tmp_path, extra_parent=True)
    parent_pre = git_out(repo, "rev-parse", "parent").strip()
    git_in(repo, "checkout", "-q", "child")

    assert not _ok_ancestor(repo, parent_pre, "child")
    fork = git_out(repo, "merge-base", parent_pre, "child").strip()
    assert fork != parent_pre
    assert _ok_ancestor(repo, fork, "child")
    assert git_out(repo, "log", "-1", "--format=%s", fork).strip() == "feat: parent two"
    assert _subjects(repo, fork, "child") == ["feat: child"]

    _advance_main_and_rebase_parent(repo)
    partial = gh.landed.partial_landing(str(repo), target_ref="parent", rev="child")
    assert partial is not None
    assert partial.fork_point == fork
    assert partial.fork_subject == "feat: parent two"


def test_rebase_refused_naming_a_fork_point_offers_rebase_from_fork_point(tmp_path):
    decision, item, _repo = _refused_child(tmp_path)
    assert batch.resolve._named_fork_point(decision.payload)

    actions = batch.resolve.available_actions(decision, item)

    assert "rebase-from-fork-point" in actions


def test_command_for_rebase_from_fork_point_passes_fork_point_not_force(tmp_path):
    decision, item, _repo = _refused_child(tmp_path)
    sha = batch.resolve._named_fork_point(decision.payload)
    assert sha

    commands = batch.resolve.command_for(
        decision, item, batch.requests.Decide("d1", "rebase-from-fork-point"), "pr",
    )

    assert commands
    argv = commands[0]
    assert "--fork-point" in argv
    assert argv[argv.index("--fork-point") + 1] == sha
    assert "--force" not in argv


def test_batch_rebase_argv_uses_merge_base_of_parent_pre_rebase_head(tmp_path):
    """When the parent grew after the child forked, the fork point is earlier.

    Parent's recorded `pre_rebase_head` is not an ancestor of the child; the
    argv must name merge-base(that tip, child), not the tip itself.
    """
    repo = _stacked(tmp_path, extra_parent=True)
    parent_pre = git_out(repo, "rev-parse", "parent").strip()
    git_in(repo, "checkout", "-q", "child")
    expected = git_out(repo, "merge-base", parent_pre, "child").strip()
    assert expected != parent_pre
    assert git_out(repo, "log", "-1", "--format=%s", expected).strip() == (
        "feat: parent two"
    )

    argv = batch.steps.step_argv(
        batch.model.Step.REBASE, "pr", str(repo),
        fork_point=expected,
    )

    assert "--fork-point" in argv
    assert argv[argv.index("--fork-point") + 1] == expected
    assert "--force" not in argv


ONLY_REBASE = {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NO}


def _child_rebase_argv(tmp_path, *, extra_parent: bool, pre_rebase_head: str | None,
                       parent_rebased: bool = True):
    """Spawn the stacked child's rebase via the scheduler and return its argv."""
    repo = _stacked(tmp_path, extra_parent=extra_parent)
    parent_pre = git_out(repo, "rev-parse", "parent").strip()
    git_in(repo, "checkout", "-q", "child")
    expected = git_out(repo, "merge-base", parent_pre, "child").strip()
    wt = str(repo)
    h = Harness(
        [row(1, ONLY_REBASE), row(2, ONLY_REBASE, base_ref="b1")], pool=2,
        worktrees=lambda d, b: WorktreeResult(wt, False, ""),
    )
    parent = h.run.item("o/r#1")
    child = h.run.item("o/r#2")
    assert child.stacked_on == "o/r#1"
    parent.pre_rebase_head = parent_pre if pre_rebase_head is None else pre_rebase_head
    parent.step(Step.REBASE).status = StepStatus.DONE if parent_rebased else StepStatus.SKIPPED
    child.worktree = wt
    h.sched._start(child, child.step(Step.REBASE), row(2, ONLY_REBASE, base_ref="b1"))
    return h.spawned[-1], expected, parent_pre


def test_scheduler_passes_merge_base_when_parent_grew_after_fork(tmp_path):
    argv, expected, parent_pre = _child_rebase_argv(
        tmp_path, extra_parent=True, pre_rebase_head=None)
    assert expected != parent_pre
    assert "--fork-point" in argv
    assert argv[argv.index("--fork-point") + 1] == expected
    assert "--force" not in argv


def test_scheduler_omits_fork_point_when_parent_pre_rebase_head_is_empty(tmp_path):
    argv, _, _ = _child_rebase_argv(tmp_path, extra_parent=True, pre_rebase_head="")
    assert "--fork-point" not in argv


def test_scheduler_omits_fork_point_when_parent_rebase_was_skipped(tmp_path):
    argv, _, _ = _child_rebase_argv(
        tmp_path, extra_parent=True, pre_rebase_head=None, parent_rebased=False)
    assert "--fork-point" not in argv


def test_named_fork_point_tolerates_bare_remedy_prefix():
    assert batch.resolve._named_fork_point({"remedy": "--fork-point "}) == ""
