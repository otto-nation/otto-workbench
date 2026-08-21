"""Rebase domain — status rendering.

Owns the display logic for RebaseSummary so the pr dispatcher
doesn't need to know rebase internals.
"""

# doc-group: pr-state

from __future__ import annotations

from pr_state import RebaseStatus, RebaseSummary


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
