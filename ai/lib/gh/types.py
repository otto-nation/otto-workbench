"""What a GitHub PR read returns: the PR's own metadata, and its conversation.

Below the `gh`-layer module that fetches both, so the shapes a read answers
with sit at or beneath the layer that answers. `review.collect` builds
the same `PRMetadata` from local git for a branch with no PR behind it, which is
why the type is not spelled in terms of the API's field names.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass, field


# One file's churn, as the prompt and the review header both list it.
FILE_STAT_FMT = "  - {path} (+{additions} -{deletions})"


@dataclass
class PRMetadata:
    title: str
    body: str
    head: str
    base: str
    head_sha: str
    additions: int
    deletions: int
    changed_files: int
    files: list[dict]
    is_draft: bool = False
    labels: list[str] = field(default_factory=list)
    author: str = ""

    @property
    def total_lines(self):
        return self.additions + self.deletions

    def file_stats(self, line_threshold: int):
        """The per-file churn breakdown, or "" for a PR small enough not to need it.

        The threshold is an argument rather than a module constant because
        ``EFFORT_PRESETS`` varies it by effort; re-deriving it here is what let
        the two owners disagree.
        """
        if self.total_lines <= line_threshold:
            return ""
        sorted_files = sorted(
            self.files, key=lambda f: f["additions"] + f["deletions"], reverse=True
        )
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in sorted_files
        )

    @property
    def all_files_formatted(self):
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in self.files
        )


@dataclass
class PRContext:
    commits: str = ""
    reviews: str = "[]"
    review_comments: str = "[]"
    comments: str = "[]"
