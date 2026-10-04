import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.steps  # noqa: E402
from batch.model import Step  # noqa: E402
from conftest import seed_repo  # noqa: E402


def test_every_step_runs_drafted():
    assert batch.steps.step_argv(Step.REBASE, "pr", "/wt") == \
        ["pr", "rebase", "--fix", "--no-push", "--repo-dir", "/wt"]
    assert batch.steps.step_argv(Step.COMMENTS, "pr", "/wt") == \
        ["pr", "comments", "--fix", "--repo-dir", "/wt"]
    assert batch.steps.step_argv(Step.REVIEW, "pr", "/wt") == \
        ["pr", "review", "--self", "--fix", "--force", "--repo-dir", "/wt"]


def test_no_step_argv_forces_a_rebase():
    assert "--force" not in batch.steps.step_argv(Step.REBASE, "pr", "/wt")


def test_the_ci_step_fixes_without_rebasing_against_the_remote_head():
    assert batch.steps.step_argv(Step.CI, "pr", "/wt", remote_sha="abc") == \
        ["pr", "ci", "--fix", "--no-rebase", "--head-sha", "abc", "--repo-dir", "/wt"]


def test_the_ci_step_waits_when_asked():
    assert "--wait" in batch.steps.step_argv(Step.CI, "pr", "/wt", remote_sha="abc",
                                             wait=True)


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
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="root-1")
    assert _wait(proc) == 3
    assert proc.drain_lines() == ["one", "two"]
    assert proc.stdout().strip() == '{"ok": true}'
    assert (tmp_path / "s.log").read_text() == "one\ntwo\n"


def test_child_has_no_stdin_no_tty_and_inherits_the_trail_root(tmp_path):
    # The has-tty probe is weak under a non-interactive test runner (CI,
    # `pytest -n`, any session with no controlling terminal): opening
    # /dev/tty from the child fails there regardless of whether
    # `StepProcess` detaches it, so this assertion can pass even if the
    # detaching regresses. `test_start_detaches_the_child_from_a_controlling_terminal`
    # below pins the actual Popen call instead, and is the one that would
    # catch that regression.
    exe = _script(tmp_path,
                  "read x && echo got-stdin >&2\n"
                  "( : > /dev/tty ) 2>/dev/null && echo has-tty >&2\n"
                  'echo "root=$WORKBENCH_TRAIL_ROOT" >&2\n')
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="root-1")
    _wait(proc)
    lines = proc.drain_lines()
    assert "got-stdin" not in lines and "has-tty" not in lines
    assert "root=root-1" in lines


def test_start_detaches_the_child_from_a_controlling_terminal(tmp_path, monkeypatch):
    """Pins the Popen call itself, independent of whether this environment has a tty.

    The behavioural probe above can't fail under a non-interactive runner even
    if `start_new_session` is dropped, so this asserts on the kwargs instead:
    a regression here is caught no matter what controlling terminal the test
    process itself has.
    """
    captured = {}
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        captured.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(batch.steps.subprocess, "Popen", recording_popen)
    exe = _script(tmp_path, "true\n")
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="root-1")
    _wait(proc)
    assert captured["start_new_session"] is True
    assert captured["stdin"] == subprocess.DEVNULL


def test_kill_terminates_the_process_group(tmp_path):
    exe = _script(tmp_path, "sleep 30 &\nwait\n")
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="r")
    proc.kill()
    assert _wait(proc, 5) is not None


def test_kill_escalates_to_sigkill_when_the_child_ignores_sigterm(tmp_path, monkeypatch):
    marker = tmp_path / "trapped"
    exe = _script(tmp_path, f"trap '' TERM\ntouch {marker}\nexec sleep 30\n")
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="r")
    # Wait for the child to actually install its trap before sending SIGTERM,
    # rather than a fixed sleep that can race under a loaded machine.
    end = time.time() + 5
    while not marker.exists() and time.time() < end:
        time.sleep(0.01)
    assert marker.exists(), "child never installed its trap"
    monkeypatch.setattr(batch.steps.StepProcess, "KILL_GRACE_S", 0)
    proc.kill()
    time.sleep(0.2)
    assert proc.poll() is None  # SIGTERM alone is ignored
    proc.kill()
    assert _wait(proc, 5) is not None


def _zombie_reported(pid):
    # WNOWAIT reports the exit without reaping, so the zombie stays; a child
    # already reaped out from under us reads as "done" too.
    try:
        return bool(os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT))
    except ChildProcessError:
        return True


def _wait_for_zombie(pid, timeout):
    end = time.time() + timeout
    while time.time() < end and not _zombie_reported(pid):
        time.sleep(0.01)


# platform-only: the EPERM answer is macOS kernel behaviour; Linux signals a zombie-only group silently
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only EPERM smoke test")
def test_kill_of_an_exited_but_unreaped_step_does_not_raise(tmp_path):
    # macOS-only smoke test: the step has exited but nothing has reaped it, so its
    # group holds only a zombie, and macOS answers killpg on that group with EPERM
    # rather than ESRCH. On Linux, signalling a zombie-only group is a silent
    # no-op either way, so this test alone does not pin the fix on this branch;
    # test_kill_treats_eperm_as_gone_only_once_the_step_has_exited below, which
    # mocks os.killpg to raise EPERM directly, is the regression guard for that.
    exe = _script(tmp_path, "true\n")
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="r")
    _wait_for_zombie(proc.pid, 5)
    proc.kill()
    assert _wait(proc, 5) == 0


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_kill_ends_what_an_exited_step_left_running_in_its_group(tmp_path):
    # The step's own process is gone but a background child it started is still in
    # its group; cancel --kill must still reach that child.
    pidfile = tmp_path / "child.pid"
    exe = _script(tmp_path, f"sleep 30 >/dev/null 2>&1 &\necho $! > {pidfile}\n")
    proc = batch.steps.StepProcess.start([exe], log_path=tmp_path / "s.log", trail_root="r")
    assert _wait(proc, 5) == 0
    child = int(pidfile.read_text())
    assert _alive(child)
    proc.kill()
    end = time.time() + 5
    while _alive(child) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(child)


def test_kill_treats_eperm_as_gone_only_once_the_step_has_exited(tmp_path, monkeypatch):
    def refuse(_pgid, _sig):
        raise PermissionError(1, "Operation not permitted")

    done = batch.steps.StepProcess.start([_script(tmp_path, "true\n")],
                                         log_path=tmp_path / "a.log", trail_root="r")
    _wait(done, 5)
    monkeypatch.setattr(batch.steps.os, "killpg", refuse)
    done.kill()

    monkeypatch.undo()
    live = batch.steps.StepProcess.start([_script(tmp_path, "exec sleep 30\n")],
                                         log_path=tmp_path / "b.log", trail_root="r")
    monkeypatch.setattr(batch.steps.os, "killpg", refuse)
    try:
        with pytest.raises(PermissionError):
            live.kill()
    finally:
        monkeypatch.undo()
        live.kill()
        _wait(live, 5)


def test_ensure_worktree_finds_the_checkout_and_reports_dirt(tmp_path):
    repo = tmp_path / "r"
    seed_repo(repo)
    (repo / "dirt.txt").write_text("x")
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            capture_output=True, text=True, check=True).stdout.strip()
    res = batch.steps.ensure_worktree(str(repo), branch)
    assert res.ok and res.dirty
    assert Path(res.path).resolve() == repo.resolve()
