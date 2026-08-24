"""The owner of every push this workbench issues, and the only thing that
checks one landed.

`git` opens the connection to the remote before running `pre-push` and sends the
packfile only once the hook returns, so the socket sits idle for the hook's whole
run. With gates taking minutes, a remote can close it and the push is lost — git
having already reported the parts it did do. An SSH keepalive removes that one
cause and not the class: a transient drop, a remote-side rejection buried in
output, or a hook exiting zero without pushing all end the same way, with a
branch that has no PR and no surviving reason why.

So a push is not finished when git exits. It is finished when the remote is asked
what it holds. That question is `git ls-remote`, it costs one round trip, and its
absence is the whole reason the failure was silent instead of loud.

Five outcomes, because "nothing was pushed" and "the push was lost" are different
problems and reporting both as a failed push is what made them indistinguishable:

| `PushStatus` | What happened |
|---|---|
| `PUSHED` | git exited zero and the remote holds the commit |
| `HELD` | the publishing gate was shut — nothing was attempted |
| `REFUSED` | git exited non-zero — nothing left the machine |
| `LOST` | git exited zero and the remote does not hold the commit |
| `UNVERIFIED` | the remote could not be asked |

`UNVERIFIED` is a warning rather than an error, and it is why `remote_head`
distinguishes "no such ref" from "could not ask": collapsing them would report an
unreachable remote as a branch that was never pushed.

This module pushes, verifies, and reports. It does not commit, and it does not
perform the hook-regenerated-files recovery that `review-threads` and `pr-rebase`
each own — those sit above it, which is what keeps this module's answer to "did
it land" independent of any caller's idea of how to fix it.

`gated` is required and has no default. Only `pr comments` opens the publishing
gate; `pr ci`, `pr rebase` and the review fix pass never do, so a gate-by-default
owner would silently stop three entrypoints pushing, and a `False` default would
let the next call site inherit the wrong answer by omitting the argument.
"""

# doc-group: platform

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import git_client
import log
import publishing
from trail import Trail


class PushStatus(StrEnum):
    """What became of a push. See the module docstring for the full table."""

    PUSHED = "pushed"
    HELD = "held"
    REFUSED = "refused"
    LOST = "lost"
    UNVERIFIED = "unverified"


class Refusal(StrEnum):
    """Why git refused, when it did.

    The distinction that matters to a caller is whether the work is repairable
    where it stands: a hook rejection names something to fix in the tree, a
    divergence names something to reconcile with the remote, and a transport
    failure names nothing the caller did wrong.
    """

    HOOK = "hook"
    DIVERGED = "diverged"
    TRANSPORT = "transport"
    OTHER = "other"


@dataclass(frozen=True)
class PushResult:
    """What a push did, and what the remote says about it.

    `remote_sha` is what `ls-remote` reported at verification time: the pushed
    commit on success, whatever the remote holds instead on `LOST`, and empty
    when the ref is absent or was never asked for.
    """

    status: PushStatus
    sha: str
    branch: str
    remote_sha: str = ""
    refusal: Refusal | None = None
    output: str = ""

    @property
    def ok(self) -> bool:
        """The commit is on the remote."""
        return self.status is PushStatus.PUSHED


_DIVERGED_MARKERS = (
    "! [rejected]",
    "non-fast-forward",
    "fetch first",
    "stale info",
)

_TRANSPORT_MARKERS = (
    "could not read from remote repository",
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "connection closed",
    "authentication failed",
    "permission denied",
    "repository not found",
)

_PUSH_REFUSED = "failed to push some refs"


def classify(output: str) -> Refusal:
    """Why a non-zero push failed, from what git said about it.

    A failing pre-push hook aborts the push locally, so git prints the generic
    "failed to push some refs" with no per-ref "! [rejected]" line and none of
    the transport or auth diagnostics a real network failure carries. That
    absence is the only signal separating a hook rejection from the rest, which
    is why the generic line is checked last: every other cause prints it too.
    """
    lowered = output.lower()
    if any(marker in lowered for marker in _DIVERGED_MARKERS):
        return Refusal.DIVERGED
    if any(marker in lowered for marker in _TRANSPORT_MARKERS):
        return Refusal.TRANSPORT
    if _PUSH_REFUSED in lowered:
        return Refusal.HOOK
    return Refusal.OTHER


