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
import core.proc  # noqa: E402
from batch.model import Step  # noqa: E402
from conftest import add_self_origin, commit_all, git_in, init_repo, seed_repo  # noqa: E402


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
        ["ci-check", "--fix", "--no-rebase", "--head-sha", "abc", "--repo-dir", "/wt"]


def test_the_ci_step_runs_ci_check_beside_pr_not_the_pr_dispatcher():
    """`pr ci` fetches into the shared .git before the command starts; a CI step that
    waits on GitHub must hold no git lock, so it runs the backing script directly."""
    argv = batch.steps.step_argv(Step.CI, "/wb/ai/bin/pr", "/wt", remote_sha="abc", wait=True)
    assert argv[0] == "/wb/ai/bin/ci-check"
    assert "ci" not in argv


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


def _branch(repo):
    return subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _commit_mise(repo, name=".mise.toml", content="[tools]\n"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git_in(repo, "add", name)
    commit_all(repo, f"add {name}")
    return path


def test_ensure_worktree_trusts_mise_toml_identical_to_the_default_branch(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "r")
    cfg = _commit_mise(repo)
    add_self_origin(repo)
    seen = []
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: "/bin/mise" if n == "mise" else None)
    monkeypatch.setattr(batch.steps, "_mise_trust",
                        lambda c: seen.append(str(c)) or core.proc.CmdResult(0, "", ""))
    res = batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert res.ok
    assert seen == [str(cfg)]


def test_ensure_worktree_does_not_trust_a_modified_mise_toml(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "r")
    cfg = _commit_mise(repo)
    add_self_origin(repo)
    cfg.write_text("[tools]\npython = \"3\"\n")
    seen = []
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: "/bin/mise" if n == "mise" else None)
    monkeypatch.setattr(batch.steps, "_mise_trust",
                        lambda c: seen.append(str(c)) or core.proc.CmdResult(0, "", ""))
    batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert seen == []


def test_ensure_worktree_does_not_trust_mise_toml_absent_on_the_default_branch(
        tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "r")
    git_in(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
    add_self_origin(repo)
    (repo / ".mise.toml").write_text("[tools]\n")
    seen = []
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: "/bin/mise" if n == "mise" else None)
    monkeypatch.setattr(batch.steps, "_mise_trust",
                        lambda c: seen.append(str(c)) or core.proc.CmdResult(0, "", ""))
    batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert seen == []


def test_ensure_worktree_does_not_trust_mise_toml_when_comparison_fails(
        tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "r")
    _commit_mise(repo)
    add_self_origin(repo)
    seen = []
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: "/bin/mise" if n == "mise" else None)
    def boom(_cwd):
        raise RuntimeError("boom")
    monkeypatch.setattr(batch.steps, "_default_rev", boom)
    monkeypatch.setattr(batch.steps, "_mise_trust",
                        lambda c: seen.append(str(c)) or core.proc.CmdResult(0, "", ""))
    res = batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert res.ok
    assert seen == []


def test_ensure_worktree_skips_mise_trust_when_mise_is_absent(tmp_path, monkeypatch):
    repo = seed_repo(tmp_path / "r")
    (repo / ".mise.toml").write_text("[tools]\n")
    seen = []
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: None)
    monkeypatch.setattr(batch.steps, "_mise_trust", lambda cfg: seen.append(str(cfg)))
    batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert seen == []


def test_mise_trust_invokes_quiet_trust(tmp_path, monkeypatch):
    cfg = tmp_path / ".mise.toml"
    cfg.write_text("[tools]\n")
    seen = []
    monkeypatch.setattr(batch.steps.core.proc, "run",
                        lambda cmd, **kw: seen.append(cmd) or core.proc.CmdResult(0, "", ""))
    batch.steps._mise_trust(cfg)
    assert seen == [["mise", "trust", "--quiet", str(cfg)]]


