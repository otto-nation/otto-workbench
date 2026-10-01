"""Detecting a branch whose leading commits already landed, and replaying it.

Driven against real repositories rather than stubs. What is under test is
whether the *signals* see a shape git produces, and every one of the failures
this exists for came from a signal that was correct about the command it ran
and wrong about the history in front of it — a stub asserting on argv would
have passed for all of them.
"""

import sys
from pathlib import Path

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import landed as branch_landed  # noqa: E402


# How a commit is copied onto main so that the copy is genuinely a *copy*.
#
# `--no-ff` alone is not enough, and the way it fails is intermittent. A
# cherry-pick whose tree, parent, message and both dates match the original
# produces the *identical* SHA — git is content-addressed, so there is no new
# commit at all and main simply fast-forwards onto the branch's own history.
# Whether that happens depends on whether the clock ticked between making the
# commit and copying it, which on a fast machine it usually has not: the same
# fixture passed one run in three.
#
# `-x` appends a "(cherry picked from commit ...)" trailer, which changes the
# message and therefore the SHA, deterministically. It does not touch the diff,
# so the patch id the signal under test reads is unaffected — which is the
# property that makes it the right lever here rather than a slept-on clock.
_COPY = ("cherry-pick", "--no-ff", "-x")


def _commit(repo: Path, name: str, body: str, subject: str) -> str:
    (repo / name).write_text(body)
    git_in(repo, "add", name)
    git_in(repo, "commit", "-q", "-m", subject)
    return git_out(repo, "rev-parse", "HEAD").strip()


def _base_repo(tmp_path) -> Path:
    repo = init_repo(tmp_path / "repo")
    _commit(repo, "root.txt", "root\n", "chore: root")
    return repo


def _branch_of(repo: Path, subjects: list[str]) -> None:
    """A `feat` branch adding one file per subject, cut from main."""
    git_in(repo, "checkout", "-q", "-b", "feat")
    for i, subject in enumerate(subjects):
        _commit(repo, f"f{i}.txt", f"body {i}\n", subject)


# One file every commit edits, so that changing an early line moves the context
# of every hunk after it. Separate files would leave each commit's patch id
# untouched by an amendment to another, which is the opposite of the case under
# test.
_SHARED = "shared.txt"
_SHARED_BASE = ["l1", "l2", "l3", "l4", "l5", "l6"]


def _shared_body(*, first: str, through: int) -> str:
    """``_SHARED_BASE`` with *through* insertions spread down the file.

    Insertion 1 is *first*, which is the line the landed copy amends.
    """
    inserts = [first, "TWO", "THREE", "FOUR"]
    lines = list(_SHARED_BASE)
    for n in range(through):
        lines.insert(1 + 2 * n, inserts[n])
    return "\n".join(lines) + "\n"


class TestPartialLandingIsSeenWherePatchIdsAreNot:
    """The case that ended a rebase: a prefix landed, and amended on the way.

    `all_commits_upstream` cannot express this at all — it asks whether the
    branch is *entirely* upstream, so one unlanded commit makes it False no
    matter how much of the prefix is there. And patch ids would not have
    matched anyway: one amendment early in the prefix changes the context
    lines of every later commit's hunks, so every one of them diverges.
    """

    _SUBJECTS = ["feat: one", "feat: two", "feat: three", "feat: four"]

    def _landed_prefix_with_an_amendment(self, tmp_path) -> Path:
        """Three of four commits land on main, the first amended on the way.

        Every commit edits the same file, so amending the line commit one adds
        shifts the context of every hunk below it — which is what makes the
        later commits' patch ids differ from the branch's copies of the very
        same work.
        """
        repo = init_repo(tmp_path / "repo")
        (repo / _SHARED).write_text("\n".join(_SHARED_BASE) + "\n")
        git_in(repo, "add", _SHARED)
        git_in(repo, "commit", "-q", "-m", "chore: root")

        git_in(repo, "checkout", "-q", "-b", "feat")
        for n, subject in enumerate(self._SUBJECTS, start=1):
            (repo / _SHARED).write_text(_shared_body(first="ONE", through=n))
            git_in(repo, "commit", "-q", "-am", subject)

        git_in(repo, "checkout", "-q", "main")
        for n, subject in enumerate(self._SUBJECTS[:3], start=1):
            (repo / _SHARED).write_text(
                _shared_body(first="ONE-AMENDED", through=n),
            )
            git_in(repo, "commit", "-q", "-am", subject)
        git_in(repo, "checkout", "-q", "feat")
        return repo

    def test_the_prefix_is_found(self, tmp_path):
        repo = self._landed_prefix_with_an_amendment(tmp_path)
        partial = branch_landed.partial_landing(str(repo), target_ref="main")

        assert partial is not None
        assert partial.landed == 3
        assert partial.unlanded == 1
        assert partial.fork_subject == "feat: three"

    def test_patch_ids_alone_would_have_missed_the_prefix(self, tmp_path):
        """Why the subject signal exists, demonstrated rather than asserted about.

        The amendment breaks the patch-id match for the commits whose hunks it
        moved, so the exact signal sees an incomplete prefix and the
        all-or-nothing signal above it sees nothing at all.
        """
        repo = self._landed_prefix_with_an_amendment(tmp_path)
        commits = branch_landed.branch_commits(
            str(repo), target_ref="main", rev="HEAD",
        )
        equivalent = branch_landed._patch_equivalent(
            str(repo), target_ref="main", rev="HEAD",
        )
        landed_by_patch_id = [c.subject for c in commits if c.sha in equivalent]

        assert "feat: one" not in landed_by_patch_id
        assert "feat: two" not in landed_by_patch_id
        assert not branch_landed.all_commits_upstream(str(repo), target_ref="main")

    def test_the_fork_point_is_what_replays_only_what_is_left(self, tmp_path):
        """The remedy is executable, which is the whole point of the finding."""
        repo = self._landed_prefix_with_an_amendment(tmp_path)
        partial = branch_landed.partial_landing(str(repo), target_ref="main")

        git_in(repo, "rebase", "--onto", "main", partial.fork_point)
        subjects = git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")

        assert [s for s in subjects if s] == ["feat: four"]


