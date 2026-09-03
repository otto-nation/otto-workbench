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
from land import CommitStatus  # noqa: E402
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


def test_land_owns_the_commit_vocabulary():
    """The enum land maps push results into is land's own, not pr_fix's.

    `pr_fix` sits above `git` in the layer order, so an enum land imports from
    it is an upward edge — and land is the only consumer of it below `pr`.
    """
    assert land.CommitStatus.PUSHED == "pushed"
    assert {s.value for s in land.CommitStatus} >= {
        "pushed", "push_held", "push_failed", "push_lost", "push_unverified",
        "commit_failed", "no_changes", "reconciled",
    }


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


@pytest.mark.parametrize("status", list(CommitStatus))
def test_a_held_push_is_its_own_answer_rather_than_a_failure(status):
    """`--no-push` finishing as asked and a push that fell over are not one case.

    Read off `ok` alone the two are indistinguishable, and the caller that most
    needs them apart is `pr rebase`, whose held landing is the run succeeding.
    Over the whole enum rather than a sample, so a status that starts answering
    `held` — or `ok` — cannot arrive unnoticed.
    """
    landed = land.LandResult(status)
    assert landed.held is (status is CommitStatus.PUSH_HELD)
    assert landed.ok is (status is CommitStatus.PUSHED)
    assert not (landed.held and landed.ok)


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


# ── regen: the hook that rewrites generated files ───────────────────────────

_REGENERATING_HOOK = """#!/bin/sh
if [ -f .git/regenerated ]; then exit 0; fi
: > .git/regenerated
echo 'regenerated' > gen.txt
echo 'docs are stale — regenerated them' >&2
exit 1
"""


@pytest.fixture
def regenerating(landable, tmp_path, live_git_hooks):
    """`landable` with a tracked generated file and a pre-push hook that rewrites it.

    The hook refuses the first push and passes the second, which is the whole
    shape the retry exists for: the commit underneath was fine, and what the
    hook wrote is what the remote was missing.
    """
    wt, remote = landable
    (wt / "gen.txt").write_text("stale\n")
    git_out(wt, "add", "gen.txt")
    git_out(wt, "commit", "-qm", "chore: add generated file")
    git_out(wt, "push", "-q", "origin", "main")

    hook = tmp_path / "hooks" / "pre-push"
    hook.write_text(_REGENERATING_HOOK)
    hook.chmod(0o755)
    return wt, remote


def test_a_regenerating_hook_is_committed_and_the_push_retried(regenerating):
    wt, remote = regenerating
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.status is CommitStatus.PUSHED
    assert (wt / "gen.txt").read_text() == "regenerated\n"
    assert _remote_head(remote) == git_out(wt, "rev-parse", "HEAD").strip()
    assert git_out(wt, "log", "-1", "--pretty=%s").strip() == "chore: regenerate"


def test_the_retry_reports_the_commit_the_pass_made(regenerating):
    """The caller's entries are stamped with its own commit — the regen rides above it."""
    wt, _ = regenerating
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.sha == git_out(wt, "rev-parse", "HEAD~1").strip()
    assert git_out(wt, "log", "-1", "--pretty=%s", landed.sha).strip() == "fix: work"


def test_a_caller_that_did_not_ask_for_the_retry_keeps_the_refusal(regenerating):
    wt, remote = regenerating
    before = _remote_head(remote)
    (wt / "src.py").write_text("edited\n")

    landed = land.land(wt, message="fix: work", gated=False)

    assert landed.status is CommitStatus.PUSH_FAILED
    assert landed.citable is False
    assert landed.resume
    assert _remote_head(remote) == before


def test_a_refusal_that_regenerated_nothing_stands(landable, tmp_path, live_git_hooks):
    """A hook can refuse for its own reasons — a failing test suite, most often."""
    wt, remote = landable
    before = _remote_head(remote)
    hook = tmp_path / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho '✗ Pytest failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.status is CommitStatus.PUSH_FAILED
    assert "Pytest failed" in landed.error
    assert _remote_head(remote) == before


def test_a_retry_that_falls_short_too_keeps_the_original(regenerating, tmp_path):
    """The commit the caller made is still the one its work is in.

    The hook here regenerates once and refuses twice — a suite that stays red
    after the generated files are current.
    """
    wt, remote = regenerating
    before = _remote_head(remote)
    hook = tmp_path / "hooks" / "pre-push"
    hook.write_text(_REGENERATING_HOOK.replace("exit 0", "exit 1"))
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.status is CommitStatus.PUSH_FAILED
    assert landed.sha == git_out(wt, "rev-parse", "HEAD~1").strip()
    assert git_out(wt, "log", "-1", "--pretty=%s").strip() == "chore: regenerate"
    assert _remote_head(remote) == before


_STILL_REFUSING_HOOK = """#!/bin/sh
if [ -f .git/regenerated ]; then echo '✗ Pytest failed' >&2; exit 1; fi
: > .git/regenerated
echo 'regenerated' > gen.txt
echo 'docs are stale — regenerated them' >&2
exit 1
"""

