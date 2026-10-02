"""Tests for rebase.lifecycle: driving a rebase through its conflict and advance steps."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.report  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.refusals  # noqa: E402
import rebase.lease  # noqa: E402
import pr.domains  # noqa: E402
import agent.backend

from pr_rebase_support import _LEASE, _TARGET


# ── _drive_to_completion ─────────────────────────────────────────────────


def test_drive_to_completion_already_done():
    """Rebase already finished — returns success immediately."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as mock_success:
        result = rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert result == 0
    mock_success.assert_called_once()
    tally = mock_success.call_args[0][3]
    assert tally.files == []
    assert tally.commits == 0


def test_drive_to_completion_recovers_lease_from_remembered_tip():
    """A resumed rebase (no ``lease=`` passed) must recover it from the
    remote-tracking ref, not from the local branch's pre-rebase tip —
    unpushed local commits would otherwise leave the lease naming a SHA the
    remote never had, and the eventual push is refused with ``stale info``.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as mock_success, \
         mock.patch.object(rebase.lease, "remembered_tip", return_value="remote-tip") as mock_remembered, \
         mock.patch.object(rebase.lease, "resolve", return_value=_LEASE) as mock_resolve:
        rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    mock_remembered.assert_called_once_with("/fake", ctx.branch)
    mock_resolve.assert_called_once_with("/fake", ctx.branch, "remote-tip")
    mock_success.assert_called_once()


def test_drive_to_completion_with_conflicts_fix():
    """Conflicts detected with --fix: resolves via AI and continues."""
    ctx = mock.MagicMock()
    rebase_state = [True, False]
    progress_state = [0]

    def fake_in_progress(cwd):
        idx = min(progress_state[0], len(rebase_state) - 1)
        progress_state[0] += 1
        return rebase_state[idx]

    conflict_rounds = [["file.go"]]

    def fake_conflicts(cwd):
        if conflict_rounds[0]:
            return [conflict_rounds[0].pop()]
        return []

    with mock.patch.object(rebase.inspect, "rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(rebase.inspect, "detect_conflicts", side_effect=fake_conflicts), \
         mock.patch.object(rebase.lifecycle, "step_conflicts", return_value=None) as mock_step, \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as mock_success:
        result = rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert result == 0
    mock_step.assert_called_once()
    mock_success.assert_called_once()
    tally = mock_success.call_args[0][3]
    assert tally.files == []
    assert tally.commits == 1


def test_drive_to_completion_conflicts_no_fix():
    """Conflicts without --fix: reports and exits 3."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=["file.go"]), \
         mock.patch.object(rebase.lifecycle, "step_conflicts", return_value=3):
        result = rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert result == 3


def test_drive_to_completion_empty_commit():
    """Empty commit detected: skips via _step_advance."""
    ctx = mock.MagicMock()
    rebase_state = [True, False]
    call_count = [0]

    def fake_in_progress(cwd):
        idx = min(call_count[0], len(rebase_state) - 1)
        call_count[0] += 1
        return rebase_state[idx]

    with mock.patch.object(rebase.inspect, "rebase_in_progress", side_effect=fake_in_progress), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(rebase.lifecycle, "step_advance", return_value=None) as mock_advance, \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert result == 0
    mock_advance.assert_called_once()


def test_drive_to_completion_safety_valve():
    """Exceeding max steps aborts the rebase."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.lifecycle, "MAX_REBASE_STEPS", 2), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(rebase.lifecycle, "step_advance", return_value=None), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        result = rebase.lifecycle.drive_to_completion(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert result == 1


# ── _step_conflicts ──────────────────────────────────────────────────────


def test_step_conflicts_no_fix_reports():
    """Without --fix, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(rebase.lifecycle, "_report_conflicts_and_stop", return_value=3):
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.PUSH, ["a.py"],
            rebase.types.ResolutionTally(), target_ref=_TARGET,
        )

    assert rc == 3


