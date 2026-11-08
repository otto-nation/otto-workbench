"""Schema-2 scheduler: record findings, never open a decision.

Revert targets are named in each test.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
from batch.model import FindingReason, ItemStatus, Step, StepStatus  # noqa: E402
# `_close_open_sessions` and `_quiet_outcomes` are autouse fixtures, imported only so
# pytest registers them (the `noqa: F401` covers them); the other names are used directly.
from batch_scheduler_support import (  # noqa: F401,E402
    NEED, Harness, _close_open_sessions, _quiet_outcomes, row,
)


def _v2(**kw):
    return Harness([row(1)], with_ai=[], **kw)


def test_conflict_records_a_finding_and_run_is_done():
    # Revert: delete the schema-2 branch in _decide.
    payload = {"aborted": True, "tree": "clean", "classes": {"a.go": "ai_merge"},
               "files": ["a.go"]}
    h = _v2(codes={("rebase", "/wt/b1"): 3},
            stdouts={("rebase", "/wt/b1"): json.dumps(payload)})
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    item = h.run.items[0]
    assert item.status is ItemStatus.NEEDS_SESSION
    assert item.findings[0].reason is FindingReason.CONFLICT_HANDWRITTEN
    assert h.run.decisions == []
    assert any(k == "finding_created" for k, _ in h.events)


def test_default_rebase_argv_has_no_fix():
    # Revert: _start passes ai=True.
    h = _v2()
    h.sched.run_until_blocked()
    assert h.spawned[0][:3] == ["pr", "rebase", "--no-push"]
    assert h.spawned[0][-2:] == ["--repo-dir", "/wt/b1"]
    assert "--fix" not in h.spawned[0]


def test_comments_without_ai_spawns_nothing():
    # Revert: delete the _note_comments call.
    h = Harness([row(1, {Step.COMMENTS: NEED})], with_ai=[])
    h.sched.run_until_blocked()
    assert not any(a[1] == "comments" for a in h.spawned)
    item = h.run.items[0]
    assert any(f.reason is FindingReason.THREADS_NEED_HUMAN for f in item.findings)
    assert item.status is ItemStatus.DONE


def test_drafted_rebase_closes_done_with_ready_to_push():
    # Revert: delete the `d is None` branch in _close.
    h = Harness([row(1, {Step.REBASE: NEED})], with_ai=[],
                moves={("rebase", "/wt/b1")})
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert item.status is ItemStatus.DONE
    assert any(f.reason is FindingReason.READY_TO_PUSH for f in item.findings)
    assert not any(d.kind is batch.model.DecisionKind.PUBLISH for d in h.run.decisions)


def test_stacked_child_waits_after_base_finishes():
    # Revert: drop the schema check in _stacked_wait.
    h = Harness([row(1), row(2, base_ref="b1")], with_ai=[])
    h.sched.run_until_blocked()
    assert h.run.item("o/r#1").status is ItemStatus.DONE
    child = h.run.item("o/r#2")
    assert any(f.reason is FindingReason.STACK_WAIT_BASE for f in child.findings)
    assert not any(a[-1] == "/wt/b2" for a in h.spawned)


def test_interrupt_requeues_the_step():
    # Revert: delete the schema-2 branch in _interrupt_running.
    h = _v2()
    rec = h.run.items[0].step(Step.REBASE)
    rec.status = StepStatus.RUNNING
    h.run.items[0].status = ItemStatus.RUNNING
    assert batch.scheduler.mark_interrupted(h.run) is True
    assert rec.status is StepStatus.PENDING
    assert h.run.items[0].status is ItemStatus.QUEUED
    assert h.run.decisions == []
