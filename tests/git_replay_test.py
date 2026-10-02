"""Tests for `git.replay` — did a rewrite orphan this commit, and what replays it.

Real git throughout, because the bug this module exists for is precisely the
disagreement between two ways of asking about a rewritten commit: the recorded
SHA still resolves as an object, and no branch anywhere contains it. Nothing
short of an actual rebase produces that pair, so a mocked `git` here would test
the mock.

`pr_history_rewrite_test.py::TestFollowHistoryRewrite` drives the same fixtures
through the `pr.history_rewrite` caller. What is here is the layer below it,
tested directly for the first time.
"""

import sys

from conftest import REPO_ROOT, git_in, git_out, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.proc  # noqa: E402
import git.client  # noqa: E402
import git.replay  # noqa: E402

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
        assert git.replay.rewritten_away(work, sha) is False

    def test_a_rebased_commit_is_orphaned(self, work):
        sha = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        assert git.replay.rewritten_away(work, sha) is True

    def test_the_orphan_still_resolves_as_an_object(self, work):
        """The whole reason existence is the wrong question to ask."""
        sha = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        assert git_out(work, "cat-file", "-t", sha).strip() == "commit"
        assert git.replay.rewritten_away(work, sha) is True

    def test_a_sha_git_cannot_resolve_is_not_read_as_a_rewrite(self, work):
        """A question git declined to answer must never clear a hold."""
        _commit(work, "fix.txt")
        assert git.replay.rewritten_away(work, "0" * 40) is False


class TestPatchIds:
    def test_a_commit_maps_to_the_patch_it_carries(self, work):
        sha = _commit(work, "fix.txt")
        ids = git.replay.patch_ids(work, "--no-walk", sha)
        assert len(ids) == 1
        assert len(next(iter(ids.values()))) == 1

    def test_two_commits_carrying_one_patch_share_an_id(self, work):
        """What makes a replay recognisable across a rebase."""
        first = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        after = _short(work)

        before_ids = git.replay.patch_ids(work, "--no-walk", first)
        after_ids = git.replay.patch_ids(work, "--no-walk", after)

        assert set(before_ids) == set(after_ids)

    def test_different_changes_do_not_collide(self, work):
        _commit(work, "one.txt")
        _commit(work, "two.txt")
        assert len(git.replay.patch_ids(work, "HEAD~2..HEAD")) == 2

    def test_an_empty_range_maps_nothing(self, work):
        _commit(work, "fix.txt")
        assert git.replay.patch_ids(work, "HEAD..HEAD") == {}

    def test_an_unresolvable_range_maps_nothing(self, work):
        assert git.replay.patch_ids(work, "no-such-ref..HEAD") == {}


class TestReplayedCommit:
    def test_a_rebased_commit_is_found_under_its_new_name(self, work):
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)

        found = git.replay.replayed_commit(work, held)

        assert found == _short(work)
        assert found != held

    def test_an_amended_commit_still_matches(self, work):
        """A reword does not touch the diff, which is what is matched on."""
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "commit", "-q", "--no-verify", "--amend", "-m", "reworded")

        assert git.replay.replayed_commit(work, held) == _short(work)

    def test_a_dropped_commit_is_not_found(self, work):
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "reset", "-q", "--hard", "HEAD~1")

        assert git.replay.replayed_commit(work, held) == ""

    def test_a_reworked_fix_is_not_found(self, work):
        """The honest answer: that change is not on the branch under any name."""
        held = _commit(work, "fix.txt", "original\n")
        # Drop the fix and land a different change in its place, which is what a
        # rework looks like to the branch: same file, same subject, new content.
        git_in(work, "reset", "-q", "--hard", "HEAD~1")
        _rebase_onto_moved_main(work)
        _commit(work, "fix.txt", "reworked entirely\n")

        assert git.replay.replayed_commit(work, held) == ""

    def test_a_duplicated_patch_is_refused_rather_than_guessed(self, work):
        """Two answers is no answer: nothing to choose between them."""
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "revert", "--no-edit", "HEAD")
        git_in(work, "cherry-pick", held)

        assert git.replay.replayed_commit(work, held) == ""

    def test_a_commit_still_on_the_branch_is_not_its_own_replay(self, work):
        """Asked out of order, this answers "no", which is why order matters.

        The search runs from the orphan's merge base, which for a commit still
        on the branch is that commit itself — so the range excludes it and
        nothing matches. Callers ask `rewritten_away` first and only reach here
        when it says yes; this pins that the pair is a sequence, not two
        independent questions.
        """
        sha = _commit(work, "fix.txt")

        assert git.replay.rewritten_away(work, sha) is False
        assert git.replay.replayed_commit(work, sha) == ""


def _conflicting_rebase(work) -> None:
    """Rebase over an upstream edit to the same line, resolving to a third text.

    The resolution changes the replayed commit's hunks, so its patch id is not
    the original's: content alone cannot recognise it.
    """
    git_in(work, "checkout", "-q", "main")
    (work / "base.txt").write_text("upstream\n")
    git_in(work, "commit", "-q", "--no-verify", "-am", "upstream edit")
    git_in(work, "push", "-q", "origin", "main")
    git_in(work, "checkout", "-q", "feature")
    stopped = run_checked(["git", "rebase", "-q", "origin/main"], cwd=work, check=False)
    assert stopped.returncode != 0
    (work / "base.txt").write_text("resolved\n")
    git_in(work, "add", "base.txt")
    git_in(work, "-c", "core.editor=true", "rebase", "--continue")


