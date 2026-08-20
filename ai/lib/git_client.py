"""One way to run git, and the reads every caller was hand-rolling.

`ai/` invoked `git` as a literal argv head in 131 places across 18 files, and
each one re-decided the same four things: whether to pass `-C` or `cwd=`,
whether to capture, whether a non-zero exit is a failure or an answer, and what
to do with stderr. The spread is why a fix applied to one call site — a
timeout, a retry, quoting non-ASCII paths — was never a fix for the other
hundred and thirty.

The runner is `run`, and `out`, `ok` and `lines` are the three shapes callers
actually wanted from it. Below them sit the reads that appeared at two or more
call sites; a read used once belongs at its call site, spelled out with `run`.

`out` returning `default` on a non-zero exit is the one place here that
discards a failure, and it is deliberate: it is what the wrappers it replaces
already did, because most of these reads are questions with a reasonable
"don't know" answer. When the exit code or stderr matters — and for a write it
always does — call `run` and read the `CmdResult`.

Writes are not modelled beyond `run`. Committing and pushing gets an owner of
its own, with the publishing gate over it, rather than a convenience wrapper
here that would turn four gate-less push sites into five.

Depends on `proc` and nothing else. Whether a failed read is worth logging is
the caller's decision, and most of them have already decided it is not.
"""

from __future__ import annotations

from pathlib import Path

import proc
from proc import CmdResult

# Subcommands whose output is a list of paths. git escapes a non-ASCII name in
# that output unless told otherwise, and an escaped name is not a pathspec a
# later `git add` can resolve — so a fix touching such a file is staged as
# nothing and reported as done. `review_fix` passed this flag by hand at one of
# the sites that needed it; applying it to the subcommand rather than to the
# caller is what stops the next one from forgetting.
_PATH_LISTING = frozenset({"diff", "ls-files", "status"})


def _argv(args: tuple[str, ...], config: dict[str, str] | None) -> list[str]:
    """Build the full argv, including any `-c key=value` overrides."""
    settings = dict(config or {})
    if args and args[0] in _PATH_LISTING:
        settings.setdefault("core.quotePath", "false")
    prefix: list[str] = []
    for key, value in settings.items():
        prefix.extend(["-c", f"{key}={value}"])
    return ["git", *prefix, *args]


def run(
    *args: str,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    config: dict[str, str] | None = None,
) -> CmdResult:
    """Run git with *args* in *cwd*, capturing both streams.

    Never raises on a non-zero exit — for git that is routine, since
    `diff --quiet`, `cat-file -e` and `rev-parse --verify` all answer a
    question with their exit code.
    """
    return proc.run(_argv(args, config), cwd=cwd, timeout=timeout)


def out(
    *args: str,
    cwd: str | Path | None = None,
    default: str = "",
    config: dict[str, str] | None = None,
) -> str:
    """Stripped stdout, or *default* when git exited non-zero."""
    r = run(*args, cwd=cwd, config=config)
    return r.stdout.strip() if r.ok else default


def ok(*args: str, cwd: str | Path | None = None) -> bool:
    """Whether git exited zero, for the subcommands that answer that way."""
    return run(*args, cwd=cwd).ok


def lines(
    *args: str, cwd: str | Path | None = None, config: dict[str, str] | None = None,
) -> list[str]:
    """Stdout split into non-empty lines, or empty when git exited non-zero."""
    return [line for line in out(*args, cwd=cwd, config=config).splitlines() if line]


# ── Reads ───────────────────────────────────────────────────────────────────

def head_sha(cwd: str | Path | None = None, *, short: bool = False) -> str:
    """The commit HEAD points at, or "" in a repo with no commits."""
    args = ("rev-parse", "--short", "HEAD") if short else ("rev-parse", "HEAD")
    return out(*args, cwd=cwd)


def current_branch(cwd: str | Path | None = None) -> str:
    """The checked-out branch, "HEAD" when detached, or "" on failure.

    Detached is a real state here rather than an error: review worktrees are
    created that way on purpose, and `--abbrev-ref` reports it as "HEAD".
    """
    return out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)


def is_dirty(cwd: str | Path | None = None) -> bool:
    """Whether the tree has staged, unstaged, or untracked changes.

    Porcelain rather than `diff --quiet`: an agent that only adds files —
    tests, fixtures — leaves the tracked diff empty, and a diff-only gate then
    skips the commit while the caller still reports the work as done.
    """
    return bool(out("status", "--porcelain", cwd=cwd))


def commit_exists(sha: str, cwd: str | Path | None = None) -> bool:
    """Whether *sha* resolves to a commit object in this repo."""
    return ok("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd)
