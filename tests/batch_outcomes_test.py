import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import commit_all, git_out, init_repo  # noqa: E402
import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.publish  # noqa: E402
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


def test_review_open_findings_become_one_step_review(monkeypatch):
    finding = {"severity": "must-fix", "title": "x"}
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [finding])
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 0, "", item=ITEM, log_tail=[])
    assert r.decisions == [batch.outcomes.DecisionDraft(
        batch.model.DecisionKind.STEP_REVIEW,
        {"evidence": [{"kind": "open_findings", "findings": [finding]}]})]


def test_lock_busy_is_a_failed_decision_with_reason_lock_busy():
    r = batch.outcomes.classify(batch.model.Step.COMMENTS, 1, "", item=ITEM,
                     log_tail=[f"pr: {batch.outcomes.LOCK_BUSY_MARKER} by pid 4"])
    assert r.status is batch.model.StepStatus.FAILED
    assert r.decisions[0].payload["reason"] == "lock_busy"


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


def _tally_stdout(**tally):
    body = {"failures": [], "fixed": [], "unfixed": [], "skipped": [], "suite_status": "",
            "commit": "", "type": "fix", **tally}
    return '{"run_id": 1}\n---\n' + json.dumps(body) + "\n"


# passes-at-base: before evidence existed classify(Step.CI) was always DONE
def test_a_clean_ci_tally_is_done():
    r = batch.outcomes.classify(batch.model.Step.CI, 0, _tally_stdout(fixed=["a"]),
                                item=ITEM, log_tail=[])
    assert r.status is batch.model.StepStatus.DONE


def test_an_all_skipped_ci_run_is_ci_unfixed():
    stdout = _tally_stdout(skipped=[{"id": "i-1", "kind": "infra"}])
    r = batch.outcomes.classify(batch.model.Step.CI, 0, stdout, item=ITEM, log_tail=[])
    assert r.status is batch.model.StepStatus.NEEDS_DECISION
    assert r.decisions[0].payload["evidence"] == [
        {"kind": "ci_unfixed", "unfixed": [], "skipped": [{"id": "i-1", "kind": "infra"}]}]


def test_a_ci_run_with_no_tally_is_not_taken_as_clean():
    r = batch.outcomes.classify(batch.model.Step.CI, 0, "", item=ITEM, log_tail=[])
    assert r.decisions[0].payload["evidence"][0]["kind"] == "ci_unfixed"


def test_a_one_sided_rebase_is_a_step_review():
    report = {"status": "completed", "files_one_sided": ["a.py"],
              "one_sided_regions": ["abc subj\n  a.py"], "pre_rebase_head": "p0"}
    r = batch.outcomes.classify(batch.model.Step.REBASE, 0, json.dumps(report),
                                item=ITEM, log_tail=[])
    assert r.decisions[0].payload["evidence"] == [
        {"kind": "one_sided", "files": ["a.py"], "regions": ["abc subj\n  a.py"]}]
    assert r.pre_rebase_head == "p0"


@pytest.mark.parametrize("status,flagged", [
    ("red", True), ("timed_out", True), ("error", True), ("green", False),
    ("not_declared", False),
])
def test_fix_checks_reads_the_trailer_from_the_steps_commits(tmp_path, status, flagged):
    repo = init_repo(tmp_path / "wt")
    (repo / "a.txt").write_text("a\n")
    # An earlier run's red verdict, before the step's lower bound, is not this step's.
    commit_all(repo, "fix: an earlier run\n\nFix-Checks: red")
    before = git_out(repo, "rev-parse", "HEAD").strip()
    (repo / "a.txt").write_text("b\n")
    commit_all(repo, f"fix: address CI failures\n\nFix-Checks: {status}")
    found = batch.outcomes.fix_checks(str(repo), before)
    assert bool(found) is flagged
    if flagged:
        assert found[0]["status"] == status


def test_a_red_trailer_on_a_review_fix_is_checks_unverified(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "fix_checks",
                        lambda wt, hb: [{"commit": "c1", "status": "red"}])
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 0, "", item=ITEM, log_tail=[],
                                head_before="h0")
    assert r.decisions[0].payload["evidence"] == [
        {"kind": "checks_unverified", "commit": "c1", "status": "red"}]


def test_comments_carry_their_items_and_a_step_review_together(monkeypatch):
    owed = [{"id": "PRRT_1", "outcome": "needs_human", "summary": "s", "reason": "",
             "file": "", "line": 0, "replyable": True}]
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: owed)
    monkeypatch.setattr(batch.outcomes, "fix_checks",
                        lambda wt, hb: [{"commit": "c1", "status": "timed_out"}])
    r = batch.outcomes.classify(batch.model.Step.COMMENTS, 0, "", item=ITEM, log_tail=[],
                                head_before="h0")
    assert [d.kind for d in r.decisions] == [batch.model.DecisionKind.COMMENT_ITEM,
                                             batch.model.DecisionKind.STEP_REVIEW]


