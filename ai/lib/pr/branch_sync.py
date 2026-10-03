"""Push the branch ``pr create`` is about to open a PR from.

Replaces the bash ``push_branch`` that ``lib/ai/pr.sh`` once carried: a
missing remote ref is a first push with ``--set-upstream``, an existing
tracking ref is compared to HEAD, and behind / diverged refuse rather than
overwrite. The push itself is
``git.push.push(gated=False)`` — running ``pr create`` is the publish
decision, and the owner in ``git.push`` is what confirms the remote moved.

The new-branch probe is ``git ls-remote <remote> refs/heads/<branch>`` with
the full ref (D8): a remote ``feat/auth`` must not make a local ``feat`` look
already published. A push whose status is ``UNVERIFIED`` is a warning, not a
failure to open the PR (D9).

A git read that fails — an unreachable remote, a fetch that broke, a
``rev-parse`` that cannot resolve — is ``FAILED`` naming the command, never
read as "absent" or "equal". The bash version let each of those fall
through to a push or to "up to date".
"""

# doc-group: publishing

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import git.client
import git.push
from core.proc import CmdResult
from core.trail import Trail
from git.push import PushResult, PushStatus

# `git_remote` and `gitenv` are workbench-wide modules rather than `ai/lib`
# ones. In a checkout that is one directory up; in the otto-ai-tools tarball,
# which flattens both into one `lib/`, it is one directory up from this file
# too.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402
import gitenv  # noqa: E402

GIT_REMOTE = git_remote.GIT_REMOTE

MSG_NEW = "→ Pushing new branch to remote..."
MSG_UP_TO_DATE = "✓ Branch is up to date with remote"
MSG_BEHIND = "✗ Remote has commits not in local branch — please pull first: git pull"
MSG_AHEAD = "→ Local has unpushed commits, pushing..."
MSG_DIVERGED = (
    "✗ Branch has diverged from remote\n"
    "→ Fix with: git pull --rebase or git reset"
)

_FAILED = frozenset({PushStatus.REFUSED, PushStatus.HELD, PushStatus.LOST})


class SyncOutcome(StrEnum):
    """What ``sync_branch`` decided, independent of whether the push verified."""

    PUSHED_NEW = "pushed_new"
    PUSHED = "pushed"
    UP_TO_DATE = "up_to_date"
    BEHIND = "behind"
    DIVERGED = "diverged"
    FAILED = "failed"


@dataclass(frozen=True)
class SyncResult:
    """The decision, the Appendix A line, and the push when one ran."""

    outcome: SyncOutcome
    message: str
    push: PushResult | None

    @property
    def ok(self) -> bool:
        """Safe to open the PR: pushed, already there, or unverified (D9).

        An unverified push carries the outcome it was attempting —
        ``PUSHED`` or ``PUSHED_NEW`` — so the outcome alone answers D9.
        """
        return self.outcome in {
            SyncOutcome.PUSHED_NEW, SyncOutcome.PUSHED, SyncOutcome.UP_TO_DATE,
        }


def _emit(msg: str) -> None:
    """Print an Appendix A line as bash ``echo`` did — the line already
    carries →/✓/✗, so ``core.log`` would double the prefix.
    """
    print(msg, file=sys.stderr, flush=True)


def _nv(no_verify: bool) -> tuple[str, ...]:
    return ("--no-verify",) if no_verify else ()


def _failed(what: str, r: CmdResult) -> SyncResult:
    """A git read that could not answer, reported as such rather than as "no".

    stderr is what git explains itself on; a git that wrote nothing still has
    its exit code, which is then the only evidence there is.
    """
    detail = r.detail or f"exit {r.returncode}"
    return SyncResult(SyncOutcome.FAILED, f"✗ {what}: {detail}", None)


def _remote_has_branch(r: CmdResult, branch: str) -> bool:
    """True when the ``ls-remote`` answer *r* names exactly ``refs/heads/<branch>``.

    The full ref is the probe (D8). ``git ls-remote origin feat`` also
    answers with ``refs/heads/feat/auth``; the suffix match is what made a
    new branch look published. The name is compared again because a pattern
    still matches any ref whose trailing components spell it.
    """
    ref = f"refs/heads/{branch}"
    return any(
        line.partition("\t")[2].strip() == ref for line in r.stdout.splitlines()
    )


