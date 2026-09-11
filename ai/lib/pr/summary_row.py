"""One row of the summary table: its cells, and the Action cell that grades it.

A row is built as cells and rendered from them, in two steps rather than one.
The cells are what the row's identity is derived from — see
`summary_model.row_key_from_cells` — so a caller that needs both takes the
cells once and gets a row and a key that cannot disagree. Rendering straight to
markdown and reading the key back out of it is what this shape replaces.

The Action cell is the graded half of the row and is built here, in the five
functions that spell what happened to a thread. What that cell *reports* once
published is `summary_model.action_outcome`'s to say: the wordings are written
here and parsed there, and the two are kept in step by a sweep test until
#1252 gives each wording one owner.
"""

# doc-group: publishing

from __future__ import annotations

from pathlib import Path

from core import markdown
from git.land import CommitStatus
from pr import attribution
from pr import comments_fix as pr_comments_fix
from pr import permalinks
from pr.thread_models import CommentItem, ReportThread

def fixed_in_cell(sha: str, repo: str) -> str:
    """The status cell that names the commit carrying a row.

    One spelling for every surface that claims a fix landed: the fixed rows,
    and the satisfied rows a commit made true after the reviewer asked.
    """
    return f"Fixed in [`{sha}`]({permalinks.commit_permalink(repo, sha)})"


def fixed_status_text(cp: attribution.CommitPushResult, repo: str) -> str:
    """Human-readable status for fixed threads in the summary table."""
    if cp.sha and cp.status == CommitStatus.PUSHED:
        return fixed_in_cell(cp.sha, repo)
    if cp.status == CommitStatus.NO_CHANGES:
        # "A fix was applied" and "nothing was committed" contradict each other,
        # and which half is wrong is not knowable from here — a hook may have
        # rejected the commit, or the edit may have been a no-op. Publishing the
        # confident reading ("no commit needed") over a rejected commit asserts
        # more than is known, so the cell states only what is certain.
        return pr_comments_fix.UNATTRIBUTED_STATUS_TEXT
    if cp.status == CommitStatus.COMMIT_FAILED:
        return "Fix applied (commit failed — pre-commit hook?)"
    if cp.status == CommitStatus.RECONCILED:
        return pr_comments_fix.RECONCILED_STATUS_TEXT
    # Both callers refuse to render an unpushed commit, so these four are not
    # reachable today. They are spelled out anyway: the fallback below reads as
    # "not done yet", which would be a false claim about a commit that exists
    # and is only waiting to be published.
    if cp.status == CommitStatus.PUSH_HELD:
        return "Fix committed locally (push held pending discussion)"
    if cp.status == CommitStatus.PUSH_FAILED:
        return "Fix committed locally (push failed)"
    if cp.status == CommitStatus.PUSH_LOST:
        return "Fix committed locally (push reported success, remote does not have it)"
    if cp.status == CommitStatus.PUSH_UNVERIFIED:
        return "Fix committed and pushed (could not reach the remote to confirm)"
    return "Fix pending"


def settled_outside_the_pass(entry: CommentItem, cp: attribution.CommitPushResult) -> bool:
    """Whether this fixed row's work landed where the running pass cannot name it.

    Two ways that happens and one cell for both. The record says so — reconciled
    at drain time, or recorded by the operator with `--settle` — or the branch
    does, HEAD having moved past the fix snapshot onto a commit no reviewer can
    be sent to.

    Asked by the renderer and by the warning that counts what the renderer is
    about to publish, so the table and the count beneath it cannot disagree
    about which rows assert a fix the branch does not account for. The warning
    used to compare the rendered cell text, which made a reworded cell a silent
    change to what gets warned about.
    """
    return attribution.handled_outside(entry) or cp.status == CommitStatus.RECONCILED


