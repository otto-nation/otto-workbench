"""What `git diff --numstat` says a change set touched.

Read by every caller that has to size a diff before anything looks at it — the
review pipeline from a worktree, and the GitHub reads from the API's own
numstat. One reader so the two agree on what a binary file counts as.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Numstat:
    """What ``git diff --numstat`` says a change set touched.

    ``files`` is one ``{path, additions, deletions}`` entry per line, in the
    order git listed them; ``additions`` and ``deletions`` are the totals over
    all of them.
    """

    files: list[dict]
    additions: int
    deletions: int


def parse_numstat(numstat_text: str) -> Numstat:
    """Read ``git diff --numstat`` output into per-file and total counts.

    A binary file's counts are ``-``; they land as zero rather than being
    dropped, so the file still appears in the review's file list.
    """
    files = []
    total_add = 0
    total_del = 0
    for line in numstat_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add = int(parts[0]) if parts[0] != "-" else 0
        delete = int(parts[1]) if parts[1] != "-" else 0
        files.append({"path": parts[2], "additions": add, "deletions": delete})
        total_add += add
        total_del += delete
    return Numstat(files=files, additions=total_add, deletions=total_del)
