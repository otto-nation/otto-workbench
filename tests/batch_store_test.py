import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model as m  # noqa: E402
import batch.store as store  # noqa: E402

RID = "20261001T120000-abcd"


def _run(run_id=RID):
    item = m.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                  steps=[m.StepRecord(m.Step.REBASE), m.StepRecord(m.Step.REVIEW)])
    return m.Run(id=run_id, started_at="t", steps=[m.Step.REBASE, m.Step.REVIEW], pool=2,
                 auto_publish=[m.Step.REBASE], items=[item],
                 decisions=[m.Decision(id="d1", item="o/r#1", step="rebase",
                                       kind=m.DecisionKind.REBASE_CONFLICT,
                                       payload={"files": ["a.py"]})])


def test_run_round_trips_through_the_state_file():
    run = _run()
    store.save(run)
    back = store.load(RID)
    assert back == run
    assert back.items[0].steps[1].step is m.Step.REVIEW
    assert back.decisions[0].kind is m.DecisionKind.REBASE_CONFLICT


def test_state_file_lives_under_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    store.save(_run())
    assert (tmp_path / "batch" / RID / "state.json").is_file()


def test_load_of_unknown_run_raises():
    with pytest.raises(store.RunNotFound):
        store.load("nope")


def test_new_run_id_sorts_by_time():
    a = store.new_run_id(datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))
    b = store.new_run_id(datetime(2026, 10, 1, 12, 0, 1, tzinfo=timezone.utc))
    assert a.startswith("20261001T120000-") and a < b


def test_latest_run_id_is_the_newest():
    store.save(_run("20261001T120000-aaaa"))
    store.save(_run("20261002T120000-bbbb"))
    assert store.latest_run_id() == "20261002T120000-bbbb"


def test_requests_are_taken_in_order_and_once():
    store.save(_run())
    store.write_request(RID, {"decision": "d1", "action": "retry"})
    store.write_request(RID, {"decision": "d2", "action": "abort"})
    assert [r["decision"] for r in store.take_requests(RID)] == ["d1", "d2"]
    assert store.take_requests(RID) == []


def test_cancel_flag_records_kill():
    store.save(_run())
    assert store.cancel_requested(RID).requested is False
    store.request_cancel(RID, kill=True)
    c = store.cancel_requested(RID)
    assert c.requested and c.kill


def test_open_decisions_excludes_resolved():
    run = _run()
    run.decisions.append(m.Decision(id="d2", item="o/r#1", step="review",
                                    kind=m.DecisionKind.OPEN_FINDINGS, resolution="accept"))
    assert [d.id for d in run.open_decisions()] == ["d1"]
