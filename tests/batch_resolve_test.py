import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model as m  # noqa: E402
import batch.resolve as res  # noqa: E402


def _run(*decisions):
    item = m.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
                  worktree="/wt", status=m.ItemStatus.AWAITING_DECISION,
                  steps=[m.StepRecord(s) for s in m.STEP_ORDER])
    return m.Run(id="r1", started_at="t", steps=list(m.STEP_ORDER), pool=1, auto_publish=[],
                 items=[item], decisions=list(decisions))


def _d(kind, step, payload=None, id="d1"):
    return m.Decision(id=id, item="o/r#1", step=step, kind=kind, payload=payload or {})


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv):
        self.calls.append(argv)
        return self.code


def test_settle_dismissed_requires_reason():
    run = _run(_d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "PRRT_1", "replyable": True}))
    with pytest.raises(res.ResolveError, match="reason"):
        res.apply(run, res.Request("d1", "settle-dismissed"), pr_bin="pr", runner=Recorder())


def test_settle_dismissed_runs_the_settle_and_closes_the_comments_step():
    run = _run(_d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "PRRT_1", "replyable": True}))
    run.items[0].step(m.Step.COMMENTS).status = m.StepStatus.NEEDS_DECISION
    rec = Recorder()
    res.apply(run, res.Request("d1", "settle-dismissed", reason="n/a"), pr_bin="pr", runner=rec)
    assert rec.calls == [["pr", "comments", "--settle", "PRRT_1", "--as", "dismissed",
                          "--reason", "n/a", "--repo-dir", "/wt"]]
    assert run.decision("d1").resolution == "settle-dismissed"
    assert run.items[0].step(m.Step.COMMENTS).status is m.StepStatus.DONE
    assert run.items[0].status is m.ItemStatus.QUEUED


def test_comments_step_stays_open_while_other_items_are_owed():
    run = _run(_d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "A", "replyable": True}, "d1"),
               _d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "B", "replyable": True}, "d2"))
    run.items[0].step(m.Step.COMMENTS).status = m.StepStatus.NEEDS_DECISION
    res.apply(run, res.Request("d1", "track"), pr_bin="pr", runner=Recorder())
    assert run.items[0].track == ["A"]
    assert run.items[0].step(m.Step.COMMENTS).status is m.StepStatus.NEEDS_DECISION
    assert run.items[0].status is m.ItemStatus.AWAITING_DECISION


def test_failed_settle_keeps_the_decision_open():
    run = _run(_d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "A", "replyable": True}))
    with pytest.raises(res.ResolveError, match="failed"):
        res.apply(run, res.Request("d1", "settle-fixed"), pr_bin="pr", runner=Recorder(code=1))
    assert run.decision("d1").open


def test_reply_refused_for_synthetic_ids():
    run = _run(_d(m.DecisionKind.COMMENT_ITEM, "comments", {"id": "ic-1-0", "replyable": False}))
    with pytest.raises(res.ResolveError, match="reply"):
        res.apply(run, res.Request("d1", "reply", body_file="/tmp/b"), pr_bin="pr",
                  runner=Recorder())


def test_unknown_action_for_kind_is_refused_and_lists_valid_ones():
    run = _run(_d(m.DecisionKind.OPEN_FINDINGS, "review"))
    with pytest.raises(res.ResolveError, match="accept"):
        res.apply(run, res.Request("d1", "abort"), pr_bin="pr", runner=Recorder())


def test_resolved_decision_cannot_be_resolved_twice():
    run = _run(_d(m.DecisionKind.OPEN_FINDINGS, "review"))
    res.apply(run, res.Request("d1", "accept"), pr_bin="pr", runner=Recorder())
    with pytest.raises(res.ResolveError, match="already"):
        res.apply(run, res.Request("d1", "accept"), pr_bin="pr", runner=Recorder())


def test_force_needs_an_override():
    run = _run(_d(m.DecisionKind.REBASE_REFUSED, "rebase", {"status": "unrelated_history",
                                                            "override": ""}))
    with pytest.raises(res.ResolveError, match="override"):
        res.apply(run, res.Request("d1", "force"), pr_bin="pr", runner=Recorder())


