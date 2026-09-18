"""Tests for rebase.lease — what the remote must hold for a force-push.

The two real-git classes at the bottom are the point of this file. The lease is
a claim about how git behaves, and a mock cannot falsify it: the bug being fixed
was a lease that looked correct and was satisfied by the tool's own fetch.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from rebase import lease as rebase_lease

GIT_TIMEOUT = 30  # seconds; a hang here should fail the test, not stall the suite

_IDENTITY = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(cwd, *args, check=True):
    """Run git in *cwd* with a fixed identity, returning the CompletedProcess."""
    import os
    env = {**os.environ, **_IDENTITY}
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
        check=check, timeout=GIT_TIMEOUT, env=env,
    )


def _push(cwd, *args):
    """Attempt a push, returning (ok, combined output) rather than raising."""
    done = _git(cwd, "push", *args, check=False)
    return done.returncode == 0, done.stdout + done.stderr


def _remote_with_branch(tmp_path):
    """A bare origin, a clone holding `feat`, and main one commit ahead.

    The shape every scenario below starts from: `feat` is published, and there
    is somewhere for it to be rebased onto.
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    hub = tmp_path / "hub"
    _git(tmp_path, "clone", "-q", str(origin), str(hub))
    _git(hub, "commit", "-q", "--allow-empty", "-m", "base")
    _git(hub, "push", "-q", "origin", "HEAD:main")
    _git(hub, "checkout", "-q", "-b", "feat")
    (hub / "f").write_text("a\n")
    _git(hub, "add", "f")
    _git(hub, "commit", "-q", "-m", "feat-A")
    # -u so a later bare `git push` resolves the branch the way it does in a
    # real checkout; without it these tests would fail on the upstream lookup
    # rather than on the lease under test.
    _git(hub, "push", "-q", "-u", "origin", "feat")
    _git(hub, "checkout", "-q", "main")
    (hub / "g").write_text("m\n")
    _git(hub, "add", "g")
    _git(hub, "commit", "-q", "-m", "main-2")
    _git(hub, "push", "-q", "origin", "main")
    _git(hub, "checkout", "-q", "feat")
    return origin, hub


def _colleague_pushes(tmp_path, origin):
    """A second clone lands a commit on `feat` that our clone has never seen."""
    other = tmp_path / "colleague"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _git(other, "checkout", "-q", "feat")
    (other / "colleague.txt").write_text("c\n")
    _git(other, "add", "colleague.txt")
    _git(other, "commit", "-q", "-m", "COLLEAGUE")
    _git(other, "push", "-q", "origin", "feat")


def _has_colleague_commit(origin):
    return "COLLEAGUE" in _git(origin, "log", "--oneline", "feat").stdout


class TestPushLease:
    """The flag a lease renders to."""

    def test_it_names_the_full_ref_and_the_expected_commit(self):
        lease = rebase_lease.PushLease(branch="isaac/feat/x", expect="abc123")
        assert lease.args == (
            "--force-with-lease=refs/heads/isaac/feat/x:abc123",
        )

    def test_it_is_one_argument_because_that_is_the_syntax_git_parses(self):
        assert len(rebase_lease.PushLease(branch="f", expect="a1").args) == 1

    def test_an_empty_expect_renders_the_create_form(self):
        lease = rebase_lease.PushLease(
            branch="feat", expect=rebase_lease.CREATES_THE_REF,
        )
        assert lease.args == ("--force-with-lease=refs/heads/feat:",)
        assert lease.creates_the_ref

    def test_a_named_commit_is_not_the_create_form(self):
        assert not rebase_lease.PushLease(branch="feat", expect="a1").creates_the_ref


class TestRememberedTip:
    """What the tool reads before it fetches."""

    def test_it_prefers_the_tracking_ref_over_the_local_branch(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)
        # An unpushed commit puts the local branch ahead of the remote. Naming
        # the local tip in a lease would fail every legitimate push.
        (hub / "local.txt").write_text("x\n")
        _git(hub, "add", "local.txt")
        _git(hub, "commit", "-q", "-m", "unpushed")

        remembered = rebase_lease.remembered_tip(str(hub), "feat")

        tracking = _git(hub, "rev-parse", "refs/remotes/origin/feat").stdout.strip()
        local = _git(hub, "rev-parse", "refs/heads/feat").stdout.strip()
        assert remembered == tracking
        assert remembered != local

    def test_it_falls_back_to_the_local_ref_when_nothing_is_tracked(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)
        _git(hub, "checkout", "-q", "-b", "brand-new")

        remembered = rebase_lease.remembered_tip(str(hub), "brand-new")

        assert remembered == _git(
            hub, "rev-parse", "refs/heads/brand-new",
        ).stdout.strip()

    def test_it_is_empty_for_a_branch_that_exists_nowhere(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)
        assert rebase_lease.remembered_tip(str(hub), "no-such-branch") == ""


