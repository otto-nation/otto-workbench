import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.resolve  # noqa: E402
import cli.pr_batch  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def _run(*decisions):
    item = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
                  worktree="/wt", status=batch.model.ItemStatus.AWAITING_DECISION,
                  steps=[batch.model.StepRecord(s) for s in batch.model.STEP_ORDER])
    return batch.model.Run(id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1, auto_publish=[],
                 items=[item], decisions=list(decisions))


def _d(kind, step, payload=None, id="d1"):
    return batch.model.Decision(id=id, item="o/r#1", step=step, kind=kind, payload=payload or {})


def _ff(item):
    return TreeState(local="new", remote=item.remote_sha,
                     divergence=RefDivergence(ahead=1, behind=0, comparable=True))


def _stale(item):
    return TreeState(local="new", remote=item.remote_sha,
                     divergence=RefDivergence(ahead=1, behind=1, comparable=True),
                     unincorporated=("c1 elsewhere",))


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv, log_path=None):
        self.calls.append(argv)
        return self.code


def test_settle_dismissed_requires_reason():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "PRRT_1", "replyable": True}))
    with pytest.raises(batch.resolve.ResolveError, match="reason"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "settle-dismissed"), pr_bin="pr", runner=Recorder())


def test_settle_dismissed_runs_the_settle_and_closes_the_comments_step():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "PRRT_1", "replyable": True}))
    run.items[0].step(batch.model.Step.COMMENTS).status = batch.model.StepStatus.NEEDS_DECISION
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "settle-dismissed", reason="n/a"), pr_bin="pr", runner=rec)
    assert rec.calls == [["pr", "comments", "--settle", "PRRT_1", "--as", "dismissed",
                          "--reason", "n/a", "--repo-dir", "/wt"]]
    assert run.decision("d1").resolution == "settle-dismissed"
    assert run.items[0].step(batch.model.Step.COMMENTS).status is batch.model.StepStatus.DONE
    assert run.items[0].status is batch.model.ItemStatus.QUEUED


def test_comments_step_stays_open_while_other_items_are_owed():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "A", "replyable": True}, "d1"),
               _d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "B", "replyable": True}, "d2"))
    run.items[0].step(batch.model.Step.COMMENTS).status = batch.model.StepStatus.NEEDS_DECISION
    batch.resolve.apply(run, batch.resolve.Request("d1", "track"), pr_bin="pr", runner=Recorder())
    assert run.items[0].track == ["A"]
    assert run.items[0].step(batch.model.Step.COMMENTS).status is batch.model.StepStatus.NEEDS_DECISION
    assert run.items[0].status is batch.model.ItemStatus.AWAITING_DECISION


def test_failed_settle_keeps_the_decision_open():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "A", "replyable": True}))
    with pytest.raises(batch.resolve.ResolveError, match="failed"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "settle-fixed"), pr_bin="pr", runner=Recorder(code=1))
    assert run.decision("d1").open


def test_reply_refused_for_synthetic_ids():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments", {"id": "ic-1-0", "replyable": False}))
    with pytest.raises(batch.resolve.ResolveError, match="reply"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "reply", body_file="/tmp/b"), pr_bin="pr",
                  runner=Recorder())


def test_unknown_action_for_kind_is_refused_and_lists_valid_ones():
    run = _run(_d(batch.model.DecisionKind.OPEN_FINDINGS, "review"))
    with pytest.raises(batch.resolve.ResolveError, match="accept"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "abort"), pr_bin="pr", runner=Recorder())


def test_resolved_decision_cannot_be_resolved_twice():
    run = _run(_d(batch.model.DecisionKind.OPEN_FINDINGS, "review"))
    batch.resolve.apply(run, batch.resolve.Request("d1", "accept"), pr_bin="pr", runner=Recorder())
    with pytest.raises(batch.resolve.ResolveError, match="already"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "accept"), pr_bin="pr", runner=Recorder())


def test_force_needs_an_override():
    run = _run(_d(batch.model.DecisionKind.REBASE_REFUSED, "rebase", {"status": "unrelated_history",
                                                            "override": ""}))
    with pytest.raises(batch.resolve.ResolveError, match="override"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "force"), pr_bin="pr", runner=Recorder())


def test_force_runs_a_forced_draft_rebase_and_marks_it_drafted(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "recorded_pre_rebase_head", lambda item: "")
    run = _run(_d(batch.model.DecisionKind.REBASE_REFUSED, "rebase", {"override": "--force"}))
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "force"), pr_bin="pr", runner=rec)
    assert rec.calls == [["pr", "rebase", "--fix", "--force", "--no-push", "--repo-dir", "/wt"]]
    rb = run.items[0].step(batch.model.Step.REBASE)
    assert rb.status is batch.model.StepStatus.DONE and rb.drafted


