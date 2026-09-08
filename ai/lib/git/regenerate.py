"""Lockfile regeneration registry and runner.

Owns the mapping from lockfile basenames to the commands that rebuild them,
the mise detection that decides how to invoke those commands, and the runner
that stages the result.  Everything here is repo-agnostic — it knows what
*kind* of file a lockfile is, not which repo it belongs to.

Higher layers (``rebase``) own the repo-specific half: which regeneration
commands a repo declares, how generated files are queued for rebuild, and
how stale files are reported.  That split is what lets ``git.land`` import
this module without pulling in ``config`` or ``pr``.
"""

# doc-group: platform

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from core import log
from core import proc
from core import timeouts
from core.proc import CmdResult
from core.trail import Trail
from git import client as git_client


# ── Data types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Regenerator:
    """A command that rebuilds a generated file from its source."""
    cmd: tuple[str, ...]
    stage_dir: bool = False


@dataclass
class RegenJob:
    """A deferred regeneration command and the files it covers."""
    regen_dir: str
    cmd: tuple[str, ...]
    stage_dir: bool = False
    files: list[str] = field(default_factory=list)


class RegenQueue:
    """Deferred regeneration commands, deduplicated by (directory, command).

    Also holds the complement — generated files no command was found for.
    They are staged from the incoming side and never rebuilt, which is the
    same end state as a regeneration that failed, so they belong in the same
    stale report rather than passing as cleanly resolved.
    """

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, tuple[str, ...]], RegenJob] = {}
        self.unrebuildable: list[str] = []

    def add(
        self, regen_dir: Path, filepath: str,
        cmd: tuple[str, ...], stage_dir: bool = False,
    ) -> None:
        key = (str(regen_dir), cmd)
        job = self._jobs.setdefault(
            key, RegenJob(regen_dir=str(regen_dir), cmd=cmd, stage_dir=stage_dir),
        )
        job.files.append(filepath)

    def mark_unrebuildable(self, filepath: str) -> None:
        self.unrebuildable.append(filepath)

    def __iter__(self) -> Iterator[RegenJob]:
        return iter(self._jobs.values())


# ── Lockfile registry ───────────────────────────────────────────────────────

# Keys are basenames — find_regenerator looks up os.path.basename(filepath),
# so a key containing a path separator would never match.
# yarn and bun have no lockfile-only flag in the versions we target, so they
# run a full install and its lifecycle scripts as a side effect of resolution.
LOCKFILE_REGENERATORS: dict[str, Regenerator] = {
    "go.sum":            Regenerator(("go", "mod", "tidy"), stage_dir=True),
    "pnpm-lock.yaml":    Regenerator(("pnpm", "install", "--lockfile-only")),
    "package-lock.json": Regenerator(("npm", "install", "--package-lock-only")),
    "yarn.lock":         Regenerator(("yarn", "install")),
    "bun.lock":          Regenerator(("bun", "install")),
    "bun.lockb":         Regenerator(("bun", "install")),
    "Cargo.lock":        Regenerator(("cargo", "generate-lockfile")),
    "uv.lock":           Regenerator(("uv", "lock")),
    "poetry.lock":       Regenerator(("poetry", "lock", "--no-update")),
    "composer.lock":     Regenerator(("composer", "update", "--lock")),
    "Gemfile.lock":      Regenerator(("bundle", "lock")),
}


def find_regenerator(filepath: str) -> Regenerator | None:
    """Look up a regeneration command by lockfile basename."""
    return LOCKFILE_REGENERATORS.get(os.path.basename(filepath))


# ── Mise detection ──────────────────────────────────────────────────────────

# mise accepts any of these config filenames; a project may use any one of
# them, so checking only the undotted mise.toml misses most real repos.
MISE_CONFIG_NAMES = (
    "mise.toml",
    ".mise.toml",
    "mise.local.toml",
    ".mise.local.toml",
    ".config/mise.toml",
    ".config/mise/config.toml",
    ".mise/config.toml",
    "mise/config.toml",
    ".tool-versions",
)