class TestResolve:
    """Choosing between the two legal shapes."""

    def test_a_published_branch_leases_against_the_remembered_tip(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)
        remembered = rebase_lease.remembered_tip(str(hub), "feat")

        lease = rebase_lease.resolve(str(hub), "feat", remembered)

        assert lease is not None
        assert lease.expect == remembered

    def test_a_branch_the_remote_lacks_leases_on_creation(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)
        lease = rebase_lease.resolve(str(hub), "never-pushed", "")
        assert lease is not None
        assert lease.creates_the_ref

    def test_it_refuses_rather_than_guess_when_the_tip_is_unknown(self, tmp_path):
        # The ref is on the remote but this run never read where it was. Both
        # fallbacks are wrong, so there is no lease to give.
        _origin, hub = _remote_with_branch(tmp_path)
        assert rebase_lease.resolve(str(hub), "feat", "") is None


class TestAgainstRealGit:
    """What git does with the leases this module builds.

    These are the assertions that would have caught the bug. Each drives the
    tool's own sequence — remember the tip, fetch, rebase, push — and checks
    the outcome on the bare remote rather than the command's exit code alone.
    """

    @staticmethod
    def _run_the_tool(hub):
        """Remember, fetch, rebase: everything up to the push."""
        remembered = rebase_lease.remembered_tip(str(hub), "feat")
        _git(hub, "fetch", "-q", "--prune", "origin")
        _git(hub, "rebase", "-q", "origin/main", check=False)
        return remembered

    def test_it_refuses_to_overwrite_a_commit_the_fetch_brought_down(self, tmp_path):
        origin, hub = _remote_with_branch(tmp_path)
        _colleague_pushes(tmp_path, origin)

        remembered = self._run_the_tool(hub)
        lease = rebase_lease.resolve(str(hub), "feat", remembered)
        ok, output = _push(hub, *lease.args)

        assert not ok
        assert "stale info" in output
        assert _has_colleague_commit(origin), (
            "the colleague's commit must survive a refused push"
        )

    def test_the_bare_lease_this_replaced_destroys_that_commit(self, tmp_path):
        # The bug, pinned. If this ever stops force-pushing, the explicit lease
        # above is no longer load-bearing and this file can shrink.
        origin, hub = _remote_with_branch(tmp_path)
        _colleague_pushes(tmp_path, origin)

        self._run_the_tool(hub)
        ok, _output = _push(hub, "--force-with-lease")

        assert ok
        assert not _has_colleague_commit(origin)

    def test_it_allows_the_ordinary_rebase_of_a_branch_nobody_touched(self, tmp_path):
        _origin, hub = _remote_with_branch(tmp_path)

        remembered = self._run_the_tool(hub)
        lease = rebase_lease.resolve(str(hub), "feat", remembered)
        ok, output = _push(hub, *lease.args)

        assert ok, output

    def test_it_allows_a_rebase_from_a_freshly_materialised_worktree(self, tmp_path):
        # The case that rules out --force-if-includes: a worktree made on
        # demand has only the zero-old "Created from refs/remotes/origin/feat"
        # reflog entry, which that flag does not accept as having seen the
        # remote. This tool makes worktrees exactly that way.
        origin, hub = _remote_with_branch(tmp_path)
        _git(hub, "checkout", "-q", "main")
        _git(hub, "branch", "-q", "-D", "feat")
        fresh = tmp_path / "fresh"
        _git(hub, "worktree", "add", "-q", str(fresh), "feat")

        remembered = self._run_the_tool(fresh)
        lease = rebase_lease.resolve(str(fresh), "feat", remembered)
        ok, output = _push(fresh, *lease.args)

        assert ok, output
        assert "--force-if-includes" not in " ".join(lease.args)

    def test_a_first_push_creates_the_branch(self, tmp_path):
        origin, hub = _remote_with_branch(tmp_path)
        _git(hub, "checkout", "-q", "-b", "brand-new")
        (hub / "n").write_text("n\n")
        _git(hub, "add", "n")
        _git(hub, "commit", "-q", "-m", "new work")

        remembered = rebase_lease.remembered_tip(str(hub), "brand-new")
        lease = rebase_lease.resolve(str(hub), "brand-new", remembered)
        ok, output = _push(hub, *lease.args, "origin", "brand-new")

        assert lease.creates_the_ref
        assert ok, output
        assert "brand-new" in _git(origin, "branch", "--list", "brand-new").stdout

    def test_naming_a_commit_on_a_branch_the_remote_lacks_is_refused(self, tmp_path):
        # Why resolve() must consult the remote rather than always naming a
        # tip: the create case needs the empty expect, and a SHA fails there.
        _origin, hub = _remote_with_branch(tmp_path)
        _git(hub, "checkout", "-q", "-b", "brand-new")
        local = _git(hub, "rev-parse", "refs/heads/brand-new").stdout.strip()

        wrong = rebase_lease.PushLease(branch="brand-new", expect=local)
        ok, output = _push(hub, *wrong.args, "origin", "brand-new")

        assert not ok
        assert "stale info" in output
