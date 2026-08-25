"""Tests for the land owner.

Against a real repo, with only the push stubbed. Everything `land` settles —
what an empty commit means, what a pathspec stages, what git says when a hook
refuses — is git's behaviour rather than this module's, and a mocked
`git_client` would agree with whatever the assertion expected.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import land  # noqa: E402
import push  # noqa: E402
from pr_fix import CommitStatus  # noqa: E402
from proc import CmdResult  # noqa: E402

_PUSHED = push.PushResult(
    push.PushStatus.PUSHED, sha="9bc3f64ab", branch="feat/x", remote_sha="9bc3f64ab",
)


@pytest.fixture
def wt(tmp_path):
    """A real repo with one commit and hooks of its own.

    The empty hooks dir is not incidental: `core.hooksPath` is a global setting
    on a developer machine, so without it the fixture runs whatever pre-commit
    that machine has installed and the suite passes or fails on it.
    """
    repo = tmp_path / "worktree"
    repo.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(repo, "init", "-q", "-b", "main")
    git_out(repo, "config", "user.email", "test@example.com")
    git_out(repo, "config", "user.name", "Test")
    git_out(repo, "config", "commit.gpgsign", "false")
    git_out(repo, "config", "core.hooksPath", str(hooks))
    (repo / "src.py").write_text("original\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", "initial")
    return repo


def _install_failing_pre_commit(tmp_path, message: str = "gate refused") -> None:
    """Make every later `git commit` in `wt` fail, the way a hook does."""
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n")
    hook.chmod(0o755)


def _committed_paths(repo: Path) -> set[str]:
    out = git_out(
        repo, "-c", "core.quotePath=false",
        "show", "--name-only", "--pretty=format:", "HEAD",
    )
    return {line for line in out.strip().splitlines() if line}


def _land(repo, *, result=_PUSHED, **kwargs):
    """`land` with the push owner stubbed, so nothing reaches a network."""
    kwargs.setdefault("message", "fix: work")
    kwargs.setdefault("gated", False)
    with patch("land.push.push", return_value=result) as mock_push:
        landed = land.land(repo, **kwargs)
    return landed, mock_push


# ── rule 1: every outcome has a status ──────────────────────────────────────


def test_every_push_status_maps_to_a_commit_status():
    """A status this map does not answer for is a KeyError after the commit."""
    assert set(land._PUSH_STATUS) == set(push.PushStatus)


@pytest.mark.parametrize("status", list(push.PushStatus))
def test_commit_status_answers_for_every_push_status(status):
    assert isinstance(land.commit_status(status), CommitStatus)


def test_an_unverified_push_is_not_folded_into_lost():
    """Neither "the remote has it" nor "it does not" is a claim the run can make."""
    assert land.commit_status(push.PushStatus.UNVERIFIED) is CommitStatus.PUSH_UNVERIFIED
    assert land.commit_status(push.PushStatus.LOST) is CommitStatus.PUSH_LOST


# ── nothing to commit ───────────────────────────────────────────────────────


def test_a_clean_whole_tree_is_no_changes(wt):
    landed, mock_push = _land(wt)
    assert landed.status is CommitStatus.NO_CHANGES
    assert landed.sha == ""
    mock_push.assert_not_called()


def test_an_empty_path_list_is_no_changes(wt):
    """A pass whose snapshot difference was empty has an outcome, not a failure."""
    (wt / "src.py").write_text("edited\n")
    landed, mock_push = _land(wt, paths=[])
    assert landed.status is CommitStatus.NO_CHANGES
    mock_push.assert_not_called()


def test_a_commit_git_calls_empty_is_no_changes(wt):
    """The dirty gate fails closed, so the emptiness is sometimes only git's to see.

    Naming a tracked file nobody edited is the shape that reaches it: staging
    succeeds and stages nothing, and git declines the commit that follows.
    """
    landed, mock_push = _land(wt, paths=["src.py"])
    assert landed.status is CommitStatus.NO_CHANGES
    mock_push.assert_not_called()


# ── what gets committed ─────────────────────────────────────────────────────


def test_the_whole_tree_includes_files_the_pass_added(wt):
    """`-u` would drop a test or fixture the fix created while still counting it."""
    (wt / "src.py").write_text("edited\n")
    (wt / "new_test.py").write_text("def test_x(): pass\n")
    landed, _ = _land(wt)
    assert landed.status is CommitStatus.PUSHED
    assert _committed_paths(wt) == {"src.py", "new_test.py"}


def test_named_paths_leave_everything_else_behind(wt):
    (wt / "src.py").write_text("operator work in progress\n")
    (wt / "fixture.json").write_text("{}\n")
    _land(wt, paths=["fixture.json"])
    assert _committed_paths(wt) == {"fixture.json"}
    assert git_out(wt, "show", "HEAD:src.py") == "original\n"


def test_a_glob_metacharacter_in_a_name_matches_only_itself(wt):
    """git reads a staged name as a pathspec, so a bracket is a character class."""
    (wt / "report[1].md").write_text("the pass's work\n")
    (wt / "report1.md").write_text("unrelated, still in progress\n")
    _land(wt, paths=["report[1].md"])
    assert _committed_paths(wt) == {"report[1].md"}
    assert "report1.md" in git_out(wt, "status", "--porcelain")


def test_the_message_is_the_commit_message(wt):
    (wt / "src.py").write_text("edited\n")
    _land(wt, message="fix: a subject\n\na body line")
    assert git_out(wt, "log", "-1", "--pretty=%B").strip() == (
        "fix: a subject\n\na body line"
    )


# ── the commit git refuses ──────────────────────────────────────────────────


def test_a_rejected_commit_is_not_pushed(wt, tmp_path, live_git_hooks):
    """There is nothing on the branch to push — `live_git_hooks` runs the hook."""
    _install_failing_pre_commit(tmp_path)
    (wt / "src.py").write_text("edited\n")

    landed, mock_push = _land(wt)

    assert landed.status is CommitStatus.COMMIT_FAILED
    assert "gate refused" in landed.error
    assert landed.sha == ""
    assert landed.citable is False
    mock_push.assert_not_called()


def test_a_failed_stage_raises_rather_than_reporting_no_changes(wt):
    """"Nothing to commit" on an unreadable repo reports success having lost the work."""
    with patch("land.git_client.run", return_value=CmdResult(128, "", "index locked")):
        with pytest.raises(RuntimeError, match="stage"):
            land.land(wt, message="fix: work", gated=False, paths=["src.py"])


def test_a_head_that_will_not_read_back_raises(wt):
    """git made the commit and then would not say what it is — that is not an outcome."""
    (wt / "src.py").write_text("edited\n")
    with patch("land.git_client.head_sha", return_value=""):
        with pytest.raises(RuntimeError, match="HEAD"):
            land.land(wt, message="fix: work", gated=False)


# ── the push under it ───────────────────────────────────────────────────────


def test_the_gate_is_passed_through_to_the_push(wt):
    (wt / "src.py").write_text("edited\n")
    _, mock_push = _land(wt, gated=True)
    assert mock_push.call_args.kwargs["gated"] is True


def test_the_committed_sha_is_the_one_pushed(wt):
    (wt / "src.py").write_text("edited\n")
    landed, mock_push = _land(wt)
    assert landed.sha == git_out(wt, "rev-parse", "HEAD").strip()
    assert mock_push.call_args.kwargs["sha"] == landed.sha


def test_push_args_reach_the_owner(wt):
    (wt / "src.py").write_text("edited\n")
    _, mock_push = _land(wt, args=["--set-upstream"])
    assert mock_push.call_args.kwargs["args"] == ["--set-upstream"]


# ── rules 2 and 3: the resume command, and what may be cited ────────────────


def test_a_landed_push_is_citable_and_needs_no_resume(wt):
    (wt / "src.py").write_text("edited\n")
    landed, _ = _land(wt)
    assert landed.ok is True
    assert landed.citable is True
    assert landed.resume == ""


@pytest.mark.parametrize("status", [
    push.PushStatus.HELD,
    push.PushStatus.REFUSED,
    push.PushStatus.LOST,
    push.PushStatus.UNVERIFIED,
])
def test_a_sha_the_remote_may_not_hold_is_never_citable(wt, status):
    """A commit link that 404s for the reviewer is worse than a reply deferred."""
    (wt / "src.py").write_text("edited\n")
    landed, _ = _land(wt, result=push.PushResult(status, sha="9bc3f64ab", branch="f"))
    assert landed.sha
    assert landed.citable is False
    assert landed.resume


def test_a_divergence_carries_the_force_push_the_operator_must_run(wt):
    (wt / "src.py").write_text("edited\n")
    landed, _ = _land(wt, result=push.PushResult(
        push.PushStatus.REFUSED, sha="9bc3f64ab", branch="feat/x",
        refusal=push.Refusal.DIVERGED,
    ))
    assert "--force-with-lease" in landed.resume


def test_a_refusal_carries_what_git_said(wt):
    (wt / "src.py").write_text("edited\n")
    landed, _ = _land(wt, result=push.PushResult(
        push.PushStatus.REFUSED, sha="9bc3f64ab", branch="feat/x",
        refusal=push.Refusal.HOOK, output="✗ Pytest failed\n",
    ))
    assert "Pytest failed" in landed.error
    assert landed.push.refusal is push.Refusal.HOOK


# ── the gate over the push, and the commit under it ─────────────────────────


@pytest.fixture
def landable(wt, tmp_path):
    """`wt`, with a bare `origin` alongside it and `main` already pushed."""
    remote = tmp_path / "remote.git"
    git_out(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git_out(wt, "remote", "add", "origin", str(remote))
    git_out(wt, "push", "-q", "-u", "origin", "main")
    return wt, remote


def _remote_head(remote: Path) -> str:
    return git_out(remote, "rev-parse", "main").strip()


def test_a_gated_pass_commits_locally_and_drafts_the_push(landable, capsys):
    """The behaviour every fix pass now has: the work is durable, nothing is sent.

    A local commit asserts nothing to anybody and is what makes the pass's work
    reviewable at all; the push is the outward act, so it waits for `--post`.
    """
    wt, remote = landable
    before = _remote_head(remote)
    (wt / "src.py").write_text("edited\n")

    landed = land.land(wt, message="fix: work", gated=True)

    assert landed.status is CommitStatus.PUSH_HELD
    assert landed.sha == git_out(wt, "rev-parse", "HEAD").strip()
    assert landed.citable is False
    assert _remote_head(remote) == before
    assert "DRAFT (not published)" in capsys.readouterr().err


def test_the_same_pass_pushes_once_the_gate_is_open(landable, publishing_on):
    wt, remote = landable
    (wt / "src.py").write_text("edited\n")

    landed = land.land(wt, message="fix: work", gated=True)

    assert landed.status is CommitStatus.PUSHED
    assert landed.citable is True
    assert _remote_head(remote) == landed.sha


def test_an_ungated_pass_pushes_with_the_gate_shut(landable):
    """`pr rebase` and the `pr:create` bridge push because pushing is the command."""
    wt, remote = landable
    (wt / "src.py").write_text("edited\n")

    landed = land.land(wt, message="fix: work", gated=False)

    assert landed.status is CommitStatus.PUSHED
    assert _remote_head(remote) == landed.sha