def test_an_unreadable_commit_range_is_checks_unverified(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [])
    monkeypatch.setattr(batch.outcomes.git.client, "run",
                        lambda *a, **k: SimpleNamespace(ok=False, stdout="", stderr="boom"))
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 0, "", item=ITEM, log_tail=[],
                                head_before="h0")
    assert r.status is batch.model.StepStatus.NEEDS_DECISION
    assert r.decisions[0].payload["evidence"] == [
        {"kind": "checks_unverified", "commit": "", "status": "unreadable"}]


def test_ci_unfixed_and_a_red_trailer_are_one_step_review():
    stdout = _tally_stdout(unfixed=[{"id": "i-1"}])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(batch.outcomes, "fix_checks",
                   lambda wt, hb: [{"commit": "c1", "status": "red"}])
        r = batch.outcomes.classify(batch.model.Step.CI, 0, stdout, item=ITEM, log_tail=[],
                                    head_before="h0")
    assert [d.kind for d in r.decisions] == [batch.model.DecisionKind.STEP_REVIEW]
    assert [e["kind"] for e in r.decisions[0].payload["evidence"]] == [
        "ci_unfixed", "checks_unverified"]


REVIEW_TAIL = [
    "✗ review agent exited with code 1 and produced no review file (agent error: pi exited "
    "before the prompt could be sent — its stdin was already closed)",
    "  Session log: /state/reviews/maximum-self-b/session.jsonl",
    "✗ Review orchestration failed",
    "  Session log: /state/reviews/maximum-self-b/session.jsonl",
]
COMMENTS_TAIL = ["", "Blocking merge: no approvals yet", "", "✗ ai prompt failed"]
HOOK_TAIL = [
    "▸ Force-pushing...",
    "✗ push refused (hook) — nothing reached the remote",
    "  Uncommitted changes in generation after regeneration",
    "  PRE-PUSH CHECKS FAILED",
    "  Resume: git -C '/wt' push --force-with-lease=refs/heads/b:abc",
]
AUTH_TAIL = ["✗ push refused — the remote would not accept your credentials"]
LOCK_TAIL = ["✗ another pr run already owns this target: pr comments --fix (pid 4, started t)"]
COLOURED_TAIL = ["\x1b[1;31m✗\x1b[0m ai prompt failed for a.py (exit 1)"]
STASHED_TAIL = ["▸ Stashing uncommitted changes...", "✗ something nobody anticipated"]


@pytest.mark.parametrize("tail, reason, detail", [
    (REVIEW_TAIL, "review_orchestration_failed", REVIEW_TAIL[0].removeprefix("✗ ")),
    (COMMENTS_TAIL, "ai_prompt_failed", "ai prompt failed"),
    (HOOK_TAIL, "pre_push_rejected", "push refused (hook) — nothing reached the remote"),
    (AUTH_TAIL, "push_rejected", "push refused — the remote would not accept your credentials"),
    (LOCK_TAIL, "lock_busy", LOCK_TAIL[0].removeprefix("✗ ")),
    (COLOURED_TAIL, "ai_prompt_failed", "ai prompt failed for a.py (exit 1)"),
    (STASHED_TAIL, "error", ""),
], ids=["review", "comments", "hook", "auth", "lock", "coloured", "unrecognised"])
def test_a_failure_reason_is_read_from_what_the_step_printed(tail, reason, detail):
    found = batch.outcomes.classify_failure(tail)
    assert (found.reason, found.detail) == (reason, detail)


def test_a_review_failure_keeps_its_session_log():
    assert batch.outcomes.classify_failure(REVIEW_TAIL).session_log == \
        "/state/reviews/maximum-self-b/session.jsonl"


def test_a_failed_step_records_reason_detail_log_and_session_log():
    r = batch.outcomes.classify(batch.model.Step.REVIEW, 1, "", item=ITEM, log_tail=REVIEW_TAIL,
                                log_path="/logs/o__r-1-review-0.log")
    assert r.decisions[0].payload == {
        "reason": "review_orchestration_failed", "exit_code": 1, "log_tail": REVIEW_TAIL,
        "detail": REVIEW_TAIL[0].removeprefix("✗ "), "log": "/logs/o__r-1-review-0.log",
        "session_log": "/state/reviews/maximum-self-b/session.jsonl"}


def test_every_recorded_reason_has_a_sentence():
    recorded = ({r.value for r in batch.outcomes.FailureReason}
                | {r.value for r in batch.publish.Refusal})
    assert recorded <= set(batch.outcomes.FAILURE_WHY)
    assert batch.outcomes.why("busy") == batch.outcomes.why("lock_busy")
    assert batch.outcomes.why("no-such-reason") == batch.outcomes.why("error")


def test_the_module_docstring_names_every_failure_reason():
    """The published reason list and the enum cannot drift apart."""
    listed = set(re.findall(r"^- `([a-z_]+)`:", batch.outcomes.__doc__, re.M))
    assert listed == {r.value for r in batch.outcomes.FailureReason}