def _push(
    wt: Path,
    branch: str,
    *,
    remote: str,
    args: tuple[str, ...],
    outcome: SyncOutcome,
    message: str,
    log: Callable[[str], None],
    trail: Trail | None,
    env: dict[str, str] | None = None,
) -> SyncResult:
    if message:
        log(message)
    result = git.push.push(
        wt, gated=False, branch=branch, remote=remote, args=args, trail=trail,
        env=env,
    )
    if result.status in _FAILED:
        return SyncResult(SyncOutcome.FAILED, result.output or message, result)
    if result.status is PushStatus.UNVERIFIED:
        git.push.report(result, wt)
    return SyncResult(outcome, message, result)


def sync_branch(
    wt: Path,
    branch: str,
    *,
    no_verify: bool,
    remote: str = GIT_REMOTE,
    log: Callable[[str], None] | None = None,
    trail: Trail | None = None,
) -> SyncResult:
    """Push *branch* from *wt* when local is ahead or the ref is new.

    ``log`` is called with the progress line *before* the push runs, so the
    operator sees ``→ Pushing new branch…`` while git is still in flight.
    The same wording is on ``SyncResult.message`` for the caller to print
    after. Defaults to stderr, not ``core.log.info``, because the strings
    already carry their own →/✓/✗ prefix. ``trail`` is handed to
    ``git.push.push`` so the push is recorded on the caller's trail.
    """
    emit = log or _emit
    nv = _nv(no_verify)

    # A probe that failed is not a branch that is absent: reading it as one
    # pushes with --set-upstream into a remote nobody could reach.
    # Every git child here runs with the inherited overrides dropped: a hook that
    # invoked `pr create` may export `GIT_DIR`, and with it set these reads
    # would answer for the hook's repo instead of *wt*.
    env = gitenv.git_env_clear()
    probe = git.client.run("ls-remote", remote, f"refs/heads/{branch}", cwd=wt, env=env)
    if not probe.ok:
        return _failed(f"Could not reach {remote}", probe)
    if not _remote_has_branch(probe, branch):
        return _push(
            wt, branch, remote=remote,
            args=("--set-upstream", remote, branch, *nv),
            outcome=SyncOutcome.PUSHED_NEW, message=MSG_NEW, log=emit, trail=trail,
            env=env,
        )

    fetched = git.client.run("fetch", remote, branch, "--quiet", cwd=wt, env=env)
    if not fetched.ok:
        return _failed(f"Could not fetch {remote}/{branch}", fetched)

    if not git.client.run("rev-parse", "--verify", "@{u}", cwd=wt, env=env).ok:
        # Bash printed nothing extra here — just pushed without --set-upstream.
        return _push(
            wt, branch, remote=remote,
            args=(*nv, remote, branch),
            outcome=SyncOutcome.PUSHED, message="", log=emit, trail=trail,
            env=env,
        )

    # Read through `run`, not `out`: `out` answers "" on failure, so two failed
    # reads compare equal and an unreadable branch reports as up to date.
    shas: list[str] = []
    for argv in (("rev-parse", "HEAD"), ("rev-parse", "@{u}"),
                 ("merge-base", "HEAD", "@{u}")):
        r = git.client.run(*argv, cwd=wt, env=env)
        if not r.ok:
            return _failed(f"git {' '.join(argv)} failed", r)
        shas.append(r.stdout.strip())
    local_sha, remote_sha, base_sha = shas

    if local_sha == remote_sha:
        return SyncResult(SyncOutcome.UP_TO_DATE, MSG_UP_TO_DATE, None)
    if local_sha == base_sha:
        return SyncResult(SyncOutcome.BEHIND, MSG_BEHIND, None)
    if remote_sha == base_sha:
        return _push(
            wt, branch, remote=remote,
            args=(*nv, remote, branch),
            outcome=SyncOutcome.PUSHED, message=MSG_AHEAD, log=emit, trail=trail,
            env=env,
        )
    return SyncResult(SyncOutcome.DIVERGED, MSG_DIVERGED, None)
