"""Resume outcomes: demotions, deferred rows, landed progress, recover."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import agent.phases  # noqa: E402
import core.publishing  # noqa: E402
import fix.containment  # noqa: E402
import fix.engine  # noqa: E402
import fix.regression  # noqa: E402
import fix.scope  # noqa: E402
import fix.suite  # noqa: E402
import git.client  # noqa: E402
import git.land  # noqa: E402
from agent.diagnosis import Diagnosis, DiagnosisKind  # noqa: E402
from fix.containment import FileRecord, Snapshot  # noqa: E402
from fix.gate import Verdict  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.fix_checkpoint import CheckpointPhase  # noqa: E402

from fix_engine_resume_test import (  # noqa: E402
    _Ckpt, _Killed, _configured, _kill_then_resume,
)
from fix_engine_support import (  # noqa: E402
    _answer, _settles_at, landed, head, snapshots,
)


def _resume_after_kill(adapter, snapshots, landed, *, verify, suite_result=None,
                       run_fix=None):
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    suite_kw = {"return_value": suite_result or fix.suite.SuiteResult(
        status=fix.suite.SuiteStatus.GREEN, command="true")}
    answer = run_fix or _answer(adapter)
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=answer),
        patch.object(fix.suite, "run", **suite_kw),
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
    return phase, progress


def test_verified_resume_keeps_gate_demotion(tmp_path, landed, head, snapshots):
    """Tracking-file FIXED must not overlay a gate demotion after AGENT_DONE."""
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.VERIFIED))
    verify = MagicMock(return_value={
        "i0": Verdict(ok=False, detail="broken i0"),
        "i1": Verdict(ok=False, detail="broken i1"),
    })
    run, agent_fn, suite_fn, _backup, _seen = _kill_then_resume(
        adapter, snapshots, landed, verify=verify)
    assert agent_fn.call_count == 0
    assert verify.call_count == 0
    assert all(o.outcome is FixOutcome.NEEDS_HUMAN for o in run.outcomes)
    assert {o.id for o in run.outcomes} == {"i0", "i1"}


def test_checked_red_resume_holds_and_keeps_demotion(
    tmp_path, landed, head, snapshots, publishing_on, monkeypatch,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.CHECKED))
    red = fix.suite.SuiteResult(
        status=fix.suite.SuiteStatus.RED, command="true")
    verify = MagicMock(return_value={})
    _resume_after_kill(adapter, snapshots, landed, verify=verify, suite_result=red)
    monkeypatch.setattr(core.publishing, "_held", "")
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=red) as suite_fn,
    ):
        run = fix.engine.run(adapter, verify=verify)
    assert suite_fn.call_count == 0
    assert "checks are red" in core.publishing.held()
    assert all(
        o.outcome is FixOutcome.FIXED and o.verified is False
        for o in run.outcomes
    )


def test_checked_preexisting_resume_holds(
    tmp_path, landed, head, snapshots, publishing_on, monkeypatch,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.CHECKED))
    preexisting = fix.suite.SuiteResult(
        status=fix.suite.SuiteStatus.PREEXISTING, command="true")
    verify = MagicMock(return_value={})
    _resume_after_kill(
        adapter, snapshots, landed, verify=verify, suite_result=preexisting)
    monkeypatch.setattr(core.publishing, "_held", "")
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=preexisting),
    ):
        fix.engine.run(adapter, verify=verify)
    assert "already red" in core.publishing.held()


def test_agent_done_keeps_deferred(tmp_path, landed, head, snapshots):
    adapter = _configured(_Ckpt(
        tmp_path, count=2, kill_at=CheckpointPhase.AGENT_DONE))
    verify = MagicMock(return_value={})
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix",
                     side_effect=_answer(adapter, ids={"i0"})),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
    phase, progress = adapter.ckpts[-1]
    assert phase is CheckpointPhase.AGENT_DONE
    assert any(o.id == "i1" and o.outcome is FixOutcome.DEFERRED
               for o in progress.answers)
    adapter.resume = progress
    adapter.resume_phase = phase
    adapter.kill_at = None
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix",
                     side_effect=_answer(adapter, ids={"i0"})) as agent_fn,
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
    ):
        run = fix.engine.run(adapter, verify=verify)
    assert agent_fn.call_count == 0
    by_id = {o.id: o for o in run.outcomes}
    assert by_id["i0"].outcome is FixOutcome.FIXED
    assert by_id["i1"].outcome is FixOutcome.DEFERRED


def test_landed_checkpoint_keeps_agent_progress(
    tmp_path, landed, head, snapshots,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=1, kill_at=CheckpointPhase.LANDED))
    verify = MagicMock(return_value={})
    head_snap = Snapshot(
        files=(FileRecord(path="a.py", sha="aaa", mode="100644"),), ok=True)
    work_snap = Snapshot(
        files=(FileRecord(path="a.py", sha="bbb", mode="100644"),),
        ok=True, tree="prepass1",
    )
    stop = Diagnosis(kind=DiagnosisKind.MAX_TURNS, num_turns=4)

    def answer_and_stop(*args, **kwargs):
        _answer(adapter)(*args, **kwargs)
        return agent.invoke.FixResult(0, None, stop=stop)

    snapshots.side_effect = _settles_at(set(), {"a.py"})
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=answer_and_stop),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
        patch.object(fix.containment, "take", return_value=work_snap),
        patch.object(fix.containment, "read_tree", return_value=head_snap),
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
    phase, progress = adapter.ckpts[-1]
    assert phase is CheckpointPhase.LANDED
    assert progress.file_shas.get("a.py") == "bbb"
    assert progress.max_turns > 0
    assert progress.diagnosis == stop
    assert progress.pre_pass_tree == "prepass1"


def test_checked_resume_recovers_when_head_moved_one(
    tmp_path, landed, head, snapshots,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=1, kill_at=CheckpointPhase.CHECKED))
    verify = MagicMock(return_value={})
    _resume_after_kill(adapter, snapshots, landed, verify=verify)
    landed.reset_mock()

    def fake_out(*args, cwd=None, default="", **kwargs):
        token = args[-1] if args else ""
        if str(token).endswith("^"):
            return "9999999"
        if token in ("9999999", "abc1234"):
            return token
        return default

    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
        patch.object(git.client, "head_sha", return_value="abc1234"),
        patch.object(git.client, "out", side_effect=fake_out),
    ):
        run = fix.engine.run(adapter, verify=verify)
    assert landed.call_count == 1
    assert landed.call_args.kwargs.get("recover_from") == "9999999"
    assert run.landed.sha == "abc1234"


def test_landed_resume_skips_regression(
    tmp_path, landed, head, snapshots,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=1, kill_at=CheckpointPhase.LANDED))
    verify = MagicMock(return_value={})
    _resume_after_kill(adapter, snapshots, landed, verify=verify)
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
        patch.object(fix.regression, "check") as regression,
        patch.object(fix.scope, "report_unattributable") as unattr,
    ):
        fix.engine.run(adapter, verify=verify)
    regression.assert_not_called()
    unattr.assert_not_called()


def test_resume_reads_persisted_pre_pass_tree(
    tmp_path, landed, head, snapshots,
):
    adapter = _configured(_Ckpt(
        tmp_path, count=1, kill_at=CheckpointPhase.AGENT_DONE))
    verify = MagicMock(return_value={})
    work_snap = Snapshot(ok=True, tree="prepass1")
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
        patch.object(fix.containment, "take", return_value=work_snap),
    ):
        try:
            fix.engine.run(adapter, verify=verify)
            raise AssertionError("pass was not killed")
        except _Killed:
            pass
    phase, progress = adapter.ckpts[-1]
    assert progress.pre_pass_tree == "prepass1"
    adapter.resume = progress
    adapter.resume_phase = phase
    adapter.kill_at = None
    with (
        patch.object(agent.phases, "phase_chunk_size", return_value=1),
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
        patch.object(fix.suite, "run", return_value=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="true")),
        patch.object(fix.containment, "read_tree") as read_tree,
    ):
        read_tree.return_value = Snapshot(ok=True)
        fix.engine.run(adapter, verify=verify)
    assert any(
        call.args[1] == "prepass1" for call in read_tree.call_args_list
    )
