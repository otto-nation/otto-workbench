"""Chain workbench git hooks in front of a repo that set ``core.hooksPath``.

The rewrite record ``git.rewrites`` reads is written only by the workbench
``post-rewrite`` hook. A repo that points ``core.hooksPath`` at its own
directory (``.githooks``, with no ``post-rewrite``) hides that hook, so a
rebase there is never recorded. ``pr rebase`` cannot change the repo's
setting — a hand-run ``git rebase --continue`` would still miss it — but
the three git children it drives can pass ``-c core.hooksPath=<wrapper>``.

The wrapper is a temp directory that lasts for one git child:

* ``post-rewrite``, ``prepare-commit-msg``, and ``pre-commit`` run the
  workbench hook first, then the repo's own if it is executable, with stdin
  replayed to both.
* every other hook file in the repo directory is forwarded as a symlink, so
  a name this process did not know about still runs. Directories are not
  forwarded, and ``$0`` in a forwarded hook is the wrapper path, so a repo
  hook that finds siblings (``.githooks/lib.sh``) via ``dirname "$0"`` will
  not find them during ``pr rebase``.

``core.hooksPath=/dev/null`` (or any non-directory) is honoured: no wrap.
When the effective path is already the workbench hooks dir, the config is
left alone. :func:`warn_if_shadowed` is the one warning at ``pr rebase``
start; it names what a hand-run git will miss.
"""

# doc-group: platform

from __future__ import annotations

import shlex
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import core.log
import git.client

# Matches `GIT_HOOKS_DIR` in lib/constants.sh — `step_global_hooks` installs
# here and sets the global `core.hooksPath` to it.
WORKBENCH_HOOKS_DIR = Path.home() / ".git-hooks"

# The checkout copies `~/.git-hooks` is a symlink into. Invoked when the
# installed dir has not been created (tests, a machine that has not synced).
WORKBENCH_HOOKS_SRC = Path(__file__).resolve().parents[3] / "git" / "hooks"

CHAIN_HOOK = Path(__file__).with_name("chain_hook.sh")

# Rebase children run these. pre-commit is included for parity with the
# installed global dir; `rebase --continue` uses `-n` and skips it.
CHAINED_HOOKS = ("post-rewrite", "prepare-commit-msg", "pre-commit")


def _hooks_path(cwd: str) -> Path | None:
    """The worktree's effective ``core.hooksPath``, or None when unset.

    Relative values are resolved against the worktree toplevel, which is how
    a repo's ``core.hooksPath=.githooks`` is stored and how git finds it.
    """
    raw = git.client.out("config", "--type=path", "--get", "core.hooksPath", cwd=cwd).strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    top = git.client.out("rev-parse", "--show-toplevel", cwd=cwd).strip()
    return (Path(top) / path) if top else Path(cwd) / path


def _is_workbench(path: Path) -> bool:
    resolved = path.resolve()
    return resolved in {
        WORKBENCH_HOOKS_DIR.expanduser().resolve(),
        WORKBENCH_HOOKS_SRC.resolve(),
    }


def _disabled(path: Path) -> bool:
    return not path.is_dir()


def warn_if_shadowed(cwd: str) -> None:
    """Warn once at ``pr rebase`` start when local hooks hide the workbench's.

    A missing *cwd* is a stub in a unit test, not a repo — skip rather than
    raise. Fail open on a git that will not answer: the rebase still runs.
    """
    if not Path(cwd).is_dir():
        return
    try:
        path = _hooks_path(cwd)
    except OSError:
        return
    if path is None or _is_workbench(path):
        return
    shown = str(path)
    if _disabled(path):
        core.log.warn(
            f"core.hooksPath is {shown} — git hooks are disabled, so rebase "
            "rewrites are not recorded and `pr rebase` will not wrap them."
        )
        return
    core.log.warn(
        f"This repo's core.hooksPath ({shown}) shadows the workbench hooks. "
        "Hand-run git will not record rewrites for history following, "
        "refuse a lossy conflict resolution (prepare-commit-msg), "
        "report dropped commits (post-rewrite), or run pre-push "
        "gitleaks/push-intents. `pr rebase` chains the workbench hooks "
        "for this run."
    )


def _write_chain(wrapper: Path, name: str, repo_hooks: Path) -> None:
    # The checkout copy, not ~/.git-hooks: that install is a symlink into
    # whichever worktree last synced, which is not this process's code.
    wb = WORKBENCH_HOOKS_SRC / name
    repo = repo_hooks / name
    hook = wrapper / name
    hook.write_text(
        "#!/usr/bin/env bash\n"
        f"export WORKBENCH_HOOK={shlex.quote(str(wb))}\n"
        f"export REPO_HOOK={shlex.quote(str(repo))}\n"
        f"exec /usr/bin/env bash {shlex.quote(str(CHAIN_HOOK))} \"$@\"\n"
    )
    hook.chmod(0o755)


def _forward_other_hooks(wrapper: Path, repo_hooks: Path) -> None:
    for entry in sorted(repo_hooks.iterdir()):
        if entry.name in CHAINED_HOOKS or not entry.is_file():
            continue
        dest = wrapper / entry.name
        if dest.exists():
            continue
        dest.symlink_to(entry)


def _build_wrapper(wrapper: Path, repo_hooks: Path) -> None:
    for name in CHAINED_HOOKS:
        _write_chain(wrapper, name, repo_hooks)
    _forward_other_hooks(wrapper, repo_hooks)


@contextmanager
def override(cwd: str) -> Iterator[dict[str, str]]:
    """Extra ``-c`` settings for one rebase git child, then clean up.

    Yields ``{}`` when there is nothing to wrap, or
    ``{"core.hooksPath": <temp dir>}`` while that dir lives. Merge into
    ``REBASE_CONFIG`` at the call site.
    """
    try:
        path = _hooks_path(cwd)
    except OSError:
        yield {}
        return
    if path is None or _is_workbench(path) or _disabled(path):
        yield {}
        return
    with tempfile.TemporaryDirectory(prefix="wb-rebase-hooks-") as tmp:
        _build_wrapper(Path(tmp), path)
        yield {"core.hooksPath": tmp}
