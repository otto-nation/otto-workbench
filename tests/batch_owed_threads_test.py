"""Batch counts the threads `pr.needs_person` says are owed, declined ones included.

Revert: restore batch.outcomes' own _OWED = (NEEDS_HUMAN, DEFERRED) filter in
comment_items; the declined test fails.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402

ITEM = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b",
                        head_sha="s", worktree="/wt")


def _with(monkeypatch, items):
    state = SimpleNamespace(fix=SimpleNamespace(fix=SimpleNamespace(items=items)))
    monkeypatch.setattr(batch.outcomes, "_load_pr_state", lambda item: state)


def test_a_declined_thread_is_owed_and_opens_a_comment_decision(monkeypatch):
    _with(monkeypatch, [ItemOutcome(id="PRRT_9", outcome=FixOutcome.DECLINED, reason="premise")])
    assert [i["id"] for i in batch.outcomes.comment_items(ITEM)] == ["PRRT_9"]
    result = batch.outcomes.classify(batch.model.Step.COMMENTS, 0, "{}", item=ITEM, log_tail=[])
    assert result.status is batch.model.StepStatus.NEEDS_DECISION


def test_no_state_owes_nothing(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "_load_pr_state", lambda item: None)
    assert batch.outcomes.comment_items(ITEM) == []
