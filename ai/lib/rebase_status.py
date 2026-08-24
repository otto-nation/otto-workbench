"""Rebase domain — status rendering.

Owns the display logic for RebaseSummary so the pr dispatcher
doesn't need to know rebase internals — including the phrase each refusal is
reported with, so a new refusal shows up by adding a row rather than by being
forgotten and rendering as a completed rebase.

The already-landed signals answer "is this work already in the base?". Two more
answer a different question — "is replaying this branch onto that base a safe
thing to do at all?" — and refuse on the same exit code, with the same `--force`
override:

| Signal | What it reads | When it fires |
|---|---|---|
| `no_merge_base` | `git merge-base <base> HEAD` exits nonzero | The branch and its base share no commit |
| `conflicts_over_budget` | distinct conflicted files across the whole rebase | The count passes `_CONFLICT_FILE_BUDGET` |

`no_merge_base` is exact rather than heuristic, and it costs one local git
command, so it is asked before the landed signals rather than after them — those
compare HEAD against a ref an unrelated branch has no relationship to, so they
answer nothing there. A repo that was re-initialised leaves branches descending
from a second root; rebasing one replays its entire history onto a base it has
nothing in common with, which conflicts in every file both roots happen to
contain.

A ref that does not resolve is not this. `git merge-base` fails identically for a
typo'd `--onto` and for a base branch the fetch never brought down, so the check
verifies the ref names a commit first and passes when it does not — refusing
those as unrelated history would send the operator after a root they do not
have, where git's own error for the missing ref says what actually went wrong.

The budget is the circuit breaker for what that produces. Conflict resolution is
an AI call per conflicted file, with edit access to the worktree, and the wider
the spread the less any single call can tell an intended change from an
unrelated one — which is how a rebase resolving 51 conflicts rewrote
`bin/otto-workbench`, a file the branch never touched, into invalid bash. Past
the budget the rebase is aborted before the first resolution call, so the
worktree is left clean rather than half-replayed.

The count is of *distinct files* across the whole rebase, not conflicts: a file
conflicting in every replayed commit is one file's worth of risk, and counting
it once per commit would refuse a narrow rebase over a long branch. The tally
carries across steps, so a rebase that widens gradually is refused at the step
that crosses the line rather than never.

A resumed rebase waives the budget. The conflicts are already sitting in the
worktree by then; refusing would strand it mid-rebase with no path forward
except the manual resolution the command exists to avoid. The waiver is the
resume path passing `force=True` into the same parameter `--force` sets, so
there is one waiver mechanism rather than two.
"""

# doc-group: pr-state

from __future__ import annotations

from pr_domains import RebaseStatus, RebaseSummary


# Every status `pr rebase` refuses on, and the phrase the dashboard reports it
# with. One table so a new refusal shows up here by adding a row, rather than by
# being forgotten and rendering as a completed rebase.
_REFUSAL_REASONS = {
    RebaseStatus.ALREADY_LANDED.value: "branch already landed",
    RebaseStatus.UNRELATED_HISTORY.value: "branch shares no history with its base",
    RebaseStatus.CONFLICTS_OVER_BUDGET.value: "too many conflicts to resolve automatically",
}


def render_status(r: RebaseSummary) -> list[str]:
    """Render rebase state as status lines for the pr dashboard."""
    if not r.updated_at:
        return ["**Rebase**: not run yet"]
    if r.status == RebaseStatus.CONFLICTS.value:
        return ["**Rebase**: conflicts — resolve manually or run `pr rebase --fix`"]
    if r.status == RebaseStatus.ABORTED.value:
        return ["**Rebase**: aborted"]
    if r.status in _REFUSAL_REASONS:
        return [f"**Rebase**: refused — {_REFUSAL_REASONS[r.status]} "
                "(rerun with `pr rebase --force` to override)"]
    if r.conflicts_resolved == 0:
        desc = f"clean rebase — {r.commits_replayed} commit(s) replayed"
    else:
        desc = f"resolved {r.conflicts_resolved} file(s) across {r.commits_replayed} commit(s)"
    if r.force_pushed:
        desc += ", force-pushed"
    lines = [f"**Rebase**: {desc}"]
    if r.files_stale:
        lines.append(
            f"**Rebase**: regeneration failed for {', '.join(r.files_stale)} — "
            "content is the incoming side, unmerged"
        )
    return lines