_UNTRACKED_REGEN_HOOK = """#!/bin/sh
if [ -f .git/regenerated ]; then exit 0; fi
: > .git/regenerated
echo 'regenerated' > gen.txt
echo 'new' > extra.txt
echo 'docs are stale — regenerated them' >&2
exit 1
"""


def test_the_retry_is_the_refusal_the_caller_is_handed(regenerating, tmp_path):
    """What the operator has to repair is what the *second* attempt said.

    The first attempt's complaint went stale the moment the regenerated files
    were committed; reporting it sends someone to fix a thing already fixed.
    """
    wt, _ = regenerating
    hook = tmp_path / "hooks" / "pre-push"
    hook.write_text(_STILL_REFUSING_HOOK)
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.status is CommitStatus.PUSH_FAILED
    assert "Pytest failed" in landed.error
    assert "docs are stale" not in landed.error


def test_a_retry_that_would_push_an_unvalidated_tree_is_abandoned(
    regenerating, tmp_path, capsys,
):
    """`add -u` reaches tracked files only, and a hook validates the worktree.

    The hook here also writes a file git has never seen, so committing what it
    regenerated still leaves the tree the second run would validate different
    from the HEAD that reaches the remote.
    """
    wt, remote = regenerating
    before = _remote_head(remote)
    hook = tmp_path / "hooks" / "pre-push"
    hook.write_text(_UNTRACKED_REGEN_HOOK)
    (wt / "src.py").write_text("edited\n")

    landed = land.land(
        wt, message="fix: work", gated=False, regen="chore: regenerate",
    )

    assert landed.status is CommitStatus.PUSH_FAILED
    assert _remote_head(remote) == before
    # The regeneration is still committed — abandoning the retry is a decision
    # about pushing, not a rollback of what the hook wrote.
    assert git_out(wt, "log", "-1", "--pretty=%s").strip() == "chore: regenerate"
    assert "Recovery left uncommitted changes" in capsys.readouterr().err


def test_a_worktree_that_will_not_answer_is_not_a_validated_one(tmp_path, capsys):
    """"Don't know" must not be spelled the same way as "clean" over a push."""
    assert land._validated(tmp_path, None) is False
    err = capsys.readouterr().err
    assert "Cannot tell whether the recovery left the worktree dirty" in err


def test_a_clean_worktree_is_a_validated_one(wt):
    assert land._validated(wt, None) is True


def test_a_lost_push_is_not_read_as_a_regenerating_hook(wt):
    """Nothing was left behind to commit, and `push` has already retried that one."""
    (wt / "src.py").write_text("edited\n")
    lost = push.PushResult(push.PushStatus.LOST, sha="9bc3f64ab", branch="main")

    landed, mock_push = _land(wt, result=lost, regen="chore: regenerate")

    assert landed.status is CommitStatus.PUSH_LOST
    assert mock_push.call_count == 1


# ── recover_from: the commit the caller did not make ────────────────────────


def _agent_commit(repo: Path, message: str = "fix: the agent's own commit") -> str:
    """A commit made outside the pass, the way a fix agent leaves one."""
    (repo / "src.py").write_text("the agent's edit\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", message)
    return git_out(repo, "rev-parse", "HEAD").strip()


def test_a_commit_the_pass_did_not_make_is_attributed_and_pushed(landable):
    wt, remote = landable
    before = git_out(wt, "rev-parse", "HEAD").strip()
    sha = _agent_commit(wt)

    landed = land.land(wt, message="fix: work", gated=False, recover_from=before)

    assert landed.status is CommitStatus.PUSHED
    assert landed.sha == sha
    assert _remote_head(remote) == sha


def test_recovery_reads_an_abbreviated_head_the_caller_recorded(landable):
    """What a caller holding a SHA for a reviewer has is the abbreviation."""
    wt, _ = landable
    before = git_out(wt, "rev-parse", "--short", "HEAD").strip()
    sha = _agent_commit(wt)

    landed = land.land(wt, message="fix: work", gated=False, recover_from=before)

    assert landed.sha == sha


def test_an_unmoved_head_leaves_no_changes_alone(landable):
    wt, _ = landable
    before = git_out(wt, "rev-parse", "HEAD").strip()

    landed = land.land(wt, message="fix: work", gated=False, recover_from=before)

    assert landed.status is CommitStatus.NO_CHANGES
    assert landed.sha == ""


def test_a_commit_the_remote_already_holds_is_not_pushed_again(landable):
    wt, _ = landable
    before = git_out(wt, "rev-parse", "HEAD").strip()
    sha = _agent_commit(wt)
    git_out(wt, "push", "-q", "origin", "main")

    with patch("land.push.push", side_effect=AssertionError("pushed again")):
        landed = land.land(wt, message="fix: work", gated=False, recover_from=before)

    assert landed.status is CommitStatus.PUSHED
    assert landed.sha == sha


def test_a_recovered_commit_waits_for_the_gate_like_any_other(landable):
    wt, remote = landable
    at_remote = _remote_head(remote)
    before = git_out(wt, "rev-parse", "HEAD").strip()
    sha = _agent_commit(wt)

    landed = land.land(wt, message="fix: work", gated=True, recover_from=before)

    assert landed.status is CommitStatus.PUSH_HELD
    assert landed.sha == sha
    assert landed.resume
    assert _remote_head(remote) == at_remote


