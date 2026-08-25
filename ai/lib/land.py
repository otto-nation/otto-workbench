"""The owner of every commit a fix pass makes, and of the push under it.

`push` answers whether a commit reached the remote. Nothing owned the step
before it, so each pass wrote its own: stage, build a message, run `commit`,
decide what an empty commit means, read HEAD back, push, and turn all of that
into a status. Four passes did it four ways, and the differences were not
choices — one forgot the empty-commit case, one never named a resume command,
one read HEAD and one did not, and only one of them consulted the publishing
gate.

Landing is one act with one result. `land` performs it and `LandResult` is what
it did, in the `CommitStatus` vocabulary `pr_fix` already defines, so a pass
records an outcome rather than reconstructing one from a `CmdResult` and a
`PushResult` it has to reconcile itself.

Three rules the passes disagreed on, settled here:

1. **Every outcome has a status.** `_PUSH_STATUS` maps the push owner's five
   answers onto the commit vocabulary and a test asserts it covers the enum, so
   a new `PushStatus` cannot arrive as a silently missing key.
2. **Every unfinished outcome names its resume command.** `resume` is the exact
   thing to run, as data — `push.resume_command` renders it, and a caller
   passes it to a reviewer, a summary, or `pr status` without knowing which of
   five ways the push fell short.
3. **A SHA is citable only once it is on the remote.** `citable` is that rule as
   a property. A commit link that 404s for the reviewer reading it is worse than
   a reply deferred a round, and `PUSH_HELD`, `PUSH_LOST` and `PUSH_UNVERIFIED`
   all leave a SHA that only exists locally.

The commit is ungated and the push is gated, which is the split every caller
wants and only one of them implemented. A local commit asserts nothing to
anybody: it makes the pass's work reviewable and durable, and it keeps the next
round from reading its own dirty tree as a refused commit. The push is the
outward act, so it waits for the same permission every posted comment waits for.
`gated=True` is therefore the answer for a pass that runs on somebody's behalf,
and the entry point opens the gate under `--post`.

`paths` decides the commit's scope. `None` stages the whole tree, which is what
a pass owning the worktree wants. A list stages exactly those paths as literal
pathspecs, which is what a pass that computed a snapshot difference wants — it
is the only thing keeping a build artifact or unrelated work in progress out of
a commit the pass then pushes.

Two conditions raise rather than returning a status, because neither is an
outcome: a `git add` that fails, and a HEAD that will not read back after a
commit git said it made. Both mean the repository is not answering, and a pass
that treats them as "nothing to commit" reports success having lost the work.

Two things can happen around a landing that are not the landing, and both are
options rather than defaults, because a caller that does not ask for them wants
the plain answer:

- `regen` — a pre-push hook that rewrites generated files leaves the tree dirty
  and the push refused, and the commit underneath it was fine. Naming a message
  commits what the hook wrote and pushes once more. The retry reports the
  original commit, which is the one the caller's entries are stamped with; the
  regeneration rides above it.
- `recover_from` — the agent a fix pass ran committed its own work, so the pass
  finds nothing to commit and has a commit it did not make to account for.
  Naming the HEAD from before the pass lets the owner attribute and push it.
  Recovery only ever *adds* information: it never overwrites what the caller's
  own commit attempt concluded, because reporting `no_changes` over a commit a
  hook rejected publishes "nothing needed doing" about work that was refused.
"""

# doc-group: platform

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import git_client
import log
import proc
import push
from pr_fix import CommitStatus
from proc import CmdResult
from push import PushResult, PushStatus
from trail import Trail

# git prints these on stdout, with exit 1, when a commit resolves to an empty
# change. `--allow-empty` is not the answer: an empty commit is noise on the
# branch, and the pass has nothing to say in it.
_EMPTY_COMMIT_MARKERS = (
    "nothing to commit",
    "nothing added to commit",
    "no changes added to commit",
)

# The push owner's answers in the commit vocabulary. Complete by construction —
# `land_test` asserts it covers `PushStatus`, because a status this map does not
# answer for is a `KeyError` in the middle of a pass that has already committed.
_PUSH_STATUS = {
    PushStatus.PUSHED: CommitStatus.PUSHED,
    PushStatus.HELD: CommitStatus.PUSH_HELD,
    PushStatus.REFUSED: CommitStatus.PUSH_FAILED,
    PushStatus.LOST: CommitStatus.PUSH_LOST,
    # Its own status rather than folded into `push_lost`: an unverified push has
    # almost certainly landed, and rendering it as a remote that does not hold
    # the commit is as unfounded as rendering it as pushed. The run cannot make
    # either claim, so it makes neither.
    PushStatus.UNVERIFIED: CommitStatus.PUSH_UNVERIFIED,
}


def commit_status(status: PushStatus) -> CommitStatus:
    """The push owner's answer, in the vocabulary a fix pass records.

    Public for the one caller that pushes a commit it did not just make —
    `review-threads` sending a held commit on a later run — which has a
    `PushResult` and no landing to read the status off. `land` uses this too, so
    the two cannot answer differently.
    """
    return _PUSH_STATUS[status]


