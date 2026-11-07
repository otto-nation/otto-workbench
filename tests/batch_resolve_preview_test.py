import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.publish  # noqa: E402
import batch.resolve  # noqa: E402
import batch.requests  # noqa: E402
import core.serde  # noqa: E402
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


def _no_carry(item, tree):
    return batch.publish.Carried(commits=[], diff="", truncated=False, remote_ahead=0)


def test_preview_plans_what_publish_would_push_and_changes_nothing():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    before = core.serde.to_dict(run)

    seen = batch.resolve.preview(run, batch.requests.Decide("d1", "publish"), pr_bin="pr",
                                 tree=_ff, closeout=lambda d, b: CloseoutDebt(),
                                 carried=_no_carry)

    assert seen.plan.ok and seen.plan.pushes
    assert seen.plan.commands == [["pr", "push", "--expect", "s", "--repo-dir", "/wt"]]
    assert core.serde.to_dict(run) == before


def test_preview_and_publish_plan_the_same_commands():
    """The point of a dry run on the resolve path: it is the publish's own plan."""
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    run.items[0].step(batch.model.Step.REVIEW).drafted = True
    seen = batch.resolve.preview(run, batch.requests.Decide("d1", "publish"), pr_bin="pr",
                                 tree=_ff, closeout=lambda d, b: CloseoutDebt(),
                                 carried=_no_carry)
    rec = Recorder()
    batch.resolve.apply(run, batch.requests.Decide("d1", "publish"), pr_bin="pr", runner=rec,
                        tree=_ff, closeout=lambda d, b: CloseoutDebt())
    assert rec.calls == seen.plan.commands


def test_preview_reports_a_refusal_instead_of_raising():
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    seen = batch.resolve.preview(run, batch.requests.Decide("d1", "publish"), pr_bin="pr",
                                 tree=_stale, closeout=lambda d, b: CloseoutDebt(),
                                 carried=_no_carry)
    assert seen.plan.refusal is batch.publish.Refusal.NOT_INCORPORATED_REMOTE
    assert seen.plan.commits == ["c1 elsewhere"]


def test_preview_of_force_publish_pushes_past_the_refused_commits_without_recording_them():
    run = _run(_d(batch.model.DecisionKind.FAILED, "publish",
                  {"reason": "not_incorporated_remote", "commits": ["c1 elsewhere"]}))
    seen = batch.resolve.preview(run, batch.requests.Decide("d1", "force-publish"), pr_bin="pr",
                                 tree=_stale, closeout=lambda d, b: CloseoutDebt(),
                                 carried=_no_carry)
    assert seen.plan.commands == [["pr", "rebase", "--push-only", "--expect", "s",
                                   "--repo-dir", "/wt"]]
    assert run.items[0].forced == []
    assert run.decisions[0].open


def test_preview_refuses_an_action_other_than_publish():
    run = _run(_d(batch.model.DecisionKind.REBASE_CONFLICT, "rebase"))
    with pytest.raises(batch.resolve.ResolveError, match="publish"):
        batch.resolve.preview(run, batch.requests.Decide("d1", "retry"), pr_bin="pr")


def test_preview_validates_like_resolve():
    run = _run(_d(batch.model.DecisionKind.FAILED, "publish", {"reason": "error"}))
    with pytest.raises(batch.resolve.ResolveError, match="not_incorporated_remote"):
        batch.resolve.preview(run, batch.requests.Decide("d1", "force-publish"), pr_bin="pr",
                              tree=_stale, closeout=lambda d, b: CloseoutDebt(),
                              carried=_no_carry)


def test_preview_reads_the_tree_without_moving_the_lease_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(batch.publish, "peek_tree", lambda item: calls.append("peek") or _ff(item))
    monkeypatch.setattr(batch.publish, "read_tree", lambda item: calls.append("read") or _ff(item))
    run = _run(_d(batch.model.DecisionKind.PUBLISH, "publish"))
    batch.resolve.preview(run, batch.requests.Decide("d1", "publish"), pr_bin="pr",
                          closeout=lambda d, b: CloseoutDebt(), carried=_no_carry)
    assert calls == ["peek"]
