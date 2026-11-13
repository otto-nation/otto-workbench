"""Resume hooks on fix.engine — skip finished phases, keep baselines."""

import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import agent.phases  # noqa: E402
import core.publishing  # noqa: E402
import fix.containment  # noqa: E402
import fix.dirty_backup  # noqa: E402
import fix.engine  # noqa: E402
import fix.pass_resume  # noqa: E402
import fix.suite  # noqa: E402
import git.land  # noqa: E402
from config.workbench_config import FixConfig, WorkbenchConfig  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.fix_checkpoint import CheckpointPhase  # noqa: E402

from fix_engine_support import (  # noqa: E402
    StubAdapter, _answer, _settles_at, landed, head, snapshots,
)


class _Killed(BaseException):
    """Simulates SIGKILL: not KeyboardInterrupt/SystemExit, so record_stop is skipped."""


class _Ckpt(StubAdapter):
    """Records deepcopied checkpoints and dies at `kill_at` like a SIGKILL."""

    keeps_checkpoints = True

    def __init__(self, wt_path, count=2, *, kill_at, spec=None):
        super().__init__(wt_path, count, spec=spec)
        self.kill_at = kill_at
        self.ckpts = []

    def checkpoint(self, phase, progress):
        self.ckpts.append((phase, deepcopy(progress)))
        if phase is not self.kill_at:
            return
        if phase is CheckpointPhase.AGENT_RUNNING and not progress.answers:
            return
        raise _Killed()


def _configured(adapter):
    adapter.config = WorkbenchConfig(
        fix=FixConfig(verify_command="true", verify_timeout=30))
    return adapter


def _kill_then_resume(adapter, snapshots, landed, *, verify, suite_result=None):
    """Run until `_Killed`, then resume from the last checkpoint. Returns the resume run."""
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    suite_kw = {}
    if suite_result is not None:
        suite_kw["return_value"] = suite_result
    else:
        suite_kw["return_value"] = fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")
    seen_tracking = []

    def capture_then_answer(*args, **kwargs):
        if adapter.tracking_path.is_file():
            seen_tracking.append(adapter.tracking_path.read_text())
        return _answer(adapter)(*args, **kwargs)

    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=capture_then_answer) as agent_fn,
        patch.object(fix.suite, "run", **suite_kw) as suite_fn,
        patch.object(fix.dirty_backup, "save", wraps=fix.dirty_backup.save) as backup,
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
        phase, progress = adapter.ckpts[-1]
        adapter.resume = progress
        adapter.resume_phase = phase
        adapter.kill_at = None
        agent_fn.reset_mock()
        suite_fn.reset_mock()
        landed.reset_mock()
        verify.reset_mock()
        backup.reset_mock()
        seen_tracking.clear()
        run = fix.engine.run(adapter, verify=verify)
        return run, agent_fn, suite_fn, backup, seen_tracking


_PHASES = (
    CheckpointPhase.AGENT_RUNNING,
    CheckpointPhase.AGENT_DONE,
    CheckpointPhase.VERIFIED,
    CheckpointPhase.CHECKED,
    CheckpointPhase.LANDED,
)


@pytest.mark.parametrize("kill_at", _PHASES)
def test_resume_skips_finished_phases(kill_at, tmp_path, landed, head, snapshots):
    """A resume must not re-call any phase that had already checkpointed."""
    adapter = _configured(_Ckpt(tmp_path, count=2, kill_at=kill_at))
    verify = MagicMock(return_value={})
    run, agent_fn, suite_fn, backup, seen_tracking = _kill_then_resume(
        adapter, snapshots, landed, verify=verify)

    assert backup.call_count == 0
    if kill_at is CheckpointPhase.AGENT_RUNNING:
        assert agent_fn.call_count == 1
        assert seen_tracking, "resume did not invoke the agent"
        assert "<!-- fix:i0 " not in seen_tracking[0]
        assert "<!-- fix:i1 " in seen_tracking[0]
    else:
        assert agent_fn.call_count == 0
    if kill_at.reached(CheckpointPhase.VERIFIED):
        assert verify.call_count == 0
    else:
        assert verify.call_count == 1
    if kill_at.reached(CheckpointPhase.CHECKED):
        assert suite_fn.call_count == 0
    else:
        assert suite_fn.call_count >= 1
    if kill_at.reached(CheckpointPhase.LANDED):
        assert landed.call_count == 0
    else:
        assert landed.call_count == 1
    assert all(o.outcome is FixOutcome.FIXED for o in run.outcomes)
    assert [o.id for o in run.outcomes] == ["i0", "i1"]
    assert run.landed is not None
    assert run.landed.sha == "abc1234"