def committed_nothing(result: CmdResult) -> bool:
    """Whether a failed `git commit` failed only because nothing was staged.

    The dirty check ahead of the commit answers "dirty" when git could not read
    the worktree at all, so reaching the commit does not prove there was work.
    git declining an empty commit is the expected end of that path rather than a
    failure to report or raise on, and this is the read that cannot be wrong
    about it.
    """
    text = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in text for marker in _EMPTY_COMMIT_MARKERS)


@dataclass(frozen=True)
class LandResult:
    """What became of a commit and its push, and what would finish the job.

    `sha` is empty for everything that never committed. `error` carries what git
    said when it refused, and `push` the owner's own result for a caller that
    needs the refusal kind or what the remote turned out to hold.
    """

    status: CommitStatus
    sha: str = ""
    error: str = ""
    # The exact command that completes what was held or refused, or "" when
    # nothing mechanical would — a commit a hook rejected needs the tree
    # repaired, which is not a command this can name.
    resume: str = ""
    push: PushResult | None = None

    @property
    def ok(self) -> bool:
        """Committed, and the remote holds it."""
        return self.status is CommitStatus.PUSHED

    @property
    def citable(self) -> bool:
        """Whether the SHA may be named outward. Rule 3, as a property."""
        return self.ok


def _pathspecs(paths: Iterable[str] | None) -> list[str]:
    """The paths as pathspecs git will read as filenames.

    `:(literal)` because git reads these as pathspecs, not filenames. A file
    actually named `report[1].md` would otherwise match as a character class and
    stage whichever unrelated paths it happened to glob — the opposite of what
    staging by name is here to guarantee.
    """
    return [f":(literal){path}" for path in sorted(paths or ())]


def _stage(wt_path: str | Path, scope: list[str], whole_tree: bool) -> None:
    """Stage the commit's scope, raising when git could not.

    `-A` rather than `-u` for the whole tree: a fix that adds files — tests,
    fixtures — would otherwise be dropped from the commit while still counted as
    fixed.
    """
    args = ["-A"] if whole_tree else ["--", *scope]
    staged = git_client.run("add", *args, cwd=wt_path)
    if not staged.ok:
        raise RuntimeError(proc.failure_message("Failed to stage the commit", staged))


def _landed(wt_path: str | Path, sha: str, result: PushResult) -> LandResult:
    """One push's answer as a landing. The only place a `LandResult` is built."""
    return LandResult(
        commit_status(result.status),
        sha=sha,
        error="" if result.ok else result.output.strip(),
        resume=push.resume_command(result, wt_path),
        push=result,
    )


def _regenerated(wt_path: str | Path) -> list[str]:
    """The tracked files something rewrote under us, from git's own status.

    `run` rather than `lines`: the marker this reads is in the first two columns,
    and stripping the output would take the leading space off the first line and
    leave it looking like anything but a modification.
    """
    status = git_client.run(
        "status", "--porcelain", "--untracked-files=no", cwd=wt_path,
    )
    return [line[3:] for line in status.stdout.splitlines() if line.startswith(" M ")]


def _retry_after_regen(
    wt_path: str | Path,
    message: str,
    *,
    gated: bool,
    args: Sequence[str],
    trail: Trail | None,
) -> PushResult | None:
    """Commit what a pre-push hook regenerated and push once more, or None.

    None whenever the recovery does not apply or does not complete — nothing was
    regenerated, staging or committing it failed, or the second push fell short
    too. The caller then reports its original push, which is the honest answer:
    the commit it made is still the one its work is in.
    """
    modified = _regenerated(wt_path)
    if not modified:
        return None

    log.info("Committing regenerated files...")
    if trail:
        trail.info("push", "committing regenerated files before retry",
                   data={"files": modified})
    if not git_client.ok("add", "-u", cwd=wt_path):
        log.error("Failed to stage regenerated files.")
        return None
    if not git_client.run("commit", "-m", message, cwd=wt_path).ok:
        return None

    # Reported whichever way it went: a second push happened, and the operator
    # who watched the first one refused is owed the same line about this one —
    # including the resume command, when it fell short too.
    result = push.push(wt_path, gated=gated, args=args, trail=trail)
    push.report(result, wt_path)
    return result if result.ok else None


def _moved_head(wt_path: str | Path, before: str) -> str:
    """HEAD, when it is no longer the commit the caller recorded, else "".

    Both sides go through git, so an abbreviated `before` — which is what a
    caller that recorded a SHA for a reviewer has — still compares equal to the
    full HEAD it names.
    """
    head = git_client.head_sha(cwd=wt_path)
    if not head or head == git_client.out("rev-parse", before, cwd=wt_path):
        return ""
    return head


def _unrecovered(wt_path: str | Path, prior: LandResult) -> LandResult:
    """What the caller knows when recovery found no commit it did not make.

    Recovery exists to add information. When it finds none, whatever the commit
    attempt already established stands — see the module docstring.

    With nothing but `NO_CHANGES` to preserve, the tree itself distinguishes the
    two remaining readings. Changes still sitting there after a commit was
    attempted mean something refused it, not that there was nothing to commit.
    """
    if prior.status is not CommitStatus.NO_CHANGES:
        return prior
    if git_client.is_dirty(wt_path):
        return LandResult(
            CommitStatus.COMMIT_FAILED,
            error="changes remain uncommitted in the worktree",
        )
    return prior


