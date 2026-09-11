"""Reading a published summary back: which rows it holds, and which are a person's.

The summary comment is one comment edited across a review cycle, and the
replacement body is built entirely from local state — which is per-target and
per-worktree, and routinely absent for a round the comment already covers.
Treating state as authoritative would silently delete rounds nobody can recover,
so the published comment is read as the record it is and anything this render
cannot account for is kept verbatim.

That reading is this module. It parses rows out of a rendered body, decides
which of them a fresh render did not reproduce, and decides which carry an
Action cell a human wrote and must not be overwritten.

What is not here: what a row's identity *is* (`summary_model.row_key_from_cells`
owns that, and both this path and the freshly-rendered one go through it), and
which rows a round may leave to an earlier comment (`pr.summary_rounds`).
"""

# doc-group: publishing

from __future__ import annotations

from collections.abc import Sequence

from core import markdown
from pr import summary_model
from pr.summary_model import TABLE_COLUMNS


def row_location_key(row: str) -> str:
    """Reviewer and file cell of a rendered row, stripped of decoration.

    The rendered counterpart of `finding_location`: what two rows about one
    point at one line still share once the anchors, the pinned SHAs and the
    outcome cell are taken away. "" for a row naming no line, which can never
    establish that two rows are the same finding.

    Same coarsening, same tradeoff — see the ceiling comment on
    `finding_location` — and one step coarser again, because a published row
    is markdown rather than a typed entry: the reviewer and file cells are all
    that survive to match on.

    The `@` the Reviewer cell is rendered with is stripped, because these keys
    are compared against `finding_location`'s, which carry the bare login. The
    two forms are only interchangeable if they spell the reviewer the same way.
    """
    cells = markdown.row_cells(row)
    if len(cells) < len(TABLE_COLUMNS):
        return ""
    location = markdown.plain_cell(cells[2])
    if ":" not in location:
        return ""
    reviewer = markdown.plain_cell(cells[1]).removeprefix("@")
    return f"{reviewer}|{location}"


def row_key(row: str) -> str:
    """Identity of one row that exists only as published text.

    The adapter for the re-parse path: a row read back off a summary comment
    has no entry behind it, so its cells are recovered from the markdown and
    handed to the one definition of what a row's identity is —
    `summary_model.row_key_from_cells`. A freshly rendered row never comes
    through here; it is keyed from the cells it was built from, before it
    becomes markdown at all.
    """
    return summary_model.row_key_from_cells(markdown.row_cells(row))


def table_rows(body: str) -> list[str]:
    """The data rows of the summary table in a rendered body, in order."""
    rows = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if not line.strip("|-: "):
            continue
        if markdown.row_cells(line) == list(TABLE_COLUMNS):
            continue
        rows.append(line)
    return rows


def row_action_cell(row: str) -> str:
    """The Action cell of a rendered row, or "" for a row that has no such cell."""
    cells = markdown.row_cells(row)
    if len(cells) < len(TABLE_COLUMNS):
        return ""
    return cells[len(TABLE_COLUMNS) - 1]


