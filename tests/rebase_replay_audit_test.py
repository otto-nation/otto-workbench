"""Finding the replay a resolution concludes, and refusing one that discards work.

Driven against real repositories: what is under test is mostly what git leaves
behind at each stop — which refs, which state files — and a stub would assert
whatever the test author believed about that. Two beliefs here were wrong when
first written down: that REBASE_HEAD goes away when a rebase finishes, and that
an index equal to HEAD means a patch was already applied upstream.

No hook fires in these repos (the suite pins `core.hooksPath` to /dev/null), so
every verdict below is the library's own, not the commit hook's.
"""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import git_in, git_out, init_repo

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.rewrites  # noqa: E402
import pr.domains  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.replay_audit  # noqa: E402
import rebase.survival  # noqa: E402
import rebase.types  # noqa: E402

BASE = "a\nb\nc\nd\ne\nf\ng\n"
# Line 2 collides with main; line 6 is clean and is what `--ours` throws away.
BRANCH = "a\nB\nc\nd\ne\nF\ng\n"
MAIN = "a\nBM\nc\nD\ne\nf\ng\n"
MERGED = "a\nBM+B\nc\nD\ne\nF\ng\n"


def _git(repo: Path, *args: str) -> None:
    """git with no editor, since `--continue` asks for one after a conflict."""
    git_in(repo, "-c", "core.editor=true", *args)


def _write(repo: Path, body: str, name: str = "f.txt") -> None:
    (repo / name).write_text(body)


@pytest.fixture
def repo(tmp_path) -> Path:
    """`feat` (one commit) conflicting with `main` in f.txt, checked out on feat."""
    repo = init_repo(tmp_path / "repo")
    _write(repo, BASE)
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feat")
    _write(repo, BRANCH)
    _git(repo, "commit", "-q", "-am", "feat: edit")
    _git(repo, "checkout", "-q", "main")
    _write(repo, MAIN)
    _git(repo, "commit", "-q", "-am", "main: edit")
    _git(repo, "checkout", "-q", "feat")
    return repo


def _stop_on_conflict(repo: Path) -> None:
    r = git.client.run("rebase", "main", cwd=repo)
    assert not r.ok and rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]


def _take_ours(repo: Path) -> None:
    _git(repo, "checkout", "--ours", "--", "f.txt")
    _git(repo, "add", "f.txt")


def _finish_with(repo: Path, body: str) -> None:
    _write(repo, body)
    _git(repo, "add", "f.txt")
    _git(repo, "rebase", "--continue")


class TestReplaying:
    def test_nothing_is_replaying_outside_a_rebase(self, repo):
        assert rebase.replay_audit.replaying(str(repo)) is None

    def test_a_conflicted_rebase_names_its_commit_and_how_to_continue(self, repo):
        _stop_on_conflict(repo)

        replay = rebase.replay_audit.replaying(str(repo))

        assert replay == rebase.replay_audit.ReplayStep(
            commit=git_out(repo, "rev-parse", "REBASE_HEAD").strip(),
            parent=git_out(repo, "rev-parse", "REBASE_HEAD^").strip(),
            continue_command="git rebase --continue",
        )

    def test_a_finished_rebase_leaves_a_stale_rebase_head_that_is_ignored(self, repo):
        """git keeps REBASE_HEAD after a rebase whose last step conflicted."""
        _stop_on_conflict(repo)
        _finish_with(repo, MERGED)

        assert git.client.ok("rev-parse", "-q", "--verify", "REBASE_HEAD", cwd=repo)
        assert rebase.replay_audit.replaying(str(repo)) is None

    def test_a_cherry_pick_after_such_a_rebase_is_named_as_a_cherry_pick(self, repo):
        _stop_on_conflict(repo)
        _finish_with(repo, MERGED)
        _git(repo, "reset", "-q", "--hard", "ORIG_HEAD")
        _git(repo, "checkout", "-q", "main")
        assert not git.client.run("cherry-pick", "feat", cwd=repo).ok

        replay = rebase.replay_audit.replaying(str(repo))

        assert replay is not None
        assert replay.continue_command == "git cherry-pick --continue"

    def test_an_edit_stop_is_the_users_restructuring_and_not_audited(self, repo):
        r = git.client.run(
            "-c", "sequence.editor=sed -i.bak s/^pick/edit/", "rebase", "-i", "HEAD~1",
            cwd=repo,
        )
        assert r.ok and git.client.ok("rev-parse", "-q", "--verify", "REBASE_HEAD", cwd=repo)

        assert rebase.replay_audit.replaying(str(repo)) is None


