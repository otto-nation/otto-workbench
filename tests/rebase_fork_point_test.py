"""Replaying only what is left of a partially-landed branch.

Detection skips the landed prefix without a flag. `--force` replays the whole
branch. `--fork-point` names the start and does not ask detection. The replay
half runs against a real repository, because what is under test is that git's
``--onto <newbase> <upstream>`` form reaches git at all — the two-argument form
this used to build collapses <newbase> and <upstream> onto one ref, and no
assertion about our own argv would have shown that the resulting replay was
wrong.
"""

import sys
from pathlib import Path
from unittest import mock

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.landed  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.refusals  # noqa: E402
import rebase.types  # noqa: E402
import git.topology

_TARGET = "main"


def _ctx(branch="feat"):
    ctx = mock.MagicMock()
    ctx.branch = branch
    ctx.current_branch = branch
    ctx.repo = "owner/repo"
    ctx.pr_number = None
    return ctx


class TestForkPointReplay:
    """Detection and the flag against a real repository — which commits land."""

    def _partially_landed(self, tmp_path) -> Path:
        """`feat` has four commits; three equivalents are already on main."""
        repo = init_repo(tmp_path / "repo")
        (repo / "root.txt").write_text("root\n")
        git_in(repo, "add", "root.txt")
        git_in(repo, "commit", "-q", "-m", "chore: root")

        git_in(repo, "checkout", "-q", "-b", "feat")
        for n in range(4):
            (repo / f"f{n}.txt").write_text(f"body {n}\n")
            git_in(repo, "add", f"f{n}.txt")
            git_in(repo, "commit", "-q", "-m", f"feat: {n}")

        git_in(repo, "checkout", "-q", "main")
        for n in range(3):
            (repo / f"f{n}.txt").write_text(f"body {n} as landed\n")
            git_in(repo, "add", f"f{n}.txt")
            git_in(repo, "commit", "-q", "-m", f"feat: {n}")
        git_in(repo, "checkout", "-q", "feat")
        return repo

    def _fresh(
        self, repo: Path, fork_point: str, *, force: bool = False,
        pause_on_conflict: bool = False, trail=None,
    ) -> int:
        """`lifecycle.fresh` with everything outside the replay stubbed out.

        Only the *tracker* and the two all-or-nothing landed signals are
        stubbed. Fork-point detection is deliberately live, since half of what
        these tests assert is which commits it skips.
        """
        with mock.patch.object(git.topology, "default_branch",
                               return_value="main"), \
             mock.patch.object(rebase.lifecycle.rebase_lease, "remembered_tip",
                               return_value=""), \
             mock.patch.object(rebase.lifecycle.rebase_lease, "resolve",
                               return_value=None), \
             mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0), \
             mock.patch.object(rebase.types.RebaseOutcome, "save",
                               lambda self, c: None), \
             mock.patch.object(rebase.refusals, "tracker_landed_check", return_value=None), \
             mock.patch.object(rebase.refusals, "git_landed_check", return_value=None), \
             mock.patch.object(rebase.refusals, "unrelated_history_check", return_value=None):
            return rebase.lifecycle.fresh(
                str(repo), _ctx(), rebase.types.RunMode.REBASE_ONLY,
                force=force, target_ref=_TARGET, fork_point=fork_point,
                pause_on_conflict=pause_on_conflict, trail=trail,
            )

    def test_only_the_commits_after_the_fork_point_are_replayed(self, tmp_path):
        repo = self._partially_landed(tmp_path)
        partial = gh.landed.partial_landing(str(repo), target_ref=_TARGET)
        assert partial is not None

        assert self._fresh(repo, partial.fork_point) == 0

        subjects = [
            s for s in git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
            if s
        ]
        assert subjects == ["feat: 3"]

    def test_a_partially_landed_branch_replays_only_what_is_left_without_a_flag(
        self, tmp_path,
    ):
        """Detection, not a refusal: the same repo the flag used to fix."""
        repo = self._partially_landed(tmp_path)

        assert self._fresh(repo, "") == 0

        subjects = [
            s for s in git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
            if s
        ]
        assert subjects == ["feat: 3"]

    def test_force_replays_the_whole_branch_and_conflicts(self, tmp_path):
        """What detection is protecting against, stated as what happens.

        Replaying the landed prefix reapplies each commit on top of its own
        landed version, which conflicts — the shape that spends a resolution
        call per file per commit and rewrites the base's copy back.
        """
        repo = self._partially_landed(tmp_path)

        self._fresh(repo, "", force=True, pause_on_conflict=True)

        assert (repo / ".git" / "rebase-merge").exists()
        git_in(repo, "rebase", "--abort")

    def test_a_detected_fork_point_is_announced_with_every_skipped_commit(
        self, tmp_path, capsys,
    ):
        repo = self._partially_landed(tmp_path)
        trail = mock.MagicMock()

        assert self._fresh(repo, "", trail=trail) == 0

        err = capsys.readouterr().err
        warn_lines = [ln for ln in err.splitlines() if "feat: 0" in ln]
        assert warn_lines
        assert "feat: 1" in warn_lines[0]
        assert "feat: 2" in warn_lines[0]
        assert "feat: 3" not in warn_lines[0]
        assert "Pass --force to replay the whole branch." in err

        fork_calls = [
            c for c in trail.decision.call_args_list
            if c.args and c.args[0] == "fork_point"
        ]
        assert fork_calls
        data = fork_calls[0].kwargs["data"]
        assert data["source"] == "detected"
        assert data["landed"] == 3
        assert data["unlanded"] == 1
        assert len(data["skipped"]) == 3

    def test_a_fork_point_that_is_not_on_the_branch_is_refused(self, tmp_path):
        """A ref that resolves but is not an ancestor replays the wrong set.

        git reports that as success, which is why it is checked here rather
        than left to git.
        """
        repo = self._partially_landed(tmp_path)

        assert self._fresh(repo, "main") == 1
        assert not (repo / ".git" / "rebase-merge").exists()

    def test_an_unresolvable_ref_is_refused_before_git_sees_it(self, tmp_path):
        repo = self._partially_landed(tmp_path)

        assert self._fresh(repo, "no-such-ref") == 1
        assert not (repo / ".git" / "rebase-merge").exists()


