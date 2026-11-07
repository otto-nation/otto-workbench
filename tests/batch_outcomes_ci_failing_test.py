"""classify CI with fixing=False uses ci_failing.

Revert: ignore fixing in _evidence.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.outcomes  # noqa: E402
from batch.model import DecisionKind, EvidenceKind, Step, StepStatus  # noqa: E402
from batch_report_support import make_item  # noqa: E402


def _report(*jobs):
    failures = [{"job": j} for j in jobs]
    return json.dumps({"failures": failures})


def test_classify_read_only_ci_failing_jobs():
    result = batch.outcomes.classify(
        Step.CI, 0, _report("lint", "test"), item=make_item(), log_tail=[],
        fixing=False)
    assert result.status is StepStatus.NEEDS_DECISION
    draft = result.decisions[0]
    assert draft.kind is DecisionKind.STEP_REVIEW
    kinds = [e["kind"] for e in draft.payload["evidence"]]
    assert kinds == [EvidenceKind.CI_FAILING.value]
    assert draft.payload["evidence"][0]["jobs"] == ["lint", "test"]
    assert draft.payload["evidence"][0]["failures"] == 2


def test_empty_stdout_fixing_false_is_done():
    result = batch.outcomes.classify(
        Step.CI, 0, "", item=make_item(), log_tail=[], fixing=False)
    assert result.status is StepStatus.DONE
    assert result.decisions == []


def test_empty_stdout_fixing_true_is_ci_unfixed():
    result = batch.outcomes.classify(
        Step.CI, 0, "", item=make_item(), log_tail=[], fixing=True)
    assert result.status is StepStatus.NEEDS_DECISION
    ev = result.decisions[0].payload["evidence"][0]
    assert ev["kind"] == EvidenceKind.CI_UNFIXED.value
    assert "no fix tally" in ev["detail"]
