"""Tests for review.collect — the delta an incremental review collects since its prior head."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import (
    add_self_origin, commit_all, git_out, init_repo, run_checked,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult
import git.client
import review.collect
from gh.types import PRContext, PRMetadata
from review.types import DeltaAttribution, ReviewJob


# ── _collect_delta ──────────────────────────────────────────────────────────


def _delta_job(head_sha: str, prior_review: str = "") -> ReviewJob:
    pr = PRMetadata(
        title="test",
        body="",
        head="feature",
        base="main",
        head_sha=head_sha,
        additions=10,
        deletions=5,
        changed_files=1,
        files=[],
    )
    return ReviewJob(
        repo="owner/repo",
        pr_number="1",
        pr=pr,
        ctx=PRContext(),
        wt_path="/tmp/fake",
        review_file="/tmp/review.md",
        session_log="/tmp/session.log",
        prior_review=prior_review,
    )


class TestCollectDeltaSameSha:
    """The reasons a run reviews the whole PR instead of a delta.

    Each returns the default scope, whose empty `prior_sha` is what
    `_is_incremental` reads as "not a re-review". None of them is a *proven*
    empty delta: the run did not measure one and skipping work on the strength
    of it would skip the full review these ask for.
    """

    def test_prior_sha_equals_head_sha_returns_empty(self):
        sha = "abc1234def5678901234567890abcdef12345678"
        prior_review = f"<!-- head_sha: {sha} -->\nsome review content"
        job = _delta_job(head_sha=sha, prior_review=prior_review)
        assert review.collect._collect_delta(job) == review.collect.DeltaScope()

    def test_no_prior_review_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="")
        assert review.collect._collect_delta(job) == review.collect.DeltaScope()

    def test_prior_review_without_sha_returns_empty(self):
        job = _delta_job(head_sha="abc123", prior_review="no sha marker here")
        assert review.collect._collect_delta(job) == review.collect.DeltaScope()

    def test_a_full_review_has_no_delta_to_attribute(self):
        delta = review.collect._collect_delta(_delta_job(head_sha="abc123", prior_review=""))
        assert delta.attribution is DeltaAttribution.NONE
        assert delta.proven_empty is False


class TestCollectDeltaMode:
    """The delta surface follows the same rule as the full diff: self mode
    reaches into the working tree, PR mode stops at HEAD."""

    @staticmethod
    def _repo_with_prior_commit(tmp_path: Path) -> tuple[Path, str]:
        repo = init_repo(tmp_path / "repo")
        (repo / "reviewed.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "committed.go").write_text("package main\nfunc committed() {}\n")
        commit_all(repo, "since review")
        (repo / "reviewed.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "untracked.go").write_text("package main\nfunc untracked() {}\n")
        return repo, prior_sha

    def _job(self, tmp_path: Path, mode: str) -> ReviewJob:
        repo, prior_sha = self._repo_with_prior_commit(tmp_path)
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        return replace(job, wt_path=str(repo), mode=mode)

    def test_self_mode_delta_includes_worktree_changes(self, tmp_path, capsys):
        delta = review.collect._collect_delta(self._job(tmp_path, "self"))
        capsys.readouterr()
        assert "func committed" in delta.diff
        assert "func uncommitted" in delta.diff
        assert "func untracked" in delta.diff
        assert sorted(delta.files) == ["committed.go", "reviewed.go", "untracked.go"]

    def test_pr_mode_delta_stops_at_head(self, tmp_path, capsys):
        delta = review.collect._collect_delta(self._job(tmp_path, "pr"))
        capsys.readouterr()
        assert "func committed" in delta.diff
        assert "func uncommitted" not in delta.diff
        assert delta.files == ["committed.go"]

    def test_self_mode_is_never_proven_empty(self, tmp_path, capsys):
        """A working tree has no commits to attribute, so nothing is proven.

        Self-review's surface reaches past HEAD deliberately. There is no
        ancestry walk that could establish the author changed nothing, so the
        flag that lets a caller skip work stays off even when the delta is
        genuinely empty — which is what this builds: a worktree restored to the
        prior review's commit, with the head SHA the only thing that differs.
        """
        repo = init_repo(tmp_path / "repo")
        (repo / "reviewed.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        add_self_origin(repo)
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        job = replace(
            _delta_job(
                head_sha="a-later-commit-this-worktree-does-not-hold",
                prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
            ),
            wt_path=str(repo), mode="self",
        )

        delta = review.collect._collect_delta(job)
        capsys.readouterr()

        assert delta.files == []
        assert delta.attribution is DeltaAttribution.UNATTRIBUTED
        assert delta.proven_empty is False


class TestCollectDeltaSurface:
    """The delta diff is bounded by the PR, not by what the base branch did.

    `prior_sha..HEAD` spans the base as well as the branch, so a rebase onto a
    moved base puts every commit the base gained into the delta. One 107-file
    review reported 4,974 changed files that way, and the file list alone —
    260KB — pushed the synthesis prompt 75% past its budget. It also defeated
    incremental group skipping: with every group's files in the delta set,
    nothing was skipped and the re-review cost a full one.

    These repos have no `origin`, so they are also what the ancestry walk falls
    back to when it cannot resolve a base ref to exclude — the whole range,
    path-scoped, over-reporting rather than reporting nothing. What the walk
    does when it *can* resolve one is `TestCollectDeltaAncestry`'s.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> tuple[Path, str]:
        repo = init_repo(tmp_path / "repo")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "reviewed")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "mine.go").write_text("package main\nfunc mine() {}\n")
        (repo / "theirs.go").write_text("package main\nfunc theirs() {}\n")
        commit_all(repo, "mine plus a rebased base commit")
        return repo, prior_sha

    def _job(self, tmp_path: Path, files: list[dict]) -> ReviewJob:
        repo, prior_sha = self._repo(tmp_path)
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        return replace(
            job, wt_path=str(repo), pr=replace(job.pr, files=files),
        )

    def test_files_outside_the_pr_are_not_in_the_delta(self, tmp_path, capsys):
        job = self._job(tmp_path, [{"path": "mine.go", "additions": 1, "deletions": 0}])
        delta = review.collect._collect_delta(job)
        capsys.readouterr()
        assert delta.files == ["mine.go"]
        assert "func mine" in delta.diff
        assert "theirs.go" not in delta.diff
        assert "theirs.go" not in delta.commit_log

    def test_a_job_with_no_surface_keeps_the_whole_range(self, tmp_path, capsys):
        """Branch reviews reach `_collect_delta` before the file list exists."""
        delta = review.collect._collect_delta(self._job(tmp_path, []))
        capsys.readouterr()
        assert sorted(delta.files) == ["mine.go", "theirs.go"]

    def test_an_unresolvable_base_ref_is_not_a_proven_empty_delta(
        self, tmp_path, capsys,
    ):
        """The guard that keeps a missing ref from reading as "nothing changed".

        `git log ... --not origin/main` against a repo without that ref exits
        128, which reports as empty output. Believing it would skip a review of
        real work and advance the marker past it.
        """
        job = self._job(tmp_path, [{"path": "mine.go", "additions": 1, "deletions": 0}])
        delta = review.collect._collect_delta(job)
        capsys.readouterr()
        assert delta.files == ["mine.go"]
        assert delta.attribution is DeltaAttribution.UNATTRIBUTED
        assert delta.proven_empty is False