class TestAuditReplay:
    def test_checkout_ours_is_refused(self, repo):
        _stop_on_conflict(repo)
        _take_ours(repo)

        audit = rebase.replay_audit.audit_replay(str(repo))

        assert [f.path for f in audit.refused] == ["f.txt"]
        assert audit.refused[0].blocking[0].kind is rebase.survival.LossKind.FILE_TAKEN_WHOLE

    def test_a_merge_keeping_both_sides_passes(self, repo):
        _stop_on_conflict(repo)
        _write(repo, MERGED)
        _git(repo, "add", "f.txt")

        audit = rebase.replay_audit.audit_replay(str(repo))

        assert audit.ok and audit.flagged == ()

    def test_checkout_merge_brings_the_conflict_back_after_a_whole_file_take(self, repo):
        """The recovery the refusal prints has to actually work."""
        _stop_on_conflict(repo)
        _take_ours(repo)

        _git(repo, "checkout", "-m", "--", "f.txt")

        assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]
        assert "<<<<<<<" in (repo / "f.txt").read_text()

    def test_a_generated_file_may_be_taken_whole(self, repo):
        """The rebase tool takes generated files whole itself; the audit agrees."""
        (repo / ".gitattributes").write_text("f.txt linguist-generated=true\n")
        _stop_on_conflict(repo)
        _take_ours(repo)

        audit = rebase.replay_audit.audit_replay(str(repo))

        assert audit.ok
        assert audit.files[0].skipped.startswith("resolved whole by design")