def test_force_runs_a_forced_draft_rebase_and_marks_it_drafted():
    run = _run(_d(m.DecisionKind.REBASE_REFUSED, "rebase", {"override": "--force"}))
    rec = Recorder()
    res.apply(run, res.Request("d1", "force"), pr_bin="pr", runner=rec)
    assert rec.calls == [["pr", "rebase", "--fix", "--force", "--no-push", "--repo-dir", "/wt"]]
    rb = run.items[0].step(m.Step.REBASE)
    assert rb.status is m.StepStatus.DONE and rb.drafted


def test_retry_resets_the_step():
    run = _run(_d(m.DecisionKind.FAILED, "review", {"reason": "busy"}))
    run.items[0].step(m.Step.REVIEW).status = m.StepStatus.FAILED
    res.apply(run, res.Request("d1", "retry"), pr_bin="pr", runner=Recorder())
    assert run.items[0].step(m.Step.REVIEW).status is m.StepStatus.PENDING


def test_drop_pr_drops_the_item():
    run = _run(_d(m.DecisionKind.DIRTY_WORKTREE, "worktree"))
    res.apply(run, res.Request("d1", "drop-pr"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is m.ItemStatus.DROPPED


def test_publish_commands_order_and_tracking():
    it = _run().items[0]
    it.step(m.Step.REBASE).drafted = True
    it.step(m.Step.COMMENTS).drafted = True
    it.track = ["T1", "T2"]
    assert res.publish_commands(it, "pr") == [
        ["pr", "rebase", "--push-only", "--repo-dir", "/wt"],
        ["pr", "comments", "--finish", "--post", "--track", "T1", "--track", "T2",
         "--repo-dir", "/wt"],
    ]


def test_publish_after_review_only_pushes_plainly():
    it = _run().items[0]
    it.step(m.Step.REVIEW).drafted = True
    assert res.publish_commands(it, "pr") == [["git-push", "/wt"]]


def test_failed_publish_leaves_a_failed_decision():
    run = _run(_d(m.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(m.Step.REVIEW).drafted = True
    res.apply(run, res.Request("d1", "publish"), pr_bin="pr", runner=Recorder(code=1))
    new = run.open_decisions()
    assert run.items[0].status is m.ItemStatus.AWAITING_DECISION
    assert [(d.kind, d.step) for d in new] == [(m.DecisionKind.FAILED, "publish")]


def test_successful_publish_finishes_the_item():
    run = _run(_d(m.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(m.Step.REVIEW).drafted = True
    res.apply(run, res.Request("d1", "publish"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is m.ItemStatus.DONE


def test_open_chat_is_left_to_the_ui():
    run = _run(_d(m.DecisionKind.OPEN_FINDINGS, "review"))
    with pytest.raises(res.ResolveError, match="UI"):
        res.apply(run, res.Request("d1", "open-chat"), pr_bin="pr", runner=Recorder())


def test_default_runner_keeps_child_stdout_off_the_event_stream(tmp_path, capfd):
    script = tmp_path / "echo_json.py"
    script.write_text("print('{\"event\": 1}')\n")
    code = res.default_runner([sys.executable, str(script)])
    assert code == 0
    captured = capfd.readouterr()
    assert captured.out == ""


def test_failed_abort_leaves_a_failed_decision_and_does_not_skip_rebase():
    run = _run(_d(m.DecisionKind.REBASE_CONFLICT, "rebase"))
    run.items[0].step(m.Step.REBASE).status = m.StepStatus.NEEDS_DECISION
    res.apply(run, res.Request("d1", "abort"), pr_bin="pr", runner=Recorder(code=1))
    new = [d for d in run.open_decisions() if d.id != "d1"]
    assert [(d.kind, d.step) for d in new] == [(m.DecisionKind.FAILED, "rebase")]
    assert run.items[0].step(m.Step.REBASE).status is m.StepStatus.NEEDS_DECISION