def test_step_conflicts_no_fix_saves_state():
    """Without --fix, saves conflicts state before returning."""
    ctx = mock.MagicMock()
    saved = []

    def fake_save(self, saved_ctx):
        saved.append((self.status, saved_ctx))

    with mock.patch.object(rebase.types.RebaseOutcome, "save", fake_save), \
         mock.patch.object(rebase.types.ConflictReport, "from_repo") as mock_report:
        rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.PUSH, ["a.py"],
            rebase.types.ResolutionTally(), target_ref=_TARGET,
        )

    assert saved == [(pr.domains.RebaseStatus.CONFLICTS, ctx)]
    mock_report.return_value.emit.assert_called_once()


def test_step_conflicts_fix_resolves():
    """With --fix, resolves conflicts via AI and returns None to continue."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()

    with mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
             return_value=rebase.types.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["a.py"], tally, target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["a.py"]
    assert tally.stale == []


def test_step_conflicts_records_stale_files():
    """Files whose regeneration failed are carried into the tally as stale."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()
    resolution = rebase.types.Resolution(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"],
    )

    with mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=0), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts", return_value=resolution), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["pnpm-lock.yaml"], tally,
            target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["pnpm-lock.yaml"]
    assert tally.stale == ["pnpm-lock.yaml"]


def _run_step_over_budget(*, force=False, already=None, conflicts=None):
    """Run one conflicted step with a tally already near the file budget."""
    over = rebase.types.CONFLICT_FILE_BUDGET + 1
    tally = rebase.types.ResolutionTally(
        files=already if already is not None else [f"f{i}.py" for i in range(over)],
    )
    with mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.refusals, "refuse_over_budget", return_value=4) as refuse, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
             return_value=rebase.types.Resolution(files=["late.py"]),
         ) as resolve, \
         mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc", "s")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=1), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", mock.MagicMock(), rebase.types.RunMode.FIX,
            conflicts if conflicts is not None else ["late.py"], tally,
            target_ref=_TARGET, force=force,
        )
    return rc, refuse, resolve


def test_step_conflicts_refuses_past_the_file_budget():
    """A rebase conflicting across too many files stops instead of resolving.

    Resolving dozens of files unattended is how a rebase rewrites a file the
    branch never touched — the spread is the signal that the branch and its
    base have diverged past what automatic resolution should attempt.
    """
    rc, refuse, resolve = _run_step_over_budget()

    assert rc == 4
    resolve.assert_not_called()
    breach = refuse.call_args[0][2]
    assert breach.signal is rebase.types.RefusalSignal.CONFLICTS_OVER_BUDGET
    assert str(rebase.types.CONFLICT_FILE_BUDGET + 2) in breach.detail


