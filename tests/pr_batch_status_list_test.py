import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"

_LIST_KEYS = {"id", "run_dir", "started_at", "status", "active", "items", "repos"}


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _minimal_run(run_id, *, repo="o/r"):
    rows = [PlanRow(repo, "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    run.id = run_id
    run.items[0].worktree = "/wt"
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


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