def test_a_dirty_tree_under_no_changes_is_a_refused_commit(wt):
    """Changes still sitting there after a commit was attempted mean something refused it.

    `NO_CHANGES` alone cannot say which of the two happened, and reporting
    "nothing needed doing" over refused work is the reading that misleads.
    """
    before = git_out(wt, "rev-parse", "HEAD").strip()

    with patch("land.git_client.is_dirty", return_value=True):
        landed = land.land(
            wt, message="fix: work", gated=False, paths=[], recover_from=before,
        )

    assert landed.status is CommitStatus.COMMIT_FAILED
    assert landed.error == "changes remain uncommitted in the worktree"


def test_recovery_never_overwrites_a_commit_the_hook_refused(wt, tmp_path,
                                                             live_git_hooks):
    """A refused commit is information recovery has nothing better to replace."""
    _install_failing_pre_commit(tmp_path)
    before = git_out(wt, "rev-parse", "HEAD").strip()
    (wt / "src.py").write_text("edited\n")

    landed, mock_push = _land(wt, recover_from=before)

    assert landed.status is CommitStatus.COMMIT_FAILED
    assert "gate refused" in landed.error
    mock_push.assert_not_called()


def test_a_caller_that_did_not_ask_recovers_nothing(landable):
    wt, remote = landable
    at_remote = _remote_head(remote)
    _agent_commit(wt)

    landed = land.land(wt, message="fix: work", gated=False)

    assert landed.status is CommitStatus.NO_CHANGES
    assert landed.sha == ""
    assert _remote_head(remote) == at_remote


# ── land_head: the caller whose commits already exist ───────────────────────


def _land_head(repo, *, result=_PUSHED, **kwargs):
    """`land_head` with the push owner stubbed, so nothing reaches a network."""
    kwargs.setdefault("gated", False)
    with patch("land.push.push", return_value=result) as mock_push:
        landed = land.land_head(repo, **kwargs)
    return landed, mock_push


def test_land_head_pushes_the_commit_head_already_points_at(wt):
    head = git_out(wt, "rev-parse", "HEAD").strip()

    landed, mock_push = _land_head(wt)

    assert landed.status is CommitStatus.PUSHED
    assert landed.sha == head
    assert mock_push.call_args.kwargs["sha"] == head


def test_land_head_makes_no_commit_of_its_own(landable):
    """`pr rebase` replays the branch's commits; a commit here would be an empty one.

    The dirty file is the case that would go wrong silently: `land` would sweep
    it into a commit nobody asked for, and force-push it.
    """
    wt, remote = landable
    before = git_out(wt, "rev-list", "--count", "HEAD").strip()
    (wt / "scratch.txt").write_text("work in progress\n")

    landed = land.land_head(wt, gated=False)

    assert landed.status is CommitStatus.PUSHED
    assert git_out(wt, "rev-list", "--count", "HEAD").strip() == before
    assert _remote_head(remote) == landed.sha
    assert git_out(wt, "status", "--porcelain").strip() == "?? scratch.txt"


def test_land_head_passes_the_push_args_through(wt):
    """`pr rebase` rewrote the branch, so every push from it is a force-push."""
    landed, mock_push = _land_head(wt, args=("--force-with-lease",))

    assert mock_push.call_args.kwargs["args"] == ("--force-with-lease",)
    assert landed.ok is True


def test_land_head_holds_behind_a_shut_gate_and_names_the_resume(landable, capsys):
    """What `pr rebase --no-push` gets: nothing sent, and the command as data."""
    wt, remote = landable
    before = _remote_head(remote)
    _agent_commit(wt)

    landed = land.land_head(wt, gated=True, args=("--force-with-lease",))

    assert landed.held is True
    assert landed.sha == git_out(wt, "rev-parse", "HEAD").strip()
    assert "--force-with-lease" in landed.resume
    assert _remote_head(remote) == before
    assert "DRAFT (not published)" in capsys.readouterr().err


def test_land_head_pushes_once_the_gate_is_open(landable, publishing_on):
    """The same call under `pr rebase` without `--no-push`."""
    wt, remote = landable
    _agent_commit(wt)

    landed = land.land_head(wt, gated=True, args=("--force-with-lease",))

    assert landed.status is CommitStatus.PUSHED
    assert _remote_head(remote) == landed.sha


def test_land_head_recovers_from_a_regenerating_hook_too(regenerating):
    """The recovery belongs to the push half, so both entry points get it."""
    wt, remote = regenerating
    _agent_commit(wt)
    replayed = git_out(wt, "rev-parse", "HEAD").strip()

    landed = land.land_head(wt, gated=False, regen="chore: regenerate")

    assert landed.status is CommitStatus.PUSHED
    # The caller's own commit is what the landing reports; the regeneration
    # rides above it, exactly as it does under `land`.
    assert landed.sha == replayed
    assert git_out(wt, "log", "-1", "--pretty=%s").strip() == "chore: regenerate"
    assert _remote_head(remote) == git_out(wt, "rev-parse", "HEAD").strip()
