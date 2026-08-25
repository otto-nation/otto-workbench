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

A lost push is retried exactly once, and only when HEAD still holds the pushed
commit and the tree is clean. The retry passes `--no-verify`, so it costs the
transfer rather than the gates; that is not a gate bypass, because the gates
already passed for this exact commit, and the guard is what keeps that true.

This module pushes, verifies, retries, and reports. It does not commit, and it
does not perform the hook-regenerated-files recovery that `review-threads` and
`pr-rebase` each own — those sit above it, which is what keeps this module's
answer to "did it land" independent of any caller's idea of how to fix it.

`gated` is required and has no default. `pr comments`, `pr ci --fix` and the
review fix pass all pass `True` and open the gate only under `--post`; `pr
rebase` and the `pr:create` bridge below pass `False`, because pushing is the
command rather than a side effect of it. A `False` default would let the next
call site inherit the ungated answer by omitting the argument, which is how
three of those four came to push without ever asking.

Every outcome but `PUSHED` names the command that would finish it, and
`resume_command` is where that mapping lives — one place rather than a line of
prose per call site, and data rather than a log line, so `land` can carry it
into `pr status` and the MCP result.

Two outcomes change what the operator does. A `LOST` push is a hard stop and
nothing downstream may run: `pr comments` records it as `push_lost` rather than
`push_failed`, because the terminal showed a clean push, and the fixes exist only
in the worktree — nothing may cite the SHA. `UNVERIFIED` records as
`push_unverified` and is a warning: a remote that could not be asked has not said
no, so the push has very likely landed and simply cannot be confirmed.

Running this module as a script is how the bash half of `pr:create` reaches it,
since a second implementation in shell is the thing being avoided. It takes
`--cwd`, `--branch`, `--remote` and `--set-upstream`, runs ungated, and answers
in exit codes — `0` pushed, `1` refused, `2` lost, `3` unverified.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import dataclasses
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


class Retry(StrEnum):
    """Whether the one retry ran, and what stopped it when it did not."""

    NONE = "none"
    ATTEMPTED = "attempted"
    HEAD_MOVED = "head_moved"
    DIRTY = "dirty"


@dataclass(frozen=True)
class PushResult:
    """What a push did, and what the remote says about it.

    `remote_sha` is what `ls-remote` reported at verification time: the pushed
    commit on success, whatever the remote holds instead on `LOST`, and empty
    when the ref is absent or was never asked for.

    `args` is what the caller passed through to `git push`, kept so
    `resume_command` can name the same push rather than a plainer one the remote
    would refuse for a second reason.
    """

    status: PushStatus
    sha: str
    branch: str
    remote_sha: str = ""
    refusal: Refusal | None = None
    output: str = ""
    retry: Retry = Retry.NONE
    remote: str = "origin"
    args: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """The commit is on the remote."""
        return self.status is PushStatus.PUSHED