def detect_mise(target_dir: str, repo_root: str) -> bool:
    """Check if mise is available and configured for this directory."""
    if not shutil.which("mise"):
        return False
    d = Path(target_dir).resolve()
    root = Path(repo_root).resolve()
    while True:
        if any((d / name).is_file() for name in MISE_CONFIG_NAMES):
            return True
        if d == root or d.parent == d:
            return False
        d = d.parent


# ── Subprocess runner ───────────────────────────────────────────────────────


def try_run(
    cmd: list[str], *, cwd: str, timeout: float | None = timeouts.UNBOUNDED,
) -> CmdResult | None:
    """Run a command, returning None if it could not be launched or finished.

    subprocess.run raises instead of exiting 127 when the executable is absent,
    not executable, or cwd is unusable. All of these mean the command never
    ran, so the caller should fall back or report the file as stale. An expired
    ``timeout`` is the same answer for the same reason: no usable output.

    Unbounded by default because the caller that omits a bound is running a
    project's own regeneration command — an arbitrary build step whose runtime
    belongs to the project, not to us. Callers with a bound take a tier from
    ``timeouts``; see that module for why the number is not theirs to pick.
    """
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout,
        )
        return CmdResult(p.returncode, p.stdout or "", p.stderr or "")
    except OSError as exc:
        log.dim(f"Could not launch {' '.join(cmd)}: {exc}")
        return None
    except subprocess.TimeoutExpired:
        log.dim(f"Timed out after {timeout:g}s: {' '.join(cmd)}")
        return None


# ── Regeneration runner ─────────────────────────────────────────────────────


def run_regeneration(
    job: RegenJob, *, cwd: str, trail: Trail | None = None,
) -> bool:
    """Run a regeneration command and stage the result. Returns success."""
    repo_root = git_client.out("rev-parse", "--show-toplevel", cwd=cwd) or cwd

    cmd = list(job.cmd)
    use_mise = detect_mise(job.regen_dir, repo_root)
    run_cmd = ["mise", "exec", "--"] + cmd if use_mise else cmd

    log.info(f"Regenerating: {' '.join(run_cmd)} (in {Path(job.regen_dir).name}/)")
    r = try_run(run_cmd, cwd=job.regen_dir)

    # A missing binary raises FileNotFoundError rather than reporting the
    # command-not-found code, and a tool-managed binary is only on PATH inside
    # `mise exec`. Retry there.
    missing = r is None or r.returncode == proc.MISSING_RETURNCODE
    if missing and not use_mise and shutil.which("mise"):
        use_mise = True
        run_cmd = ["mise", "exec", "--"] + cmd
        log.info(f"Retrying with mise: {' '.join(run_cmd)}")
        r = try_run(run_cmd, cwd=job.regen_dir)

    if r is None:
        if trail:
            trail.error("regeneration", f"could not run {cmd[0]} in {job.regen_dir}",
                        data={"cmd": cmd})
        log.warn(f"Regeneration failed: could not run {cmd[0]}")
        return False

    if r.returncode != 0:
        if trail:
            trail.failure("regeneration", f"{' '.join(cmd)} failed in {job.regen_dir}",
                          output=r.combined_output,
                          data={"cmd": cmd, "exit_code": r.returncode})
        log.warn(f"Regeneration failed: {' '.join(cmd)} (exit {r.returncode})")
        if r.stderr.strip():
            log.dim(r.stderr.strip()[:proc.DETAIL_LIMIT])
        return False

    if job.stage_dir:
        if not git_client.ok("add", "-u", ".", cwd=job.regen_dir):
            if trail:
                trail.error("regeneration", f"git add -u failed in {job.regen_dir}")
            log.warn(f"git add -u failed after regeneration in {Path(job.regen_dir).name}/")
            return False
    else:
        ok = True
        for f in job.files:
            if not git_client.ok("add", f, cwd=cwd):
                ok = False
        if not ok:
            return False

    if trail:
        trail.info("regeneration", f"regenerated {', '.join(job.files)}",
                   data={"cmd": cmd, "dir": job.regen_dir, "mise": use_mise})
    log.ok(f"Regenerated: {', '.join(job.files)}")
    return True
