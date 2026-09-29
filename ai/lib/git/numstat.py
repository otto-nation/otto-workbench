"""What `git diff --numstat` says a change set touched.

Read by every caller that has to size a diff before anything looks at it — the
review pipeline from a worktree, and the GitHub reads from the API's own
numstat. One reader so the two agree on what a binary file counts as.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass

# What one deleted line costs to review, against an added line's 1.0.
#
# Not zero: a deletion still has to be reviewed for what it broke — callers
# left dangling, a test that no longer covers anything, a doc still describing
# it. But that work scales with the number of deleted *regions* rather than
# with their length, and one function removed costs the same to check whether
# it was five lines or five hundred. Pricing deletions like additions is what
# bought the five-agent pipeline for branches that mostly removed code.
#
# A reader can tell whether 0.25 was right from the trail, which records the
# weighted count beside the real one: a single-agent review that visibly ran
# out of room says it is too aggressive, and a branch of pure deletions still
# fanning out says it is too timid.
#
# ceiling: a line count standing in for a region count. Upgrade to counting
# hunks if the review collector ever parses diff ranges, which is the same
# data this approximates and would make the weight unnecessary rather than
# better-tuned.
DELETION_WEIGHT = 0.25


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


def weighted_lines(additions: int, deletions: int) -> int:
    """How much review work a diff of this shape is, in added-line equivalents.

    One owner for the weighting because two callers price a diff — the review
    pipeline sizing a run from a worktree, and the same pipeline sizing one
    from the GitHub API's counts — and a scale that disagreed between them
    would put the same branch on different pipelines depending on where its
    numbers came from.

    Deliberately not applied to group sizing. `review.grouping` asks a
    different question with the same arithmetic: not "how much review is
    this" but "how much will this prompt hold", and a deleted line occupies
    the same bytes in a diff as an added one. Weighting there would make the
    planner under-count a prompt against a budget whose overrun is a hard API
    rejection rather than a slightly wrong pipeline.
    """
    return round(additions + DELETION_WEIGHT * deletions)


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
