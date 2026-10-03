"""The child `pr` processes a batch run spawns, and the worktrees they run in.

Every step is its own process so concurrent steps share no interpreter state,
and each gets a new session with stdin closed: no prompt in any child can
reach a terminal, whether the batch runs under a server or in a shell.
"""

# doc-group: batch

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import core.timeouts
import core.trail
import git.client
import git.topology
from batch.model import Step


def step_argv(step: Step, pr_bin: str, worktree: str, *, publish: bool) -> list[str]:
    if step is Step.REBASE:
        argv = [pr_bin, "rebase", "--fix"] + ([] if publish else ["--no-push"])
    elif step is Step.COMMENTS:
        argv = [pr_bin, "comments", "--fix"] + (["--finish", "--post"] if publish else [])
    else:
        argv = [pr_bin, "review", "--self", "--fix", "--force"] + (["--push"] if publish else [])
    return argv + ["--repo-dir", worktree]


@dataclass(frozen=True)
class WorktreeResult:
    path: str
    dirty: bool
    error: str

    @property
    def ok(self) -> bool:
        return not self.error


def ensure_worktree(repo_dir: str, branch: str) -> WorktreeResult:
    found = git.topology.find_worktree_for_branch(branch, cwd=repo_dir)
    path = str(found) if found else git.topology.wt_switch(branch, cwd=repo_dir)
    if not path:
        return WorktreeResult("", False, f"could not create a worktree for {branch}")
    return WorktreeResult(path, git.client.is_dirty(cwd=path), "")


class StepProcess:
    # How long a SIGTERM is given to land before a repeated kill() escalates
    # to SIGKILL. A class attribute so a test can shrink it instead of
    # waiting out the real grace window.
    KILL_GRACE_S = core.timeouts.QUICK

    def __init__(self, popen: subprocess.Popen, log_path: Path):
        self._popen = popen
        self._lines: queue.Queue[str] = queue.Queue()
        self._out: list[str] = []
        self._kill_sent_at: float | None = None
        self.pid = popen.pid
        self._readers = [
            threading.Thread(target=self._read_err, args=(log_path,), daemon=True),
            threading.Thread(target=self._read_out, daemon=True),
        ]
        for t in self._readers:
            t.start()

    @classmethod
    def start(cls, argv: list[str], *, log_path: Path, trail_root: str) -> StepProcess:
        env = dict(os.environ)
        env[core.trail.TRAIL_ROOT_ENV] = trail_root
        popen = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env,
                                 start_new_session=True)
        return cls(popen, log_path)

    def _read_err(self, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as log:
            for line in self._popen.stderr:
                log.write(line)
                log.flush()
                self._lines.put(line.rstrip("\n"))

    def _read_out(self) -> None:
        self._out.append(self._popen.stdout.read())

    def poll(self) -> int | None:
        code = self._popen.poll()
        if code is not None:
            for t in self._readers:
                t.join(timeout=core.timeouts.QUICK)
        return code

    def drain_lines(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self._lines.get_nowait())
            except queue.Empty:
                return out

    def stdout(self) -> str:
        return "".join(self._out)

    def kill(self) -> None:
        """Signal the process group, escalating to SIGKILL once TERM has had its chance.

        Cancel and interrupt both poll by calling this again every tick while the
        process stays alive, so a first call sends SIGTERM and starts the grace
        window; a later call past ``KILL_GRACE_S`` sends SIGKILL instead, so a
        child that ignores or is slow to act on TERM still ends.
        """
        now = time.monotonic()
        if self._kill_sent_at is None:
            self._kill_sent_at = now
            sig = signal.SIGTERM
        elif now - self._kill_sent_at >= self.KILL_GRACE_S:
            sig = signal.SIGKILL
        else:
            sig = signal.SIGTERM
        try:
            os.killpg(self._popen.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS answers EPERM rather than ESRCH when the only process left in the
            # group is the step's own unreaped zombie: it has already exited. Reaping
            # it confirms that; a step still running means the signal was refused.
            if self._popen.poll() is None:
                raise
