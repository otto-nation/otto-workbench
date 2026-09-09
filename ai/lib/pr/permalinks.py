"""Links back to the code a review comment is about, pinned so they keep pointing.

`git-operations.md` § Replying to Review Comments makes a SHA-pinned permalink a
standing requirement for every reply: a branch-relative blob URL drifts as the
branch moves, so a claim posted to a reviewer stops resolving to the code it was
making a claim about. This is the implementation that gets it right, including
the part that is easy to leave out — a line number is a coordinate in one tree,
and pinning one to a tree it was not read in sends the reviewer to whatever code
inherited the number.

Two questions are answered here rather than one, because a citation is only as
good as both: *where does this link point*, and *is there anything there*.
:func:`evidence_is_real` is the second, and it sits beside the linkers rather
than beside triage because the thing it guards is the link.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git import client as git_client
from pr.fix import ItemOutcome
from pr.thread_models import CommentItem, ReportThread


def blob_permalink(repo: str, sha: str, filepath: str, line: int = 0) -> str:
    """A blob URL pinned to a SHA, so the link still points at the cited code.

    Branch-relative blob URLs drift as the branch moves; a claim posted to a
    reviewer has to survive the next push. Omit line for a whole-file link.
    """
    url = f"https://github.com/{repo}/blob/{sha}/{filepath}"
    return f"{url}#L{line}" if line else url


def anchored_line(
    entry: CommentItem,
    filepath: str, line: int, sha: str, wt_path: Path | None,
) -> int:
    """`line` when it still points at the same code in `sha`, else 0.

    A line number is a coordinate in one tree. Both kinds an entry carries were
    read before the fix pass edited the branch — GitHub anchored the review
    comment at the head it was written against, and triage cited what it read
    at that same head — so pinning either to the tree the fix commit produced
    sends the reviewer to whatever code inherited the number.

    Unknown reads as no. An entry with no recorded tree, or one this call has no
    worktree to compare against, loses its anchor and keeps a whole-file link:
    the anchor is the part of the claim that has to be earned, and a file link
    still resolves. `git diff --quiet` answers per file rather than per line,
    which over-drops on a file changed elsewhere and never under-drops.
    """
    read_sha = getattr(entry, "read_sha", "")
    if not line or not read_sha:
        return 0
    if read_sha == sha:
        return line
    if not wt_path:
        return 0
    unchanged = git_client.ok(
        "diff", "--quiet", read_sha, sha, "--", filepath, cwd=wt_path)
    return line if unchanged else 0


def anchored_link(
    entry: CommentItem,
    repo: str, filepath: str, line: int, sha: str, wt_path: Path | None,
) -> str:
    """A markdown blob link to `filepath`, anchored only where the line holds."""
    anchor = anchored_line(entry, filepath, line, sha, wt_path)
    url = blob_permalink(repo, sha, filepath, anchor)
    label = f"{filepath}:{anchor}" if anchor else filepath
    return f"[`{label}`]({url})"


def evidence_is_real(repo_dir: Path, entry: CommentItem) -> bool:
    """Whether the entry cites a line that actually exists inside `repo_dir`.

    Both halves matter, and neither is checked by joining and stating the
    result.  Containment needs the resolved paths compared, because joining a
    directory with an absolute path discards the directory and a relative path
    made of `..` walks straight out of the tree — either way a citation from
    outside the repo would stat a real file and pass for verified.  The line
    number needs the file's length, because a permalink past EOF highlights
    nothing.
    """
    if not entry.has_evidence():
        return False
    root = repo_dir.resolve()
    try:
        cited = (root / entry.evidence_file).resolve()
        if not cited.is_relative_to(root) or not cited.is_file():
            return False
        with cited.open(encoding="utf-8", errors="replace") as fh:
            return entry.evidence_line <= sum(1 for _ in fh)
    except OSError:
        return False


def evidence_link(
    entry: CommentItem, repo: str, sha: str, wt_path: Path | None = None,
) -> str:
    """Render an entry's cited location as a markdown link, or "" if uncited."""
    if not entry.has_evidence() or not sha:
        return ""
    return anchored_link(
        entry, repo, entry.evidence_file, entry.evidence_line, sha, wt_path,
    )


def code_link(
    entry: CommentItem, repo: str, sha: str, wt_path: Path | None = None,
    verify_evidence: bool = True,
) -> str:
    """Link the code an entry is about, or "" when there is nothing to point at.

    Triage's citation is the better anchor where there is one, but entries
    rebuilt from stored thread records carry no citation at all.  Those still
    have the review comment's own file and line, which always resolve as a
    file — GitHub anchored the thread there — so a reply is never left
    asserting something with no link behind it.

    `verify_evidence` is what a caller turns off when an earlier gate already
    refused to let an uncitable verdict through; `wt_path` is still wanted in
    that case, because the line anchor is a separate question from whether the
    cited file exists.
    """
    if entry.has_evidence() and (
        not verify_evidence or wt_path is None or evidence_is_real(wt_path, entry)
    ):
        link = evidence_link(entry, repo, sha, wt_path)
        if link:
            return link
    if not entry.file or not sha:
        return ""
    return anchored_link(entry, repo, entry.file, entry.line, sha, wt_path)


@dataclass(frozen=True)
class CommentSource:
    """The top-level comment a decomposed item was split out of.

    Empty for anything that is not such an item — a review thread's id parses to
    no source at all — and `ok` is how a caller asks which it got. A type with a
    predicate rather than a pair of strings because "is this a comment item"
    and "which comment" are asked together everywhere they are asked.
    """

    type: str = ""
    id: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.id)

    def permalink(self, repo: str, pr_number: int) -> str | None:
        """The anchor a reader follows back to the comment, or None."""
        if not self.ok:
            return None
        base = f"https://github.com/{repo}/pull/{pr_number}"
        if self.type == "issue_comment":
            return f"{base}#issuecomment-{self.id}"
        if self.type == "review_body":
            return f"{base}#pullrequestreview-{self.id}"
        return None


def comment_item_source(entry: CommentItem | ItemOutcome) -> CommentSource:
    """The comment a decomposed item came from, empty when it is not one.

    Takes either shape because reconciliation asks it of the record directly,
    before an outcome has been rebuilt into an entry — an `ItemOutcome` carries
    no source of its own, so the synthetic id is all there is to read.
    """
    source_id = getattr(entry, "source_id", "")
    source_type = getattr(entry, "source_type", "")
    if not source_id and not source_type:
        # Parse the synthetic id: ic-{source_id}-{index} or rb-{source_id}-{index}
        eid = entry.id
        if eid.startswith("ic-"):
            source_type = "issue_comment"
            source_id = "-".join(eid.split("-")[1:-1])
        elif eid.startswith("rb-"):
            source_type = "review_body"
            source_id = "-".join(eid.split("-")[1:-1])
    return CommentSource(source_type, source_id)


def comment_item_permalink(
    entry: CommentItem,
    repo: str, pr_number: int,
) -> str | None:
    """Build a permalink for a decomposed comment item from its source comment."""
    return comment_item_source(entry).permalink(repo, pr_number)


def thread_permalink(
    entry: CommentItem,
    threads_by_id: dict[str, ReportThread],
    repo: str, pr_number: int,
) -> str | None:
    """Build a GitHub permalink for a review thread or comment item."""
    thread = threads_by_id.get(entry.id)
    if thread and thread.comments:
        db_id = thread.comments[0].get("databaseId")
        if db_id:
            return f"https://github.com/{repo}/pull/{pr_number}#discussion_r{db_id}"
    return comment_item_permalink(entry, repo, pr_number)
