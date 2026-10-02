import stat
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.steps as steps  # noqa: E402
from batch.model import Step  # noqa: E402
from conftest import seed_repo  # noqa: E402


def test_draft_argvs():
    assert steps.step_argv(Step.REBASE, "pr", "/wt", publish=False) == \
        ["pr", "rebase", "--fix", "--no-push", "--repo-dir", "/wt"]
    assert steps.step_argv(Step.COMMENTS, "pr", "/wt", publish=False) == \
        ["pr", "comments", "--fix", "--repo-dir", "/wt"]
    assert steps.step_argv(Step.REVIEW, "pr", "/wt", publish=False) == \
        ["pr", "review", "--self", "--fix", "--force", "--repo-dir", "/wt"]


def test_publish_argvs():
    assert steps.step_argv(Step.REBASE, "pr", "/wt", publish=True) == \
        ["pr", "rebase", "--fix", "--repo-dir", "/wt"]
    assert steps.step_argv(Step.COMMENTS, "pr", "/wt", publish=True) == \
        ["pr", "comments", "--fix", "--finish", "--post", "--repo-dir", "/wt"]
    assert steps.step_argv(Step.REVIEW, "pr", "/wt", publish=True) == \
        ["pr", "review", "--self", "--fix", "--force", "--push", "--repo-dir", "/wt"]


def test_no_step_argv_forces_a_rebase():
    for publish in (True, False):
        assert "--force" not in steps.step_argv(Step.REBASE, "pr", "/wt", publish=publish)


def _script(tmp_path, body):
    p = tmp_path / "fake.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _wait(proc, timeout=10):
    end = time.time() + timeout
    while proc.poll() is None and time.time() < end:
        time.sleep(0.05)
    return proc.poll()


def test_streams_stderr_logs_it_and_captures_stdout(tmp_path):
    exe = _script(tmp_path, "echo one >&2\necho two >&2\necho '{\"ok\": true}'\nexit 3\n")
    proc = steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="root-1")
    assert _wait(proc) == 3
    assert proc.drain_lines() == ["one", "two"]
    assert proc.stdout().strip() == '{"ok": true}'
    assert (tmp_path / "s.log").read_text() == "one\ntwo\n"


def test_child_has_no_stdin_no_tty_and_inherits_the_trail_root(tmp_path):
    exe = _script(tmp_path,
                  "read x && echo got-stdin >&2\n"
                  "( : > /dev/tty ) 2>/dev/null && echo has-tty >&2\n"
                  'echo "root=$WORKBENCH_TRAIL_ROOT" >&2\n')
    proc = steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="root-1")
    _wait(proc)
    lines = proc.drain_lines()
    assert "got-stdin" not in lines and "has-tty" not in lines
    assert "root=root-1" in lines


def test_kill_terminates_the_process_group(tmp_path):
    exe = _script(tmp_path, "sleep 30 &\nwait\n")
    proc = steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="r")
    proc.kill()
    assert _wait(proc, 5) is not None


def test_ensure_worktree_finds_the_checkout_and_reports_dirt(tmp_path):
    repo = tmp_path / "r"
    seed_repo(repo)
    (repo / "dirt.txt").write_text("x")
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            capture_output=True, text=True, check=True).stdout.strip()
    res = steps.ensure_worktree(str(repo), branch)
    assert res.ok and res.dirty
    assert Path(res.path).resolve() == repo.resolve()