class TestCollectDeltaAncestry:
    """The delta's file list is the author's commits, not everything in range.

    Path-scoping bounds the delta by what is reviewable; it cannot bound it by
    who wrote it. A base commit touching a file the PR also touches is inside
    the surface, so it survives that filter and reads as author work — the case
    the 4,974-file incident could not have been prevented by scoping alone.
    Excluding the base by ancestry is what closes it, and what makes a merge of
    main cost nothing while a merge of a sub-branch still costs a review.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> tuple[Path, str]:
        """A branch and a base that both touch `shared.go`, ready to merge.

        Returns the repo and the SHA the prior review was written against — the
        branch's own tip, before the base is merged into it.
        """
        repo = init_repo(tmp_path / "repo")
        (repo / "shared.go").write_text("package main\n")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)

        git_out(repo, "checkout", "-q", "-b", "feat")
        (repo / "mine.go").write_text("package main\nfunc reviewed() {}\n")
        commit_all(repo, "work the prior review saw")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()

        git_out(repo, "checkout", "-q", "main")
        (repo / "shared.go").write_text("package main\nfunc fromBase() {}\n")
        commit_all(repo, "base work on a file the PR also touches")
        git_out(repo, "fetch", "-q", "origin", "main")
        git_out(repo, "checkout", "-q", "feat")
        return repo, prior_sha

    @staticmethod
    def _merge_expecting_conflict(repo: Path, ref: str) -> None:
        """Merge `ref`, which is expected to stop with a conflict.

        Not `git_out`: a conflicting merge exits non-zero, which that helper
        reports as a failed test rather than as the state being set up here.
        `run_checked` still runs it, so a process killed by machine contention
        is reported as contention rather than passing for the wrong reason.
        """
        result = run_checked(
            ["git", "-C", str(repo), "merge", ref, "-m", "Merge main"],
            check=False,
        )
        assert result.returncode != 0, "expected the merge to conflict"

    @staticmethod
    def _job(repo: Path, prior_sha: str, files: list[str]) -> ReviewJob:
        job = _delta_job(
            head_sha=git_out(repo, "rev-parse", "HEAD").strip(),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        surface = [{"path": p, "additions": 1, "deletions": 0} for p in files]
        return replace(
            job, wt_path=str(repo), pr=replace(job.pr, files=surface),
        )

    def _merged(self, tmp_path: Path, files: list[str]) -> ReviewJob:
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        return self._job(repo, prior_sha, files)

    def test_a_merge_of_main_alone_leaves_the_delta_empty(self, tmp_path, capsys):
        delta = review.collect._collect_delta(self._merged(tmp_path, ["mine.go", "shared.go"]))
        capsys.readouterr()
        assert delta.files == []
        assert delta.weighted_lines == 0

    def test_base_work_on_a_file_the_pr_also_touches_is_excluded(
        self, tmp_path, capsys,
    ):
        """The case path-scoping cannot close: `shared.go` is in the surface."""
        job = self._merged(tmp_path, ["mine.go", "shared.go"])
        delta = review.collect._collect_delta(job)
        capsys.readouterr()
        assert "shared.go" not in delta.files

    def test_a_merge_of_main_is_a_proven_empty_delta(self, tmp_path, capsys):
        delta = review.collect._collect_delta(self._merged(tmp_path, ["mine.go", "shared.go"]))
        capsys.readouterr()
        assert delta.attribution is DeltaAttribution.ATTRIBUTED
        assert delta.proven_empty is True

    def test_author_work_on_top_of_a_merge_is_kept(self, tmp_path, capsys):
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        (repo / "mine.go").write_text("package main\nfunc afterTheMerge() {}\n")
        commit_all(repo, "more author work")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.files == ["mine.go"]
        assert delta.weighted_lines > 0
        assert delta.proven_empty is False

    def test_a_merge_of_a_sub_branch_keeps_the_topics_own_commits(
        self, tmp_path, capsys,
    ):
        """Ancestry distinguishes the two merges a path filter cannot.

        A sub-branch's commits are the topic's own work and have to be
        reviewed; main's are everyone's and must not be. Both arrive as a merge
        commit on the branch.
        """
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "checkout", "-q", "-b", "sub")
        (repo / "sub.go").write_text("package main\nfunc fromSubBranch() {}\n")
        commit_all(repo, "sub-branch work")
        git_out(repo, "checkout", "-q", "feat")
        git_out(repo, "merge", "-q", "sub", "-m", "Merge sub")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "sub.go"]))
        capsys.readouterr()

        assert delta.files == ["sub.go"]
        assert delta.proven_empty is False

    def test_a_conflict_resolution_counts_as_author_work(self, tmp_path, capsys):
        """Work living only in a merge commit, which `--no-merges` cannot see.

        Resolving a conflict is hand-written code, on the file the author was
        most likely to get wrong. Dropping every merge commit would report this
        re-review as having nothing to do.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "shared.go").write_text("package main\nfunc fromBranch() {}\n")
        commit_all(repo, "branch edits the same file the base did")
        self._merge_expecting_conflict(repo, "main")
        (repo / "shared.go").write_text(
            "package main\nfunc fromBranch() {}\nfunc fromBase() {}\n"
        )
        git_out(repo, "add", "shared.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert "shared.go" in delta.files
        assert delta.proven_empty is False

    def test_an_edit_made_during_a_clean_merge_counts_as_author_work(
        self, tmp_path, capsys,
    ):
        repo, prior_sha = self._repo(tmp_path)
        # `--no-commit` stops before the merge commit so the tree can be edited
        # while merging; it exits zero because this merge does not conflict.
        git_out(repo, "merge", "-q", "--no-commit", "--no-ff", "main")
        (repo / "mine.go").write_text("package main\nfunc snuckIn() {}\n")
        git_out(repo, "add", "mine.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert "mine.go" in delta.files
        assert delta.proven_empty is False

    def test_the_delta_reports_the_lines_the_author_changed(self, tmp_path, capsys):
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        (repo / "mine.go").write_text("package main\n" + "func f() {}\n" * 5)
        commit_all(repo, "five more lines")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        # The rewrite replaces the file's one existing line, so this is five
        # additions and one deletion rather than six additions: six lines of
        # diff, priced as five lines of review. Both are pinned because only
        # the pair shows the weighting applied to the right half — an equal
        # split, or a weight on additions, moves one of these and not the
        # other.
        assert delta.raw_lines == 6
        assert delta.weighted_lines == 5

    def test_a_delta_that_removes_lines_is_priced_below_one_that_adds_them(
        self, tmp_path, capsys,
    ):
        """A deletion is review too, but less of it than an addition is.

        Pinned on the delta path specifically. The PR path weights at its own
        call site, so a weighting applied there and not here would leave a
        re-review sized by the unweighted count with nothing failing.
        """
        repo, prior_sha = self._repo(tmp_path)
        git_out(repo, "merge", "-q", "main", "-m", "Merge main")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "strip it back")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.raw_lines > 0
        assert delta.weighted_lines < delta.raw_lines

    def test_a_file_both_walks_report_is_named_once(self, tmp_path, capsys):
        """A commit and a later conflict resolution can touch the same file.

        Each consumer counts what it is handed — the prompt says how many files
        changed, the line total sums them — so a path arriving from both walks
        would be reported twice.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "shared.go").write_text("package main\nfunc fromBranch() {}\n")
        commit_all(repo, "branch edits the same file the base did")
        self._merge_expecting_conflict(repo, "main")
        (repo / "shared.go").write_text(
            "package main\nfunc fromBranch() {}\nfunc fromBase() {}\n"
        )
        git_out(repo, "add", "shared.go")
        git_out(repo, "commit", "-q", "--no-verify", "-m", "Merge main")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["mine.go", "shared.go"]))
        capsys.readouterr()

        assert delta.files.count("shared.go") == 1

    def _delta_with_failing(self, tmp_path, capsys, failing) -> review.collect.DeltaScope:
        """The delta for a merged branch, with `failing` git reads exiting 128.

        `failing` takes the argument tuple and says whether that read fails.
        Everything else runs for real, so the fallback still has a diff and a
        log to build from.
        """
        job = self._merged(tmp_path, ["mine.go", "shared.go"])
        real_run = git.client.run

        def _run(*args, **kwargs):
            if failing(args):
                return CmdResult(returncode=128, stderr="fatal: bad revision")
            return real_run(*args, **kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(git.client, "run", _run)
            delta = review.collect._collect_delta(job)
        capsys.readouterr()
        return delta

    def test_a_failed_numstat_walk_is_not_an_empty_delta(self, tmp_path, capsys):
        """git failing must not read as "the author changed nothing".

        `git.client.out` reports a non-zero exit and a timeout alike as no
        output, which is exactly what an author who changed nothing produces.
        Believing it would carry every group forward and skip a real review.
        """
        delta = self._delta_with_failing(
            tmp_path, capsys,
            lambda args: args and args[0] in {"log", "show"} and "--numstat" in args,
        )

        assert delta.attribution is DeltaAttribution.UNATTRIBUTED
        assert delta.proven_empty is False
        assert delta.files, "a failed walk falls back to the whole range"
        # The log describes the same range the file list does, rather than the
        # ancestry-scoped one the walk was going to use.
        assert "Merge main" in delta.commit_log

    def test_a_failed_merge_listing_is_not_an_empty_delta(self, tmp_path, capsys):
        """The other walk: without the merge list, merge-only work is invisible."""
        delta = self._delta_with_failing(
            tmp_path, capsys, lambda args: args and args[0] == "rev-list",
        )

        assert delta.attribution is DeltaAttribution.UNATTRIBUTED
        assert delta.proven_empty is False
        assert delta.files, "a failed walk falls back to the whole range"

    def test_a_non_ascii_path_is_named_as_git_stores_it(self, tmp_path, capsys):
        """`core.quotePath` is not applied to `log` by the client's own default.

        An escaped name matches no group's file list, so the group holding the
        file would be skipped as unchanged.
        """
        repo, prior_sha = self._repo(tmp_path)
        (repo / "caf\u00e9.go").write_text("package main\n")
        commit_all(repo, "a path git would escape")

        delta = review.collect._collect_delta(self._job(repo, prior_sha, ["caf\u00e9.go"]))
        capsys.readouterr()

        assert "caf\u00e9.go" in delta.files


class TestCollectDeltaEndsAtTheStampedHead:
    """The delta ends at the commit the review header will record, not live HEAD.

    `job.pr.head_sha` is read before the delta is collected and is what the
    written review stamps. A commit landing in between must not reach the
    prompt's commit list or file list under a header naming its parent — the
    review would then claim less than it was shown, and the commit list would
    contradict the incremental note's `prior..head` span.
    """

    def _job_after_a_late_commit(
        self, tmp_path: Path, head_sha: str | None = None, base: str = "main",
    ) -> ReviewJob:
        repo = init_repo(tmp_path / "repo")
        (repo / "mine.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-q", "-b", "feat")
        (repo / "mine.go").write_text("package main\nfunc reviewed() {}\n")
        commit_all(repo, "work the prior review saw")
        prior_sha = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "mine.go").write_text("package main\nfunc stamped() {}\n")
        commit_all(repo, "work this review is stamped with")
        stamped = git_out(repo, "rev-parse", "HEAD").strip()
        (repo / "late.go").write_text("package main\n")
        commit_all(repo, "a commit made after the snapshot")

        job = _delta_job(
            head_sha=stamped if head_sha is None else head_sha,
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        surface = [
            {"path": p, "additions": 1, "deletions": 0}
            for p in ("mine.go", "late.go")
        ]
        return replace(
            job, wt_path=str(repo),
            pr=replace(job.pr, files=surface, base=base),
        )

    def test_a_commit_after_the_snapshot_is_left_out(self, tmp_path, capsys):
        delta = review.collect._collect_delta(self._job_after_a_late_commit(tmp_path))
        capsys.readouterr()

        assert delta.attribution is DeltaAttribution.ATTRIBUTED
        assert delta.files == ["mine.go"]
        assert "work this review is stamped with" in delta.commit_log
        assert "a commit made after the snapshot" not in delta.commit_log
        assert "late.go" not in delta.diff

    def test_the_unattributed_range_is_pinned_too(self, tmp_path, capsys):
        job = self._job_after_a_late_commit(tmp_path, base="no-such-base")
        delta = review.collect._collect_delta(job)
        capsys.readouterr()

        assert delta.attribution is DeltaAttribution.UNATTRIBUTED
        assert delta.files == ["mine.go"]
        assert "work this review is stamped with" in delta.commit_log
        assert "a commit made after the snapshot" not in delta.commit_log
        assert "late.go" not in delta.diff

    def test_no_stamped_sha_ends_at_head(self, tmp_path, capsys):
        job = self._job_after_a_late_commit(tmp_path, head_sha="")
        delta = review.collect._delta_head(job)

        assert delta == "HEAD"

    def test_a_stamped_sha_the_worktree_lacks_ends_at_head(self, tmp_path, capsys):
        # The API snapshot can be newer than the checkout. A range ending at a
        # SHA git cannot resolve comes back empty, so the delta would vanish.
        missing = "f" * 40
        job = self._job_after_a_late_commit(tmp_path, head_sha=missing)
        delta = review.collect._collect_delta(job)
        capsys.readouterr()

        assert review.collect._delta_head(job) == "HEAD"
        assert delta.attribution is DeltaAttribution.ATTRIBUTED
        assert sorted(delta.files) == ["late.go", "mine.go"]
        assert "a commit made after the snapshot" in delta.commit_log

    def test_the_full_history_and_diff_end_at_the_stamped_sha(self, tmp_path, capsys):
        job = self._job_after_a_late_commit(tmp_path)

        data = review.collect.collect_preflight_data(job)
        capsys.readouterr()
        diff, commit_log = data.diff, data.commit_log

        assert "work this review is stamped with" in commit_log
        assert "a commit made after the snapshot" not in commit_log
        assert "late.go" not in diff

    def test_self_review_history_is_pinned_while_its_diff_is_the_tree(
        self, tmp_path, capsys,
    ):
        """Self-review reads the working tree, but its history still ends at the stamp."""
        job = replace(self._job_after_a_late_commit(tmp_path), mode="self")

        data = review.collect.collect_preflight_data(job)
        capsys.readouterr()

        assert "work this review is stamped with" in data.commit_log
        assert "a commit made after the snapshot" not in data.commit_log