class TestReplayFinder:
    """The typed answer, and the evidence it rests on."""

    def test_a_conflict_resolved_pick_is_found_from_gits_record(
        self, work, record_rewrites,
    ):
        record_rewrites(work)
        held = _commit(work, "base.txt", "feature\n")
        _conflicting_rebase(work)

        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.status is git.replay.ReplayStatus.FOUND
        assert replay.source is git.replay.ReplaySource.REWRITE_LOG
        assert replay.sha == _short(work)

    def test_without_the_record_a_changed_patch_is_not_found(self, work):
        """What the record adds: content matching cannot see this replay."""
        held = _commit(work, "base.txt", "feature\n")
        _conflicting_rebase(work)

        assert git.replay.ReplayFinder(work).find(held).status is git.replay.ReplayStatus.NONE

    def test_a_fixup_target_is_followed_to_the_commit_it_was_folded_into(
        self, work, record_rewrites,
    ):
        record_rewrites(work)
        held = _commit(work, "fix.txt", "first\n")
        (work / "fix.txt").write_text("first, fixed\n")
        git_in(work, "commit", "-q", "--no-verify", "-am", f"fixup! {_SUBJECT}")
        git_in(work, "-c", "sequence.editor=true", "rebase", "-q", "-i",
               "--autosquash", "origin/main")

        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.found
        assert replay.sha == _short(work)

    def test_a_rebase_then_an_amend_is_followed_to_the_end(self, work, record_rewrites):
        record_rewrites(work)
        held = _commit(work, "fix.txt", "first\n")
        _rebase_onto_moved_main(work)
        (work / "fix.txt").write_text("amended\n")
        git_in(work, "commit", "-q", "--no-verify", "-a", "--amend", "--no-edit")

        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.source is git.replay.ReplaySource.REWRITE_LOG
        assert replay.sha == _short(work)

    def test_a_fix_that_landed_upstream_is_found_on_upstream(self, work):
        """The rebase drops the branch copy, so the upstream one is the replay.

        Only a search from the old merge base reaches it: a range limited to the
        branch's own commits would report the work as gone.
        """
        held = _commit(work, "fix.txt")
        git_in(work, "checkout", "-q", "main")
        # Move main first: a cherry-pick onto the fix's own parent within the
        # same second reproduces the very same commit, which orphans nothing.
        (work / "upstream.txt").write_text("upstream\n")
        git_in(work, "add", "-A")
        git_in(work, "commit", "-q", "--no-verify", "-m", "upstream work")
        git_in(work, "cherry-pick", held)
        upstream_copy = _short(work)
        git_in(work, "push", "-q", "origin", "main")
        git_in(work, "checkout", "-q", "feature")
        git_in(work, "rebase", "-q", "origin/main")
        assert git_out(work, "rev-parse", "HEAD").strip() == git_out(
            work, "rev-parse", "origin/main").strip()

        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.status is git.replay.ReplayStatus.FOUND
        assert replay.source is git.replay.ReplaySource.PATCH_ID
        assert replay.sha == upstream_copy

    def test_only_commits_sharing_a_path_are_diffed_in_full(self, work, monkeypatch):
        """A big rebase drags in every upstream commit; most cannot match."""
        held = _commit(work, "fix.txt")
        git_in(work, "checkout", "-q", "main")
        for n in range(25):
            (work / f"upstream-{n}.txt").write_text(f"{n}\n")
            git_in(work, "add", "-A")
            git_in(work, "commit", "-q", "--no-verify", "-m", f"upstream {n}")
        git_in(work, "push", "-q", "origin", "main")
        git_in(work, "checkout", "-q", "feature")
        git_in(work, "rebase", "-q", "origin/main")
        diffed = []
        real = git.replay._patch_ids
        monkeypatch.setattr(git.replay, "_patch_ids",
                            lambda wt, *revs: diffed.append(revs) or real(wt, *revs))

        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.sha == _short(work)
        candidate_calls = [revs for revs in diffed if held not in "".join(revs)]
        assert candidate_calls == [("--no-walk", git_out(work, "rev-parse", "HEAD").strip())]

    def test_a_range_git_cannot_list_in_time_is_unknown_not_none(
        self, work, monkeypatch,
    ):
        """A timeout says nothing about the work, and must not read as "gone"."""
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        real = git.client.run

        def slow_listing(*args, **kwargs):
            if "--name-only" in args and any(a.endswith("..HEAD") for a in args):
                return core.proc.CmdResult(
                    returncode=core.proc.TIMEOUT_RETURNCODE,
                    stderr="timed out after 10s: git log --name-only",
                )
            return real(*args, **kwargs)

        monkeypatch.setattr(git.client, "run", slow_listing)
        replay = git.replay.ReplayFinder(work).find(held)

        assert replay.status is git.replay.ReplayStatus.UNKNOWN
        assert "timed out" in replay.detail

    def test_a_duplicated_patch_is_ambiguous_and_a_dropped_one_is_none(self, work):
        held = _commit(work, "fix.txt")
        _rebase_onto_moved_main(work)
        git_in(work, "revert", "--no-edit", "HEAD")
        git_in(work, "cherry-pick", held)
        assert git.replay.ReplayFinder(work).find(held).status is (
            git.replay.ReplayStatus.AMBIGUOUS)

        git_in(work, "reset", "-q", "--hard", "origin/main")
        assert git.replay.ReplayFinder(work).find(held).status is (
            git.replay.ReplayStatus.NONE)

    def test_one_finder_walks_a_shared_range_once(self, work, monkeypatch):
        first = _commit(work, "one.txt")
        second = _commit(work, "two.txt")
        _rebase_onto_moved_main(work)
        listings = []
        real = git.client.run

        def counting(*args, **kwargs):
            if "--name-only" in args and any(a.endswith("..HEAD") for a in args):
                listings.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(git.client, "run", counting)
        finder = git.replay.ReplayFinder(work)

        assert finder.find(first).found and finder.find(second).found
        assert len(listings) == 1
