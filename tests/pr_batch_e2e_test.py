import json
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from conftest import seed_repo  # noqa: E402


def _fake_pr(bin_dir: Path, log: Path) -> None:
    bin_dir.mkdir()
    script = bin_dir / "pr"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\necho working >&2\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _main(argv, bin_dir):
    try:
        return cli.pr.main(list(argv), bin_dir=bin_dir)
    except SystemExit as exc:
        return exc.code


def test_run_resolve_status_round_trip(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    seed_repo(repo)
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            capture_output=True, text=True, check=True).stdout.strip()
    row = PlanRow("o/r", str(repo), 1, "t", branch, "h", False,
                  {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", [row]))
    monkeypatch.setattr(batch.plan, "replan_row", lambda r: row)
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    _fake_pr(bin_dir, log)

    assert _main(["batch", "run", "--checkout", str(repo)], bin_dir) == 10
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    kinds = [l["kind"] for l in lines]
    assert kinds[0] == "run_started" and "run_waiting" in kinds
    assert all(l["schema_version"] == 1 for l in lines)

    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["rebase", "comments", "review"]
    assert all(c.endswith(f"--repo-dir {repo}") or c.endswith(f"--repo-dir {repo.resolve()}")
               for c in calls)
    assert "--no-push" in calls[0]
    assert all("--force" not in c.split() or c.split()[0] == "review" for c in calls)

    run_id = batch.store.latest_run_id()
    run = batch.store.load(run_id)
    publish = [d for d in run.open_decisions() if d.kind is batch.model.DecisionKind.PUBLISH]
    assert len(publish) == 1

    assert _main(["batch", "resolve", run_id, publish[0].id, "--action", "discard"], bin_dir) == 0
    capsys.readouterr()
    assert _main(["batch", "status", run_id], bin_dir) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["run"]["items"][0]["status"] == "done"
