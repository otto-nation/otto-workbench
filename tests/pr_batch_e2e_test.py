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
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import otto_queue.submit  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from conftest import seed_repo  # noqa: E402
from otto_queue.submit import SubmitOutcome, SubmitResult  # noqa: E402


def _fake_pr(bin_dir: Path, log: Path) -> None:
    bin_dir.mkdir()
    script = bin_dir / "pr"
    tally = ('{"failures": [], "fixed": [], "unfixed": [], "skipped": [], '
             '"suite_status": "", "commit": "", "type": "fix"}')
    # A default rebase has no --fix; commit so HEAD moves and _close records
    # ready_to_push. --repo-dir is the worktree the child is told to use.
    script.write_text(
        f'#!/bin/sh\n'
        f'printf "%s\\n" "$*" >> {log}\n'
        f'echo working >&2\n'
        f'if [ "$1" = rebase ]; then\n'
        f'  repo=""\n'
        f'  prev=""\n'
        f'  for a in "$@"; do\n'
        f'    if [ "$prev" = "--repo-dir" ]; then repo=$a; fi\n'
        f'    prev=$a\n'
        f'  done\n'
        f'  git -C "$repo" -c user.name=t -c user.email=t@t commit '
        f'--allow-empty -q --no-verify -m rebase\n'
        f'fi\n'
        f'exit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    # The batch's CI step runs the backing script beside `pr`, not `pr ci`.
    ci = bin_dir / "ci-check"
    ci.write_text(
        f'#!/bin/sh\nprintf "ci %s\\n" "$*" >> {log}\necho working >&2\n'
        f'printf -- "---\\n%s\\n" \'{tally}\'\nexit 0\n')
    ci.chmod(ci.stat().st_mode | stat.S_IEXEC)


def _main(argv, bin_dir):
    try:
        return cli.pr.main(list(argv), bin_dir=bin_dir)
    except SystemExit as exc:
        return exc.code


def test_run_exits_after_submit(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    seed_repo(repo)
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            capture_output=True, text=True, check=True).stdout.strip()
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    row = PlanRow("o/r", str(repo), 1, "t", branch, head, False,
                  {batch.model.Step.REBASE: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", [row]))
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    _fake_pr(bin_dir, log)
    submitted = []

    def fake_submit(request, policy=None, **kwargs):
        submitted.append(request)
        return SubmitResult(0, SubmitOutcome.QUEUED, 1)

    monkeypatch.setattr(otto_queue.submit, "submit", fake_submit)
    assert _main(["batch", "run", "--checkout", str(repo), "--steps", "rebase"], bin_dir) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    kinds = [l["kind"] for l in lines]
    assert kinds[0] == "run_started"
    assert "run_finished" not in kinds
    assert not log.exists() or log.read_text() == ""
    assert submitted
    run = batch.store.load(batch.store.latest_run_id())
    assert run.items[0].jobs[0].entry_id == 1