_DIVERGED_MARKERS = (
    "! [rejected]",
    "non-fast-forward",
    "fetch first",
    "stale info",
    "updates were rejected",
    "behind its remote",
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

# What the LOST report says about the retry. A message claiming an attempt that
# never ran is the same class of wrong reporting this module exists to remove,
# so every state names itself and a test asserts the map covers the enum.
_RETRY_NOTE = {
    Retry.ATTEMPTED: "Retried once without the gates; the remote still does not hold it.",
    Retry.HEAD_MOVED: "HEAD moved since the push; not retried.",
    Retry.DIRTY: "The worktree is dirty; not retried.",
    Retry.NONE: "Not retried.",
}


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

    An `ls-remote` pattern matches any ref whose trailing path components spell
    it, so asking for `main` also answers with `refs/heads/topic/main` — and
    since git sorts its output, the impostor can come first. The full refname
    narrows the query and comparing it again reads the one line that is actually
    an answer to the question.
    """
    ref = f"refs/heads/{branch}"
    r = git_client.run("ls-remote", "--heads", remote, ref, cwd=wt_path)
    if not r.ok:
        return None
    for line in r.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref:
            return sha.strip()
    return ""


def _verify(
    wt_path: str | Path, sha: str, branch: str, remote: str, output: str,
) -> PushResult:
    """Ask the remote what it holds, and turn that into an outcome."""
    held = remote_head(wt_path, branch, remote=remote)
    if held is None:
        return PushResult(PushStatus.UNVERIFIED, sha, branch,
                          output=output, remote=remote)
    if held == sha:
        return PushResult(PushStatus.PUSHED, sha, branch, remote_sha=held,
                          output=output, remote=remote)
    return PushResult(PushStatus.LOST, sha, branch, remote_sha=held,
                      output=output, remote=remote)


def _retry_block(wt_path: str | Path, sha: str) -> Retry | None:
    """Why a retry would be unsafe, or None when it is safe.

    `--no-verify` is what makes the retry cheap, and it is only defensible
    because the gates already passed for this exact commit. Both halves of that
    sentence have to still be true: HEAD must be the commit they approved, and
    the tree must be the one they validated. This repo's own pre-push
    regenerates files, so the dirty check is not hypothetical.
    """
    if git_client.head_sha(cwd=wt_path) != sha:
        return Retry.HEAD_MOVED
    if git_client.is_dirty(cwd=wt_path):
        return Retry.DIRTY
    return None


def _retry_lost(
    wt_path: str | Path,
    lost: PushResult,
    remote: str,
    args: Sequence[str],
    trail: Trail | None,
) -> PushResult:
    """One more attempt at a push that vanished, then the final answer.

    Bounded at a single retry on purpose: a second one answers no question the
    first did not, and the point of verifying was to stop spending minutes
    learning what one round trip already reported.
    """
    blocked = _retry_block(wt_path, lost.sha)
    if blocked:
        return dataclasses.replace(lost, retry=blocked)

    if trail:
        trail.warn("push", "push did not land — retrying without the gates",
                   data={"sha": lost.sha, "branch": lost.branch})
    log.warn("push did not land — retrying once without the gates")

    r = git_client.run("push", "--no-verify", *args, cwd=wt_path)
    if not r.ok:
        output = r.combined_output
        return dataclasses.replace(
            lost, status=PushStatus.REFUSED, refusal=classify(output),
            output=output, retry=Retry.ATTEMPTED,
        )

    verified = _verify(wt_path, lost.sha, lost.branch, remote, r.combined_output)
    return dataclasses.replace(verified, retry=Retry.ATTEMPTED, args=lost.args)


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

    The gate is asked before the two local reads, not after: it answers whether
    anything may happen at all, and a held run should leave the repository
    untouched rather than shell out twice to describe a push it will not make.
    That is why a held result carries only what the caller passed in — no caller
    reads a SHA off one, and the gate's own draft names the command instead.
    """
    argv = tuple(args)
    if gated and not publishing.enabled():
        publishing.draft("push", _push_command(wt_path, argv))
        return PushResult(PushStatus.HELD, sha, branch, remote=remote, args=argv)

    sha = sha or git_client.head_sha(cwd=wt_path)
    branch = branch or git_client.current_branch(cwd=wt_path)

    r = git_client.run("push", *argv, cwd=wt_path)
    if not r.ok:
        output = r.combined_output
        if trail:
            trail.error("push", "git refused the push",
                        data={"sha": sha, "branch": branch, "error": output[:500]})
        return PushResult(PushStatus.REFUSED, sha, branch, refusal=classify(output),
                          output=output, remote=remote, args=argv)

    verified = _verify(wt_path, sha, branch, remote, r.combined_output)
    result = dataclasses.replace(verified, args=argv)
    if result.status is not PushStatus.LOST:
        return result
    return _retry_lost(wt_path, result, remote, argv, trail)


_HOOK_OUTPUT_LINES = 20


def output_tail(output: str, *, indent: str = "") -> str:
    """The last few meaningful lines of what git and its hooks printed.

    A pre-push hook that fails is often a whole test suite, and the line naming
    which gate failed is at the end of it. Printing all of it buries the report
    that follows; printing the tail keeps the part that identifies the failure.

    Blank lines go because a hook splits itself across both streams and
    `combined_output` joins them — an empty stream would otherwise contribute a
    gap that reads as missing output.
    """
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return "\n".join(f"{indent}{line}" for line in lines[-_HOOK_OUTPUT_LINES:])


def _push_command(wt_path: str | Path, args: Sequence[str]) -> str:
    """The `git push` the caller asked for, spelled out for a human to re-run."""
    return " ".join(["git", "-C", f"'{wt_path}'", "push", *args])


def _forced(args: Sequence[str]) -> bool:
    """Whether these push arguments already overwrite what the remote holds."""
    return any(arg == "-f" or arg.startswith("--force") for arg in args)


def resume_command(result: PushResult, wt_path: str | Path) -> str:
    """The command that finishes what this push held, refused, or lost.

    Data rather than a log line, so `land.LandResult` can carry it into
    `pr status` and the MCP result — the two readers that most need to know what
    would complete the job and could surface none of the six prose variants this
    replaces. Empty only for `PUSHED`, which needs nothing.

    The arguments the caller pushed with are replayed, because a resume that
    drops them is a second refusal waiting to happen: `pr rebase` pushes with
    `--force-with-lease`, and after its pre-push hook is fixed a plain `git push`
    is still non-fast-forward. A divergence adds that flag when the push did not
    already carry it — this names it and never runs it, since reconciling with a
    remote somebody else may have moved is the operator's call.

    `UNVERIFIED` is the one answer that is a check rather than a retry — the push
    has very likely landed, so re-pushing it would be acting on a question
    `ls-remote` answers in one round trip.
    """
    if result.status is PushStatus.PUSHED:
        return ""
    if result.status is PushStatus.UNVERIFIED:
        return f"git -C '{wt_path}' ls-remote {result.remote} {result.branch}"
    args = list(result.args)
    if result.refusal is Refusal.DIVERGED and not _forced(args):
        args.append("--force-with-lease")
    return _push_command(wt_path, args)


def report(result: PushResult, wt_path: str | Path) -> None:
    """Say what happened, in the terms the reader has to act on."""
    if result.status is PushStatus.PUSHED:
        log.ok(f"Pushed {result.sha[:7]} to {result.branch}")
        return

    # A draft is not a failure, and `publishing.draft` has already said so.
    if result.status is PushStatus.HELD:
        return

    resume = resume_command(result, wt_path)

    if result.status is PushStatus.UNVERIFIED:
        log.warn(
            f"pushed {result.sha[:7]} but could not reach the remote to confirm "
            f"it landed — check with: {resume}"
        )
        return

    if result.status is PushStatus.REFUSED:
        log.error(f"push refused ({result.refusal}) — nothing reached the remote")
        for line in output_tail(result.output).splitlines():
            log.dim(line)
        log.dim(f"Resume: {resume}")
        return

    log.error("push reported success but the remote did not move")
    # Named rather than left to the resume line below, because the reader is not
    # always standing in the repository this happened in: `push_intent` reports a
    # push made in another terminal, and a review fix pass pushes from a worktree
    # under the state root that nobody has seen.
    log.dim(f"repo:     {wt_path}")
    log.dim(f"branch:   {result.branch}")
    log.dim(f"expected: {result.sha[:7]}")
    log.dim(f"origin:   {result.remote_sha[:7] or 'no such ref'}")
    log.dim(_RETRY_NOTE[result.retry])
    log.dim(f"Resume: {resume}")


# The bash half of `pr:create` reads these rather than parsing output. HELD
# shares REFUSED's code: the CLI always runs ungated, so it is unreachable, and
# a status this map did not answer for must not read as success.
_EXIT_CODES = {
    PushStatus.PUSHED: 0,
    PushStatus.REFUSED: 1,
    PushStatus.HELD: 1,
    PushStatus.LOST: 2,
    PushStatus.UNVERIFIED: 3,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Push and verify from the shell — see the module docstring.

    The bridge for `lib/ai/pr.sh`, which is bash and cannot reach the owner any
    other way. It stays ungated: the shell half runs under `pr:create`, where
    pushing the branch is the point of the command rather than something the
    publishing gate decides.
    """
    parser = argparse.ArgumentParser(description="Push a branch and verify it landed.")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--set-upstream", action="store_true")
    ns = parser.parse_args(argv)

    # The branch is named explicitly rather than left to git's push.default,
    # because the shell caller has already decided which branch it means.
    args = (["-u"] if ns.set_upstream else []) + [ns.remote, ns.branch]
    result = push(ns.cwd, gated=False, branch=ns.branch, remote=ns.remote, args=args)
    report(result, ns.cwd)
    return _EXIT_CODES[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