class TestTheDetectionIsSkippedWhenTheRemedyIsAlreadyGiven:
    def test_naming_a_fork_point_skips_the_partial_landing_check(self, tmp_path):
        """An explicit start is the whole answer; detection is not consulted."""
        repo = init_repo(tmp_path / "repo")
        (repo / "a.txt").write_text("a\n")
        git_in(repo, "add", "a.txt")
        git_in(repo, "commit", "-q", "-m", "chore: root")
        git_in(repo, "checkout", "-q", "-b", "feat")
        (repo / "b.txt").write_text("b\n")
        git_in(repo, "add", "b.txt")
        git_in(repo, "commit", "-q", "-m", "feat: b")

        with mock.patch.object(gh.landed, "partial_landing") as check, \
             mock.patch.object(git.topology, "default_branch",
                               return_value="main"), \
             mock.patch.object(rebase.lifecycle.rebase_lease, "remembered_tip",
                               return_value=""), \
             mock.patch.object(rebase.lifecycle.rebase_lease, "resolve", return_value=None), \
             mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0), \
             mock.patch.object(rebase.refusals, "tracker_landed_check", return_value=None), \
             mock.patch.object(rebase.refusals, "git_landed_check", return_value=None), \
             mock.patch.object(rebase.refusals, "unrelated_history_check", return_value=None):
            rebase.lifecycle.fresh(
                str(repo), _ctx(), rebase.types.RunMode.REBASE_ONLY,
                target_ref="main", fork_point="main",
            )

        check.assert_not_called()
