"""Where a review's findings can be posted, given the PR's diff.

A finding whose path:line falls inside a diff hunk can be posted inline; one
outside every hunk is demoted to a file-level comment; one whose path is not
in the diff at all is skipped. `cli.review_positions` is the command over this;
`review.format.parse_diff_hunks` reads the diff.
"""

# doc-group: findings

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Positions:
    """Where each finding can be posted, once the diff has had its say.

    The field names are the payload's keys — the JSON this prints is the
    dataclass, so a caller reading the output and a caller reading the return
    value are looking at one shape.
    """

    valid: list[dict] = field(default_factory=list)
    file_level: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Every finding can be posted where the review put it."""
        return not self.file_level and not self.skipped


def validate(hunks, findings) -> Positions:
    """Classify findings as valid, file_level, or skipped."""
    valid, file_level, skipped = [], [], []

    for f in findings:
        path = f.get("path", "")
        line = f.get("line")

        if path not in hunks:
            skipped.append({**f, "reason": "path not in diff"})
        elif line is None:
            valid.append(f)
        elif not any(hunk.contains(line) for hunk in hunks[path]):
            file_level.append(
                {**f, "reason": f"line {line} not in any diff hunk"}
            )
        else:
            valid.append(f)

    return Positions(valid=valid, file_level=file_level, skipped=skipped)
