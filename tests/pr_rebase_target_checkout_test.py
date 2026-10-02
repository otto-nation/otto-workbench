"""Tests for rebase.target: checking out the branch and resolving the target ref."""

import sys
from pathlib import Path
from unittest import mock

from conftest import init_worktree

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.topology  # noqa: E402
import rebase.types  # noqa: E402
import rebase.target  # noqa: E402
import rebase.pr_snapshot  # noqa: E402

from pr_rebase_support import _OTHER_BASE, _OTHER_TARGET, _git, _landed_ctx

# ── _checkout_target_branch ─────────────────────────────────────────────────
#
# Against a real repo rather than a subprocess stub: the bug these cover is
# `checkout -B` resetting the branch ref, and only git itself decides where
# a ref lands. A stub asserting on the argv would have passed throughout.

_CHECKOUT_BRANCH = "feat/checkout"


def _commit(repo, name, message):
    """Add a one-file commit and return its sha."""
    (Path(repo) / name).write_text(f"{name}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo_on_main(tmp_path):
    """A repo with one commit on main, checked out there."""
    repo = init_worktree(tmp_path / "repo")
    _git(repo, "config", "user.email", "rebase-test@example.com")
    _git(repo, "config", "user.name", "Rebase Test")
    _commit(repo, "base.txt", "base")
    return repo


def _checkout_ctx():
    ctx = mock.MagicMock()
    ctx.branch = _CHECKOUT_BRANCH
    return ctx


def test_checkout_target_branch_keeps_unpushed_commits(tmp_path):
    """A commit that was never pushed survives the checkout."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    pushed = _commit(repo, "pushed.txt", "pushed work")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", pushed)
    unpushed = _commit(repo, "unpushed.txt", "unpushed work")
    _git(repo, "checkout", "-q", "main")

    rc = rebase.target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == unpushed
    assert _git(repo, "rev-parse", _CHECKOUT_BRANCH) == unpushed
    assert "unpushed work" in _git(repo, "log", "--oneline")


def test_checkout_target_branch_fast_forwards_when_behind(tmp_path):
    """A local ref with nothing of its own still takes the remote's newer tip."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    local = _commit(repo, "one.txt", "one")
    remote = _commit(repo, "two.txt", "two")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "reset", "-q", "--hard", local)
    _git(repo, "checkout", "-q", "main")

    rc = rebase.target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == remote


def test_checkout_target_branch_refuses_when_diverged(tmp_path):
    """Neither side can be dropped, so the run stops instead of picking one."""
    repo = _repo_on_main(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    local = _commit(repo, "local.txt", "local only")
    _git(repo, "checkout", "-q", "-b", "remote-side", base)
    remote = _commit(repo, "remote.txt", "remote only")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-qD", "remote-side")

    rc = rebase.target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 1
    assert _git(repo, "rev-parse", _CHECKOUT_BRANCH) == local
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_checkout_target_branch_names_the_commits_it_will_not_discard(tmp_path, capsys):
    """The refusal has to be actionable, and truncation has to admit itself."""
    repo = _repo_on_main(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    for i in range(rebase.types.UNPUSHED_SUBJECT_LIMIT + 2):
        _commit(repo, f"local{i}.txt", f"local work {i}")
    _git(repo, "checkout", "-q", "-b", "remote-side", base)
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}",
         _commit(repo, "remote.txt", "remote only"))
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-qD", "remote-side")

    assert rebase.target.checkout_target_branch(str(repo), _checkout_ctx()) == 1

    err = capsys.readouterr().err
    assert "local work 11" in err
    assert "... and 2 more" in err


def test_checkout_target_branch_creates_from_origin_when_absent(tmp_path):
    """No local ref means nothing to preserve — -B is how the branch arrives."""
    repo = _repo_on_main(tmp_path)
    remote = _commit(repo, "remote.txt", "remote work")
    _git(repo, "update-ref", f"refs/remotes/origin/{_CHECKOUT_BRANCH}", remote)
    _git(repo, "reset", "-q", "--hard", "HEAD~1")

    rc = rebase.target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == remote


def test_checkout_target_branch_uses_local_when_remote_ref_is_gone(tmp_path):
    """--prune drops origin/<branch>; every commit on it is then unpushed."""
    repo = _repo_on_main(tmp_path)
    _git(repo, "checkout", "-q", "-b", _CHECKOUT_BRANCH)
    tip = _commit(repo, "only.txt", "only local")
    _git(repo, "checkout", "-q", "main")

    rc = rebase.target.checkout_target_branch(str(repo), _checkout_ctx())

    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == tip


