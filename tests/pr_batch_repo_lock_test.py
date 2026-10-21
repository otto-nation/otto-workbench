"""Per-repo batch locks: `_batch_need` declares them; `run`/`resume` verify and hold them."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
import cli.registry  # noqa: E402
import core.run_lock  # noqa: E402
import pr.context  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402
from cli.needs import NO_LOCK, NONE, Need  # noqa: E402
from conftest import run_checked, seed_repo  # noqa: E402
from run_lock_support import held_elsewhere, live_pid, repo_with_origin  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _origin_repo(path, slug):
    seed_repo(path)
    run_checked(["git", "-C", str(path), "remote", "add", "origin",
                 f"https://github.com/{slug}.git"])
    return Path(path)


def _key(repo):
    return pr.target.repo_key_from_origin(str(repo))


def _scope(key):
    return batch.store.batch_lock(key)


def _path(key):
    return core.run_lock.global_lock_path(_scope(key).name)


def _need(argv):
    return cli.registry._batch_need(argv)


def _row(repo, n, branch, key=""):
    return PlanRow("acme/widget", str(repo), n, "t", branch, "h", False,
                   {batch.model.Step.REVIEW: StepNeed(True, "x")}, repo_key=key)


def _saved(repo="o/r", repo_key="", run_id="20261001T120000-aaaa"):
    row = PlanRow(repo, "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")}, repo_key=repo_key)
    run = batch.scheduler.new_run([row], steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1, auto_publish=[])
    run.id = run_id
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


# ── `_batch_need` ─────────────────────────────────────────────────────────────


def test_batch_need_declares_sorted_deduplicated_locks_for_checkouts(tmp_path):
    one = _origin_repo(tmp_path / "one", "acme/one")
    two = _origin_repo(tmp_path / "two", "acme/two")
    ka, kb = _key(one), _key(two)
    both = _need(["run", "--checkout", str(one), "--checkout", str(two)])
    assert both.locks == frozenset({_scope(ka), _scope(kb)})
    again = _need(["run", "--checkout", str(one), "--checkout", str(one)])
    assert again.locks == frozenset({_scope(ka)})
    equals = _need(["run", f"--checkout={one}"])
    assert equals.locks == frozenset({_scope(ka)})


def test_batch_need_reads_keys_from_a_plan_file(tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"rows": [
        {"repo_key": "plan-key-aaaaaaaa"},
        {"repo_dir": str(repo)},
        {"repo_key": "plan-key-aaaaaaaa"},
    ]}))
    need = _need(["run", "--plan", str(plan)])
    assert need.locks == frozenset({_scope("plan-key-aaaaaaaa"), _scope(_key(repo))})


def test_batch_need_resume_reads_keys_from_the_saved_run():
    run = _saved(repo_key="stored-key-bbbbbbbb")
    need = _need(["resume", run.id])
    assert need.locks == frozenset({_scope("stored-key-bbbbbbbb")})


def test_batch_need_noid_resume_reads_keys_from_the_covering_run(monkeypatch):
    run = _saved(repo="o/r", repo_key="cover-key-cccccccc")
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label="other/label", key="cover-key-cccccccc"))
    need = _need(["resume"])
    assert need.locks == frozenset({_scope("cover-key-cccccccc")})
    assert run.id == "20261001T120000-aaaa"


def test_batch_need_empty_and_help_take_no_locks_and_do_no_io(monkeypatch):
    seen = []

    def boom(*_a, **_k):
        seen.append(1)
        raise AssertionError("must not do I/O")

    monkeypatch.setattr(pr.target, "repo_key_from_origin", boom)
    monkeypatch.setattr(pr.target, "repo_identity_from_origin", boom)
    monkeypatch.setattr(batch.store, "load", boom)
    empty = Need(NONE, update=False, locks=NO_LOCK)
    assert _need([]) == empty
    assert _need(["run", "--help"]) == empty
    assert _need(["run", "--help", "--checkout", "/r"]) == empty
    assert _need(["resume", "-h"]) == empty
    assert seen == []


def test_batch_need_help_still_prints_via_the_real_entry(capsys):
    assert _main(["batch", "run", "--help"]) == 0
    assert "--checkout" in capsys.readouterr().out


def test_batch_need_broken_input_yields_no_lock_not_an_exception(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text("not json")
    empty = Need(NONE, update=False, locks=NO_LOCK)
    assert _need(["run", "--plan", str(plan)]) == empty
    assert _need(["resume", "no-such-run"]) == empty


# ── concurrent batches ────────────────────────────────────────────────────────


def test_a_run_over_a_different_repo_is_not_refused(monkeypatch, tmp_path):
    a = _origin_repo(tmp_path / "a", "acme/one")
    b = _origin_repo(tmp_path / "b", "acme/two")
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", []))
    with live_pid() as pid, held_elsewhere(
            _path(_key(a)),
            {"pid": pid, "command": "pr batch run", "started": "t0",
             "run": "20261013T000000-aaaa"}):
        code = _main(["batch", "run", "--checkout", str(b)])
    assert code == 0


def test_a_run_over_the_same_repo_is_refused_naming_the_lock(monkeypatch, tmp_path, capsys):
    repo = repo_with_origin(tmp_path / "repo")
    key = _key(repo)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", []))
    with live_pid() as pid, held_elsewhere(
            _path(key),
            {"pid": pid, "command": "pr batch run --checkout /x", "started": "t0",
             "run": "20261013T000000-abcd"}):
        code = _main(["batch", "run", "--checkout", str(repo)])
    assert code == 1
    err = capsys.readouterr().err
    assert f"owns this global batch-{key}" in err
    assert "run 20261013T000000-abcd" in err


def test_the_handler_refuses_when_a_chosen_runs_key_is_not_held(monkeypatch, tmp_path, capsys):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(
        batch.plan, "build_run_plan",
        lambda dirs: Plan("me", [_row(repo, 1, "feat/a", key="other-key-ffffffff")]))
    code = _main(["batch", "run", "--checkout", str(repo)])
    assert code == 1
    assert "the batch lock for other-key-ffffffff is not held" in capsys.readouterr().err


def test_require_batch_locks_names_keys_this_process_does_not_hold():
    with pytest.raises(cli.pr_batch._Refused, match="the batch lock for missing-key is not held"):
        cli.pr_batch._require_batch_locks(["missing-key"])


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


def test_dispatch_holds_the_repo_batch_lock_before_it_resolves(tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    key = _key(repo)
    seen = {}
    real = pr.context.resolve_at

    def spy(*args, **kwargs):
        seen["held"] = core.run_lock.is_global_held(_scope(key))
        return real(*args, **kwargs)

    with patch("pr.context.resolve_at", side_effect=spy), \
         patch("cli.pr._dispatch", return_value=0):
        cli.pr.main(["batch", "run", "--checkout", str(repo)], bin_dir=BIN_DIR)
    assert seen["held"] is True
    assert not core.run_lock.is_global_held(_scope(key))
