"""A done item with drafted work and no published_sha keeps the failed window.

Revert: `_kept_work` treating status==DONE with drafted steps as clean when
there is no publish-discard decision.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.gc  # noqa: E402
import batch.plan  # noqa: E402
import batch.store  # noqa: E402
import core.serde  # noqa: E402
import review.gc  # noqa: E402
from batch.model import (Item, ItemStatus, Run, RunStatus, Step,  # noqa: E402
                         StepRecord, StepStatus)
from batch.model import Finding, FindingReason, Severity  # noqa: E402

NOW = 2_000_000_000.0
DAY = 86400


def _save(run_id, item, age_days):
    run = Run(id=run_id, started_at="t", steps=[Step.REBASE], pool=1,
              auto_publish=[], status=RunStatus.DONE, items=[item])
    core.serde.write_json(batch.store.run_dir(run.id) / batch.store.STATE_FILE,
                          core.serde.to_dict(run))
    stamp = NOW - age_days * DAY
    os.utime(batch.store.run_dir(run_id) / batch.store.STATE_FILE, (stamp, stamp))


def test_done_drafted_without_published_sha_uses_the_failed_window(monkeypatch):
    # Revert: return False from _kept_work when status is DONE and drafted.
    monkeypatch.setattr(batch.plan, "drop_refs", lambda *a, **k: True)
    item = Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b",
                head_sha="aaa", status=ItemStatus.DONE, published_sha="",
                steps=[StepRecord(Step.REBASE, status=StepStatus.DONE, drafted=True)])
    _save("kept", item, age_days=review.gc.GC_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 0
    _save("kept", item, age_days=review.gc.GC_FAILED_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 1


def test_non_act_findings_use_the_failed_window(monkeypatch):
    # Revert: ignore item.findings in _kept_work.
    monkeypatch.setattr(batch.plan, "drop_refs", lambda *a, **k: True)
    item = Item(
        key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
        status=ItemStatus.NEEDS_SESSION,
        findings=[Finding(FindingReason.CONFLICT_HANDWRITTEN, "rebase",
                          data={"severity": Severity.BLOCK.value})],
        steps=[StepRecord(Step.REBASE, status=StepStatus.DONE)])
    _save("blocked", item, age_days=review.gc.GC_STALE_DAYS + 1)
    assert batch.gc.gc_runs(now=NOW) == 0
