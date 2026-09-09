"""Tests for `git.replay` — did a rewrite orphan this commit, and what replays it.

Real git throughout, because the bug this module exists for is precisely the
disagreement between two ways of asking about a rewritten commit: the recorded
SHA still resolves as an object, and no branch anywhere contains it. Nothing
short of an actual rebase produces that pair, so a mocked `git` here would test
the mock.

`test_review_threads.py::TestFollowHistoryRewrite` drives the same fixtures
through the `pr.history_rewrite` caller. What is here is the layer below it,
tested directly for the first time.
"""

import sys
from pathlib import Path

from conftest import REPO_ROOT, git_in, git_out, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from git import replay  # noqa: E402

# One static subject and one fixed date for every fix commit, which is the
# situation `replayed_commit` has to work in: the pass commits under a single
# subject, so two rounds are indistinguishable by header alone.
_SUBJECT = "fix(ai): address review comments"
_DATE = "2026-01-01T00:00:00"


def _short(work) -> str:
    return git_out(work, "rev-parse", "--short", "HEAD").strip()


def _commit(work, name, content=None) -> str:
    (work / name).write_text(content if content is not None else f"{name}\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "--date", _DATE, "-m", _SUBJECT)
    return _short(work)


@pytest.fixture
def work(tmp_path):
    """A clone with `main` pushed and `feature` checked out."""
    origin = tmp_path / "origin"
    run_checked(["git", "init", "--bare", "-q", "-b", "main", str(origin)])
    wt = tmp_path / "work"
    run_checked(["git", "clone", "-q", str(origin), str(wt)])
    git_in(wt, "config", "user.email", "t@example.com")
    git_in(wt, "config", "user.name", "Test")
    (wt / "base.txt").write_text("base\n")
    git_in(wt, "add", "-A")
    git_in(wt, "commit", "-q", "--no-verify", "-m", "base")
    git_in(wt, "push", "-q", "-u", "origin", "main")
    git_in(wt, "checkout", "-q", "-b", "feature")
    return wt


def _rebase_onto_moved_main(wt) -> None:
    """Move `main` on and replay `feature` over it, the way `pr rebase` does."""
    git_in(wt, "checkout", "-q", "main")
    (wt / "upstream.txt").write_text("upstream\n")
    git_in(wt, "add", "-A")
    git_in(wt, "commit", "-q", "--no-verify", "-m", "upstream work")
    git_in(wt, "push", "-q", "origin", "main")
    git_in(wt, "checkout", "-q", "feature")
    git_in(wt, "rebase", "-q", "origin/main")


class TestRewrittenAway:
    def test_a_commit_still_on_the_branch_is_not_orphaned(self, work):
        sha = _commit(work, "fix.txt")
        assert replay.rewritten_away(work, sha) is False

    def test_a_rebased_commit_is_orphaned(self, work):
        sha = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        assert replay.rewritten_away(work, sha) is True

    def test_the_orphan_still_resolves_as_an_object(self, work):
        """The whole reason existence is the wrong question to ask."""
        sha = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        assert git_out(work, "cat-file", "-t", sha).strip() == "commit"
        assert replay.rewritten_away(work, sha) is True

    def test_a_sha_git_cannot_resolve_is_not_read_as_a_rewrite(self, work):
        """A question git declined to answer must never clear a hold."""
        _commit(work, "fix.txt")
        assert replay.rewritten_away(work, "0" * 40) is False


class TestPatchIds:
    def test_a_commit_maps_to_the_patch_it_carries(self, work):
        sha = _commit(work, "fix.txt")
        ids = replay.patch_ids(work, "--no-walk", sha)
        assert len(ids) == 1
        assert len(next(iter(ids.values()))) == 1

    def test_two_commits_carrying_one_patch_share_an_id(self, work):
        """What makes a replay recognisable across a rebase."""
        first = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        after = _short(work)

        before_ids = replay.patch_ids(work, "--no-walk", first)
        after_ids = replay.patch_ids(work, "--no-walk", after)

        assert set(before_ids) == set(after_ids)

    def test_different_changes_do_not_collide(self, work):
        _commit(work, "one.txt")
        _commit(work, "two.txt")
        assert len(replay.patch_ids(work, "HEAD~2..HEAD")) == 2

    def test_an_empty_range_maps_nothing(self, work):
        _commit(work, "fix.txt")
        assert replay.patch_ids(work, "HEAD..HEAD") == {}

    def test_an_unresolvable_range_maps_nothing(self, work):
        assert replay.patch_ids(work, "no-such-ref..HEAD") == {}


class TestReplayedCommit:
    def test_a_rebased_commit_is_found_under_its_new_name(self, work):
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)

        found = replay.replayed_commit(work, held)

        assert found == _short(work)
        assert found != held

    def test_an_amended_commit_still_matches(self, work):
        """A reword does not touch the diff, which is what is matched on."""
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "commit", "-q", "--no-verify", "--amend", "-m", "reworded")

        assert replay.replayed_commit(work, held) == _short(work)

    def test_a_dropped_commit_is_not_found(self, work):
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "reset", "-q", "--hard", "HEAD~1")

        assert replay.replayed_commit(work, held) == ""

    def test_a_reworked_fix_is_not_found(self, work):
        """The honest answer: that change is not on the branch under any name."""
        held = _commit(work, "fix.txt", "original\n")
        # Drop the fix and land a different change in its place, which is what a
        # rework looks like to the branch: same file, same subject, new content.
        git_in(work, "reset", "-q", "--hard", "HEAD~1")
        _rebase_onto_moved_main(work)
        _commit(work, "fix.txt", "reworked entirely\n")

        assert replay.replayed_commit(work, held) == ""

    def test_a_duplicated_patch_is_refused_rather_than_guessed(self, work):
        """Two answers is no answer: nothing to choose between them."""
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "revert", "--no-edit", "HEAD")
        git_in(work, "cherry-pick", held)

        assert replay.replayed_commit(work, held) == ""

    def test_a_commit_still_on_the_branch_is_not_its_own_replay(self, work):
        """Asked out of order, this answers "no", which is why order matters.

        The search runs from the orphan's merge base, which for a commit still
        on the branch is that commit itself — so the range excludes it and
        nothing matches. Callers ask `rewritten_away` first and only reach here
        when it says yes; this pins that the pair is a sequence, not two
        independent questions.
        """
        sha = _commit(work, "fix.txt")

        assert replay.rewritten_away(work, sha) is False
        assert replay.replayed_commit(work, sha) == ""