def test_retry_resets_the_step():
    run = _run(_d(batch.model.DecisionKind.FAILED, "review", {"reason": "lock_busy"}))
    run.items[0].step(batch.model.Step.REVIEW).status = batch.model.StepStatus.FAILED
    batch.resolve.apply(run, batch.resolve.Request("d1", "retry"), pr_bin="pr", runner=Recorder())
    assert run.items[0].step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_drop_pr_drops_the_item():
    run = _run(_d(batch.model.DecisionKind.DIRTY_WORKTREE, "worktree"))
    batch.resolve.apply(run, batch.resolve.Request("d1", "drop-pr"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is batch.model.ItemStatus.DROPPED


def test_drop_pr_on_rebase_refused_drops_the_item():
    run = _run(_d(batch.model.DecisionKind.REBASE_REFUSED, "rebase", {"override": "--force"}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "drop-pr"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is batch.model.ItemStatus.DROPPED


def test_skip_pr_is_not_an_action():
    assert all("skip-pr" not in actions for actions in batch.resolve.ACTIONS.values())


def test_skip_step_on_a_worktree_failure_drops_the_item():
    run = _run(_d(batch.model.DecisionKind.FAILED, "worktree", {"reason": "error"}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "skip-step"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is batch.model.ItemStatus.DROPPED


def test_skip_step_on_a_publish_failure_drops_the_item():
    run = _run(_d(batch.model.DecisionKind.FAILED, "publish", {"reason": "error"}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "skip-step"), pr_bin="pr", runner=Recorder())
    assert run.items[0].status is batch.model.ItemStatus.DROPPED


def test_failed_publish_leaves_a_failed_decision():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=Recorder(code=1),
                        tree=_ff)
    new = run.open_decisions()
    assert run.items[0].status is batch.model.ItemStatus.AWAITING_DECISION
    assert [(d.kind, d.step) for d in new] == [(batch.model.DecisionKind.FAILED, "publish")]


def test_successful_publish_finishes_the_item():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=Recorder(),
                        tree=_ff)
    assert run.items[0].status is batch.model.ItemStatus.DONE
    assert run.items[0].remote_sha == "new"


def test_force_publish_answers_only_a_not_incorporated_remote_refusal():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"),
               _d(batch.model.DecisionKind.FAILED, "publish", {"reason": "error"}, id="d0"))
    with pytest.raises(batch.resolve.ResolveError, match="not_incorporated_remote"):
        batch.resolve.apply(run, batch.resolve.Request("d0", "force-publish"), pr_bin="pr",
                            runner=Recorder(), tree=_stale)
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=rec,
                        tree=_stale)
    refusal = run.open_decisions()[-1]
    assert rec.calls == []
    assert (refusal.kind, refusal.payload["reason"], refusal.payload["commits"]) == (
        batch.model.DecisionKind.FAILED, "not_incorporated_remote", ["c1 elsewhere"])
    batch.resolve.apply(run, batch.resolve.Request(refusal.id, "force-publish"), pr_bin="pr",
                        runner=rec, tree=_stale)
    assert rec.calls == [["pr", "rebase", "--push-only", "--expect", "s", "--repo-dir", "/wt"]]
    assert run.items[0].status is batch.model.ItemStatus.DONE


def test_force_publish_refuses_again_when_a_remote_commit_appeared_since():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=rec,
                        tree=_stale)
    refusal = run.open_decisions()[-1]

    def grown(item):
        return TreeState(local="new", remote=item.remote_sha,
                         divergence=RefDivergence(ahead=1, behind=2, comparable=True),
                         unincorporated=("c1 elsewhere", "c2 later"))

    batch.resolve.apply(run, batch.resolve.Request(refusal.id, "force-publish"), pr_bin="pr",
                        runner=rec, tree=grown)
    again = run.open_decisions()[-1]
    assert rec.calls == []
    assert again.id != refusal.id
    assert (again.payload["reason"], again.payload["commits"]) == (
        "not_incorporated_remote", ["c1 elsewhere", "c2 later"])
    assert run.items[0].status is batch.model.ItemStatus.AWAITING_DECISION


def test_a_landed_push_moves_the_lease_even_when_the_replies_fail():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    item = run.items[0]
    item.remote_sha = "s"
    item.step(batch.model.Step.COMMENTS).drafted = True
    origin = {"tip": "s"}

    def tree(it):
        return TreeState(local="new", remote=origin["tip"],
                         divergence=RefDivergence(ahead=int(origin["tip"] != "new"), behind=0,
                                                  comparable=True))

    def runner(argv, log_path=None):
        if argv[0] == batch.resolve.GIT_PUSH:
            origin["tip"] = "new"
            return 0
        return 1

    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=runner,
                        tree=tree)
    failed = run.open_decisions()[-1]
    assert (item.remote_sha, item.published_sha) == ("new", "new")
    assert failed.payload["reason"] == "error"
    # A retry re-queues the item; the scheduler then asks for the publish again.
    batch.resolve.apply(run, batch.resolve.Request(failed.id, "retry"), pr_bin="pr",
                        runner=Recorder())
    run.decisions.append(_d(batch.model.DecisionKind.PUBLISH, "publish", id="d2"))
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d2", "publish"), pr_bin="pr",
                        runner=rec, tree=tree)
    assert rec.calls == [["pr", "comments", "--finish", "--post", "--repo-dir", "/wt"]]
    assert item.status is batch.model.ItemStatus.DONE


