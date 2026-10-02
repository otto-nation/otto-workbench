import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.store  # noqa: E402

RID = "20261001T120000-abcd"


def _run(run_id=RID):
    item = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                  steps=[batch.model.StepRecord(batch.model.Step.REBASE), batch.model.StepRecord(batch.model.Step.REVIEW)])
    return batch.model.Run(id=run_id, started_at="t", steps=[batch.model.Step.REBASE, batch.model.Step.REVIEW], pool=2,
                 auto_publish=[batch.model.Step.REBASE], items=[item],
                 decisions=[batch.model.Decision(id="d1", item="o/r#1", step="rebase",
                                       kind=batch.model.DecisionKind.REBASE_CONFLICT,
                                       payload={"files": ["a.py"]})])


def test_run_round_trips_through_the_state_file():
    run = _run()
    batch.store.save(run)
    back = batch.store.load(RID)
    assert back == run
    assert back.items[0].steps[1].step is batch.model.Step.REVIEW
    assert back.decisions[0].kind is batch.model.DecisionKind.REBASE_CONFLICT


def test_state_file_lives_under_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    batch.store.save(_run())
    assert (tmp_path / "batch" / RID / "state.json").is_file()


def test_load_of_unknown_run_raises():
    with pytest.raises(batch.store.RunNotFound):
        batch.store.load("nope")


def test_new_run_id_sorts_by_time():
    a = batch.store.new_run_id(datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))
    b = batch.store.new_run_id(datetime(2026, 10, 1, 12, 0, 1, tzinfo=timezone.utc))
    assert a.startswith("20261001T120000-") and a < b


def test_latest_run_id_is_the_newest():
    batch.store.save(_run("20261001T120000-aaaa"))
    batch.store.save(_run("20261002T120000-bbbb"))
    assert batch.store.latest_run_id() == "20261002T120000-bbbb"


def test_requests_are_taken_in_order_and_once():
    batch.store.save(_run())
    batch.store.write_request(RID, {"decision": "d1", "action": "retry"})
    batch.store.write_request(RID, {"decision": "d2", "action": "abort"})
    assert [r["decision"] for r in batch.store.take_requests(RID)] == ["d1", "d2"]
    assert batch.store.take_requests(RID) == []


def test_cancel_flag_records_kill():
    batch.store.save(_run())
    assert batch.store.cancel_requested(RID).requested is False
    batch.store.request_cancel(RID, kill=True)
    c = batch.store.cancel_requested(RID)
    assert c.requested and c.kill


def test_clear_cancel_removes_the_file_and_is_idempotent():
    batch.store.save(_run())
    batch.store.request_cancel(RID, kill=True)
    batch.store.clear_cancel(RID)
    assert batch.store.cancel_requested(RID).requested is False
    batch.store.clear_cancel(RID)


def test_open_decisions_excludes_resolved():
    run = _run()
    run.decisions.append(batch.model.Decision(id="d2", item="o/r#1", step="review",
                                    kind=batch.model.DecisionKind.OPEN_FINDINGS, resolution="accept"))
    assert [d.id for d in run.open_decisions()] == ["d1"]