# ── target ref resolution ───────────────────────────────────────────────────


def test_pr_base_branch_reads_the_base_the_context_resolved():
    """`pr.context.resolve` already read `baseRefName` off whichever call found
    the PR, so by the time a rebase asks, the answer is in hand."""
    ctx = _landed_ctx(repo="owner/repo", base=_OTHER_BASE)

    assert rebase.target.pr_base_branch("/fake", ctx) == _OTHER_BASE


def test_pr_base_branch_spends_no_round_trip_of_its_own():
    """The read it used to make is the one the context already paid for."""
    ctx = _landed_ctx(repo="owner/repo", base=_OTHER_BASE)

    with mock.patch("core.proc.subprocess.run") as mock_gh:
        rebase.target.pr_base_branch("/fake", ctx)

    mock_gh.assert_not_called()


def test_pr_base_branch_stays_quiet_when_the_context_has_no_base():
    """Empty is "gh could not say" — no PR, no auth, no network alike. Asking
    again here would be the same refusal a second time."""
    with mock.patch("core.proc.subprocess.run") as mock_gh:
        assert rebase.target.pr_base_branch("/fake", _landed_ctx(base="")) is None

    mock_gh.assert_not_called()


def test_pr_base_branch_prefers_a_snapshot_the_caller_already_fetched():
    """The snapshot read six fields in one call; its base is the same fact."""
    snapshot = rebase.pr_snapshot.PRSnapshot(base_ref=_OTHER_BASE)

    assert rebase.target.pr_base_branch(
        "/fake", _landed_ctx(base="stale"), snapshot) == _OTHER_BASE


def _resolve_target(onto=None, *, pr_base=None, default_branch="main",
                    parent_branch=""):
    """Resolve the target ref with every probe forced.

    `parent_branch` pins what `git.topology.stack_parent` returns: left live
    it walks the ancestry of whatever repo the suite is running inside, so
    the rung under test would be decided by the checkout rather than by the
    case. Named apart from the patched attribute so the two are not
    conflated on a fast read.
    """
    with mock.patch.object(rebase.target, "pr_base_branch", return_value=pr_base), \
         mock.patch.object(git.topology, "stack_parent", return_value=parent_branch), \
         mock.patch.object(git.topology, "default_branch",
                           return_value=default_branch):
        return rebase.target.resolve_target_ref("/fake", _landed_ctx(), onto)


def test_resolve_target_ref_prefers_the_onto_flag():
    """The flag is taken verbatim — it may name a remote the probes never see."""
    assert _resolve_target(
        "upstream/trunk", pr_base=_OTHER_BASE, default_branch="master",
    ) == "upstream/trunk"


def test_resolve_target_ref_prefers_the_pr_base_over_the_default_branch():
    assert _resolve_target(pr_base=_OTHER_BASE, default_branch="main") == _OTHER_TARGET


def test_resolve_target_ref_falls_back_to_the_default_branch():
    """Regression: a repo on master was rebased onto a ref it does not have."""
    assert _resolve_target(default_branch="master") == "origin/master"


def test_resolve_target_ref_replays_a_stacked_branch_onto_its_parent():
    """A stack whose parent has no PR yet: GitHub has no base to report, and
    rebasing onto the trunk replays the parent's commits along with this
    branch's own — which is what produces conflicts against work already
    landed on the parent."""
    assert _resolve_target(
        parent_branch="feat/parent", default_branch="main",
    ) == "origin/feat/parent"


def test_resolve_target_ref_prefers_a_pr_base_over_a_derived_parent():
    """An open PR states its base; ancestry only infers one."""
    assert _resolve_target(
        pr_base=_OTHER_BASE, parent_branch="feat/parent",
    ) == _OTHER_TARGET


def test_resolve_target_ref_never_asks_the_default_branch_when_a_pr_answers():
    with mock.patch.object(rebase.target, "pr_base_branch", return_value=_OTHER_BASE), \
         mock.patch.object(git.topology, "default_branch") as mock_default:
        rebase.target.resolve_target_ref("/fake", _landed_ctx(), None)

    mock_default.assert_not_called()
