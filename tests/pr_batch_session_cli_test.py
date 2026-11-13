"""`pr batch session`: print the run-session recipe, or exec pi with it.

Revert: build argv in cmd_session without `--session-id`.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.model import Finding, FindingReason, ItemStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _seeded(tmp_path, checkouts=None):
    checkout = tmp_path / "r"
    checkout.mkdir(exist_ok=True)
    rows = [PlanRow("o/r", str(checkout), 1, "t", "b1", "h", False,
                    {Step.REBASE: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(
        rows, steps=[Step.REBASE], selected=None, pool=1,
        checkouts=[str(checkout)] if checkouts is None else checkouts)
    item = run.items[0]
    item.findings.append(Finding(FindingReason.CI_FAILING, "ci"))
    item.status = ItemStatus.NEEDS_SESSION
    return seed(run)


def test_session_json_prints_the_recipe(tmp_path, capsys):
    run = _seeded(tmp_path)
    assert _main(["batch", "session", run.id, "--json"]) == 0
    sid, name = f"batch-{run.id}", f"batch o/r {run.id}"
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "session_id": sid, "name": name, "cwd": str(tmp_path / "r"),
        "argv": ["pi", "--session-id", sid, "--name", name],
        "env": {"WORKBENCH_RUN_DIR": str(batch.store.run_dir(run.id))},
    }


def test_session_without_json_execs_pi_in_the_checkout(tmp_path, monkeypatch):
    run = _seeded(tmp_path)
    calls = {}
    monkeypatch.setattr(os, "chdir", lambda path: calls.setdefault("cwd", path))

    def fake_exec(file, argv, env):
        calls.update(file=file, argv=argv, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    assert _main(["batch", "session", run.id]) == 0
    assert calls["cwd"] == str(tmp_path / "r")
    assert calls["file"] == "pi"
    assert calls["argv"] == ["pi", "--session-id", f"batch-{run.id}",
                             "--name", f"batch o/r {run.id}"]
    assert calls["env"]["WORKBENCH_RUN_DIR"] == str(batch.store.run_dir(run.id))
    assert calls["env"]["PATH"] == os.environ["PATH"]


def test_session_refuses_a_run_over_two_checkouts(tmp_path, capsys):
    run = _seeded(tmp_path, checkouts=["/a", "/b"])
    assert _main(["batch", "session", run.id, "--json"]) == 1
    assert "2 checkouts" in capsys.readouterr().err


def test_session_of_an_unknown_run_fails(capsys):
    assert _main(["batch", "session", "20990101T000000-0000", "--json"]) == 1
    assert "no run 20990101T000000-0000" in capsys.readouterr().err


def test_session_refuses_a_checkout_that_is_gone(tmp_path, monkeypatch, capsys):
    run = _seeded(tmp_path, checkouts=[str(tmp_path / "gone")])
    monkeypatch.setattr(os, "execvpe", lambda *a: (_ for _ in ()).throw(AssertionError("exec")))
    assert _main(["batch", "session", run.id]) == 1
    assert "does not exist" in capsys.readouterr().err
