"""Replaying only what is left of a partially-landed branch.

The refusal and the flag are one feature and are tested as one: a refusal
whose remedy the tool cannot express is a dead end, which is what
`--fork-point` exists to close. The replay half runs against a real repository,
because what is under test is that git's ``--onto <newbase> <upstream>`` form
reaches git at all — the two-argument form this used to build collapses
<newbase> and <upstream> onto one ref, and no assertion about our own argv
would have shown that the resulting replay was wrong.
"""

import sys
from pathlib import Path
from unittest import mock

from conftest import git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import landed as branch_landed  # noqa: E402
from git import client as git_client  # noqa: E402
from pr.domains import RebaseStatus  # noqa: E402
from rebase import lifecycle  # noqa: E402
from rebase import refusals  # noqa: E402
from rebase import types as rebase_types  # noqa: E402

_TARGET = "main"


def _ctx(branch="feat"):
    ctx = mock.MagicMock()
    ctx.branch = branch
    ctx.current_branch = branch
    ctx.repo = "owner/repo"
    ctx.pr_number = None
    return ctx


class TestTheRefusalCarriesAnExecutableRemedy:
    def _refusal(self, landed=3, unlanded=1, fork="abc1234"):
        partial = branch_landed.PartialLanding(
            landed=landed, unlanded=unlanded,
            fork_point=fork, fork_subject="feat: three",
        )
        with mock.patch.object(branch_landed, "partial_landing",
                               return_value=partial):
            return refusals.partially_landed_check(
                "/fake", _ctx(), target_ref=_TARGET,
            )

    def test_it_names_the_flag_and_the_ref_to_pass(self):
        report = self._refusal()
        assert report.remedy == f"{refusals.FORK_POINT_FLAG} abc1234"

    def test_the_remedy_is_the_flag_the_cli_actually_accepts(self):
        """A remedy naming a flag that does not parse is the dead end.

        Checked against the parser rather than against a second copy of the
        string, so renaming the flag in one place fails here.
        """
        from cli import pr_rebase as pr_rebase_cli

        flag, ref = self._refusal().remedy.split()
        args = pr_rebase_cli.build_parser().parse_args([flag, ref])
        assert args.fork_point == ref

    def test_the_status_is_not_already_landed(self):
        """The branch has work left, so the remedy is not deleting it."""
        assert self._refusal().status == RebaseStatus.PARTIALLY_LANDED.value

    def test_it_reports_the_whole_branch_not_just_the_prefix(self):
        report = self._refusal(landed=3, unlanded=1)
        assert report.commits_ahead == 4
        assert "3 of 4" in report.detail

    def test_nothing_found_is_no_refusal(self):
        with mock.patch.object(branch_landed, "partial_landing", return_value=None):
            assert refusals.partially_landed_check(
                "/fake", _ctx(), target_ref=_TARGET,
            ) is None

    def test_the_refusal_prints_the_remedy_before_the_override(self, capsys):
        """Offering `--force` first trains the operator past the actual fix."""
        with mock.patch.object(rebase_types.RebaseOutcome, "save",
                               lambda self, c: None):
            refusals.refuse(_ctx(), self._refusal(), target_ref=_TARGET)

        err = capsys.readouterr().err
        assert err.index("--fork-point abc1234") < err.index("Pass --force")


class TestForkPointReplay:
    """The flag against a real repository \u2014 which commits actually land."""

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

    def _fresh(self, repo: Path, fork_point: str, *, force: bool = False) -> int:
        """`lifecycle.fresh` with everything outside the replay stubbed out.

        Only the *tracker* and the two all-or-nothing landed signals are
        stubbed. The partially-landed check is deliberately live, since half
        of what these tests assert is which runs it stops.
        """
        with mock.patch.object(lifecycle.git_topology, "default_branch",
                               return_value="main"), \
             mock.patch.object(lifecycle.rebase_lease, "remembered_tip",
                               return_value=""), \
             mock.patch.object(lifecycle.rebase_lease, "resolve",
                               return_value=None), \
             mock.patch.object(lifecycle, "rebase_success", return_value=0), \
             mock.patch.object(rebase_types.RebaseOutcome, "save",
                               lambda self, c: None), \
             mock.patch.object(refusals, "tracker_landed_check", return_value=None), \
             mock.patch.object(refusals, "git_landed_check", return_value=None), \
             mock.patch.object(refusals, "unrelated_history_check", return_value=None):
            return lifecycle.fresh(
                str(repo), _ctx(), rebase_types.RunMode.REBASE_ONLY,
                force=force, target_ref=_TARGET, fork_point=fork_point,
            )

    def test_only_the_commits_after_the_fork_point_are_replayed(self, tmp_path):
        repo = self._partially_landed(tmp_path)
        partial = branch_landed.partial_landing(str(repo), target_ref=_TARGET)
        assert partial is not None

        assert self._fresh(repo, partial.fork_point) == 0

        subjects = [
            s for s in git_out(repo, "log", "--format=%s", "main..HEAD").split("\n")
            if s
        ]
        assert subjects == ["feat: 3"]

    def test_without_a_fork_point_the_run_is_refused(self, tmp_path):
        """End to end: the same repo the flag fixes is the one that refuses."""
        repo = self._partially_landed(tmp_path)

        assert self._fresh(repo, "") == rebase_types.REFUSAL_EXIT
        assert not (repo / ".git" / "rebase-merge").exists()

    def test_forcing_past_the_refusal_replays_the_prefix_and_conflicts(self, tmp_path):
        """What the refusal is protecting against, stated as what happens.

        Replaying the landed prefix reapplies each commit on top of its own
        landed version, which conflicts — the shape that spends a resolution
        call per file per commit and rewrites the base's copy back.
        """
        repo = self._partially_landed(tmp_path)

        self._fresh(repo, "", force=True)

        assert (repo / ".git" / "rebase-merge").exists()
        git_in(repo, "rebase", "--abort")

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
        """The refusal's only purpose is to ask for the flag now supplied."""
        repo = init_repo(tmp_path / "repo")
        (repo / "a.txt").write_text("a\n")
        git_in(repo, "add", "a.txt")
        git_in(repo, "commit", "-q", "-m", "chore: root")
        git_in(repo, "checkout", "-q", "-b", "feat")
        (repo / "b.txt").write_text("b\n")
        git_in(repo, "add", "b.txt")
        git_in(repo, "commit", "-q", "-m", "feat: b")

        with mock.patch.object(refusals, "partially_landed_check") as check, \
             mock.patch.object(lifecycle.git_topology, "default_branch",
                               return_value="main"), \
             mock.patch.object(lifecycle.rebase_lease, "remembered_tip",
                               return_value=""), \
             mock.patch.object(lifecycle.rebase_lease, "resolve", return_value=None), \
             mock.patch.object(lifecycle, "rebase_success", return_value=0), \
             mock.patch.object(refusals, "tracker_landed_check", return_value=None), \
             mock.patch.object(refusals, "git_landed_check", return_value=None), \
             mock.patch.object(refusals, "unrelated_history_check", return_value=None):
            lifecycle.fresh(
                str(repo), _ctx(), rebase_types.RunMode.REBASE_ONLY,
                target_ref="main", fork_point="main",
            )

        check.assert_not_called()
