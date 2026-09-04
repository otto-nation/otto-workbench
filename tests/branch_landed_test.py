"""Tests for the landed-branch signals.

The git signals run against a real repo rather than a mocked `subprocess`:
each one is a claim about what a particular merge style leaves behind, and a
stub returning the string the test author expected would pass whether or not
`git diff --quiet` and `git cherry` actually answer that way for a squash. The
three merge shapes are built here — unlanded, squashed, cherry-picked onto a
target that moved on — because telling them apart is the whole contract.

The tracker signal has no local equivalent, so its transport is stubbed under
`gh_client` and the argv the client builds stays observable from the call.
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import landed as branch_landed  # noqa: E402
from core import timeouts  # noqa: E402

from conftest import git_in, seed_repo  # noqa: E402

_BRANCH = "feat/landed"
_TARGET = "main"
_PR = 726
_URL = "https://x/pull/726"


def _commit(path: Path, name: str, content: str = "x") -> None:
    """Write a file and commit it, with an identity passed per command.

    Never written into the repo's config — the suite's repo-config guard
    watches that, and the fixtures below already commit under the same rule.
    """
    (path / name).write_text(content)
    git_in(path, "add", "--", name)
    git_in(path, "-c", "user.email=t@t", "-c", "user.name=t",
           "commit", "-q", "--no-verify", "-m", f"add {name}")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A one-commit `main` with a two-commit branch beside it, checked out.

    The branch's work is nowhere on `main`, which is the shape every landed
    signal has to answer "no" for before any of them is worth trusting.
    """
    path = seed_repo(tmp_path / "repo")
    git_in(path, "checkout", "-q", "-b", _BRANCH)
    _commit(path, "b.txt", "b")
    _commit(path, "c.txt", "c")
    git_in(path, "checkout", "-q", _TARGET)
    return path


@pytest.fixture
def squashed(repo: Path) -> Path:
    """`repo` with the branch squash-merged into `main`.

    The trees match and not one of the branch's commits is reachable from the
    squashed commit — the merge style this repo uses, and the one that defeats
    every signal that compares commits.
    """
    git_in(repo, "merge", "-q", "--squash", _BRANCH)
    git_in(repo, "-c", "user.email=t@t", "-c", "user.name=t",
           "commit", "-q", "--no-verify", "-m", "squash")
    return repo


@pytest.fixture
def replayed(repo: Path) -> Path:
    """`repo` with the branch's commits cherry-picked onto a `main` that moved on.

    Patch ids still match, so `git cherry` recognises them; the extra commit is
    what stops the trees matching, which is the case an empty diff misses.

    `main` moves on *before* the replay, not after. A cherry-pick onto the same
    parent reproduces the commit byte for byte, sha included, and the branch
    then has nothing of its own for either signal to be asked about.
    """
    _commit(repo, "d.txt", "d")
    git_in(repo, "-c", "user.email=t@t", "-c", "user.name=t",
           "cherry-pick", f"{_BRANCH}~1", _BRANCH)
    return repo


# ── merged_pr ───────────────────────────────────────────────────────────────


def _gh_response(payload: str, returncode: int = 0):
    """Patch the transport so the gh call answers with *payload*.

    Stubbed under `gh_client` rather than at it, so the argv the client builds
    and the tier it picks are both still observable from the call.
    """
    return mock.patch(
        "core.proc.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=returncode, stdout=payload, stderr="",
        ),
    )


def test_merged_pr_reports_a_merged_pull_request():
    payload = f'{{"state": "MERGED", "number": {_PR}, "url": "{_URL}"}}'

    with _gh_response(payload) as mock_try:
        answer = branch_landed.merged_pr(
            "/fake", branch=_BRANCH, repo="owner/repo", pr_number=_PR,
        )

    assert answer == branch_landed.MergedPR(number=_PR, url=_URL)

    cmd = mock_try.call_args[0][0]
    assert cmd[:4] == ["gh", "pr", "view", str(_PR)]
    assert cmd[4:6] == ["--repo", "owner/repo"]


def test_merged_pr_falls_back_to_the_branch_without_a_pr_number():
    with _gh_response('{"state": "MERGED", "number": 1, "url": ""}') as mock_try:
        answer = branch_landed.merged_pr("/fake", branch=_BRANCH)

    assert answer == branch_landed.MergedPR(number=1)
    assert mock_try.call_args[0][0][3] == _BRANCH


