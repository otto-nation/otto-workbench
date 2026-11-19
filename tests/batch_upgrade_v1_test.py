"""upgrade_v1 converts in-flight v1 runs one-way.

Revert targets are named in each test.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.findings  # noqa: E402
import core.serde  # noqa: E402
from batch.model import (DecisionKind, FindingReason, ItemStatus,  # noqa: E402
                         RunStatus, Step, StepRecord, StepStatus)
from batch_report_support import make_decision, make_item, make_run  # noqa: E402


def test_waiting_v1_run_converts_one_way():
    # Revert: record PUBLISH; skip the INTERRUPTED branch; drop schema_version = 2.
    events = []
    conflict = make_item(1, status=ItemStatus.AWAITING_DECISION,
                         steps=[StepRecord(Step.REBASE)])
    comments = make_item(2, status=ItemStatus.AWAITING_DECISION,
                         steps=[StepRecord(Step.COMMENTS)])
    publish = make_item(3, status=ItemStatus.READY_TO_PUBLISH,
                        steps=[StepRecord(Step.CI, status=StepStatus.DONE, drafted=True)])
    interrupted = make_item(4, status=ItemStatus.AWAITING_DECISION,
                            steps=[StepRecord(Step.REBASE, status=StepStatus.INTERRUPTED)])
    run = make_run(
        [conflict, comments, publish, interrupted],
        decisions=[
            make_decision(DecisionKind.REBASE_CONFLICT, "rebase", {
                "aborted": True, "classes": {"a.go": "ai_merge"}, "files": ["a.go"],
            }, id="d1", item=conflict.key),
            make_decision(DecisionKind.COMMENT_ITEM, "comments", id="d2",
                          item=comments.key),
            make_decision(DecisionKind.PUBLISH, "publish", {"drafted": ["ci"]},
                          id="d3", item=publish.key),
            make_decision(DecisionKind.INTERRUPTED, "rebase", {"log_path": "x"},
                          id="d4", item=interrupted.key),
        ],
        status=RunStatus.WAITING)
    run.schema_version = 1
    assert batch.findings.upgrade_v1(
        run, emit=lambda kind, **fields: events.append((kind, fields))) is True
    assert run.schema_version == 2
    assert run.with_ai is None
    assert all(d.resolution == "upgraded" for d in run.decisions)
    assert conflict.status is ItemStatus.NEEDS_SESSION
    assert conflict.findings[0].reason is FindingReason.CONFLICT_HANDWRITTEN
    assert comments.status is ItemStatus.QUEUED
    assert comments.findings[0].reason is FindingReason.THREADS_NEED_HUMAN
    assert publish.status is ItemStatus.QUEUED
    assert publish.findings == []
    assert interrupted.step(Step.REBASE).status is StepStatus.PENDING
    assert any(k == "finding_created" for k, _ in events)


def test_finished_or_v2_runs_are_untouched():
    # Revert: drop the FINISHED_RUN guard.
    item = make_item(1, status=ItemStatus.DONE)
    done = make_run(
        [item],
        decisions=[make_decision(DecisionKind.PUBLISH, "publish", id="d1")],
        status=RunStatus.DONE)
    done.schema_version = 1
    before = core.serde.to_dict(done)
    assert batch.findings.upgrade_v1(done, emit=lambda *a, **k: None) is False
    assert core.serde.to_dict(done) == before

    live = make_run([make_item(1, status=ItemStatus.QUEUED)], status=RunStatus.WAITING)
    live.schema_version = 2
    before = core.serde.to_dict(live)
    assert batch.findings.upgrade_v1(live, emit=lambda *a, **k: None) is False
    assert core.serde.to_dict(live) == before