def _recover(
    wt_path: str | Path,
    prior: LandResult,
    before: str,
    *,
    gated: bool,
    args: Sequence[str],
    trail: Trail | None,
) -> LandResult:
    """Account for a commit made outside the caller's own attempt."""
    sha = _moved_head(wt_path, before)
    if not sha:
        return _unrecovered(wt_path, prior)

    if push.holds(wt_path, sha):
        return LandResult(CommitStatus.PUSHED, sha=sha)

    log.info(f"Recovered {sha[:7]} — committed outside the pass")
    result = push.push(wt_path, gated=gated, sha=sha, args=args, trail=trail)
    push.report(result, wt_path)
    return _landed(wt_path, sha, result)


@dataclass(frozen=True)
class _Commit:
    """What the commit half produced: a SHA to push, or the outcome to report.

    Exactly one of the two is set. A commit that was made has no status yet —
    the push decides it — and the `CommitStatus` vocabulary has no spelling for
    "committed, not pushed" on purpose, so the two cases are separate fields
    rather than one `LandResult` carrying a status the next line overwrites.
    """

    sha: str = ""
    outcome: LandResult | None = None


def _commit(
    wt_path: str | Path,
    *,
    message: str,
    paths: Iterable[str] | None,
    trail: Trail | None,
) -> _Commit:
    """Stage the scope and commit it, or say why there is nothing to push."""
    whole_tree = paths is None
    scope = _pathspecs(paths)
    if whole_tree and not git_client.is_dirty(wt_path):
        return _Commit(outcome=LandResult(CommitStatus.NO_CHANGES))
    if not whole_tree and not scope:
        return _Commit(outcome=LandResult(CommitStatus.NO_CHANGES))

    _stage(wt_path, scope, whole_tree)

    # A pathspec commit when the scope is explicit, so content the operator
    # staged before the pass started stays staged instead of riding along.
    limit = [] if whole_tree else ["--", *scope]
    committed = git_client.run("commit", "-m", message, *limit, cwd=wt_path)
    if not committed.ok:
        if committed_nothing(committed):
            return _Commit(outcome=LandResult(CommitStatus.NO_CHANGES))
        error = committed.stderr.strip() or committed.stdout.strip()
        log.error(f"commit failed: {error}")
        if trail:
            trail.error("commit", "commit failed", data={"error": error[:500]})
        return _Commit(outcome=LandResult(CommitStatus.COMMIT_FAILED, error=error))

    sha = git_client.head_sha(cwd=wt_path)
    if not sha:
        # The commit above succeeded, so HEAD not reading back is the repo
        # saying something is wrong with it — louder than reporting no sha.
        raise RuntimeError("Committed, but could not read the new HEAD")

    subject = message.splitlines()[0] if message.strip() else "(no subject)"
    log.info(f"Committed {sha[:7]} — {subject}")
    if trail:
        trail.info("commit", "committed the pass's work", data={"sha": sha})
    return _Commit(sha=sha)


def land(
    wt_path: str | Path,
    *,
    message: str,
    gated: bool,
    paths: Iterable[str] | None = None,
    args: Sequence[str] = (),
    trail: Trail | None = None,
    regen: str | None = None,
    recover_from: str | None = None,
) -> LandResult:
    """Commit the work in *wt_path* and push it, and say what became of both.

    `message` is the full commit message, subject and body. `gated` decides
    whether the push consults the publishing gate and is required, the same way
    `push.push` requires it. `paths` scopes the commit — `None` for the whole
    tree, a list for exactly those files. `args` is passed through to `git push`.
    `regen` is the commit message for files a pre-push hook rewrote, and asks for
    the retry that recovers from one; `recover_from` is HEAD before the caller's
    work began, and asks for a commit made outside the caller to be accounted
    for. Both are described in the module docstring, and both are off by default.

    Nothing to commit is `NO_CHANGES`, not a failure: a pass whose agent changed
    nothing has an outcome, and it is the same outcome whether the emptiness was
    visible before the commit or only to git.
    """
    made = _commit(wt_path, message=message, paths=paths, trail=trail)
    if made.outcome is not None:
        if recover_from is None:
            return made.outcome
        return _recover(wt_path, made.outcome, recover_from,
                        gated=gated, args=args, trail=trail)

    result = push.push(wt_path, gated=gated, sha=made.sha, args=args, trail=trail)
    push.report(result, wt_path)
    # Only a refusal can be a regenerating hook: a push the remote dropped left
    # nothing behind to commit, and `push` has already retried that one itself.
    if result.status is PushStatus.REFUSED and regen is not None:
        retried = _retry_after_regen(
            wt_path, regen, gated=gated, args=args, trail=trail,
        )
        if retried:
            return _landed(wt_path, made.sha, retried)
    return _landed(wt_path, made.sha, result)
