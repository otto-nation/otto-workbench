"""Markdown table cells, written and read back.

A table this workbench publishes is not only output: the summary comment on a
PR is re-read on the next round to recover which rows it already carried, so
the same cell text has to survive a round trip through GitHub. That makes the
escaping and the un-escaping one subject with two halves, and splitting them
across the writer and the reader is how they drift.

Both halves are here, below any package that knows what a row *means*. What a
cell says is a domain question; that a pipe inside one has to be escaped, and
that a link renders as `[label](url)`, is not.
"""

# doc-group: platform

from __future__ import annotations

import re

# A cell boundary is an unescaped pipe. The lookbehind is what lets a summary
# containing a literal pipe survive being split back into cells.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def escape_cell(text: str) -> str:
    """Escape a literal pipe so free-form text cannot break out of its cell.

    Summaries and reasons are prose written per round, and nothing constrains
    them to avoid a pipe. One unescaped pipe shifts every later cell in the row,
    which breaks the rendered table and corrupts the key that carry-over reads
    back out of the published comment — a row whose key never matches itself
    looks new every round.
    """
    return text.replace("|", "\\|")


def row_cells(row: str) -> list[str]:
    """The cells of one rendered table row, stripped of their padding."""
    return [cell.strip() for cell in CELL_SPLIT_RE.split(row.strip().strip("|"))]


def plain_cell(cell: str) -> str:
    """A cell's text with link targets and code ticks removed.

    What is left is what a reader sees, which is the half of the cell that
    identifies it: the SHA inside a permalink changes every round, and a cell
    compared with the target still attached would never match itself twice.
    """
    return LINK_RE.sub(r"\1", cell).replace("`", "").strip()