def fixed_status_for(
    entry: CommentItem,
    cp: attribution.CommitPushResult,
    repo: str,
    history: "attribution.AddressingHistory | None" = None,
    thread: ReportThread | None = None,
) -> str:
    """Status cell for one fixed row, rendering what `attribution.attribute_commit` allows.

    The row asks the resolver which commit carries it and renders that answer;
    it does not reach past the resolver for a SHA. Falling through to the
    pass-level text is only correct when the resolver says the pass's own
    commit is the whole story for this row — otherwise that text credits the
    running pass for work landed in an earlier round, or by an operator across
    several commits it cannot tell apart.

    `history` is what lets the row be told apart in that last case. Without it
    the cell reads the same as before; the thread reply has always passed one,
    so a table built without it contradicts the reply posted beside it.
    """
    attributed = attribution.attribute_commit(entry, cp, history, thread)
    if attributed.cited:
        return fixed_in_cell(attributed.sha, repo)
    # A row settled outside the pass landed in a commit this run could not
    # resolve. That is true of the row whatever the running pass did, so it is
    # answered before the pass-level text gets a say.
    if settled_outside_the_pass(entry, cp):
        return pr_comments_fix.RECONCILED_STATUS_TEXT
    if attributed.claim is attribution.CommitClaim.PASS:
        return fixed_status_text(cp, repo)
    return pr_comments_fix.UNATTRIBUTED_STATUS_TEXT


def addressed_status_for(framing: attribution.AddressedFraming, repo: str) -> str:
    """Status cell for one satisfied row, in the framing its history earned.

    A row whose commit postdates the review comment reports as a fix, because
    that is what it was — the same question the thread reply asks, read through
    the same resolver so the table and the reply cannot say different things
    about one thread.
    """
    if framing.in_response and framing.cited:
        return fixed_in_cell(framing.sha, repo)
    return "Already addressed"


def _summary_cell(
    entry: CommentItem,
    threads_by_id: dict[str, ReportThread],
    repo: str, pr_number: int,
) -> str:
    """Thread summary as a markdown link if a permalink is available, plain text otherwise."""
    summary = markdown.escape_cell(entry.summary or "—")
    url = permalinks.thread_permalink(entry, threads_by_id, repo, pr_number)
    if url:
        return f"[{summary}]({url})"
    return summary


def row_cells_for(
    entry: CommentItem, status: str,
    threads_by_id: dict[str, ReportThread], repo: str, pr_number: int,
    head_sha: str = "",
    wt_path: Path | None = None,
) -> list[str]:
    """The cells of one summary table row, in column order.

    The file cell is a permalink whenever a SHA is known — every claim the table
    makes should be one line away from the code that backs it. The line anchor
    is kept only where it still holds in that SHA, on the same terms as a reply's
    link: see `permalinks.anchored_line`.

    Cells rather than a rendered row, because the row's identity is derived from
    them: `summary_model.row_key_from_cells` reads what this returns, and
    `render_row` turns the same list into markdown. A caller taking both gets an
    identity for the row it is actually publishing. The alternative this
    replaced — render, then parse the row back to recover its key — made every
    change to how a cell renders a silent change to row identity.
    """
    summary = _summary_cell(entry, threads_by_id, repo, pr_number)
    reviewer = f"@{entry.reviewer}" if entry.reviewer else "—"
    if entry.file and head_sha:
        anchor = permalinks.anchored_line(entry, entry.file, entry.line, head_sha, wt_path)
        label = f"{entry.file}:{anchor}" if anchor else entry.file
        url = permalinks.blob_permalink(repo, head_sha, entry.file, anchor)
        file_loc = f"[`{label}`]({url})"
    elif entry.file and entry.line:
        file_loc = f"`{entry.file}:{entry.line}`"
    elif entry.file:
        file_loc = f"`{entry.file}`"
    else:
        file_loc = "—"
    return [summary, reviewer, file_loc, markdown.escape_cell(status)]


def render_row(cells: list[str]) -> str:
    """One table row, from its cells.

    The only place a summary row becomes markdown. Kept apart from
    `row_cells_for` so the cells can be keyed before they are rendered, and
    trivial on purpose: a row is its cells, and anything that reads as more than
    that belongs in the cell that carries it.
    """
    return f"| {' | '.join(cells)} |"
