import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.queue_entries  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch_views  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import holding, seed  # noqa: E402
from otto_queue.model import EntryState  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"

_LIST_KEYS = {"id", "run_dir", "started_at", "status", "active", "items", "repos"}


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _minimal_run(run_id, *, repo="o/r", jobs=None):
    rows = [PlanRow(repo, "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    run.id = run_id
    run.items[0].worktree = "/wt"
    run.status = batch.model.RunStatus.WAITING
    if jobs is not None:
        run.items[0].jobs = list(jobs)
    return seed(run)


def _job(entry_id=1):
    return batch.model.Job(step=batch.model.Step.REVIEW, entry_id=entry_id,
                           submitted_at="t")


def _fake_states(monkeypatch, mapping):
    monkeypatch.setattr(
        batch.queue_entries.QueueEntries, "states",
        lambda self, ids: {i: mapping[i] for i in ids if i in mapping})


def test_list_is_newest_first_with_picker_fields(capsys):
    _minimal_run("r1")
    _minimal_run("r2")
    assert _main(["batch", "status", "--list"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 2
    assert [r["id"] for r in doc["runs"]] == ["r2", "r1"]
    first = doc["runs"][0]
    assert set(first) == _LIST_KEYS
    assert first["active"] is False
    assert first["repos"] == ["o/r"]
    assert first["items"] == 1


def test_list_omits_a_reaping_directory(capsys):
    _minimal_run("r1")
    tomb = batch.store.batch_root() / "gone.reaping"
    tomb.mkdir()
    (tomb / batch.store.STATE_FILE).write_text("{}")
    assert _main(["batch", "status", "--list"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in doc["runs"]] == ["r1"]


def test_list_keeps_a_run_whose_state_is_invalid_json(capsys):
    broken = batch.store.batch_root() / "bad"
    broken.mkdir(parents=True)
    (broken / batch.store.STATE_FILE).write_text("{")
    assert _main(["batch", "status", "--list"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert len(doc["runs"]) == 1
    entry = doc["runs"][0]
    assert entry["id"] == "bad"
    assert "error" in entry


def test_list_keeps_a_run_whose_state_does_not_fit_a_run(capsys):
    _minimal_run("r1")
    odd = batch.store.batch_root() / "r0"
    odd.mkdir()
    (odd / batch.store.STATE_FILE).write_text("{}")
    assert _main(["batch", "status", "--list"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in doc["runs"]] == ["r1", "r0"]
    assert "error" in doc["runs"][1] and "error" not in doc["runs"][0]


def test_list_refuses_a_run_id_full_and_offline():
    _minimal_run("r1")
    assert _main(["batch", "status", "--list", "r1"]) == 1
    assert _main(["batch", "status", "--list", "--full"]) == 1
    assert _main(["batch", "status", "--list", "--offline"]) == 1


def test_list_with_no_runs_is_empty(capsys):
    assert _main(["batch", "status", "--list"]) == 0
    assert json.loads(capsys.readouterr().out) == {"schema_version": 2, "runs": []}


def test_list_handshake_is_schema_2():
    assert _main(["--schema-version", "2", "batch", "status", "--list"]) == 0


def test_list_active_when_a_job_is_running_not_when_a_driver_lock_is_held(monkeypatch):
    _minimal_run("live", jobs=[_job(1)])
    idle = _minimal_run("idle")
    _fake_states(monkeypatch, {1: EntryState.RUNNING})
    with holding(idle.id, Role.DRIVER):
        runs = {r["id"]: r for r in cli.pr_batch_views.run_list()["runs"]}
    assert runs["live"]["active"] is True
    assert runs["idle"]["active"] is False


def test_list_not_active_when_jobs_are_queued_or_done(monkeypatch):
    _minimal_run("r1", jobs=[_job(1)])
    _fake_states(monkeypatch, {1: EntryState.QUEUED})
    assert cli.pr_batch_views.run_list()["runs"][0]["active"] is False
    _fake_states(monkeypatch, {1: EntryState.DONE})
    assert cli.pr_batch_views.run_list()["runs"][0]["active"] is False
