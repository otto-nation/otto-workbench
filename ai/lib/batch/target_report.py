"""What one PR target waits on, as the one-entry sessions document a run session reads.

`report(state, worktree)` is `{schema_version, sessions}` with at most one entry,
in `batch.session_report.SessionEntry`'s shape, so the reader of a batch report
reads it unchanged. It is read from the target's saved `pr` state only,
through `pr.needs_person`:

- threads that need a person (`threads_need_human`; each thread's id, outcome
  and summary in `data.threads`, and the ids in `detail`);
- the latest stored CI run's failing jobs (`ci_failing`; `data.jobs`, and the
  commit in `detail`);
- an owed closeout (`closeout_owed`).

Each finding carries its `pr.needs_person.next_command` on the worktree. With
no worktree there are no commands, and a note says so. Threads are settled one
at a time with `pr comments --settle`, which a note names with its verdicts.
An entry's `state` is `needs_session`, the batch item status the session
counts as waiting. No state, no PR number, or nothing found is an empty
`sessions`. `FINDING_KEYS` is every finding key this report can show.
"""

# doc-group: batch

from __future__ import annotations

import git.client
import pr.needs_person
import pr.settlement
import pr.state
import pr.target
from batch.model import Finding, FindingReason, ItemStatus
from batch.session_report import NO_CHECKOUT, SessionCommand, SessionEntry, severity_for

SCHEMA_VERSION = 1
FINDING_KEYS = frozenset(f"{reason}:{step}"
                         for reason, step in pr.needs_person.FINDING_STEPS.items())
SETTLE_NOTE = ("settle one thread with pr comments --settle ID --as "
               + "|".join(o.value for o in pr.settlement.SETTLE_OUTCOMES))


def pr_key_of(state: pr.state.PRState | None) -> str | None:
    """The PR key the target's state records, or None when it records no PR."""
    if state is None or state.identity.pr_number is None:
        return None
    return pr.needs_person.pr_key(state.identity.repo, state.identity.pr_number)


def _finding(reason: str, detail: str, data: dict | None = None) -> Finding:
    return Finding(FindingReason(reason), pr.needs_person.FINDING_STEPS[reason], detail,
                   data=dict(data or {}))


def findings(state: pr.state.PRState) -> list[Finding]:
    found: list[Finding] = []
    owed = pr.needs_person.owed_threads(state)
    if owed:
        ids = ", ".join(t["id"] for t in owed)
        found.append(_finding(pr.needs_person.THREADS_NEED_HUMAN, f"{len(owed)} open: {ids}",
                              {"threads": owed}))
    ci = pr.needs_person.failing_ci(state.ci)
    if ci is not None:
        found.append(_finding(pr.needs_person.CI_FAILING, f"failing at {git.client.abbrev(ci.head_sha)}",
                              {"jobs": list(ci.jobs)}))
    if state.fix.closeout_debt().owed:
        found.append(_finding(pr.needs_person.CLOSEOUT_OWED, pr.needs_person.CLOSEOUT_WHY))
    return found


def _commands(found: list[Finding], worktree: str) -> list[SessionCommand]:
    if not worktree:
        return []
    repo = pr.needs_person.repo_args(worktree)
    commands = []
    for finding in found:
        nxt = pr.needs_person.next_command(finding.reason.value, repo,
                                           finding.data.get("jobs") or ())
        commands.append(SessionCommand(nxt.argv, nxt.why, action=nxt.action))
    return commands


def entry(state: pr.state.PRState | None, worktree: str) -> SessionEntry | None:
    key = pr_key_of(state)
    if key is None:
        return None
    found = findings(state)
    if not found:
        return None
    notes = [] if worktree else [NO_CHECKOUT]
    if any(f.reason is FindingReason.THREADS_NEED_HUMAN for f in found):
        notes.append(SETTLE_NOTE)
    identity = state.identity
    # Offline: draft state, base and remote tip are GitHub's and are not read.
    return SessionEntry(
        pr=key, url=f"{pr.target.forge_base_url()}/{identity.repo}/pull/{identity.pr_number}",
        branch=identity.branch, base_ref="", after=[], is_draft=False,
        state=ItemStatus.NEEDS_SESSION.value, severity=severity_for(found), worktree=worktree,
        remote_sha="", local_sha="", findings=found, commands=_commands(found, worktree),
        notes=notes)


def report(state: pr.state.PRState | None, worktree: str) -> dict:
    one = entry(state, worktree)
    return {"schema_version": SCHEMA_VERSION, "sessions": [one.to_dict()] if one else []}
