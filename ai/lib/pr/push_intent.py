"""What every push on this machine was about to do, and whether it did it.

`push.py` owns the pushes this workbench issues: it pushes, asks the remote
whether the ref moved, and reports loudly when it did not. It cannot see a push
you type yourself. `git push` from a terminal, from an editor, or from a script
that is not ours reaches the remote through the same socket, behind the same
gates, and nothing asks afterwards whether it landed — so the failure `push.py`
exists to catch happens in the place where it is least likely to be noticed.
No wrapper printed a summary, so there is nothing to scroll back to; the next
signal is a branch with no PR, long after the reason is gone.

The global `pre-push` hook is the one thing every push on this machine passes
through, whoever issued it. It hands the ref lines git gave it to `record`,
which writes down what is about to be pushed. `reconcile` asks the remote about
each record later, through `push.remote_head`, and reports what did not land
through `push.report` — the same words an automated push is reported in, rather
than a second vocabulary for the same failure.

Reconciliation runs at the start of the next `pr` command. That is later than
the shell prompt and much cheaper: no network call sits near the prompt, and
`pr` is both the workbench's git surface and somewhere that can always print.
It costs one failed `stat` when nothing is pending — every run but the ones
that matter — and one `ls-remote` per pending ref when something is. A ref that
comes back looking lost costs a few local ref reads on top, and at most one `gh`
call, for the reason `_landed_elsewhere` gives.

The record's whole lifecycle is designed against a permanent false alarm:

* A second push to the same ref replaces the first record rather than adding
  one, so `push A; push B` never reports A as lost.
* A delete push drops the record for that ref and writes none of its own —
  a ref being removed has no commit left to verify.
* A remote that has moved *past* the recorded commit landed it and was built
  upon; `_built_upon` asks that locally before anything is reported.
* A commit whose work reached the default branch under another sha landed too,
  however its own ref ended up. `_landed_elsewhere` is what covers the squash
  merge that deletes its head branch — the ordinary end of a PR here, and
  otherwise a guaranteed false alarm for every one of them.
* A record whose working tree has since been removed drains in silence, which
  is what makes a push between throwaway repositories — a test suite's, say —
  cost nothing to have recorded, without this having to know what a temp root
  looks like.
* Reconciliation reports a record at most once and then drops it. A push that
  landed drops silently, one that did not is reported once and drops too.
  Nothing survives a report, so nothing can repeat one.
* A record the remote could not be asked about survives, because nothing was
  learned — but for `_MAX_ATTEMPTS` tries only, after which it is reported as
  unverified and dropped.
* `_MAX_RECORDS` bounds the file on a machine where reconciliation never runs.

`record` never raises. It is called from `pre-push`, where anything non-zero
refuses the push — in every repo on this machine — and no bookkeeping entry is
worth that. The hook adds its own `|| true` over the top; both are deliberate,
because only one of them is visible from each side. `reconcile` raises, and its
one call site in `pr` catches and warns, for a related reason: every subcommand
passes through it on the way to work that has nothing to do with pushing, so an
escaping exception would take the whole CLI down over a side-feature. Warning
rather than passing is what keeps a bug in here loud without coupling anything
to it.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from gh import landed as branch_landed
from git import client as git_client
from core import log
from git import push
from core import serde
from core import workbench_paths

# `git_remote` is a workbench-wide module rather than an `ai/lib` one, because
# the pre-push hooks and the surface gate resolve the same default branch. In a
# checkout that is one directory up; in the otto-ai-tools tarball, which
# flattens both into one `lib/`, it is already beside this file and the path
# below does not exist.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

# One file, under the state root, because a record has to outlive the shell the
# push was typed into — and the shell it is reported in is a different one.
INTENTS_FILENAME = "push-intents.json"

# How many reconciliations an unreachable remote gets before its record is
# reported as unverified and dropped. Bounded because "could not ask" is the one
# answer that teaches nothing: a machine that stays offline would otherwise keep
# asking forever, and never say what it was holding.
_MAX_ATTEMPTS = 3

# The most records the file keeps. Only reachable on a machine where the hook
# records pushes and no `pr` command ever runs to drain them. The oldest go
# first: the newest are the ones whose outcome is still worth asking about.
_MAX_RECORDS = 100

_HEADS_PREFIX = "refs/heads/"


class Outcome(StrEnum):
    """What reconciling one record learned, and so what becomes of it."""

    LANDED = "landed"
    LOST = "lost"
    UNANSWERED = "unanswered"
    GONE = "gone"


@dataclass(frozen=True)
class PushedRef:
    """One line of what git told the hook it is about to push.

    The hook's stdin is the only honest source for this. `git push origin
    foo:bar` moves neither HEAD nor a local branch of the same name, so reading
    HEAD would record a commit the push is not sending — and a push carrying
    several refs would be recorded as one.
    """

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def deleted(self) -> bool:
        """Whether this line removes the remote ref rather than moving it.

        An all-zero local sha is git's spelling of a delete, in whatever hash
        length the repository uses — hence the digit test rather than a
        forty-character constant, which a SHA-256 repository would not match.
        """
        return bool(self.local_sha) and set(self.local_sha) == {"0"}

    @property
    def branch(self) -> str:
        """The branch this line moves on the remote, or "" for anything else.

        Only `refs/heads/*` is verifiable here: `push.remote_head` asks
        `ls-remote --heads`, and a tag or a note has no answer in that output.
        Recording one would guarantee a report that the push was lost.
        """
        if not self.remote_ref.startswith(_HEADS_PREFIX):
            return ""
        return self.remote_ref[len(_HEADS_PREFIX):]


@dataclass(frozen=True)
class PushIntent:
    """A push that reached the remote's door, pending an answer about whether
    it got in.

    `repo` is the working tree the push was made from. Kept because that is
    where `ls-remote` has to run to reach the same remote through the same
    configuration and credentials, and because a report about a push made in
    another terminal has to name where it happened.

    `refspec` is the one git was given, spelled in full. A resume command that
    dropped it would name a different push: `foo:bar` is not `origin bar`, and
    the remote answers the two differently.
    """

    repo: str
    remote: str
    branch: str
    sha: str
    refspec: str
    recorded: str
    attempts: int = 0


@dataclass(frozen=True)
class IntentFile:
    """Every push recorded and not yet answered for, oldest first."""

    intents: list[PushIntent] = field(default_factory=list)


@dataclass(frozen=True)
class Reconciled:
    """One record's answer: what became of it, and what the remote holds now.

    `remote_sha` is empty both when the remote has no such ref and when it was
    never asked — `outcome` is what tells those apart, and it is the field the
    report is built from.
    """

    intent: PushIntent
    outcome: Outcome
    remote_sha: str = ""


def intents_path() -> Path:
    """The one file the hook writes and `reconcile` drains."""
    return workbench_paths.state_dir() / INTENTS_FILENAME


def parse_refs(text: str) -> list[PushedRef]:
    """The ref lines git writes to a `pre-push` hook's stdin.

    Each is `<local ref> <local sha> <remote ref> <remote sha>`. A line of any
    other shape is dropped rather than guessed at: git's own format is the only
    thing that produces these, so a line that does not match it is not a push
    this can say anything true about. Empty stdin — a push carrying no refs, or
    a hook run by hand — parses to nothing, which records nothing.
    """
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append(PushedRef(*parts))
    return refs


def record(refs: Sequence[PushedRef], *, repo: str, remote: str) -> None:
    """Write down what this push is about to do, for `reconcile` to check.

    A record already held for the same repo, remote and branch is replaced
    rather than added to: the newest push to a branch is the only one whose
    outcome is still an open question, and keeping the older one is how
    `push A; push B` would come to report A as lost. A delete performs the same
    replacement and contributes nothing, which is how a branch deleted on
    purpose stops being something to report about.
    """
    verifiable = [ref for ref in refs if ref.branch]
    if not verifiable:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    superseded = {(repo, remote, ref.branch) for ref in verifiable}
    kept = [i for i in _load() if (i.repo, i.remote, i.branch) not in superseded]
    _save(kept + [
        PushIntent(
            repo=repo, remote=remote, branch=ref.branch, sha=ref.local_sha,
            refspec=f"{ref.local_ref}:{ref.remote_ref}", recorded=now,
        )
        for ref in verifiable if not ref.deleted
    ])


def reconcile() -> None:
    """Ask the remote about every recorded push, and report what did not land.

    The single entry point for the start of a workbench command. Returns before
    touching the network — before touching anything but one `stat` — when
    nothing is pending, which is the ordinary case.

    The records that survive are written back before anything is printed, so a
    report interrupted part way through still leaves the file drained. Repeating
    a report is the failure this whole module is arranged against, and it is the
    one an unwritten file would produce.
    """
    intents = _load()
    if not intents:
        return
    answers = [_answer(intent) for intent in intents]
    _save([dataclasses.replace(a.intent, attempts=a.intent.attempts + 1)
           for a in answers if _still_asking(a)])
    _report([a for a in answers if _worth_reporting(a)])


# ── Reconciling one record ──────────────────────────────────────────────────


def _answer(intent: PushIntent) -> Reconciled:
    """What the remote says about one record.

    `GONE` is the working tree having been removed since the push. A worktree is
    deleted on purpose, its remote can no longer be reached through the
    configuration the push used, and its record is therefore not something to
    report on — checked first, so a removed worktree costs no round trip.
    """
    if not Path(intent.repo).is_dir():
        return Reconciled(intent, Outcome.GONE)
    held = push.remote_head(intent.repo, intent.branch, remote=intent.remote)
    if held is None:
        return Reconciled(intent, Outcome.UNANSWERED)
    if held == intent.sha or _built_upon(intent, held):
        return Reconciled(intent, Outcome.LANDED, remote_sha=held)
    if _landed_elsewhere(intent):
        return Reconciled(intent, Outcome.LANDED, remote_sha=held)
    return Reconciled(intent, Outcome.LOST, remote_sha=held)


def _built_upon(intent: PushIntent, ref: str) -> bool:
    """Whether *ref* has the recorded commit in its history rather than without it.

    Asked of two refs, for the same reason each time. A branch somebody else
    pushed to after this push landed holds a descendant of the recorded commit,
    not the commit itself; and a merge commit on the default branch holds the
    branch tip whole, which is stronger evidence than any tree comparison —
    the commit is upstream, not merely something that looks like it.

    Only answerable when the repository holds both commits: a remote sha it has
    never fetched makes `merge-base` exit non-zero, which reads here as "not an
    ancestor". That is the safe direction — an unanswerable question is not a
    reason to call a push landed.
    """
    if not ref:
        return False
    return git_client.ok(
        "merge-base", "--is-ancestor", intent.sha, ref, cwd=intent.repo,
    )


def _landed_elsewhere(intent: PushIntent) -> bool:
    """Whether the recorded commit's work is already in the default branch.

    The branch the record names is not the only place its commits can be. A
    squash merge rewrites them into one commit nothing here can recognise, and
    the merge then deletes the head ref — so `remote_head` answers "no such ref"
    for a push that landed perfectly, which is `_built_upon`'s question asked
    against a ref that no longer exists to answer it. Every branch that merges
    and is deleted would otherwise be reported as a push that vanished, which is
    the whole population of merged PRs in a repo configured this way.

    Ancestry is asked first and separately, because it is the one signal
    `branch_landed` cannot answer for a caller naming a commit. A merge commit
    leaves the recorded commit reachable from the base, which reads there as a
    rev with nothing of its own — indistinguishable from a freshly cut worktree,
    which `pr rebase` must not refuse. The question is the same one `_built_upon`
    just asked of the branch's own ref; only the ref it is asked of differs.

    `branch_landed` owns the rest of the evidence and `pr rebase` reads the same
    three signals. The two cheap ones are asked first and answer while the base
    is still near the merge; the tracker is the only one that survives the base
    moving on, and is reached only when they do not.

    Three questions are refused rather than guessed at, all of them in the
    direction that keeps a genuinely lost push reportable:

    * A push to the default branch has no base to be measured against — it *is*
      the base, so both git signals would call every such push landed.
    * A base ref this repo has never fetched cannot be compared against, and
      `default_base_ref` answers None rather than naming a ref that resolves to
      nothing.
    * A record for some remote other than `origin` was pushed somewhere the
      default branch is not the trunk of, and a fork's branch measured against
      upstream's trunk answers about the wrong repository.
    """
    if intent.remote != git_remote.GIT_REMOTE:
        return False
    if intent.branch == git_remote.resolve_default_branch(intent.repo):
        return False
    base = git_remote.default_base_ref(intent.repo)
    if base is None:
        return False
    if _built_upon(intent, base):
        return True
    return branch_landed.check(
        intent.repo, target_ref=base, branch=intent.branch, rev=intent.sha,
    ) is not None


def _exhausted(answer: Reconciled) -> bool:
    """Whether an unanswered record has used up its tries."""
    return answer.intent.attempts + 1 >= _MAX_ATTEMPTS


def _still_asking(answer: Reconciled) -> bool:
    """Whether this record survives to be asked about again."""
    return answer.outcome is Outcome.UNANSWERED and not _exhausted(answer)


def _worth_reporting(answer: Reconciled) -> bool:
    """Whether this record has something the operator has to act on.

    A landed push and a vanished worktree are drained in silence: reporting
    either would train the reader to scroll past the one report that matters.
    """
    if answer.outcome is Outcome.LOST:
        return True
    return answer.outcome is Outcome.UNANSWERED and _exhausted(answer)


# ── Reporting ───────────────────────────────────────────────────────────────


# `push.report` speaks for both of these already. Partial on purpose: a landed
# or vanished record is never reported, and a status invented for one here would
# be a claim about a push nobody is being told about.
_STATUS = {
    Outcome.LOST: push.PushStatus.LOST,
    Outcome.UNANSWERED: push.PushStatus.UNVERIFIED,
}


def _report(answers: Sequence[Reconciled]) -> None:
    """Say what did not land, in the words every other push report uses.

    The heading exists because the reader has no context for what follows: the
    push it describes may have been made in another terminal, hours ago, in a
    repository this command is not running in. `push.report` supplies the rest —
    the repo, the branch, the commit expected, what the remote holds instead,
    and the command that would finish the job.
    """
    if not answers:
        return
    log.blank()
    log.warn("pushes recorded by the pre-push hook that nothing has confirmed:")
    for answer in answers:
        push.report(_as_push_result(answer), answer.intent.repo)


def _as_push_result(answer: Reconciled) -> push.PushResult:
    """The record, in the shape `push.report` reads."""
    intent = answer.intent
    return push.PushResult(
        status=_STATUS[answer.outcome],
        sha=intent.sha,
        branch=intent.branch,
        remote_sha=answer.remote_sha,
        remote=intent.remote,
        args=(intent.remote, intent.refspec),
    )


# ── The file ────────────────────────────────────────────────────────────────


def _load() -> list[PushIntent]:
    """Every pending record, or none when the file is absent or unreadable."""
    return (serde.load_file(IntentFile, intents_path()) or IntentFile()).intents


def _save(intents: Sequence[PushIntent]) -> None:
    """Replace the record file, or remove it when nothing is pending.

    Removing rather than writing an empty list is what keeps the ordinary case —
    no unanswered push anywhere on the machine — a single failed `stat`.

    ceiling: two pushes finishing their hooks at the same moment both
    read-modify-write this file, so the later write can drop the earlier
    record. `serde.write_json` keeps a reader from seeing a half-written file,
    which is the failure that would matter; a dropped record costs one
    unreconciled push. Upgrade trigger: if a lost record is ever observed in
    practice, take `run_lock` around the read and the write.
    """
    path = intents_path()
    try:
        if not intents:
            path.unlink(missing_ok=True)
            return
        serde.write_json(path, serde.to_dict(IntentFile(list(intents[-_MAX_RECORDS:]))))
    except OSError as exc:
        log.warn(f"could not update {path}: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    """Record a push from the global `pre-push` hook — see the module docstring.

    Always returns zero. This runs inside `pre-push`, where any non-zero exit
    refuses the push, in every repository on this machine. The broad catch is
    the same decision: a bug in bookkeeping must cost a warning and a missing
    record, never somebody's push.
    """
    parser = argparse.ArgumentParser(description="Record a push for later verification.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--remote", default="origin")
    ns = parser.parse_args(argv)
    try:
        record(parse_refs(sys.stdin.read()), repo=ns.repo, remote=ns.remote)
    except Exception as exc:
        log.warn(f"could not record this push for later verification: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
