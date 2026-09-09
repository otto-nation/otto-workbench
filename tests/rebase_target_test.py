"""Tests for rebase.target — which ref a run replays onto, and the checkout.

The checkout half runs against real fixture repos rather than stubs: its whole
contract is that no path through it discards a commit only one ref holds, and
a stubbed git cannot demonstrate that.
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

from conftest import init_worktree

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import client as gh_client
from git import topology as git_topology
from rebase import target as rebase_target
from rebase import types as rebase_types

_TARGET = "origin/main"
_BRANCH = "isaac/feat/x"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _commit(repo, name, message):
    (Path(repo) / name).write_text(f"{name}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo_on_main(tmp_path):
    repo = init_worktree(tmp_path / "repo")
    _git(repo, "config", "user.email", "rebase-test@example.com")
    _git(repo, "config", "user.name", "Rebase Test")
    _commit(repo, "base.txt", "base")
    return repo


def _ctx(branch=_BRANCH, pr_number=None):
    ctx = mock.MagicMock()
    ctx.branch = branch
    ctx.pr_number = pr_number
    return ctx


class TestResolveTargetRef:
    """Four sources, in authority order — the first that answers wins."""

    def test_onto_beats_every_probe(self):
        with mock.patch.object(gh_client, "pr_view") as view, \
             mock.patch.object(git_topology, "default_branch") as default:
            got = rebase_target.resolve_target_ref(
                "/fake", _ctx(pr_number=7), "origin/release/1.2")

        assert got == "origin/release/1.2"
        view.assert_not_called()
        default.assert_not_called()

    def test_the_prs_base_beats_the_repo_default(self):
        """A stacked or release-targeted PR is replayed onto its own base."""
        with mock.patch.object(gh_client, "pr_view",
                               return_value={"baseRefName": "release/1.2"}), \
             mock.patch.object(git_topology, "default_branch", return_value="main"):
            assert rebase_target.resolve_target_ref(
                "/fake", _ctx(pr_number=7), None) == "origin/release/1.2"

    def test_no_pr_falls_back_to_the_repo_default(self):
        with mock.patch.object(gh_client, "pr_view") as view, \
             mock.patch.object(git_topology, "default_branch", return_value="trunk"):
            assert rebase_target.resolve_target_ref(
                "/fake", _ctx(), None) == "origin/trunk"
        view.assert_not_called()

    def test_a_tracker_that_cannot_say_falls_back(self):
        """gh may be absent, unauthenticated or rate-limited — not a failure."""
        with mock.patch.object(gh_client, "pr_view", return_value={}), \
             mock.patch.object(git_topology, "default_branch", return_value="main"):
            assert rebase_target.resolve_target_ref(
                "/fake", _ctx(pr_number=7), None) == _TARGET


class TestResumeTargetRef:
    """A resume replays onto what the run that started it recorded."""

    def test_a_recorded_base_wins_over_this_runs_resolution(self):
        with mock.patch.object(rebase_types, "recorded_target_base",
                               return_value="origin/release/1.2"):
            assert rebase_target.resume_target_ref(
                _ctx(), _TARGET) == "origin/release/1.2"

    def test_no_prior_state_keeps_this_runs_ref(self):
        with mock.patch.object(rebase_types, "recorded_target_base",
                               return_value=None):
            assert rebase_target.resume_target_ref(_ctx(), _TARGET) == _TARGET


class TestCheckoutTargetBranch:
    """No path here may discard a commit that only one of the two refs holds."""

    def test_an_absent_branch_is_created_from_origin(self, tmp_path):
        repo = _repo_on_main(tmp_path)
        head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "update-ref", f"refs/remotes/origin/{_BRANCH}", head)

        assert rebase_target.checkout_target_branch(str(repo), _ctx()) == 0
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == _BRANCH

    def test_unpushed_commits_are_kept(self, tmp_path):
        """`checkout -B` would reset them away before the rebase could replay them."""
        repo = _repo_on_main(tmp_path)
        _git(repo, "checkout", "-q", "-b", _BRANCH)
        pushed = _commit(repo, "pushed.txt", "pushed work")
        _git(repo, "update-ref", f"refs/remotes/origin/{_BRANCH}", pushed)
        unpushed = _commit(repo, "unpushed.txt", "unpushed work")
        _git(repo, "checkout", "-q", "main")

        assert rebase_target.checkout_target_branch(str(repo), _ctx()) == 0
        assert _git(repo, "rev-parse", "HEAD") == unpushed

    def test_a_branch_merely_behind_is_fast_forwarded(self, tmp_path):
        repo = _repo_on_main(tmp_path)
        _git(repo, "checkout", "-q", "-b", _BRANCH)
        behind = _git(repo, "rev-parse", "HEAD")
        ahead = _commit(repo, "remote.txt", "remote work")
        _git(repo, "update-ref", f"refs/remotes/origin/{_BRANCH}", ahead)
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-f", _BRANCH, behind)

        assert rebase_target.checkout_target_branch(str(repo), _ctx()) == 0
        assert _git(repo, "rev-parse", "HEAD") == ahead

    def test_true_divergence_is_refused_rather_than_resolved(self, tmp_path):
        """Replaying a stale local over a force-pushed remote loses the other side."""
        repo = _repo_on_main(tmp_path)
        _git(repo, "checkout", "-q", "-b", _BRANCH)
        base = _commit(repo, "shared.txt", "shared")
        theirs = _commit(repo, "theirs.txt", "their work")
        _git(repo, "update-ref", f"refs/remotes/origin/{_BRANCH}", theirs)
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-f", _BRANCH, base)
        _git(repo, "checkout", "-q", _BRANCH)
        local = _commit(repo, "ours.txt", "our work")
        _git(repo, "checkout", "-q", "main")

        assert rebase_target.checkout_target_branch(str(repo), _ctx()) == 1
        assert _git(repo, "rev-parse", _BRANCH) == local

    def test_a_refusal_names_the_commits_it_will_not_discard(self, tmp_path, capsys):
        repo = _repo_on_main(tmp_path)
        _git(repo, "checkout", "-q", "-b", _BRANCH)
        base = _commit(repo, "shared.txt", "shared")
        theirs = _commit(repo, "theirs.txt", "their work")
        _git(repo, "update-ref", f"refs/remotes/origin/{_BRANCH}", theirs)
        _git(repo, "checkout", "-q", "main")
        _git(repo, "branch", "-f", _BRANCH, base)
        _git(repo, "checkout", "-q", _BRANCH)
        _commit(repo, "ours.txt", "the one they would lose")
        _git(repo, "checkout", "-q", "main")

        rebase_target.checkout_target_branch(str(repo), _ctx())

        assert "the one they would lose" in capsys.readouterr().err