def test_open_chat_is_left_to_the_ui():
    run = _run(_d(batch.model.DecisionKind.OPEN_FINDINGS, "review"))
    with pytest.raises(batch.resolve.ResolveError, match="UI"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "open-chat"), pr_bin="pr", runner=Recorder())


def test_default_runner_keeps_child_stdout_off_the_event_stream(tmp_path, capfd):
    script = tmp_path / "echo_json.py"
    script.write_text("print('{\"event\": 1}')\n")
    code = batch.resolve.default_runner([sys.executable, str(script)])
    assert code == 0
    captured = capfd.readouterr()
    assert captured.out == ""


def test_failed_abort_leaves_a_failed_decision_and_does_not_skip_rebase():
    run = _run(_d(batch.model.DecisionKind.REBASE_CONFLICT, "rebase"))
    run.items[0].step(batch.model.Step.REBASE).status = batch.model.StepStatus.NEEDS_DECISION
    batch.resolve.apply(run, batch.resolve.Request("d1", "abort"), pr_bin="pr", runner=Recorder(code=1))
    new = [d for d in run.open_decisions() if d.id != "d1"]
    assert [(d.kind, d.step) for d in new] == [(batch.model.DecisionKind.FAILED, "rebase")]
    assert run.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.NEEDS_DECISION
    # apply() resolves the triggering decision even though the command failed;
    # the new FAILED decision above is where the operator acts next.
    assert run.decision("d1").resolution == "abort" and not run.decision("d1").open


from types import SimpleNamespace  # noqa: E402

import batch.outcomes  # noqa: E402
from conftest import commit_all, git_out, init_repo  # noqa: E402


def test_accept_on_a_ci_step_review_finishes_ci_not_review():
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "ci", {"evidence": []}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "accept"), pr_bin="pr", runner=Recorder())
    it = run.items[0]
    assert it.step(batch.model.Step.CI).status is batch.model.StepStatus.DONE
    assert it.step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_skip_step_on_a_step_review_skips_that_step():
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "comments", {"evidence": []}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "skip-step"), pr_bin="pr",
                        runner=Recorder())
    assert run.items[0].step(batch.model.Step.COMMENTS).status is batch.model.StepStatus.SKIPPED


def test_undo_resets_to_the_pre_rebase_head_and_undrafts_the_rebase():
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "rebase", {"evidence": []}))
    it = run.items[0]
    it.pre_rebase_head = "p0"
    it.step(batch.model.Step.REBASE).drafted = True
    rec = Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "undo"), pr_bin="pr", runner=rec)
    assert rec.calls == [["git", "-C", "/wt", "reset", "--hard", "p0"]]
    rb = it.step(batch.model.Step.REBASE)
    assert rb.status is batch.model.StepStatus.SKIPPED and rb.drafted is False
    assert it.pre_rebase_head == ""


def test_undo_is_refused_off_a_rebase_or_without_a_recorded_tip():
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "review", {"evidence": []}))
    run.items[0].pre_rebase_head = "p0"
    with pytest.raises(batch.resolve.ResolveError, match="undo"):
        batch.resolve.apply(run, batch.resolve.Request("d1", "undo"), pr_bin="pr",
                            runner=Recorder())
    bare = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "rebase", {"evidence": []}))
    with pytest.raises(batch.resolve.ResolveError, match="undo"):
        batch.resolve.apply(bare, batch.resolve.Request("d1", "undo"), pr_bin="pr",
                            runner=Recorder())