def test_merged_pr_omits_repo_when_the_caller_names_none():
    """gh infers the repo from the remote — an empty --repo value would not."""
    with _gh_response('{"state": "MERGED", "number": 1, "url": ""}') as mock_try:
        branch_landed.merged_pr("/fake", branch=_BRANCH, repo="")

    assert "--repo" not in mock_try.call_args[0][0]


def test_merged_pr_bounds_the_gh_call():
    """One round trip, so the bound is the network tier rather than a number
    this file picked for itself.
    """
    with _gh_response('{"state": "OPEN"}') as mock_try:
        branch_landed.merged_pr("/fake", branch=_BRANCH)

    assert mock_try.call_args.kwargs["timeout"] == timeouts.NETWORK


def test_merged_pr_degrades_when_gh_times_out():
    """A timeout is "the tracker has nothing to say", not a crash."""
    expired = subprocess.TimeoutExpired(cmd=["gh"], timeout=timeouts.NETWORK)

    with mock.patch("subprocess.run", side_effect=expired):
        assert branch_landed.merged_pr("/fake", branch=_BRANCH) is None


@pytest.mark.parametrize("payload,returncode", [
    ('{"state": "OPEN", "number": 726, "url": ""}', 0),
    ('{"state": "CLOSED", "number": 726, "url": ""}', 0),
    ("no such pull request", 1),
    ("not json at all", 0),
    ("{}", 0),
])
def test_merged_pr_stays_silent_unless_github_says_merged(payload, returncode):
    """Anything short of MERGED is "the tracker has nothing to say"."""
    with _gh_response(payload, returncode=returncode):
        assert branch_landed.merged_pr("/fake", branch=_BRANCH) is None


def test_merged_pr_survives_gh_being_absent():
    """The client answers a missing gh with a result, not an exception."""
    with mock.patch("core.proc.subprocess.run", side_effect=FileNotFoundError):
        assert branch_landed.merged_pr("/fake", branch=_BRANCH) is None


# ── diff_is_empty ───────────────────────────────────────────────────────────


def test_diff_is_empty_sees_the_tree_a_squash_merge_left(squashed):
    assert branch_landed.diff_is_empty(squashed, target_ref=_TARGET, rev=_BRANCH)


def test_diff_is_empty_is_false_for_a_branch_whose_work_is_not_there(repo):
    assert not branch_landed.diff_is_empty(repo, target_ref=_TARGET, rev=_BRANCH)


def test_diff_is_empty_misses_a_target_that_moved_on(replayed):
    """Why the patch-id signal exists: same work, and the trees still differ."""
    assert not branch_landed.diff_is_empty(replayed, target_ref=_TARGET, rev=_BRANCH)


def test_diff_is_empty_compares_the_named_rev(squashed):
    """The rev is the caller's to name — `pr rebase` means HEAD, `push_intent`
    means the commit it recorded.
    """
    assert branch_landed.diff_is_empty(squashed, target_ref=_TARGET, rev=f"{_BRANCH}~1") is False


# ── all_commits_upstream ────────────────────────────────────────────────────


def test_all_commits_upstream_matches_patch_ids_across_a_replay(replayed):
    assert branch_landed.all_commits_upstream(replayed, target_ref=_TARGET, rev=_BRANCH)


def test_all_commits_upstream_misses_a_squash_merge(squashed):
    """Why the empty-diff signal exists: the work landed, no patch id survived."""
    assert not branch_landed.all_commits_upstream(
        squashed, target_ref=_TARGET, rev=_BRANCH,
    )


def test_all_commits_upstream_is_false_when_one_commit_is_missing(repo):
    """Every commit has to have an equivalent — a half-landed branch has not."""
    _commit(repo, "d.txt", "d")
    git_in(repo, "-c", "user.email=t@t", "-c", "user.name=t",
           "cherry-pick", f"{_BRANCH}~1")
    assert not branch_landed.all_commits_upstream(repo, target_ref=_TARGET, rev=_BRANCH)


def test_all_commits_upstream_is_false_when_git_cherry_cannot_answer(repo):
    """A ref this repo has never fetched is not evidence the work landed."""
    assert not branch_landed.all_commits_upstream(
        repo, target_ref="origin/never-fetched", rev=_BRANCH,
    )


# ── by_tracker ──────────────────────────────────────────────────────────────


def _run_by_tracker(merged=None):
    """The tracker signal with gh's answer forced."""
    with mock.patch.object(branch_landed, "merged_pr", return_value=merged):
        return branch_landed.by_tracker("/fake", branch=_BRANCH)


