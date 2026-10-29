"""The child processes a batch run spawns — `pr`, or `ci-check` for CI — and their worktrees.

Every step is its own process so concurrent steps share no interpreter state,
and each gets a new session with stdin closed: no prompt in any child can
reach a terminal, whether the batch runs under a server or in a shell.
"""

# doc-group: batch

from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import core.log
import core.proc
import core.timeouts
import core.trail
import git.client
import git.regenerate
import git.topology
import rebase.conflicts
from batch.model import Step


def step_argv(step: Step, pr_bin: str, worktree: str, *, remote_sha: str = "",
              wait: bool = False, fork_point: str = "",
              focus: Sequence[str] = ()) -> list[str]:
    """The child process for one step. Every step runs drafted — nothing here pushes
    or posts — because publishing is the batch's own decision, made once per item
    against the tree.

    The CI step runs `ci-check`, from the same directory as *pr_bin*, rather than
    `pr ci`: the `pr` dispatcher fetches into the repo's shared `.git` before the
    command starts, and a CI step that waits on GitHub must hold no git lock.

    A stacked child's rebase whose parent was rewritten in this run passes
    `--fork-point` so only the child's unique commits replay; never `--force`.
    Only the review step takes *focus*.
    """
    if step is Step.REBASE:
        argv = [pr_bin, "rebase", "--fix", "--no-push"]
        if fork_point:
            argv += ["--fork-point", fork_point]
    elif step is Step.CI:
        # ci-check claims the run lock itself and makes no startup fetch.
        # --no-rebase because the batch rebases in its own step
        # (and with it ci-check fetches nothing), and --head-sha because after
        # a drafted rebase local HEAD is a commit GitHub has no runs for.
        ci_bin = str(Path(pr_bin).with_name("ci-check"))
        argv = [ci_bin, "--fix", "--no-rebase"]
        if remote_sha:
            argv += ["--head-sha", remote_sha]
        if wait:
            argv.append("--wait")
    elif step is Step.COMMENTS:
        argv = [pr_bin, "comments", "--fix"]
    else:
        argv = [pr_bin, "review", "--self", "--fix", "--force"]
        argv += [f"--focus={text}" for text in focus]
    return argv + ["--repo-dir", worktree]


@dataclass(frozen=True)
class WorktreeResult:
    path: str
    dirty: bool
    error: str

    @property
    def ok(self) -> bool:
        return not self.error


def _porcelain_z_entries(stdout: str) -> list[tuple[str, str]] | None:
    """Parse ``git status --porcelain -z`` into ``(xy, path)`` pairs.

    Rename and copy entries carry a second path field after the first NUL.
    Those still mean dirty — the pair is returned with ``xy`` containing
    ``R`` or ``C`` so the caller can refuse, rather than dropping the entry.
    Unparseable output is None (fail closed).
    """
    entries: list[tuple[str, str]] = []
    parts = stdout.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        if len(entry) < 3 or entry[2] != " ":
            return None
        xy, path = entry[:2], entry[3:]
        has_second_path = "R" in xy or "C" in xy
        if has_second_path and i >= len(parts):
            return None
        if has_second_path:
            i += 1
        entries.append((xy, path))
    return entries


def restore_generated_dirt(cwd: str) -> list[str] | None:
    """Restore generated-only unstaged modifications in *cwd*.

    Returns the restored paths (empty if the tree was already clean), or None
    when the tree is dirty in any other way — untracked files, staged changes,
    a non-generated path, a mix, a rename or copy — including a ``status`` git
    could not read, which is treated as dirty for the same reason as
    ``git.client.is_dirty``. Only unstaged modifications of tracked generated
    files (porcelain `` M``) are restored, via ``git restore``.

    Status is read with ``-z`` so a path that git would otherwise quote
    (spaces, non-ASCII) is the real path, not a quoted string that fails the
    generated check and is never restored.
    """
    try:
        status = git.client.run("status", "--porcelain", "-z", cwd=cwd)
    except FileNotFoundError:
        return None
    if not status.ok:
        return None
    parsed = _porcelain_z_entries(status.stdout)
    if parsed is None:
        return None
    if not parsed:
        return []
    paths = []
    for xy, path in parsed:
        if xy != " M":
            return None
        if rebase.conflicts.is_generated_file(path, Path(cwd) / path, cwd) is None:
            return None
        paths.append(path)
    restored = git.client.run("restore", "--", *paths, cwd=cwd)
    if not restored.ok:
        return None
    return paths


def _default_rev(cwd: str) -> str | None:
    """The commit-ish to read default-branch content from, or None.

    ``origin/<default>`` when that ref exists, else the local default branch.
    Either missing is a comparison failure, not a reason to trust.
    """
    default = git.topology.default_branch(cwd)
    for rev in (f"origin/{default}", default):
        if git.client.ok("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", cwd=cwd):
            return rev
    return None


