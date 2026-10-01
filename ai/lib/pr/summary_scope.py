"""Reading a published summary back: which rows it holds, and which are a person's.

The summary comment is one comment edited across a review cycle, and the
replacement body is built entirely from local state — which is per-target and
per-worktree, and routinely absent for a round the comment already covers.
Treating state as authoritative would silently delete rounds nobody can recover,
so the published comment is read as the record it is and anything this render
cannot account for is kept verbatim.

That reading is this module. `published_rows` turns a body into typed rows,
and every other reader goes through it: which rows a fresh render did not
reproduce, and which a person edited and must not be overwritten.

A body this code wrote declares each row's identity and digest in a marker
(`pr.published_record`), so those rows are read, not re-derived. A body written
before the marker is read the old way, from its cells, and that path is
confined to `_legacy_row`.

What is not here: what a row's identity *is* (`summary_model.row_key_from_cells`
owns that, and both this path and the freshly-rendered one go through it), and
which rows a round may leave to an earlier comment (`pr.summary_rounds`).
"""

# doc-group: publishing

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import core.markdown
import pr.published_record
import pr.summary_model
from pr.fix import FixOutcome
from pr.summary_model import TABLE_COLUMNS


def row_key(row: str) -> str:
    """Identity of one row that exists only as published text.

    The adapter for the re-parse path: a row read back off a summary comment
    has no entry behind it, so its cells are recovered from the markdown and
    handed to the one definition of what a row's identity is —
    `summary_model.row_key_from_cells`. A freshly rendered row never comes
    through here; it is keyed from the cells it was built from, before it
    becomes markdown at all.
    """
    return pr.summary_model.row_key_from_cells(core.markdown.row_cells(row))


def table_rows(body: str) -> list[str]:
    """The data rows of the summary table in a rendered body, in order."""
    rows = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if not line.strip("|-: "):
            continue
        if core.markdown.row_cells(line) == list(TABLE_COLUMNS):
            continue
        rows.append(line)
    return rows


def row_action_cell(row: str) -> str:
    """The Action cell of a rendered row, or "" for a row that has no such cell."""
    cells = core.markdown.row_cells(row)
    if len(cells) < len(TABLE_COLUMNS):
        return ""
    return cells[len(TABLE_COLUMNS) - 1]


@dataclass(frozen=True)
class PublishedRow:
    """One row of a published summary, with what the record says about it.

    ``key`` is the hashed identity every reader compares in, and "" for a row
    whose identity nothing declares — a row a person added, or one whose marker
    they deleted. Such a row can be carried but never matched, scoped or held.

    ``ours`` is whether the row still reads as we wrote it. ``outcome`` is what
    it reported when we wrote it, and None for any row that is not ours: a
    person's cell states no outcome to differ from.

    ``legacy`` marks a row read off a comment written before rows were marked.
    Its record was recovered from its cells rather than declared, and it is
    stamped with that record once, by `lifted`, if it is ever written again.
    """

    line: str
    key: str
    location: str = ""
    text_key: str = ""
    outcome: FixOutcome | None = None
    ours: bool = True
    legacy: bool = False

    def lifted(self) -> str:
        """The row as it is re-emitted into a body this code writes.

        A marked row goes back verbatim, marker and all — a held row's digest
        still disagrees with its text, so it stays held with no extra state. A
        legacy row is stamped here, the one point it crosses into the new
        format, so the legacy reader never has to read a row we copied forward.
        """
        if not self.legacy:
            return self.line
        record = pr.published_record.RowRecord(
            self.key, self.location, self.text_key, self.outcome)
        return pr.published_record.mark_row(self.line, record, ours=self.ours)


def published_rows(body: str) -> list[PublishedRow]:
    """Every row of a published summary body, in order, with its record."""
    if pr.published_record.FORMAT_MARKER in body:
        return [_marked_row(line) for line in table_rows(body)]
    return [_legacy_row(line) for line in table_rows(body)]