def remote_head(
    wt_path: str | Path, branch: str, *, remote: str = "origin",
) -> str | None:
    """The commit *remote* holds for *branch*.

    Three answers, deliberately distinct: a SHA, `""` when the remote has no
    such ref, and `None` when the remote could not be asked at all. Collapsing
    the last two would report an unreachable remote as a branch that was never
    pushed, which is the opposite of what a caller should do about it.

    This asks the remote. `git branch -r --contains` reads the local
    remote-tracking ref, which a lost push leaves pointing at the commit that
    never arrived — so it answers "landed" for exactly the failure being looked
    for.
    """
    r = git_client.run("ls-remote", "--heads", remote, branch, cwd=wt_path)
    if not r.ok:
        return None
    line = r.stdout.strip()
    return line.split()[0] if line else ""


def _verify(
    wt_path: str | Path, sha: str, branch: str, remote: str, output: str,
) -> PushResult:
    """Ask the remote what it holds, and turn that into an outcome."""
    held = remote_head(wt_path, branch, remote=remote)
    if held is None:
        return PushResult(PushStatus.UNVERIFIED, sha, branch, output=output)
    if held == sha:
        return PushResult(PushStatus.PUSHED, sha, branch, remote_sha=held, output=output)
    return PushResult(PushStatus.LOST, sha, branch, remote_sha=held, output=output)


def push(
    wt_path: str | Path,
    *,
    gated: bool,
    sha: str = "",
    branch: str = "",
    remote: str = "origin",
    args: Sequence[str] = (),
    trail: Trail | None = None,
) -> PushResult:
    """Push, then confirm the remote moved. See the module docstring.

    `sha` and `branch` default to the worktree's HEAD and current branch. `args`
    is passed through to `git push` — `--force-with-lease` for a rebase, or an
    explicit remote and refspec for a first push. `gated` consults the
    publishing gate and is required of every caller.
    """
    sha = sha or git_client.head_sha(cwd=wt_path)
    branch = branch or git_client.current_branch(cwd=wt_path)

    if gated and not publishing.enabled():
        publishing.draft("push", f"git -C '{wt_path}' push")
        return PushResult(PushStatus.HELD, sha, branch)

    r = git_client.run("push", *args, cwd=wt_path)
    if not r.ok:
        output = r.combined_output
        if trail:
            trail.error("push", "git refused the push",
                        data={"sha": sha, "branch": branch, "error": output[:500]})
        return PushResult(PushStatus.REFUSED, sha, branch,
                          refusal=classify(output), output=output)

    return _verify(wt_path, sha, branch, remote, r.combined_output)


def report(result: PushResult, wt_path: str | Path) -> None:
    """Say what happened, in the terms the reader has to act on."""
    if result.status is PushStatus.PUSHED:
        log.ok(f"Pushed {result.sha[:7]} to {result.branch}")
        return

    # A draft is not a failure, and `publishing.draft` has already said so.
    if result.status is PushStatus.HELD:
        return

    if result.status is PushStatus.UNVERIFIED:
        log.warn(
            f"pushed {result.sha[:7]} but could not reach the remote to confirm "
            f"it landed — check with: git ls-remote origin {result.branch}"
        )
        return

    if result.status is PushStatus.REFUSED:
        log.error(f"push refused ({result.refusal}) — nothing reached the remote")
        for line in result.output.splitlines():
            log.dim(line)
        return

    log.error("push reported success but the remote did not move")
    log.dim(f"branch:   {result.branch}")
    log.dim(f"expected: {result.sha[:7]}")
    log.dim(f"origin:   {result.remote_sha[:7] or 'no such ref'}")
    log.dim(f"Re-run: git -C '{wt_path}' push")