class TestMain:
    def test_a_refusal_exits_with_the_code_the_hook_refuses_on(self, repo, monkeypatch, capsys):
        _stop_on_conflict(repo)
        _take_ours(repo)
        monkeypatch.chdir(repo)

        rc = rebase.replay_audit.main(["commit"])

        assert rc == rebase.replay_audit.REFUSED_EXIT
        err = capsys.readouterr().err
        assert "refusing to commit" in err
        assert "git checkout -m -- f.txt" in err
        assert "WORKBENCH_ALLOW_DROPPED_CHANGES=1 git rebase --continue" in err

    def test_the_override_lets_the_commit_through(self, repo, monkeypatch):
        _stop_on_conflict(repo)
        _take_ours(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setenv(rebase.replay_audit.ALLOW_ENV, "1")

        assert rebase.replay_audit.main(["commit"]) == 0

    def test_a_crash_in_the_audit_fails_open(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        with mock.patch.object(rebase.replay_audit, "replaying", side_effect=RuntimeError("x")):
            assert rebase.replay_audit.main(["commit"]) == 0

    def test_the_recovery_command_quotes_a_path_with_a_space(self):
        """A refused path can contain shell metacharacters; the suggested
        recovery command must still mean what it says when copy-pasted.
        """
        loss = rebase.survival.Loss(
            kind=rebase.survival.LossKind.FILE_TAKEN_WHOLE,
            side=rebase.survival.Side.REPLAYED,
        )
        audit = rebase.replay_audit.CommitAudit(
            commit="abc123", subject="subj",
            files=(rebase.survival.FileAudit("my file.txt", (loss,)),),
        )

        rendered = rebase.replay_audit.render_refusal(audit, "pr rebase --fix")

        assert "git checkout -m -- 'my file.txt'" in rendered

    def test_an_amend_rewrite_is_recorded_whole_and_not_audited(self, monkeypatch):
        common = Path("/nonexistent/common")
        monkeypatch.setattr(git.rewrites, "common_dir", lambda cwd: common)
        with mock.patch.object(git.rewrites, "record") as record, \
                mock.patch.object(rebase.replay_audit, "dropped_commits") as dropped:
            with mock.patch("sys.stdin.read", return_value=f"{'a' * 40} {'b' * 40}\n"):
                assert rebase.replay_audit.main(["rewritten", "amend"]) == 0
        dropped.assert_not_called()
        record.assert_called_once_with(common, [git.rewrites.Rewrite("a" * 40, "b" * 40)])


def test_a_record_that_cannot_be_written_still_reports_the_drop(
    tmp_path, monkeypatch, capsys,
):
    """The record is a side job: its failure must not cost the audit's report."""
    state = tmp_path / rebase.inspect.GIT_REBASE_MERGE_DIR
    state.mkdir()
    (state / "onto").write_text("0" * 40 + "\n")
    (state / "done").write_text(f"pick {'a' * 40} subject\n")
    monkeypatch.setattr(rebase.inspect, "git_dir", lambda cwd: tmp_path)
    monkeypatch.setattr(rebase.replay_audit, "dropped_commits", lambda *a: ("lossy",))
    monkeypatch.setattr(rebase.replay_audit, "render_dropped", lambda *a: "DROPPED-REPORT")

    def unwritable(*a):
        raise OSError("read-only file system")

    monkeypatch.setattr(git.rewrites, "record", unwritable)
    with mock.patch("sys.stdin.read", return_value=f"{'a' * 40} {'b' * 40}\n"):
        assert rebase.replay_audit.main(["rewritten", "rebase"]) == 0

    err = capsys.readouterr().err
    assert "DROPPED-REPORT" in err
    assert "could not record this rewrite" in err


def test_a_rebase_whose_done_file_is_unreadable_records_nothing(tmp_path, monkeypatch):
    """With no todo commands a skipped commit cannot be told from a rewrite, and
    git maps it onto its predecessor: recording it would follow a dropped fix
    commit to an unrelated one."""
    state = tmp_path / rebase.inspect.GIT_REBASE_MERGE_DIR
    state.mkdir()
    (state / "onto").write_text("0" * 40 + "\n")
    monkeypatch.setattr(rebase.inspect, "git_dir", lambda cwd: tmp_path)
    # Mapped onto `onto`: a drop, were its command known.
    stdin = f"{'a' * 40} {'0' * 40}\n{'b' * 40} {'c' * 40}\n"
    with mock.patch.object(git.rewrites, "record") as record, \
            mock.patch("sys.stdin.read", return_value=stdin):
        assert rebase.replay_audit.main(["rewritten", "rebase"]) == 0
    record.assert_not_called()


class TestPrRebaseHalts:
    """`pr rebase` asks before `--continue`, so a refusal is one clean stop."""

    @pytest.fixture
    def saved(self):
        outcomes = []
        with mock.patch.object(
            rebase.types.RebaseOutcome, "save",
            lambda self, ctx: outcomes.append(self.status),
        ):
            yield outcomes

    def test_a_hand_staged_checkout_ours_is_not_skipped_as_already_upstream(
        self, repo, saved, capsys,
    ):
        """Index equal to HEAD used to read as an empty patch, and was skipped."""
        _stop_on_conflict(repo)
        _take_ours(repo)
        assert rebase.inspect.is_empty_patch(str(repo))

        rc = rebase.lifecycle.step_advance(
            str(repo), mock.MagicMock(), target_ref="main",
        )

        assert rc == rebase.types.CONFLICTS_EXIT
        assert rebase.inspect.rebase_in_progress(str(repo))
        assert saved == [pr.domains.RebaseStatus.CONFLICTS]
        assert json.loads(capsys.readouterr().out)["files"] == ["f.txt"]
        # Somebody's staged work: left as it was, not overwritten with markers.
        assert (repo / "f.txt").read_text() == MAIN

    def test_a_genuinely_empty_patch_is_still_skipped(self, repo, saved):
        """Main already carries the branch's change; nothing is lost by skipping."""
        _git(repo, "checkout", "-q", "main")
        _write(repo, MERGED)
        _git(repo, "commit", "-q", "-am", "main: take the branch's change too")
        _git(repo, "checkout", "-q", "feat")
        _stop_on_conflict(repo)
        _write(repo, MERGED)
        _git(repo, "add", "f.txt")

        rc = rebase.lifecycle.step_advance(str(repo), mock.MagicMock(), target_ref="main")

        assert rc is None
        assert not rebase.inspect.rebase_in_progress(str(repo))
        assert saved == []

    def test_a_discarding_resolution_made_by_the_run_gets_its_conflict_back(
        self, repo, saved, capsys,
    ):
        _stop_on_conflict(repo)

        def resolve_by_taking_ours(conflicts, cwd, *args, **kwargs):
            _take_ours(Path(cwd))
            return rebase.types.Resolution(files=list(conflicts))

        with mock.patch.object(rebase.lifecycle.agent.backend, "is_available", return_value=True), \
             mock.patch.object(rebase.lifecycle.rebase_resolve, "resolve_file_conflicts",
                               side_effect=resolve_by_taking_ours):
            rc = rebase.lifecycle.step_conflicts(
                str(repo), mock.MagicMock(), rebase.types.RunMode.FIX, ["f.txt"],
                rebase.types.ResolutionTally(), target_ref="main",
            )

        assert rc == rebase.types.CONFLICTS_EXIT
        assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]
        assert json.loads(capsys.readouterr().out)["files"] == ["f.txt"]

    def test_the_override_lets_pr_rebase_continue(self, repo, saved, monkeypatch):
        _stop_on_conflict(repo)
        _take_ours(repo)
        monkeypatch.setenv(rebase.replay_audit.ALLOW_ENV, "1")

        rc = rebase.lifecycle.step_advance(str(repo), mock.MagicMock(), target_ref="main")

        assert rc is None
        assert saved == []