def _marked_row(line: str) -> PublishedRow:
    record = pr.published_record.read_row_marker(line)
    if record is None:
        return PublishedRow(line, key="", ours=False)
    ours = pr.published_record.row_is_intact(line, record)
    return PublishedRow(
        line, record.key, record.location, record.text_key,
        record.outcome if ours else None, ours=ours,
    )


def _legacy_row(line: str) -> PublishedRow:
    """A row of a comment written before rows were marked, recovered from its cells.

    ceiling: identity and authorship are re-derived from rendered text here, with
    every fragility the marker exists to remove. Confined to comments written
    before the marker, and to this function. Upgrade trigger: once
    `gh search prs --author @me --state open --created "<2026-10-02"` returns
    nothing, delete this branch, `summary_model.is_generated_action`, and the
    cell readers above it.

    A row with no Action cell to read is not a hand edit but a row whose shape
    this renderer no longer produces, so it reads as ours and re-rendering it is
    the repair.
    """
    cells = core.markdown.row_cells(line)
    cell = row_action_cell(line)
    ours = not cell or pr.summary_model.is_generated_action(cell)
    location = text_key = ""
    if pr.summary_model.ITEM_ANCHOR_RE.search(line):
        location = pr.published_record.fingerprint(pr.summary_model.location_from_cells(cells))
        if not location:
            text_key = pr.published_record.fingerprint(
                pr.summary_model.text_key_from_cells(cells))
    return PublishedRow(
        line,
        key=pr.published_record.fingerprint(row_key(line)),
        location=location,
        text_key=text_key,
        outcome=pr.summary_model.action_outcome(cell) if ours else None,
        ours=ours,
        legacy=True,
    )


def carried_over_rows(
    published: str, fresh: str, held_elsewhere: frozenset[str] = frozenset(),
    folded: frozenset[str] = frozenset(),
    folded_texts: frozenset[str] = frozenset(),
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

    ``folded_texts`` is the same question for the items the fold reached on
    text rather than on location — see `summary_model.folded_restatements`. An
    item with no line has no location key at all, so `folded` can never name it
    and its published row would come back every round for the life of the PR.

    The ceiling on `finding_location` still bounds this — an item row colliding
    with an unrelated fresh thread row is dropped rather than carried.

    Folding is decided on the record's fold keys, which a marked row declares
    and a legacy row recovers from its cells. Both are hashed, so ``folded`` and
    ``folded_texts`` are hashed here to compare in the same space.

    A row whose identity nothing declares — a person's row in a marked comment
    — is always carried: nothing could have reproduced it, and dropping it would
    delete what they wrote.
    """
    if not published:
        return []
    fresh_keys = {row.key for row in published_rows(fresh) if row.key}
    folded_hashed = {pr.published_record.fingerprint(f) for f in folded}
    folded_texts_hashed = {pr.published_record.fingerprint(t) for t in folded_texts}

    def was_folded(row: PublishedRow) -> bool:
        if row.location:
            return row.location in folded_hashed
        return bool(row.text_key) and row.text_key in folded_texts_hashed

    return [
        row.lifted() for row in published_rows(published)
        if not row.key or (
            row.key not in fresh_keys
            and row.key not in held_elsewhere
            and not was_folded(row)
        )
    ]


def hand_written_rows(published: Sequence[str], fresh: str) -> list[pr.summary_model.HeldRow]:
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
    row it covers. A row whose digest no longer matches the text we wrote was
    edited by a person — in any cell, not only the Action cell — and stays for
    the life of the PR. Deleting the row from the comment is the way to hand it
    back to the renderer: a key no comment holds is always written fresh.

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
    fresh_by_key = {row.key: row.line for row in published_rows(fresh) if row.key}
    newest_by_key = {
        row.key: row for body in published for row in published_rows(body) if row.key
    }
    return [
        pr.summary_model.HeldRow(key, row.lifted(), fresh_by_key[key])
        for key, row in newest_by_key.items()
        if not row.ours and key in fresh_by_key
    ]