def test_a_failed_mise_trust_does_not_fail_the_worktree(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "r")
    _commit_mise(repo)
    add_self_origin(repo)
    monkeypatch.setattr(batch.steps.shutil, "which", lambda n: "/bin/mise" if n == "mise" else None)
    monkeypatch.setattr(batch.steps, "_mise_trust",
                        lambda cfg: core.proc.CmdResult(1, "", "not trusted"))
    res = batch.steps.ensure_worktree(str(repo), _branch(repo))
    assert res.ok and Path(res.path).resolve() == repo.resolve()


def test_a_watch_run_reads_ci_for_the_pushed_head_without_fixing():
    assert batch.steps.step_argv(Step.CI, "pr", "/wt", remote_sha="new", watch=True) == \
        ["ci-check", "--head-sha", "new", "--wait", "--repo-dir", "/wt"]


GEN_HEADER = "// Code generated by protoc\n"


def _tracked(repo, rel, content):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(content)
    git_in(repo, "add", rel)
    commit_all(repo, f"add {rel}")


def test_restore_generated_dirt_puts_back_an_unstaged_generated_file(tmp_path):
    repo = init_repo(tmp_path / "r")
    _tracked(repo, "lib-proto/gen/ts/agent_orc_pb.ts", GEN_HEADER + "v1\n")
    (repo / "lib-proto/gen/ts/agent_orc_pb.ts").write_text(GEN_HEADER + "v2\n")
    assert batch.steps.restore_generated_dirt(str(repo)) == [
        "lib-proto/gen/ts/agent_orc_pb.ts"]
    assert (repo / "lib-proto/gen/ts/agent_orc_pb.ts").read_text() == GEN_HEADER + "v1\n"
    assert batch.steps.restore_generated_dirt(str(repo)) == []


def test_restore_generated_dirt_puts_back_a_generated_file_whose_name_has_a_space(tmp_path):
    repo = init_repo(tmp_path / "r")
    rel = "lib-proto/gen/ts/agent orc_pb.ts"
    _tracked(repo, rel, GEN_HEADER + "v1\n")
    (repo / rel).write_text(GEN_HEADER + "v2\n")
    assert batch.steps.restore_generated_dirt(str(repo)) == [rel]
    assert (repo / rel).read_text() == GEN_HEADER + "v1\n"


def test_restore_generated_dirt_leaves_linguist_generated_unstaged_edits(tmp_path):
    repo = init_repo(tmp_path / "r")
    (repo / ".gitattributes").write_text("*.ts linguist-generated=true\n")
    git_in(repo, "add", ".gitattributes")
    commit_all(repo, "mark generated")
    _tracked(repo, "agent_orc_pb.ts", "no header\nv1\n")
    (repo / "agent_orc_pb.ts").write_text("no header\nv2\n")
    assert batch.steps.restore_generated_dirt(str(repo)) == ["agent_orc_pb.ts"]
    assert (repo / "agent_orc_pb.ts").read_text() == "no header\nv1\n"


def test_restore_generated_dirt_refuses_untracked_or_staged_or_nongenerated(tmp_path):
    repo = init_repo(tmp_path / "r")
    _tracked(repo, "gen.ts", GEN_HEADER + "v1\n")
    (repo / "gen.ts").write_text(GEN_HEADER + "v2\n")
    (repo / "scratch.txt").write_text("untracked\n")
    assert batch.steps.restore_generated_dirt(str(repo)) is None
    assert (repo / "gen.ts").read_text() == GEN_HEADER + "v2\n"

    git_in(repo, "clean", "-f")
    git_in(repo, "add", "gen.ts")
    assert batch.steps.restore_generated_dirt(str(repo)) is None

    git_in(repo, "restore", "--staged", "--worktree", "--", "gen.ts")
    _tracked(repo, "hand.ts", "not generated\nv1\n")
    (repo / "hand.ts").write_text("not generated\nv2\n")
    assert batch.steps.restore_generated_dirt(str(repo)) is None


def test_restore_generated_dirt_treats_a_missing_tree_as_dirty(tmp_path):
    assert batch.steps.restore_generated_dirt(str(tmp_path / "gone")) is None