def test_undo_really_restores_the_worktree(tmp_path):
    repo = init_repo(tmp_path / "wt")
    (repo / "a.txt").write_text("a\n")
    commit_all(repo, "before")
    pre = git_out(repo, "rev-parse", "HEAD").strip()
    (repo / "b.txt").write_text("b\n")
    commit_all(repo, "rebased")
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "rebase", {"evidence": []}))
    run.items[0].worktree, run.items[0].pre_rebase_head = str(repo), pre
    batch.resolve.apply(run, batch.resolve.Request("d1", "undo"), pr_bin="pr")
    assert git_out(repo, "rev-parse", "HEAD").strip() == pre


def test_a_forced_rebase_records_the_tip_it_started_from(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "_load_pr_state",
                        lambda item: SimpleNamespace(rebase=SimpleNamespace(pre_rebase_head="p9")))
    run = _run(_d(batch.model.DecisionKind.REBASE_REFUSED, "rebase", {"override": "--force"}))
    batch.resolve.apply(run, batch.resolve.Request("d1", "force"), pr_bin="pr", runner=Recorder())
    assert run.items[0].pre_rebase_head == "p9"


def test_a_failed_publish_records_its_reason_detail_and_log():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True

    def refused(argv, log_path=None):
        log_path.write_text("▸ Force-pushing...\n"
                            "✗ push refused (hook) — nothing reached the remote\n"
                            "  PRE-PUSH CHECKS FAILED\n")
        return 1

    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=refused, tree=_ff)
    failed = run.open_decisions()[-1]
    assert failed.payload["reason"] == "pre_push_rejected"
    assert failed.payload["detail"] == "push refused (hook) — nothing reached the remote"
    assert failed.payload["log"].endswith("/logs/o__r-1-publish-0.log")
    assert failed.payload["log_tail"][-1] == "  PRE-PUSH CHECKS FAILED"


def test_default_runner_writes_a_logged_commands_output_to_the_log(tmp_path, capfd):
    script = tmp_path / "talk.py"
    script.write_text("import sys\nprint('out line')\nprint('err line', file=sys.stderr)\n"
                      "sys.exit(3)\n")
    log = tmp_path / "publish.log"
    assert batch.resolve.default_runner([sys.executable, str(script)], log_path=log) == 3
    text = log.read_text()
    assert "out line" in text and "err line" in text
    assert capfd.readouterr().out == ""


def test_available_actions_drop_a_reply_the_item_cannot_take_and_open_chat():
    run = _run(_d(batch.model.DecisionKind.COMMENT_ITEM, "comments",
                  {"id": "ic-1-0", "replyable": False}))
    assert batch.resolve.available_actions(run.decision("d1"), run.items[0]) == [
        "settle-addressed", "settle-dismissed", "settle-fixed", "track"]


def test_force_publish_is_offered_only_for_a_remote_commit_refusal():
    run = _run(_d(batch.model.DecisionKind.FAILED, "publish", {"reason": "remote_moved"}),
               _d(batch.model.DecisionKind.FAILED, "publish",
                  {"reason": "not_incorporated_remote"}, id="d2"))
    item = run.items[0]
    assert batch.resolve.available_actions(run.decision("d1"), item) == [
        "drop-pr", "retry", "skip-step"]
    assert "force-publish" in batch.resolve.available_actions(run.decision("d2"), item)


def test_undo_is_offered_only_with_a_recorded_pre_rebase_head():
    run = _run(_d(batch.model.DecisionKind.STEP_REVIEW, "rebase"))
    assert "undo" not in batch.resolve.available_actions(run.decision("d1"), run.items[0])
    run.items[0].pre_rebase_head = "abc"
    assert "undo" in batch.resolve.available_actions(run.decision("d1"), run.items[0])


def test_resolve_command_spells_each_actions_inputs():
    cmd = batch.resolve.resolve_command
    assert cmd("r1", "d1", "settle-dismissed") == \
        "pr batch resolve r1 d1 --action settle-dismissed --reason <text>"
    assert cmd("r1", "d1", "reply") == "pr batch resolve r1 d1 --action reply --body-file <path>"
    assert cmd("r1", "d1", "settle-fixed") == \
        "pr batch resolve r1 d1 --action settle-fixed [--commit <sha>]"
    assert cmd("r1", "d1", "accept") == "pr batch resolve r1 d1 --action accept"


def test_every_rendered_command_parses_with_the_batch_parser():
    parser = cli.pr_batch.build_parser()
    actions = set().union(*batch.resolve.ACTIONS.values()) - {"open-chat"}
    for action in sorted(actions):
        words = batch.resolve.resolve_command("r1", "d1", action).replace("[", "").replace(
            "]", "").split()
        args = parser.parse_args(["VALUE" if w.startswith("<") else w for w in words[2:]])
        assert (args.command, args.run_id, args.decision_id, args.action) == (
            "resolve", "r1", "d1", action)


