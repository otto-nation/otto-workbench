"""Tests for rebase.lifecycle.step_advance: skip, continue, replay audit, and stall paths."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

from conftest import commit_all, git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc  # noqa: E402
import core.report  # noqa: E402
import git.client  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.survival  # noqa: E402
import rebase.replay_audit  # noqa: E402

from pr_rebase_support import _LEASE, _TARGET


# ── _step_advance ────────────────────────────────────────────────────────


def test_step_advance_empty_patch_skips():
    """Empty patch triggers git rebase --skip."""
    skip_called = []

    def fake_run(cmd, **kwargs):
        if "--skip" in cmd:
            skip_called.append(True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=True), \
         mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=3), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle.step_advance("/fake", mock.MagicMock(), target_ref=_TARGET)

    assert rc is None
    assert skip_called


def test_step_advance_empty_commit_event_carries_remaining():
    """`data.remaining` rides on both `step` events or the trail lies.

    The conflict step carried it and this one did not, so a run whose commits
    were mostly empty — which is what a partially-landed branch produces —
    reported no progress at all to anyone reading the trail to see whether it
    was moving.
    """
    fake_trail = mock.MagicMock()
    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=True), \
         mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=4), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")):
        rebase.lifecycle.step_advance(
            "/fake", mock.MagicMock(), target_ref=_TARGET, trail=fake_trail,
        )

    assert fake_trail.decision.call_args.kwargs["data"]["remaining"] == 4


def test_step_advance_survives_a_broken_audit():
    """A crash inside the replay audit fails open rather than killing the run.

    The same contract as the hook entry point, `replay_audit.main`: a bug in
    the audit costs a warning, never the resolutions already staged.
    """
    with mock.patch.object(rebase.lifecycle.replay_audit, "audit_replay",
                           side_effect=RuntimeError("boom")), \
         mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")):
        rc = rebase.lifecycle.step_advance("/fake", mock.MagicMock(), target_ref=_TARGET)

    assert rc is None


def test_step_advance_discard_refusal_names_no_push_when_the_run_held_it(capsys):
    """The resume command a refusal prints carries --no-push along.

    Resuming with a plain `pr rebase --fix` would push a run its operator
    started with the push held.
    """
    loss = rebase.survival.Loss(
        kind=rebase.survival.LossKind.FILE_TAKEN_WHOLE,
        side=rebase.survival.Side.REPLAYED,
    )
    audit = rebase.replay_audit.CommitAudit(
        commit="abc123", subject="subj",
        files=(rebase.survival.FileAudit("f.txt", (loss,)),),
    )

    with mock.patch.object(rebase.lifecycle.replay_audit, "audit_replay", return_value=audit), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "subj")), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=0):
        rc = rebase.lifecycle.step_advance(
            "/fake", mock.MagicMock(), target_ref=_TARGET,
            mode=rebase.types.RunMode.FIX_ONLY,
        )

    assert rc == rebase.types.CONFLICTS_EXIT
    assert "pr rebase --fix --no-push" in capsys.readouterr().err


def test_halt_if_discarding_reports_a_file_checkout_m_could_not_restore(capsys):
    """`git checkout -m` is a no-op for a path the two sides never conflicted over.

    `audit_replay` audits every path the replayed commit touched, not only the
    ones this step had conflicts in — a cleanly auto-merged file can still be
    named here (a questionable three-way merge `survival.audit` flagged
    blocking). Restoring it is a no-op, since there is no conflict between HEAD
    and the replayed commit to recreate, and that must be reported rather than
    silently claimed as handled.
    """
    loss = rebase.survival.Loss(
        kind=rebase.survival.LossKind.HUNK_REVERTED,
        side=rebase.survival.Side.REPLAYED,
    )
    audit = rebase.replay_audit.CommitAudit(
        commit="abc123", subject="subj",
        files=(
            rebase.survival.FileAudit("conflicted.py", (loss,)),
            rebase.survival.FileAudit("clean.py", (loss,)),
        ),
    )

    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(rebase.lifecycle.replay_audit, "audit_replay", return_value=audit), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=["conflicted.py"]), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle._halt_if_discarding(
            "/fake", mock.MagicMock(), rebase.types.ResolutionTally(),
            target_ref=_TARGET, restore=True,
        )

    assert rc == rebase.types.CONFLICTS_EXIT
    checkout_cmds = [cmd for cmd in commands if "checkout" in cmd and "-m" in cmd]
    assert len(checkout_cmds) == 1
    assert "conflicted.py" in checkout_cmds[0] and "clean.py" in checkout_cmds[0]
    assert "No conflict to restore in clean.py" in capsys.readouterr().err


def test_step_advance_continue_succeeds():
    """Non-empty patch with successful --continue returns None."""
    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        rc = rebase.lifecycle.step_advance("/fake", mock.MagicMock(), target_ref=_TARGET)

    assert rc is None


def test_step_advance_continue_fails_but_rebase_in_progress():
    """--continue fails because next commit has conflicts — continue loop."""
    def fake_run(cmd, **kwargs):
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="could not apply")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle.step_advance("/fake", mock.MagicMock(), target_ref=_TARGET)

    assert rc is None


def test_step_advance_continue_fails_records_instead_of_aborting():
    """Same dead abort as its sibling, and the same missing state file."""
    commands = []
    saved = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="stuck state")

    tally = rebase.types.ResolutionTally(files=["a.py"])
    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: saved.append(self)), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle.step_advance(
            "/fake", mock.MagicMock(), tally, target_ref=_TARGET,
        )

    assert rc == 1
    assert not any("--abort" in cmd for cmd in commands)
    assert saved and saved[-1].files_resolved == ["a.py"]


def test_step_advance_continue_failure_records_the_whole_output():
    """The failure path recorded a warn carrying a bare `stderr` key before this.

    Its sibling `_step_conflicts` ends identically and already routes through
    `Trail.failure`, so the whole of what git said reaches an artifact.
    """
    stderr = "".join(f"detail line {n}\n" for n in range(200))
    fake_trail = mock.MagicMock()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=stderr)

    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle.step_advance(
            "/fake", mock.MagicMock(), target_ref=_TARGET, trail=fake_trail,
        )

    assert rc == 1
    fake_trail.warn.assert_not_called()
    fake_trail.failure.assert_called_once()
    kwargs = fake_trail.failure.call_args.kwargs
    assert kwargs["output"].count("detail line") == 200
    assert "detail line 0\n" in kwargs["output"]
    assert kwargs["data"]["exit_code"] == 1



def _two_commit_feat_repo(tmp_path) -> Path:
    repo = init_repo(tmp_path / "repo")
    (repo / "f.txt").write_text("f-base\n")
    (repo / "g.txt").write_text("g-base\n")
    commit_all(repo, "base")
    git_in(repo, "checkout", "-q", "-b", "feat")
    (repo / "f.txt").write_text("f-feat\n")
    commit_all(repo, "feat1")
    (repo / "g.txt").write_text("g-feat\n")
    commit_all(repo, "feat2")
    git_in(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("f-main\n")
    commit_all(repo, "main-f")
    (repo / "g.txt").write_text("g-main\n")
    commit_all(repo, "main-g")
    git_in(repo, "checkout", "-q", "feat")
    return repo


def test_step_advance_resumes_past_manually_committed_step_into_next_conflict(tmp_path):
    """A hand-committed paused step is continued, not skipped."""
    repo = _two_commit_feat_repo(tmp_path)
    paused = git.client.run("rebase", "main", cwd=str(repo))
    assert not paused.ok
    assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]
    (repo / "f.txt").write_text("f-feat\n")
    git_in(repo, "add", "f.txt")
    git_in(repo, "commit", "-q", "--no-verify", "-m", "manual feat1")

    skip_cmds = []
    original = rebase.lifecycle._git_rebase

    def spy(cwd, *args, **kwargs):
        if "--skip" in args:
            skip_cmds.append(args)
        return original(cwd, *args, **kwargs)

    with mock.patch.object(rebase.lifecycle, "_git_rebase", side_effect=spy):
        rc = rebase.lifecycle.step_advance(
            str(repo), mock.MagicMock(), target_ref="main",
        )

    assert rc is None
    assert skip_cmds == []
    assert "manual feat1" in git_out(repo, "log", "-1", "--format=%s")
    assert rebase.inspect.detect_conflicts(str(repo)) == ["g.txt"]


def test_step_advance_skip_into_conflicting_next_commit_returns_none(tmp_path):
    """`--skip` into the next conflict is the next loop step, not a failed skip."""
    repo = _two_commit_feat_repo(tmp_path)
    paused = git.client.run("rebase", "main", cwd=str(repo))
    assert not paused.ok
    assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]
    git_in(repo, "checkout", "--ours", "--", "f.txt")
    git_in(repo, "add", "f.txt")
    assert rebase.inspect.is_empty_patch(str(repo))
    assert rebase.inspect.step_committed_manually(str(repo)) is False

    with mock.patch.object(
        rebase.lifecycle.replay_audit, "audit_replay", return_value=None,
    ):
        rc = rebase.lifecycle.step_advance(
            str(repo), mock.MagicMock(), target_ref="main",
        )

    assert rc is None
    assert rebase.inspect.rebase_in_progress(str(repo))
    assert rebase.inspect.detect_conflicts(str(repo)) == ["g.txt"]


def test_step_advance_clears_requeued_stopped_pick_before_continue(tmp_path):
    """A rescheduled stopped pick is dropped so --continue does not double-pick."""
    repo = init_repo(tmp_path / "requeued")
    (repo / "f.txt").write_text("f-base\n")
    (repo / "g.txt").write_text("g-base\n")
    commit_all(repo, "base")
    git_in(repo, "checkout", "-q", "-b", "feat")
    (repo / "f.txt").write_text("f-feat\n")
    commit_all(repo, "feat1")
    feat1_short = git.client.out("rev-parse", "--short", "HEAD", cwd=str(repo))
    (repo / "g.txt").write_text("g-feat\n")
    commit_all(repo, "feat2")
    git_in(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("f-main\n")
    commit_all(repo, "main-f")
    git_in(repo, "checkout", "-q", "feat")
    paused = git.client.run("rebase", "main", cwd=str(repo))
    assert not paused.ok
    assert rebase.inspect.detect_conflicts(str(repo)) == ["f.txt"]

    (repo / "f.txt").write_text("f-feat\nf-main\n")
    git_in(repo, "add", "f.txt")
    merge_dir = rebase.inspect.git_dir(str(repo)) / rebase.inspect.GIT_REBASE_MERGE_DIR
    todo_path = merge_dir / rebase.inspect.GIT_REBASE_TODO
    todo_path.write_text(f"pick {feat1_short} feat1\n" + todo_path.read_text())

    rc = rebase.lifecycle.step_advance(
        str(repo), mock.MagicMock(), target_ref="main",
    )

    assert rc is None
    assert not rebase.inspect.rebase_in_progress(str(repo))
    subjects = git.client.lines(
        "log", "--format=%s", "main..HEAD", cwd=str(repo),
    )
    assert subjects == ["feat2", "feat1"]


def test_step_advance_refused_clean_pick_returns_1_without_continue(tmp_path):
    """A refused pick is left in place; never auto-committed."""
    saved = []
    with mock.patch.object(rebase.lifecycle, "_halt_if_discarding", return_value=None), \
         mock.patch.object(rebase.inspect, "step_committed_manually", return_value=False), \
         mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase.inspect, "is_refused_clean_pick", return_value="abc1234"), \
         mock.patch.object(rebase.inspect, "rebase_head_info",
                           return_value=("abc1234", "feat: x")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=1), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: saved.append(self)), \
         mock.patch.object(rebase.lifecycle, "rebase_continue") as cont:
        rc = rebase.lifecycle.step_advance(
            str(tmp_path), mock.MagicMock(), target_ref="main",
        )

    assert rc == 1
    cont.assert_not_called()
    assert saved


def test_step_advance_continue_progress_on_conflicts_returns_none():
    """A failed continue that left conflicts is the next loop step."""
    def fake_run(cmd, **kwargs):
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="could not apply",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch.object(rebase.inspect, "is_empty_patch", return_value=False), \
         mock.patch.object(rebase.inspect, "is_refused_clean_pick", return_value=""), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.inspect, "continue_pause_is_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        rc = rebase.lifecycle.step_advance("/fake", mock.MagicMock(), target_ref="main")

    assert rc is None


def test_drive_loop_stalls_when_step_makes_no_progress(tmp_path):
    """A no-op step fails the run and leaves the rebase in place."""
    saved = []
    commands = []

    def fake_run(*args, **kwargs):
        commands.append(args)
        return core.proc.CmdResult()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.lifecycle, "_drive_one_step", return_value=(None, False)), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: saved.append(self)), \
         mock.patch.object(git.client, "run", side_effect=fake_run):
        rc = rebase.lifecycle._drive_loop(
            str(tmp_path), mock.MagicMock(), rebase.types.RunMode.PUSH,
            target_ref="origin/main", lease=_LEASE,
        )

    assert rc == 1
    assert saved
    assert not any("--abort" in args for args in commands)


def test_drive_loop_finished_rebase_is_not_a_stall(tmp_path):
    """Leaving rebase-merge is success, not a stuck step."""
    progress = [True]

    def in_progress(cwd):
        if progress[0]:
            progress[0] = False
            return True
        return False

    with mock.patch.object(rebase.inspect, "rebase_in_progress", side_effect=in_progress), \
         mock.patch.object(rebase.lifecycle, "_drive_one_step", return_value=(None, False)), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as success:
        rc = rebase.lifecycle._drive_loop(
            str(tmp_path), mock.MagicMock(), rebase.types.RunMode.PUSH,
            target_ref="origin/main", lease=_LEASE,
        )

    assert rc == 0
    success.assert_called_once()