def _same_bytes_as_default(cfg: Path, worktree: str) -> str | None:
    """Empty if *cfg* matches the default branch; otherwise why it must not be trusted.

    Never raises: a comparison that cannot be made is not a match.
    """
    try:
        rel = cfg.resolve().relative_to(Path(worktree).resolve()).as_posix()
        rev = _default_rev(worktree)
        if rev is None:
            return f"could not resolve the default branch in {worktree}"
        tree = git.client.run("rev-parse", f"{rev}:{rel}", cwd=worktree)
        if not tree.ok:
            return f"{rel} is absent on {rev}"
        work = git.client.run("hash-object", str(cfg), cwd=worktree)
        if not work.ok:
            return f"could not hash {cfg}"
        if tree.stdout.strip() != work.stdout.strip():
            return f"{rel} differs from {rev}"
        return None
    except Exception as exc:
        return f"could not compare {cfg} to the default branch: {exc}"


def trust_mise_config(worktree: str) -> None:
    """Trust mise config files in *worktree* so a child hook can run tasks.

    Only a file whose bytes match the same path on the repo's default branch
    is trusted. ``author:@me`` is not enough: PR content can come from a fork
    or a collaborator commit, and trusting lets mise run arbitrary tasks that
    persist in the user's trust store. A file that differs, is absent on the
    default branch, or whose comparison fails is left untrusted — the reason
    is logged and the item still starts. A failed ``mise trust`` is the same:
    logged, never fatal; an untrusted config surfaces as the hook's own error.
    """
    if not shutil.which("mise"):
        return
    root = Path(worktree)
    for name in git.regenerate.MISE_CONFIG_NAMES:
        if not name.endswith(".toml"):
            continue
        cfg = root / name
        if not cfg.is_file():
            continue
        reason = _same_bytes_as_default(cfg, worktree)
        if reason is not None:
            core.log.warn(f"mise trust of {cfg} skipped — {reason}")
            continue
        result = _mise_trust(cfg)
        if not result.ok:
            core.log.warn(core.proc.failure_message(
                f"mise trust of {cfg} failed — continuing", result,
            ))


def _mise_trust(cfg: Path) -> core.proc.CmdResult:
    return core.proc.run(
        ["mise", "trust", "--quiet", str(cfg)], timeout=core.timeouts.QUICK,
    )


def checked_out_branch(worktree: str) -> str:
    """The branch *worktree* stands on — the one being rebased while a rebase is
    paused — or "" when git cannot say or HEAD is detached outside a rebase.

    A paused rebase detaches HEAD by design; the branch it will land on is in
    `rebase-merge/head-name` (or `rebase-apply/` for an `am`-style rebase).
    """
    if not worktree or not Path(worktree).is_dir():
        return ""
    current = git.client.current_branch(worktree)
    if current and current != "HEAD":
        return current
    for state in ("rebase-merge", "rebase-apply"):
        path = git.client.out("rev-parse", "--git-path", f"{state}/head-name", cwd=worktree)
        head = Path(worktree, path) if path else None
        if head is not None and head.is_file():
            return head.read_text().strip().removeprefix("refs/heads/")
    return ""


def ensure_worktree(repo_dir: str, branch: str) -> WorktreeResult:
    # git.client raises on a cwd that does not exist; a checkout removed since
    # the plan is a decision for the operator, not a traceback out of the driver.
    if not Path(repo_dir).is_dir():
        return WorktreeResult("", False, f"the checkout {repo_dir} no longer exists")
    found = git.topology.find_worktree_for_branch(branch, cwd=repo_dir)
    path = str(found) if found else git.topology.wt_switch(branch, cwd=repo_dir)
    if not path:
        return WorktreeResult("", False, f"could not create a worktree for {branch}")
    trust_mise_config(path)
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
    def start(cls, argv: list[str], *, log_path: Path, trail_root: str,
              env: dict[str, str] | None = None) -> StepProcess:
        """Spawn *argv* as a step. *env* is its environment — this process's when None —
        and the trail root is set on it either way."""
        env = dict(os.environ if env is None else env)
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

    def kill(self, *, force: bool = False) -> None:
        """Signal the process group, escalating to SIGKILL once TERM has had its chance.

        Cancel and interrupt both poll by calling this again every tick while the
        process stays alive, so a first call sends SIGTERM and starts the grace
        window; a later call past ``KILL_GRACE_S`` sends SIGKILL instead, so a
        child that ignores or is slow to act on TERM still ends.

        ``force`` skips straight to SIGKILL regardless of the grace window. The
        scheduler uses it once the tracked step itself has already exited: the
        "give TERM a chance" concern is about the step we are watching, not about
        whatever it left running in its group, and that group gets no further
        ``kill()`` calls once the step is reaped.
        """
        now = time.monotonic()
        if force:
            sig = signal.SIGKILL
        elif self._kill_sent_at is None:
            self._kill_sent_at = now
            sig = signal.SIGTERM
        elif now - self._kill_sent_at >= self.KILL_GRACE_S:
            sig = signal.SIGKILL
        else:
            sig = signal.SIGTERM
        # Signal the group even when the step itself has exited: anything it left
        # running in the background is still in the group and must end too.
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
