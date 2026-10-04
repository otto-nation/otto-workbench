import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import pr.fix  # noqa: E402

ITEM = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
              worktree="/wt")

# Minimal real review document (grammar from tests/test_review_post.py): one
# checked finding and one open finding. OPEN_TITLE is the body after the em dash.
OPEN_TITLE = "genuinely open"
DECLINED_TITLE = "not worth it *(declined — tradeoff)*"
REVIEW_WITH_ONE_OPEN_ONE_CHECKED = (
    "<!-- head_sha: aaa1111bbb2222 -->\n"
    "## Summary\nOk\n\n"
    "## Should fix\n"
    "- [x] **[S1]** **`file.go:4`** — already fixed by the fix pass\n"
    "- [ ] **[S2]** **`file.go:5`** — genuinely open\n"
    "- [ ] **[S3]** **`file.go:6`** — not worth it *(declined — tradeoff)*\n"
)


def test_rebase_clean_is_done():
    assert batch.outcomes.classify(batch.model.Step.REBASE, 0, "{}", item=ITEM, log_tail=[]).status is batch.model.StepStatus.DONE


def test_rebase_null_pre_rebase_head_is_empty():
    r = batch.outcomes.classify(
        batch.model.Step.REBASE, 0, '{"pre_rebase_head": null}', item=ITEM, log_tail=[])
    assert r.pre_rebase_head == ""


def test_rebase_conflicts_carry_the_conflict_report():
    report = {"status": "conflicts", "files": ["a.py"], "rebase_head": "abc",
              "rebase_head_subject": "s", "remaining_commits": 2}
    r = batch.outcomes.classify(batch.model.Step.REBASE, 3, json.dumps(report), item=ITEM, log_tail=[])
    assert r.status is batch.model.StepStatus.NEEDS_DECISION
    assert r.decisions == [batch.outcomes.DecisionDraft(batch.model.DecisionKind.REBASE_CONFLICT, report)]


def test_rebase_refusal_carries_status_and_override():
    report = {"status": "already_landed", "override": "--force", "remedy": "r", "detail": "d"}
    r = batch.outcomes.classify(batch.model.Step.REBASE, 4, json.dumps(report), item=ITEM, log_tail=[])
    assert r.decisions[0].kind is batch.model.DecisionKind.REBASE_REFUSED
    assert r.decisions[0].payload["override"] == "--force"


def test_rebase_conflicts_without_json_carry_log_tail():
    r = batch.outcomes.classify(batch.model.Step.REBASE, 3, "not json", item=ITEM, log_tail=["x"])
    assert r.status is batch.model.StepStatus.NEEDS_DECISION
    assert r.decisions[0].payload == {"log_tail": ["x"]}


def test_comments_owed_items_each_become_a_decision(monkeypatch):
    owed = [{"id": "PRRT_1", "outcome": "needs_human", "summary": "s", "reason": "r",
             "file": "a.py", "line": 3, "replyable": True},
            {"id": "ic-9-0", "outcome": "deferred", "summary": "s2", "reason": "",
             "file": "", "line": 0, "replyable": False}]
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: owed)
    r = batch.outcomes.classify(batch.model.Step.COMMENTS, 0, "{}", item=ITEM, log_tail=[])
    assert r.status is batch.model.StepStatus.NEEDS_DECISION
    assert [(d.kind, d.payload["id"]) for d in r.decisions] == [
        (batch.model.DecisionKind.COMMENT_ITEM, "PRRT_1"), (batch.model.DecisionKind.COMMENT_ITEM, "ic-9-0")]


def test_comments_with_nothing_owed_is_done(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: [])
    assert batch.outcomes.classify(batch.model.Step.COMMENTS, 0, "{}", item=ITEM, log_tail=[]).status is batch.model.StepStatus.DONE


def test_review_open_findings_become_one_decision(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [{"severity": "must-fix", "title": "x"}])
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 0, "", item=ITEM, log_tail=[])
    assert r.decisions == [batch.outcomes.DecisionDraft(batch.model.DecisionKind.OPEN_FINDINGS,
                                             {"findings": [{"severity": "must-fix", "title": "x"}]})]


def test_lock_busy_is_a_failed_decision_with_reason_busy():
    r = batch.outcomes.classify(batch.model.Step.COMMENTS, 1, "", item=ITEM,
                     log_tail=[f"pr: {batch.outcomes.LOCK_BUSY_MARKER} by pid 4"])
    assert r.status is batch.model.StepStatus.FAILED
    assert r.decisions[0].payload["reason"] == "busy"


def test_other_failures_carry_exit_code_and_log_tail():
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 1, "", item=ITEM, log_tail=["a", "b"])
    assert r.status is batch.model.StepStatus.FAILED
    assert r.decisions[0].payload == {"reason": "error", "exit_code": 1, "log_tail": ["a", "b"]}


def test_comment_items_reads_only_owed_outcomes_and_marks_synthetic_ids(monkeypatch):
    items = [pr.fix.ItemOutcome(id="PRRT_1", outcome=pr.fix.FixOutcome.NEEDS_HUMAN,
                                summary="s", file="a.py", line=2),
             pr.fix.ItemOutcome(id="PRRT_2", outcome=pr.fix.FixOutcome.FIXED),
             pr.fix.ItemOutcome(id="ic-5-0", outcome=pr.fix.FixOutcome.DEFERRED)]

    state = SimpleNamespace(fix=SimpleNamespace(fix=SimpleNamespace(items=items)))
    monkeypatch.setattr(batch.outcomes, "_load_pr_state", lambda item: state)
    assert [(i["id"], i["replyable"]) for i in batch.outcomes.comment_items(ITEM)] == [
        ("PRRT_1", True), ("ic-5-0", False)]


def test_open_findings_reads_unchecked_findings(tmp_path, monkeypatch):
    f = tmp_path / "review.md"
    f.write_text(REVIEW_WITH_ONE_OPEN_ONE_CHECKED)
    monkeypatch.setattr(batch.outcomes.review.paths, "self_review_file_path", lambda repo, branch: f)
    assert batch.outcomes.open_findings(ITEM) == [
        {"severity": "should-fix", "title": OPEN_TITLE, "declined": False},
        {"severity": "should-fix", "title": DECLINED_TITLE, "declined": True},
    ]