def test_mid_batch_kill_skips_the_ticked_item(tmp_path, landed, head, snapshots):
    """A tick written to the tracking file is an answer even without a checkpoint."""
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.AGENT_DONE))

    def tick_then_die(_phase, _prompt, **_kwargs):
        _answer(adapter, ids={"i0"})(_phase, _prompt, **_kwargs)
        raise _Killed()

    snapshots.side_effect = _settles_at(set(), {"a.py"})
    verify = MagicMock(return_value={})
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=tick_then_die),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
    phase, progress = adapter.ckpts[-1]
    adapter.resume = progress
    adapter.resume_phase = phase
    adapter.kill_at = None
    seen = []

    def run_fix(_phase, _prompt, **_kwargs):
        seen.append(adapter.tracking_path.read_text())
        return _answer(adapter)(_phase, _prompt, **_kwargs)

    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=run_fix) as agent_fn,
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
    ):
        run = fix.engine.run(adapter, verify=verify)
    assert agent_fn.call_count == 1
    assert seen, "resume did not invoke the agent for the remaining item"
    assert "<!-- fix:i0 " not in seen[0]
    assert "<!-- fix:i1 " in seen[0]
    assert all(o.outcome is FixOutcome.FIXED for o in run.outcomes)
    assert {o.id for o in run.outcomes} == {"i0", "i1"}


def test_empty_remaining_with_unbounded_chunk_does_not_invoke(
    tmp_path, landed, head, snapshots,
):
    """`chunks([], 0) == [[]]` would otherwise invoke the agent on nothing."""
    adapter = _configured(_Ckpt(
        tmp_path, count=1, kill_at=CheckpointPhase.AGENT_RUNNING))
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    verify = MagicMock(return_value={})

    def tick_then_die(_phase, _prompt, **_kwargs):
        _answer(adapter)(_phase, _prompt, **_kwargs)
        raise _Killed()

    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=0),
        patch.object(agent.invoke, "run_fix", side_effect=tick_then_die),
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
    phase, progress = adapter.ckpts[-1]
    adapter.resume = progress
    adapter.resume_phase = phase
    adapter.kill_at = None
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=0),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)) as agent_fn,
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
    ):
        run = fix.engine.run(adapter, verify=verify)
    assert agent_fn.call_count == 0
    assert run.outcomes[0].outcome is FixOutcome.FIXED


def test_checked_timed_out_resume_does_not_rerun_suite_or_hold(
    tmp_path, landed, head, snapshots,
):
    """TIMED_OUT is not a red suite: resume must not hold publishing extra."""
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.CHECKED))
    timed = fix.suite.SuiteResult(
        status=fix.suite.SuiteStatus.TIMED_OUT, command="true")
    verify = MagicMock(return_value={})
    run, agent_fn, suite_fn, _backup, _seen = _kill_then_resume(
        adapter, snapshots, landed, verify=verify, suite_result=timed)
    assert agent_fn.call_count == 0
    assert suite_fn.call_count == 0
    assert verify.call_count == 0
    assert landed.call_count == 1
    assert adapter.suite.status is fix.suite.SuiteStatus.TIMED_OUT
    assert core.publishing.held() == ""
    assert run.landed.sha == "abc1234"


def test_plain_stub_adapter_never_checkpoints_or_unlinks(
    tmp_path, landed, head, snapshots,
):
    """CI and review passes keep the default: no checkpoint, tracking file stays."""
    adapter = StubAdapter(tmp_path)
    adapter.artifacts.mkdir(parents=True)
    adapter.tracking_path.write_text("keep\n")
    unlinked = []
    orig = Path.unlink

    def spy(self, *args, **kwargs):
        if Path(self) == adapter.tracking_path:
            unlinked.append(True)
        return orig(self, *args, **kwargs)

    orig_take = fix.containment.take
    takes = []

    def count_take(*args, **kwargs):
        takes.append(1)
        return orig_take(*args, **kwargs)

    with (
        patch.object(StubAdapter, "checkpoint") as ck,
        patch.object(Path, "unlink", spy),
        patch.object(fix.containment, "take", count_take),
        patch.object(fix.pass_resume, "agent_files") as af,
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
    ):
        run = fix.engine.run(adapter)
    ck.assert_not_called()
    af.assert_not_called()
    assert unlinked == []
    assert run.landed is not None
    assert adapter.keeps_checkpoints is False
    assert adapter.resume is None
    # Zero extra takes: agent_files (the checkpoint-only snapshot) was not called.
    assert takes
