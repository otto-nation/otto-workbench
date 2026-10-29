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
from batch_session_support import seed  # noqa: E402

RID = "20261001T120000-abcd"


def _ref(label, key=""):
    return batch.store.RepoRef(key=key, label=label)


def _run(run_id=RID, *, repo="o/r", status=None, repo_key=""):
    item = batch.model.Item(key=f"{repo}#1", repo=repo, repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                  steps=[batch.model.StepRecord(batch.model.Step.REBASE), batch.model.StepRecord(batch.model.Step.REVIEW)],
                  repo_key=repo_key)
    run = batch.model.Run(id=run_id, started_at="t", steps=[batch.model.Step.REBASE, batch.model.Step.REVIEW], pool=2,
                 auto_publish=[batch.model.Step.REBASE], items=[item],
                 decisions=[batch.model.Decision(id="d1", item=f"{repo}#1", step="rebase",
                                       kind=batch.model.DecisionKind.REBASE_CONFLICT,
                                       payload={"files": ["a.py"]})])
    if status is not None:
        run.status = status
    return run


def test_run_round_trips_through_the_state_file():
    run = _run()
    seed(run)
    back = batch.store.load(RID)
    assert back == run
    assert back.items[0].steps[1].step is batch.model.Step.REVIEW
    assert back.decisions[0].kind is batch.model.DecisionKind.REBASE_CONFLICT


def test_state_file_saved_before_explicit_existed_still_loads():
    import json
    run = _run()
    seed(run)
    path = batch.store.run_dir(RID) / "state.json"
    data = json.loads(path.read_text())
    for step in data["items"][0]["steps"]:
        del step["explicit"]
    path.write_text(json.dumps(data))
    assert batch.store.load(RID).items[0].steps[1].explicit is False


def test_state_file_saved_before_no_verify_existed_still_loads():
    run = _run()
    seed(run)
    path = batch.store.run_dir(RID) / "state.json"
    data = json.loads(path.read_text())
    del data["no_verify"]
    path.write_text(json.dumps(data))
    assert batch.store.load(RID).no_verify is False


def test_a_run_saved_before_focus_existed_loads_with_none():
    run = _run()
    seed(run)
    path = batch.store.run_dir(RID) / "state.json"
    data = json.loads(path.read_text())
    del data["focus"]
    path.write_text(json.dumps(data))
    assert batch.store.load(RID).focus == []


def test_focus_round_trips():
    run = _run()
    run.focus = ["a", "-b"]
    seed(run)
    assert batch.store.load(RID).focus == ["a", "-b"]


def test_state_file_lives_under_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    seed(_run())
    assert (tmp_path / "batch" / RID / "state.json").is_file()


def test_load_of_unknown_run_raises():
    with pytest.raises(batch.store.RunNotFound):
        batch.store.load("nope")


def test_new_run_id_sorts_by_time():
    a = batch.store.new_run_id(datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc))
    b = batch.store.new_run_id(datetime(2026, 10, 1, 12, 0, 1, tzinfo=timezone.utc))
    assert a.startswith("20261001T120000-") and a < b


def test_latest_run_id_is_the_newest():
    seed(_run("20261001T120000-aaaa"))
    seed(_run("20261002T120000-bbbb"))
    assert batch.store.latest_run_id() == "20261002T120000-bbbb"


def test_ids_covering_matches_repo_casefold():
    seed(_run("20261001T120000-aaaa", repo="Acme/Widget"))
    seed(_run("20261002T120000-bbbb", repo="other/r"))
    assert batch.store.ids_covering(_ref("acme/widget")) == ["20261001T120000-aaaa"]


def test_default_run_id_prefers_unfinished_matching_over_newer_finished_other():
    seed(_run("20261001T120000-aaaa", repo="o/r"))
    seed(_run("20261002T120000-bbbb", repo="other/r", status=batch.model.RunStatus.DONE))
    assert batch.store.latest_run_id() == "20261002T120000-bbbb"
    assert batch.store.default_run_id(_ref("o/r")) == "20261001T120000-aaaa"


def test_default_run_id_all_finished_falls_back_to_newest_matching():
    seed(_run("20261001T120000-aaaa", repo="o/r", status=batch.model.RunStatus.DONE))
    seed(_run("20261002T120000-bbbb", repo="o/r", status=batch.model.RunStatus.CANCELLED))
    seed(_run("20261003T120000-cccc", repo="x/y", status=batch.model.RunStatus.DONE))
    assert batch.store.default_run_id(_ref("o/r")) == "20261002T120000-bbbb"


def test_default_run_id_no_repo_or_unmatched_falls_back_to_latest():
    seed(_run("20261001T120000-aaaa"))
    seed(_run("20261002T120000-bbbb"))
    assert batch.store.default_run_id(None) == "20261002T120000-bbbb"
    assert batch.store.default_run_id(_ref("no/such")) == "20261002T120000-bbbb"
    assert batch.store.covering_run_id(_ref("no/such")) is None
    assert batch.store.covering_run_id(None) is None


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
    seed(run)
    path = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    raw = json.loads(path.read_text())
    del raw["items"][0]["remote_sha"]
    path.write_text(json.dumps(raw))
    assert batch.store.load(run.id).items[0].remote_sha == "h1"


def test_attempt_log_path_numbers_each_attempt_of_a_step():
    first = batch.store.attempt_log_path(RID, "o/r", 1, "publish")
    first.touch()
    assert first == batch.store.logs_dir(RID) / "o__r-1-publish-0.log"
    assert batch.store.attempt_log_path(RID, "o/r", 1, "publish").name == "o__r-1-publish-1.log"
    assert batch.store.attempt_log_path(RID, "o/r", 12, "publish").name == "o__r-12-publish-0.log"


def test_ids_covering_and_default_skip_an_unreadable_run():
    seed(_run("20261001T120000-aaaa", repo="o/r"))
    seed(_run("20261002T120000-bbbb", repo="o/r"))
    (batch.store.run_dir("20261002T120000-bbbb") / batch.store.STATE_FILE).write_text("{not json")
    assert batch.store.ids_covering(_ref("o/r")) == ["20261001T120000-aaaa"]
    assert batch.store.unfinished_covering(_ref("o/r")) == ["20261001T120000-aaaa"]
    assert batch.store.default_run_id(_ref("o/r")) == "20261001T120000-aaaa"


def test_covering_matches_by_repo_key_even_when_the_label_differs():
    seed(_run("20261001T120000-aaaa", repo="other/label", repo_key="acme-widget-abcd1234"))
    seed(_run("20261002T120000-bbbb", repo="o/r", repo_key="other-key-ffff9999"))
    ref = _ref("cwd/label", key="acme-widget-abcd1234")
    assert batch.store.ids_covering(ref) == ["20261001T120000-aaaa"]
    assert batch.store.covering_run_id(ref) == "20261001T120000-aaaa"
    assert batch.store.default_run_id(ref) == "20261001T120000-aaaa"


def test_covering_falls_back_to_the_label_when_an_item_has_no_repo_key():
    seed(_run("20261001T120000-aaaa", repo="o/r"))
    seed(_run("20261002T120000-bbbb", repo="other/r", repo_key="k"))
    ref = _ref("o/r", key="unrelated-key-00000000")
    assert batch.store.ids_covering(ref) == ["20261001T120000-aaaa"]
    assert batch.store.covering_run_id(ref) == "20261001T120000-aaaa"
