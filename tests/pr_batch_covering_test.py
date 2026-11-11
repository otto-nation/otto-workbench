"""Several batch runs may cover one repo: `pr batch` declares no lock of its own, and
no-id commands act on the run covering this checkout."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import cli.registry  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402
from cli.needs import NO_LOCK, NONE, Need  # noqa: E402
from run_lock_support import repo_with_origin  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _saved(repo="o/r", repo_key="", run_id="20261001T120000-aaaa"):
    row = PlanRow(repo, "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")}, repo_key=repo_key)
    run = batch.scheduler.new_run([row], steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    run.id = run_id
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


# ── no lock per repo ──────────────────────────────────────────────────────────


def test_batch_declares_no_lock_for_a_run_or_resume_over_a_real_repo(tmp_path):
    """Run and resume over a repo with an origin are where a per-repo lock was declared."""
    repo = repo_with_origin(tmp_path / "repo")
    run = _saved(repo_key=pr.target.repo_key_from_origin(str(repo)))
    spec = cli.registry.COMMANDS["batch"]
    empty = Need(NONE, update=False, locks=NO_LOCK)
    for argv in (["run", "--checkout", str(repo)], ["resume", run.id], ["cancel"], []):
        assert cli.registry.need_for(spec, argv) == empty


# ── no-id resume / cancel / status ────────────────────────────────────────────


def test_noid_resume_refuses_when_no_run_covers_the_cwd_repo(monkeypatch, capsys):
    other = _saved(repo="other/r", repo_key="other-key-dddddddd")
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label="cwd/repo", key="cwd-key-eeeeeeee"))
    assert _main(["batch", "resume"]) == 1
    assert "no batch run covers this repo; pass a run id" in capsys.readouterr().err
    assert batch.store.load(other.id).status is batch.model.RunStatus.WAITING


def test_noid_cancel_refuses_when_no_run_covers_the_cwd_repo(monkeypatch, capsys):
    other = _saved(repo="other/r", repo_key="other-key-dddddddd")
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label="cwd/repo", key="cwd-key-eeeeeeee"))
    assert _main(["batch", "cancel"]) == 1
    assert "no batch run covers this repo; pass a run id" in capsys.readouterr().err
    assert batch.store.load(other.id).status is batch.model.RunStatus.WAITING


def test_status_noid_outside_any_covering_repo_notes_stderr(monkeypatch, tmp_path, capsys):
    run = _saved(repo="other/r", repo_key="other-key-dddddddd")
    monkeypatch.chdir(tmp_path)
    assert _main(["batch", "status", "--offline"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["run"]["id"] == run.id
    assert f"showing {run.id}, the newest run on this machine" in captured.err
    assert "no run covers" not in captured.err


def test_status_noid_in_an_uncovered_repo_names_the_label(monkeypatch, tmp_path, capsys):
    run = _saved(repo="other/r", repo_key="other-key-dddddddd")
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label="cwd/repo", key="cwd-key-eeeeeeee"))
    assert _main(["batch", "status", "--offline"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["run"]["id"] == run.id
    assert (f"no run covers cwd/repo; showing {run.id}, the newest run on this machine"
            in captured.err)
