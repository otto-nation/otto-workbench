"""Contract for `pr batch resolve`: inline vs queued, and the JSON it prints.

Resolve always writes the inbox first (`batch.store.write_request`), then
tries to open the run as an APPLIER. A live DRIVER or APPLIER that drains
refuses the lock; resolve exits 0 with `queued: true` and leaves the request
for that holder (or the next session) to apply. Otherwise this process
applies it and prints `applied`. There is no lost-update on `state.json`:
only a Session holding the run lock may commit.

The two JSON shapes are the bug. A consumer that reads `applied` KeyErrors
on the queued path — run 20261016T180136-9071, decision a2f4398b, while a
resume DRIVER held the lock. Three queued publishes that later needed
`pr batch resume` are run 20261016T152509-3ac7: exit 0 did not mean applied.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.inbox  # noqa: E402
import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.requests import Decide  # noqa: E402
from batch_session_support import holding, open_session, seed  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _saved_run(n=1):
    """A WAITING run of *n* items, each awaiting `accept` on decision `d<i>`."""
    rows = [PlanRow("o/r", "/r", i, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")}) for i in range(1, n + 1)]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1, auto_publish=[])
    for i, item in enumerate(run.items, 1):
        item.worktree = "/wt"
        item.step(batch.model.Step.REVIEW).status = batch.model.StepStatus.NEEDS_DECISION
        run.decisions.append(batch.model.Decision(
            id=f"d{i}", item=item.key, step="review",
            kind=batch.model.DecisionKind.OPEN_FINDINGS))
        item.status = batch.model.ItemStatus.AWAITING_DECISION
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


def _resolve_json(capsys, run_id, decision_id, action="accept"):
    code = _main(["batch", "resolve", run_id, decision_id, "--action", action])
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def test_inline_resolve_json_always_has_applied(capsys):
    run = _saved_run()
    code, out, _ = _resolve_json(capsys, run.id, "d1")
    assert code == 0
    assert out["applied"] is True
    assert out["decision"] == "d1"
    assert out["open_decisions"] == 0
    assert batch.store.load(run.id).decision("d1").resolution == "accept"
    assert batch.store.pending_requests(run.id) == 0


def test_queued_resolve_json_always_has_applied_false(capsys):
    """Queued is success of the write, not of the apply — `applied` must say so.

    Before the fix, this path printed only `queued` and `decision`, which is
    what left a2f4398b looking applied to anything that expected the inline
    object.
    """
    run = _saved_run()
    with holding(run.id, "driver", "pr batch resume"):
        code, out, _ = _resolve_json(capsys, run.id, "d1")
    assert code == 0
    assert out["queued"] is True
    assert out["applied"] is False
    assert out["decision"] == "d1"
    saved = batch.store.load(run.id)
    assert saved.decision("d1").open
    assert batch.store.pending_requests(run.id) == 1


def test_three_queued_resolves_are_not_lost_and_need_a_later_drain(capsys):
    """Three exit-0 resolves against a live driver leave all three open.

    Run 20261016T152509-3ac7: the publishes only moved once a later session
    drained the inbox. Nothing in store overwrites an undrained request.
    """
    run = _saved_run(3)
    with holding(run.id, "driver", "pr batch resume"):
        for decision in ("d1", "d2", "d3"):
            code, out, _ = _resolve_json(capsys, run.id, decision)
            assert code == 0
            assert out["queued"] is True
    saved = batch.store.load(run.id)
    assert [d.id for d in saved.open_decisions()] == ["d1", "d2", "d3"]
    assert batch.store.pending_requests(run.id) == 3

    with open_session(run.id):
        pass
    drained = batch.store.load(run.id)
    assert drained.open_decisions() == []
    assert {d.resolution for d in drained.decisions} == {"accept"}
    assert batch.store.pending_requests(run.id) == 0


def test_resolve_does_not_claim_applied_when_the_drain_did_not_see_the_request(
        monkeypatch, capsys):
    """`own is None` and `queued is False` is not a successful apply.

    `_cmd_resolve` treated a missing outcome as `applied: true` whenever
    `rejected/` had no error for that name. A drain that never reported the
    request then printed success over an still-open decision.
    """
    run = _saved_run()
    name = batch.store.write_request(run.id, Decide("d1", "accept"))

    def missed(run_id, request, ctx, *, command):
        return batch.inbox.Delivery(
            name=name, queued=False, own=None, others=(), holder=None)

    monkeypatch.setattr(batch.inbox, "deliver", missed)
    code, out, _ = _resolve_json(capsys, run.id, "d1")
    assert out["applied"] is False
    assert batch.store.load(run.id).decision("d1").open
    assert code != 0
