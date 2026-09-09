"""The code and history a reviewer's comment has to be read against.

A review thread is a claim about a line, and neither triage nor a fix agent can
judge one from the comment alone. Four readings answer that, and they are here
together because they are the same subject — what the branch looks like around
the thread — asked at two granularities:

- :func:`code_context_for_thread` for one location, and
  :func:`gather_code_context` for a whole triage round
- :func:`diff_context_for_file` for what the PR changed there
- :func:`branch_commit_log` for what earlier rounds already did

The last one is not incidental. Triage reads code at current HEAD, which already
contains fixes made in earlier rounds of the same cycle, so without the log it
cannot tell a suggestion that was acted on from one that never applied — and it
defaults to calling the reviewer wrong.
"""

# doc-group: publishing

from __future__ import annotations

from pathlib import Path

from git import client as git_client
from git import topology as git_topology
from pr.thread_models import ReportThread

# Lines either side of a cited line. Wide enough to show the construct the
# comment is about, narrow enough that a triage round over forty threads still
# fits a prompt.
TRIAGE_CONTEXT_LINES = 10
DIFF_CONTEXT_MAX_LINES = 100


def _window(file_path: str, line: int, repo_dir: Path) -> tuple[str, int, int]:
    """The source around `line`, and the 1-based bounds it was taken from.

    One reader for both framings below. They differ in how the snippet is
    labelled — a fenced block for one thread, a `--- path:from-to ---` header
    for a whole round — and differed in nothing else, which is how one of them
    could have drifted to a different window than the other.

    Every failure to read is the same answer: no context. A file the comment
    names but the tree does not have is the ordinary case after a rename, not
    an error worth stopping a triage round for.
    """
    if not file_path or line <= 0:
        return "", 0, 0
    full_path = repo_dir / file_path
    if not full_path.is_file():
        return "", 0, 0
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", 0, 0
    start = max(0, line - 1 - TRIAGE_CONTEXT_LINES)
    end = min(len(lines), line + TRIAGE_CONTEXT_LINES)
    return "\n".join(lines[start:end]), start + 1, end


def code_context_for_thread(file_path: str, line: int | str, repo_dir: Path) -> str:
    """The source around one thread's location, fenced for a prompt."""
    snippet, _, _ = _window(file_path, int(line) if line else 0, repo_dir)
    return f"```\n{snippet}\n```" if snippet else ""


def gather_code_context(threads: list[ReportThread], repo_dir: Path) -> str:
    """The source around every thread's location, one labelled block each.

    Labelled rather than fenced because the reader is one prompt covering many
    threads: without the path and line range on each block, a model has no way
    to tell which snippet belongs to which thread.
    """
    snippets = []
    for thread in threads:
        snippet, start, end = _window(
            thread.file, int(thread.line or 0), repo_dir)
        if snippet:
            snippets.append(f"--- {thread.file}:{start}-{end} ---\n{snippet}\n---")
    return "\n".join(snippets)


def diff_context_for_file(
    file_path: str, wt_path: Path, default_branch: str | None = None,
) -> str:
    """Get the PR diff for a file, truncated to DIFF_CONTEXT_MAX_LINES.

    An omitted default_branch is resolved rather than assumed to be "main", so a
    caller that does not know the trunk gets the same answer as one that does
    instead of an empty diff from `origin/main` in a `master` repository.
    """
    if not file_path:
        return ""
    default_branch = default_branch or git_topology.default_branch_cached(wt_path)
    diff = git_client.out(
        "diff", f"origin/{default_branch}", "--", file_path, cwd=wt_path)
    if not diff:
        return ""
    lines = diff.splitlines()
    total = len(lines)
    if total > DIFF_CONTEXT_MAX_LINES:
        lines = lines[:DIFF_CONTEXT_MAX_LINES]
        lines.append(f"... ({total - DIFF_CONTEXT_MAX_LINES} more lines)")
    return "```diff\n" + "\n".join(lines) + "\n```"


def branch_commit_log(wt_path: Path | None) -> str:
    """Commit subjects on this branch, newest first.

    Triage sees code context from current HEAD, which already contains fixes made
    in earlier rounds of the same review cycle.  Without the log it cannot tell a
    suggestion that was acted on from one that never applied, and defaults to
    calling the reviewer wrong.
    """
    if not wt_path:
        return ""
    base = f"origin/{git_topology.default_branch_cached(wt_path)}"
    return git_client.out("log", "--format=%h %s", f"{base}..HEAD", cwd=wt_path)


def thread_comment_text(comments: list) -> str:
    """Format a thread's comments as quoted conversation."""
    parts = []
    for c in comments:
        author = c.get("author", {}).get("login", "unknown")
        body = c.get("body", "").strip()
        if body:
            quoted = "\n".join(f"> {line}" for line in body.splitlines())
            parts.append(f"**@{author}:**\n{quoted}")
    return "\n\n".join(parts)