def carried_over_rows(
    published: str, fresh: str, held_elsewhere: frozenset[str] = frozenset(),
    folded: frozenset[str] = frozenset(),
) -> list[str]:
    """Rows the published summary holds that a fresh render does not.

    ``held_elsewhere`` is the rows another summary comment also carries — see
    `RoundScope.elsewhere_keys`. Those are the one case that is not a lost
    round: the render left them to the comment that published them, and the
    reader reaches them through the footer chain. Carrying them anyway would
    undo the scoping row for row, since every row it drops is by construction
    a row this render does not reproduce.

    The summary is one comment edited in place across a review cycle, but the
    replacement body is built entirely from local state — which is per-target
    and per-worktree, and routinely absent for a round the comment already
    covers: `pr gc`, a recreated worktree, a later round run from another
    machine. Treating state as authoritative then silently deletes rounds
    nobody can recover from the comment.

    So the published comment is read as the record it is. Anything in it this
    render cannot account for is kept verbatim rather than overwritten. The
    table therefore only ever grows: a row no later render reproduces is carried
    forward for the life of the PR. That is the intended trade — a stale row a
    reader can see beats a round nobody can recover.

    The one thing that is not a lost round is a row this render folded into
    another: a comment item restating a review thread was published under its
    own anchor before `summary_model.duplicate_item_ids` started collapsing the pair, and
    carrying it forward would restore the duplicate this render just removed.

    ``folded`` is the locations a fresh thread row accounts for, from the
    typed entries the render was built from, not the markdown it produced.
    Reading it back off the rendered rows made the fold depend on a cell the
    renderer is free not to write: `summary_row.row_cells_for` drops the line
    anchor whenever
    `permalinks.anchored_line` cannot place the line in the pinned SHA — an
    unfetched commit, a drifted line, no worktree — and a File cell with no
    colon keys as "", so the fresh row's identity was lost and the published
    duplicate came straight back. Nothing about the render is allowed to
    decide this, so it is computed where the types are.

    The published side still has to be parsed: those rows have no live entry,
    which is the whole reason carry-over exists. The ceiling on
    `finding_location` therefore still bounds it — an item row colliding with
    an unrelated fresh thread row is dropped rather than carried.
    """
    if not published:
        return []
    fresh_rows = table_rows(fresh)
    fresh_keys = {row_key(row) for row in fresh_rows}
    return [
        row for row in table_rows(published)
        if row_key(row) not in fresh_keys
        and row_key(row) not in held_elsewhere
        and not (summary_model.ITEM_ANCHOR_RE.search(row) and row_location_key(row) in folded)
    ]


def hand_written_rows(published: Sequence[str], fresh: str) -> list[summary_model.HeldRow]:
    """Published rows this render would overwrite a human's Action cell on.

    `carried_over_rows` is the sibling case and covers the opposite one: a
    published row this render cannot account for. Between them they compose into
    the protection a hand-written thread reply already has — except that the
    carry-forward path alone gave the inverse of it, because `row_key`
    deliberately excludes the Action cell so that a round changing it (deferred
    to fixed) still counts as the same row. That exclusion is right, and it also
    means the Action cell — the only cell a human ever edits — is the one the
    key cannot defend. A hand edit therefore survived exactly as long as local
    state could not account for its thread, and was overwritten the round state
    regained coverage.

    So the two questions are asked separately: carry-forward asks whether the
    render covers the row at all, and this asks whether the render may write the
    row it covers. A cell no generated opening claims was written by a person,
    and stays for the life of the PR — `gh api -X PATCH` on the summary comment
    is the way to hand a row back to the renderer.

    A row with no Action cell to read is not a hand edit but a row whose shape
    this renderer no longer produces, and re-rendering it is the repair.

    The Action cell is excluded from every form of the key, but that is not all
    the key is: a row cut out of a top-level comment carries its summary cell
    too, because the anchor it shares with its siblings cannot tell them apart.
    A round that rewords such a row's summary therefore presents it as a new
    row, and the held one carries forward beside it — see the ceiling note on
    `row_key`.

    ``published`` is every summary comment on the PR, oldest first, not only the
    one being edited. Once a round posts its own comment instead of editing the
    last one, the cell a person rewrote is on a comment no later round targets,
    and reading only the target would hand the row straight back to the
    renderer. A key on more than one comment is read from the newest of them:
    that is where the reader looks, and a generated cell there means the row was
    handed back deliberately.
    """
    fresh_by_key = {row_key(row): row for row in table_rows(fresh)}
    newest_by_key: dict[str, str] = {}
    for body in published:
        for row in table_rows(body):
            newest_by_key[row_key(row)] = row
    held = []
    for key, row in newest_by_key.items():
        cell = row_action_cell(row)
        if not cell or key not in fresh_by_key or summary_model.is_generated_action(cell):
            continue
        held.append(summary_model.HeldRow(key, row, fresh_by_key[key]))
    return held