def test_step_conflicts_counts_distinct_files_not_conflicts():
    """A file conflicting in several replayed commits counts once *for spread*."""
    repeated = ["same.py"] * (rebase.types.CONFLICT_FILE_BUDGET + 5)
    rc, refuse, resolve = _run_step_over_budget(already=repeated, conflicts=["same.py"])

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_refuses_past_the_resolution_budget():
    """The count the file budget is deliberately blind to.

    Nine files conflicting in each of seven replayed commits is a spread of
    nine — well inside the file budget — while the run makes sixty-three AI
    calls. Before this budget existed the run burned every one of them against
    a branch whose work had already landed in another shape.
    """
    files = [f"f{i}.py" for i in range(9)]
    spent = files * (rebase.types.CONFLICT_RESOLUTION_BUDGET // len(files) + 1)
    rc, refuse, resolve = _run_step_over_budget(already=spent, conflicts=files)

    assert rc == 4
    resolve.assert_not_called()
    breach = refuse.call_args[0][2]
    assert breach.signal is rebase.types.RefusalSignal.RESOLUTIONS_OVER_BUDGET
    # The spread stayed well inside the file budget the whole time, which is
    # why the older signal never fired on this shape.
    assert len(set(spent)) <= rebase.types.CONFLICT_FILE_BUDGET


def test_step_conflicts_resolution_budget_is_waived_by_force():
    files = [f"f{i}.py" for i in range(9)]
    spent = files * (rebase.types.CONFLICT_RESOLUTION_BUDGET // len(files) + 1)
    rc, refuse, resolve = _run_step_over_budget(
        already=spent, conflicts=files, force=True,
    )

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_budget_is_waived_by_force():
    rc, refuse, resolve = _run_step_over_budget(force=True)

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_under_the_budget_resolves():
    rc, refuse, resolve = _run_step_over_budget(already=["a.py"], conflicts=["b.py"])

    assert rc is None
    refuse.assert_not_called()
    assert resolve.called


def test_step_conflicts_unresolvable_file_pauses_without_aborting():
    """An unresolvable file stops the run; it does not destroy it.

    The abort this replaces cost one run nine resolved files and a completed
    commit, because a tenth file's answer would not parse. The rebase stays in
    the worktree, the exit code is the same 3 a human-resolvable conflict
    returns, and `git rebase --abort` is not run.
    """
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()
    resolution = rebase.types.Resolution(files=["ok.py"], failed=["bad.py"])
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr="",
        )

    saved = []
    with mock.patch.object(rebase.inspect, "rebase_head_info",
                           return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=["bad.py"]), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
                           return_value=resolution), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: saved.append(self)), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["ok.py", "bad.py"], tally,
            target_ref=_TARGET,
        )

    assert rc == 3
    assert not any("--abort" in cmd for cmd in commands)
    # The resolution that did succeed is kept, in the tally and in the state
    # a resume reads — the whole point of not aborting.
    assert tally.files == ["ok.py"]
    assert saved and saved[-1].files_resolved == ["ok.py"]
    assert saved[-1].status is rebase.types.RebaseStatus.CONFLICTS


def test_step_conflicts_fix_ai_unavailable():
    """With --fix but AI unavailable, reports conflicts and returns 3."""
    ctx = mock.MagicMock()
    with mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.lifecycle, "_report_conflicts_and_stop", return_value=3):
        mock_ai.is_available.return_value = False
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["a.py"],
            rebase.types.ResolutionTally(), target_ref=_TARGET,
        )

    assert rc == 3


def test_step_conflicts_continue_fails_but_rebase_in_progress():
    """rebase --continue fails because next commit has conflicts — continue loop."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()

    def fake_run(cmd, **kwargs):
        r = subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "rebase" in cmd and "--continue" in cmd:
            r.returncode = 1
            r.stderr = "error: could not apply abc123... next commit"
        return r

    with mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
             return_value=rebase.types.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["a.py"], tally, target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["a.py"]


def test_step_conflicts_continue_fails_records_instead_of_aborting():
    """The abort here was a no-op that made the log claim something false.

    This path is past the check that says the rebase is *not* in progress, so
    `git rebase --abort` had nothing to abort and failed silently — while the
    console said "aborting" and nothing recorded what the run had resolved.
    """
    ctx = mock.MagicMock()
    commands = []
    saved = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "rebase" in cmd and "--continue" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="fatal: error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    tally = rebase.types.ResolutionTally()
    with mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=0), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
             return_value=rebase.types.Resolution(files=["a.py"]),
         ), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types.RebaseOutcome, "save",
                           lambda self, c: saved.append(self)), \
         mock.patch("subprocess.run", side_effect=fake_run):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX, ["a.py"], tally,
            target_ref=_TARGET,
        )

    assert rc == 1
    assert not any("--abort" in cmd for cmd in commands)
    assert saved and saved[-1].files_resolved == ["a.py"]


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


def test_fix_only_still_resolves_conflicts():
    """--no-push suppresses the push, not the AI — the two must stay separable."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()

    with mock.patch.object(rebase.inspect, "rebase_head_info", return_value=("abc123", "feat: thing")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits", return_value=2), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(rebase.resolve_ai, "resolve_file_conflicts",
             return_value=rebase.types.Resolution(files=["a.py"]),
         ), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", ctx, rebase.types.RunMode.FIX_ONLY, ["a.py"], tally,
            target_ref=_TARGET,
        )

    assert rc is None
    assert tally.files == ["a.py"]