def _failing_publish(text):
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True

    def runner(argv, log_path=None):
        log_path.write_text(text)
        return 1

    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=runner, tree=_ff)
    return run.open_decisions()[-1]


def test_a_failed_publish_keeps_the_last_forty_lines_of_its_log():
    failed = _failing_publish("".join(f"line {n}\n" for n in range(50)))
    assert len(failed.payload["log_tail"]) == 40
    assert failed.payload["log_tail"][-1] == "line 49"


def test_a_failed_publish_reads_its_reason_from_the_failing_command_only():
    failed = _failing_publish("$ pr comments --post\n"
                              "✓ push refused (hook) was the subject of a reply\n"
                              "$ pr comments --settle ic-1\n"
                              "✗ could not settle the thread\n")
    assert failed.payload["reason"] == "error"
    assert "detail" not in failed.payload
    assert failed.payload["log_tail"][0] == "$ pr comments --post"


def _publish_decision():
    return _d(batch.model.DecisionKind.PUBLISH, "publish", {"drafted": [], "track": []})


def _even(item):
    return TreeState(local=item.remote_sha, remote=item.remote_sha,
                     divergence=RefDivergence(ahead=0, behind=0, comparable=True))


def test_publish_pays_a_closeout_pr_recorded_with_nothing_to_push():
    run, rec = _run(_publish_decision()), Recorder()
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr", runner=rec,
                        tree=_even, closeout=lambda d, b: CloseoutDebt(summary=True))
    assert rec.calls == [["pr", "comments", "--finish", "--post", "--repo-dir", "/wt"]]
    assert run.items[0].status is batch.model.ItemStatus.DONE


def test_the_closeout_seam_is_asked_about_the_items_checkout_and_branch():
    asked = []
    run = _run(_publish_decision())
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=Recorder(), tree=_even,
                        closeout=lambda d, b: asked.append((d, b)) or CloseoutDebt())
    assert asked == [("/r", "b")]


def test_a_push_finish_made_becomes_the_lease():
    run = _run(_publish_decision())
    item = run.items[0]
    planned = item.remote_sha
    reads = iter([_even(item),
                  TreeState(local="held", remote="held",
                            divergence=RefDivergence(ahead=0, behind=0, comparable=True))])
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=Recorder(), tree=lambda i: next(reads),
                        closeout=lambda d, b: CloseoutDebt(summary=True))
    assert planned != "held"
    assert (item.remote_sha, item.published_sha) == ("held", "held")


def test_a_remote_somebody_else_moved_during_finish_is_not_adopted():
    run = _run(_publish_decision())
    item = run.items[0]
    planned = item.remote_sha
    reads = iter([_even(item),
                  TreeState(local=planned, remote="theirs",
                            divergence=RefDivergence(ahead=0, behind=1, comparable=True))])
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=Recorder(), tree=lambda i: next(reads),
                        closeout=lambda d, b: CloseoutDebt(summary=True))
    assert (item.remote_sha, item.published_sha) == (planned, "")


def test_adopting_a_finish_push_reopens_ci_under_watch_ci():
    run = _run(_publish_decision())
    run.watch_ci = True
    item = run.items[0]
    reads = iter([_even(item),
                  TreeState(local="held", remote="held",
                            divergence=RefDivergence(0, 0, True))])
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=Recorder(), tree=lambda i: next(reads),
                        closeout=lambda d, b: CloseoutDebt(summary=True))
    ci = item.step(batch.model.Step.CI)
    assert ci.status is batch.model.StepStatus.PENDING and ci.watch is True
    assert item.status is batch.model.ItemStatus.QUEUED


def test_a_failed_finish_still_adopts_the_head_it_pushed():
    run = _run(_publish_decision())
    item = run.items[0]
    planned = item.remote_sha
    reads = iter([_even(item),
                  TreeState(local="held", remote="held",
                            divergence=RefDivergence(ahead=0, behind=0, comparable=True))])
    batch.resolve.apply(run, batch.resolve.Request("d1", "publish"), pr_bin="pr",
                        runner=Recorder(code=1), tree=lambda i: next(reads),
                        closeout=lambda d, b: CloseoutDebt(summary=True))
    assert planned != "held"
    assert (item.remote_sha, item.published_sha) == ("held", "held")
    failed = run.open_decisions()[-1]
    assert failed.kind is batch.model.DecisionKind.FAILED
    assert item.status is batch.model.ItemStatus.AWAITING_DECISION