class TestPartialLandingStaysQuietWhereItShould:
    def test_a_wholly_unlanded_branch_is_not_partial(self, tmp_path):
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: one", "feat: two"])

        assert branch_landed.partial_landing(str(repo), target_ref="main") is None

    def test_a_wholly_landed_branch_is_not_partial(self, tmp_path):
        """That is `by_git`'s finding, with a different remedy."""
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: one", "feat: two"])
        git_in(repo, "checkout", "-q", "main")
        git_in(repo, "merge", "-q", "--ff-only", "feat")
        git_in(repo, "checkout", "-q", "feat")

        assert branch_landed.partial_landing(str(repo), target_ref="main") is None

    def test_a_one_commit_branch_is_never_partial(self, tmp_path):
        """All-or-nothing by construction, whichever way it goes."""
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: only"])

        assert branch_landed.partial_landing(str(repo), target_ref="main") is None

    def test_a_later_commit_landing_alone_is_not_a_prefix(self, tmp_path):
        """A cherry-pick out of the middle, which no fork point can express.

        Forking past it would drop the commit before it, so this must report
        nothing rather than a remedy that loses work.
        """
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: one", "feat: two", "feat: three"])
        second = git_out(repo, "rev-parse", "HEAD~1").strip()

        git_in(repo, "checkout", "-q", "main")
        git_in(repo, *_COPY, second)
        git_in(repo, "checkout", "-q", "feat")

        assert branch_landed.partial_landing(str(repo), target_ref="main") is None

    def test_a_repeated_subject_out_of_order_is_not_a_prefix(self, tmp_path):
        """The ordering constraint on the loose signal, exercised.

        The branch's first commit lands for real (by subject, not patch id, so
        the watermark moves), which puts an unrelated, decoy `chore:
        regenerate` commit *before* that watermark on main. The branch's own
        `chore: regenerate` never lands anywhere after it. Matching by subject
        without the `since` bound would find the decoy anyway — it is still
        reachable from `target_ref` — and wrongly report the whole branch as
        landed instead of a one-commit prefix.
        """
        repo = _base_repo(tmp_path)
        _commit(repo, "gen.txt", "v0\n", "chore: regenerate")

        git_in(repo, "checkout", "-q", "-b", "feat")
        branch_feat_one = _commit(repo, "f0.txt", "branch feat one\n", "feat: one")
        _commit(repo, "f1.txt", "branch regenerate\n", "chore: regenerate")

        git_in(repo, "checkout", "-q", "main")
        # A genuinely different diff under the same subject: this must match by
        # subject, not by patch id, so it is what moves `since` forward.
        _commit(repo, "main_feat_one.txt", "main feat one\n", "feat: one")
        git_in(repo, "checkout", "-q", "feat")

        partial = branch_landed.partial_landing(str(repo), target_ref="main")

        assert partial is not None
        assert partial.landed == 1
        assert partial.unlanded == 1
        assert partial.fork_point == branch_feat_one
        assert partial.fork_subject == "feat: one"

    def test_a_subject_that_is_only_a_prefix_of_an_upstream_one_does_not_match(
        self, tmp_path,
    ):
        """`--grep` matches a substring, so the whole subject is re-checked."""
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["fix: auth", "feat: two"])

        git_in(repo, "checkout", "-q", "main")
        _commit(repo, "other.txt", "x\n", "fix: auth token refresh")
        git_in(repo, "checkout", "-q", "feat")

        assert branch_landed.partial_landing(str(repo), target_ref="main") is None

    def test_an_unresolvable_target_answers_none_rather_than_raising(self, tmp_path):
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: one", "feat: two"])

        assert branch_landed.partial_landing(
            str(repo), target_ref="origin/nope",
        ) is None


class TestPatchIdsStillCarryThePrefixWhereTheyCan:
    """The exact signal is tried first and is enough on its own when it fires."""

    def test_a_cleanly_cherry_picked_prefix_is_found_by_patch_id(self, tmp_path):
        repo = _base_repo(tmp_path)
        _branch_of(repo, ["feat: one", "feat: two", "feat: three"])
        first = git_out(repo, "rev-parse", "HEAD~2").strip()
        second = git_out(repo, "rev-parse", "HEAD~1").strip()

        git_in(repo, "checkout", "-q", "main")
        git_in(repo, *_COPY, first, second)
        git_in(repo, "checkout", "-q", "feat")

        partial = branch_landed.partial_landing(str(repo), target_ref="main")
        assert partial is not None
        assert partial.landed == 2
        assert partial.unlanded == 1
        assert partial.fork_subject == "feat: two"
