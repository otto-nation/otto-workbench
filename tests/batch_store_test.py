import json
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


def test_state_file_saved_before_explicit_existed_still_loads():
    import json
    run = _run()
    batch.store.save(run)
    path = batch.store.run_dir(RID) / "state.json"
    data = json.loads(path.read_text())
    for step in data["items"][0]["steps"]:
        del step["explicit"]
    path.write_text(json.dumps(data))
    assert batch.store.load(RID).items[0].steps[1].explicit is False


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
    assert batch.store.has_requests(RID) is True
    assert [r["decision"] for r in batch.store.take_requests(RID)] == ["d1", "d2"]
    assert batch.store.take_requests(RID) == []
    assert batch.store.has_requests(RID) is False


def test_take_requests_rejects_malformed_files_without_raising():
    batch.store.save(_run())
    reqs = batch.store.run_dir(RID) / "requests"
    reqs.mkdir(parents=True, exist_ok=True)
    (reqs / "20261001T000000000000-aa.json").write_text("not json")
    (reqs / "20261001T000000000001-bb.json").write_text("[]")
    batch.store.write_request(RID, {"decision": "d1", "action": "retry"})
    taken = batch.store.take_requests(RID)
    errors = [r for r in taken if r.get("error")]
    goods = [r for r in taken if not r.get("error")]
    assert len(errors) == 2
    assert [g["decision"] for g in goods] == ["d1"]
    rejected = sorted((reqs / "rejected").glob("*.json"))
    assert len(rejected) == 2
    assert not list(reqs.glob("*.json"))


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


def test_a_run_saved_before_remote_sha_existed_leases_on_its_head():
    run = batch.model.Run(id="20261003T000000-aaaa", started_at="t", steps=[], pool=1,
                          auto_publish=[], items=[batch.model.Item(
                              key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b",
                              head_sha="h1")])
    batch.store.save(run)
    path = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    raw = json.loads(path.read_text())
    del raw["items"][0]["remote_sha"]
    path.write_text(json.dumps(raw))
    assert batch.store.load(run.id).items[0].remote_sha == "h1"