def test_by_tracker_reports_a_merged_pr():
    landed = _run_by_tracker(branch_landed.MergedPR(number=_PR, url=_URL))

    assert landed.signal == branch_landed.LandedSignal.PR_MERGED
    assert landed.pr_number == _PR
    assert landed.detail == f"PR #{_PR} is merged ({_URL})"


def test_by_tracker_omits_the_link_when_gh_reports_no_url():
    """The detail sentence reaches an operator — no empty parentheses."""
    landed = _run_by_tracker(branch_landed.MergedPR(number=_PR))

    assert landed.detail == f"PR #{_PR} is merged"


def test_by_tracker_leaves_commits_ahead_unmeasured():
    """It can run before the checkout, so there is no honest count — null, not
    a number read off somebody else's HEAD.
    """
    assert _run_by_tracker(branch_landed.MergedPR(number=_PR)).commits_ahead is None


def test_by_tracker_answers_none_when_github_has_nothing_to_say():
    assert _run_by_tracker(None) is None


def test_by_tracker_never_reads_the_worktree():
    """`pr rebase` runs it before the checkout, so reaching for HEAD here would
    answer about whatever branch the worktree is still on.
    """
    with mock.patch.object(branch_landed, "merged_pr",
                           return_value=branch_landed.MergedPR(number=_PR)), \
         mock.patch.object(branch_landed, "diff_is_empty") as diff, \
         mock.patch.object(branch_landed, "all_commits_upstream") as cherry:
        branch_landed.by_tracker("/fake", branch=_BRANCH)

    diff.assert_not_called()
    cherry.assert_not_called()


# ── by_git ──────────────────────────────────────────────────────────────────


def test_by_git_catches_a_squash_merge_by_empty_diff(squashed):
    landed = branch_landed.by_git(squashed, target_ref=_TARGET, rev=_BRANCH)

    assert landed.signal == branch_landed.LandedSignal.EMPTY_DIFF
    assert landed.commits_ahead == 2
    assert landed.pr_number is None
    assert landed.detail == f"2 commit(s) ahead of {_TARGET} but no diff against it"


def test_by_git_catches_a_replay_by_patch_id(replayed):
    landed = branch_landed.by_git(replayed, target_ref=_TARGET, rev=_BRANCH)

    assert landed.signal == branch_landed.LandedSignal.COMMITS_UPSTREAM
    assert landed.commits_ahead == 2
    assert landed.detail == f"all 2 commit(s) already have an equivalent in {_TARGET}"


def test_by_git_passes_an_unlanded_branch(repo):
    assert branch_landed.by_git(repo, target_ref=_TARGET, rev=_BRANCH) is None


def test_by_git_ignores_a_rev_with_no_commits_of_its_own(repo):
    """Both git signals read as landed for a freshly cut branch — an empty diff
    and an empty `git cherry` are vacuously true there.
    """
    assert branch_landed.by_git(repo, target_ref=_TARGET, rev=_TARGET) is None


def test_by_git_answers_none_for_a_ref_it_cannot_resolve(repo):
    """The count comes back 0, which is the same door the empty branch takes."""
    assert branch_landed.by_git(repo, target_ref="origin/never-fetched") is None


# ── check ───────────────────────────────────────────────────────────────────


def test_check_spends_no_round_trip_when_git_can_see_it(squashed):
    with mock.patch.object(branch_landed, "merged_pr") as gh:
        landed = branch_landed.check(
            squashed, target_ref=_TARGET, branch=_BRANCH, rev=_BRANCH,
        )

    assert landed.signal == branch_landed.LandedSignal.EMPTY_DIFF
    gh.assert_not_called()


def test_check_falls_back_to_the_tracker_when_git_cannot_see_it(repo):
    """The squash whose target moved on: no matching tree, no matching patch id,
    and the head ref the merge deleted. Only GitHub still knows.
    """
    with mock.patch.object(branch_landed, "merged_pr",
                           return_value=branch_landed.MergedPR(number=_PR)):
        landed = branch_landed.check(
            repo, target_ref=_TARGET, branch=_BRANCH, rev=_BRANCH,
        )

    assert landed.signal == branch_landed.LandedSignal.PR_MERGED
    assert landed.pr_number == _PR


def test_check_answers_none_when_no_signal_does(repo):
    with mock.patch.object(branch_landed, "merged_pr", return_value=None):
        assert branch_landed.check(
            repo, target_ref=_TARGET, branch=_BRANCH, rev=_BRANCH,
        ) is None
