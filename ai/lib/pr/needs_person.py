"""What on one PR needs a person: the reasons, the readers over saved state, the next commands.

Shared by `pr batch`'s report and the one-PR target report, so neither
re-derives the other's answer. Everything here reads saved `pr` state; nothing
fetches.

- The reason strings `threads_need_human`, `ci_failing` and `closeout_owed`,
  and the steps they are shown under (`FINDING_STEPS`: `comments`, `ci`).
  `batch.model.FindingReason` and `batch.model.Step` take their values from
  these, so a finding key `<reason>:<step>` is spelled once.
- `owed_threads(state)`: the comment pass's outcomes still owed, exactly
  `pr.settlement.UNSETTLED_OUTCOMES` (deferred, needs_human, declined). A
  declined thread is owed: the pass handed it to a person, and
  `pr comments --finish --post` refuses the summary until it is answered.
  Synthetic `ic-`/`rb-` ids are marked not replyable.
- `failing_ci(ci)`: the failing jobs of the latest stored CI run and the commit
  it ran on, or None when it recorded none.
- `next_command(reason, repo, jobs)`: the command that acts on a reason —
  `pr ci --fix`, `pr comments --fix`, or the bare closeout command — with the
  `repo_args(repo_dir, branch)` it was given appended: `--repo-dir` an
  absolute checkout, `--branch` only when that checkout is not the branch's
  worktree. No `cd`.
- `pr_key(repo, number)`: `owner/repo#N`, how every report and history names a PR.
"""

# doc-group: pr-state

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

import pr.comments_fix
import pr.domains
import pr.settlement
import pr.state

THREADS_NEED_HUMAN = "threads_need_human"
CI_FAILING = "ci_failing"
CLOSEOUT_OWED = "closeout_owed"
STEP_CI = "ci"
STEP_COMMENTS = "comments"
FINDING_STEPS: dict[str, str] = {
    THREADS_NEED_HUMAN: STEP_COMMENTS,
    CI_FAILING: STEP_CI,
    CLOSEOUT_OWED: STEP_COMMENTS,
}
CLOSEOUT_WHY = "closeout owed per pr state"
# Issue-comment and review-body items the comment pass synthesises: GitHub has
# no thread to reply in for them.
_SYNTHETIC_ID = re.compile(r"^(ic|rb)-\d+-\d+$")


@dataclass(frozen=True)
class NextCommand:
    """A command that acts on one reason: its argv, why it is run, and its action name."""

    argv: tuple[str, ...]
    why: str
    action: str


@dataclass(frozen=True)
class FailingCI:
    """The latest stored CI run's failing jobs, and the commit that run was for."""

    jobs: tuple[str, ...]
    head_sha: str


_NEXT: dict[str, NextCommand] = {
    CI_FAILING: NextCommand(("pr", "ci", "--fix"), "fix failing CI", "ci"),
    THREADS_NEED_HUMAN: NextCommand(("pr", "comments", "--fix"), "address review comments",
                                    "comments"),
    CLOSEOUT_OWED: NextCommand(tuple(shlex.split(pr.comments_fix.CLOSEOUT_COMMAND)),
                               CLOSEOUT_WHY, "closeout"),
}
NEXT_REASONS = frozenset(_NEXT)


def pr_key(repo: str, number: int | str) -> str:
    return f"{repo}#{number}"


def repo_args(repo_dir: str, branch: str = "") -> tuple[str, ...]:
    return ("--repo-dir", repo_dir, "--branch", branch) if branch else ("--repo-dir", repo_dir)


def next_command(reason: str, repo: Sequence[str], jobs: Sequence[str] = ()) -> NextCommand:
    """The command acting on *reason*, run against *repo* (`repo_args`); *jobs* sharpen CI's why."""
    base = _NEXT[reason]
    why = f"{base.why} ({', '.join(jobs)})" if jobs else base.why
    return NextCommand((*base.argv, *repo), why, base.action)


def owed_threads(state: pr.state.PRState) -> list[dict]:
    return [{"id": i.id, "outcome": i.outcome.value, "summary": i.summary, "reason": i.reason,
             "file": i.file, "line": i.line, "replyable": not _SYNTHETIC_ID.match(i.id)}
            for i in state.fix.fix.items if i.outcome in pr.settlement.UNSETTLED_OUTCOMES]


def failing_ci(ci: pr.domains.CIDomain) -> FailingCI | None:
    run = ci.latest_run()
    if run is None or not run.failures:
        return None
    return FailingCI(tuple(sorted({g.job for g in run.failures.values()})), run.head_sha)
