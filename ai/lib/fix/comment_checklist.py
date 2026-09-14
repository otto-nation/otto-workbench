"""What the comment fix agent is shown: the checklist, and where to read from.

The comments pass's half of the prompt. `fix.comments` is what the engine runs
and what happens to the answer; this is the question — one section per thread
or decomposed comment item, carrying the conversation, the code around the line
and the PR's diff for the file.

The heading, the id marker and the outcome boxes are deliberately not here.
They belong to `fix.tracking`, which is also what reads them back, so the two
halves of the format cannot drift apart. What this contributes is the body
under each heading.
"""

# doc-group: pipeline

from __future__ import annotations

from pathlib import Path

from fix import types as fix_types
from git import topology as git_topology
from pr import sync as pr_sync
from pr import thread_context
from pr.thread_models import CommentItem, ReportThread


def fix_items(
    fixable: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo_dir: Path,
    fixable_items: list[CommentItem] | None = None,
    default_branch: str | None = None,
) -> list[fix_types.FixItem]:
    """The checklist entries this domain hands the fix agent.

    This domain's whole contribution to the file is the body of each section —
    the conversation, the code around the line, and the PR's diff for the file.
    The heading, the id marker and the outcome boxes belong to `fix_tracking`,
    which is also what reads them back, so the two halves of the format cannot
    drift apart.
    """
    diff_cache: dict[str, str] = {}

    def _shared_context(file: str, line: int) -> str:
        code_ctx = thread_context.code_context_for_thread(file, line, repo_dir) if file and line else ""
        if file and file not in diff_cache:
            diff_cache[file] = thread_context.diff_context_for_file(file, repo_dir, default_branch)
        diff_ctx = diff_cache.get(file, "")
        block = f"**Code context:**\n{code_ctx}\n\n" if code_ctx else ""
        return block + (f"**PR diff for this file:**\n{diff_ctx}\n\n" if diff_ctx else "")

    items: list[fix_types.FixItem] = []
    for thread in fixable:
        report_thread = threads_by_id.get(thread.id)
        conversation = thread_context.thread_comment_text(report_thread.comments if report_thread else [])
        body = f"**Summary:** {thread.summary}\n\n" if thread.summary else ""
        if conversation:
            body += f"**Conversation:**\n{conversation}\n\n"
        items.append(fix_types.FixItem(
            id=thread.id, file=thread.file, line=thread.line,
            label=f"@{thread.reviewer}",
            body=body + _shared_context(thread.file, thread.line),
        ))

    for item in (fixable_items or []):
        body = f"**Summary:** {item.summary}\n\n" if item.summary else ""
        if item.body:
            quoted = "\n".join(f"> {ln}" for ln in item.body.splitlines())
            body += f"**From comment by @{item.reviewer}:**\n{quoted}\n\n"
        items.append(fix_types.FixItem(
            id=item.id, file=item.file, line=item.line,
            label=f"@{item.reviewer}",
            body=body + _shared_context(item.file, item.line),
        ))

    return items


def main_worktree_block(main_wt: Path | None) -> str:
    """What the prompt says about the default-branch checkout, when there is one.

    Empty otherwise: a fix pass with no second worktree must not be told to read
    from a path that does not exist.
    """
    if not main_wt:
        return ""
    return (
        "## Main branch (read-only reference)\n"
        f"Checked out at: {main_wt}\n\n"
        "When a reviewer comment references code outside the PR diff — imports, "
        "callers, existing patterns, or shared utilities — read from this path "
        "to understand the baseline. Do not modify files in this worktree.\n"
    )


def find_and_update_main_worktree(wt_path: Path) -> Path | None:
    """Find the default-branch worktree and update it to match origin."""
    default_branch = git_topology.default_branch_cached(wt_path)

    main_wt = git_topology.find_worktree_for_branch(default_branch, str(wt_path))
    if not main_wt:
        return None

    pr_sync.fetch_and_reset(str(main_wt), default_branch)
    return main_wt
