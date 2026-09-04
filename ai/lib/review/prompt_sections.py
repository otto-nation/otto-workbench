"""What a prompt says about the PR, rendered from what was collected.

Every function here takes what it renders and returns markdown, so a phase
composes the sections it wants without knowing how any of them is built —
nothing in this module decides which sections a phase asks for or what order
they run in.

Which sections a phase asks for is `review_prompt`'s, and how much room they
get is `review_budget`'s. What a prompt says about the *prior* review — the
scoped findings, the ledger instruction, the thread annotations — is
`review_prompt_prior`'s.

Not to be confused with `review_sections`, which is the posting pipeline's
config-driven registry of sections already written to a review document —
that module reads what an agent wrote, this one decides what an agent is
shown before it writes anything.
"""

# doc-group: pipeline

from __future__ import annotations

import json

from git import client as git_client
from agent.types import EFFORT_PRESETS
from gh.types import FILE_STAT_FMT, PRContext, PRMetadata
from core.phases import Effort
from review.budget import (
    FileFit, MAX_DELTA_LIST_ENTRIES, MAX_REVIEW_BODY_LEN, MIN_DELTA_DIFF_BYTES,
)
from review.collect import scope_diff, truncate_diff
from review.reply_threads import ReplyThreads
from review.scout import format_leads_block, is_scout_output, parse_scout_output
from review.types import PreflightData, ReplyState, ReviewJob


def _build_pr_header(
    pr: PRMetadata, ctx: PRContext,
    effort: Effort,
    file_filter: list[str] | None = None,
    viewer_role: str = "",
) -> str:
    if file_filter:
        filter_set = set(file_filter)
        scoped_files = [f for f in pr.files if f["path"] in filter_set]
        additions = sum(f["additions"] for f in scoped_files)
        deletions = sum(f["deletions"] for f in scoped_files)
        size_line = f"- **Size:** +{additions} -{deletions} across {len(scoped_files)} files (of {pr.changed_files} total)"
    else:
        scoped_files = None
        size_line = f"- **Size:** +{pr.additions} -{pr.deletions} across {pr.changed_files} files"

    role_line = f"- **Your role:** {viewer_role}" if viewer_role else ""

    lines = [
        "## PR metadata",
        f"- **Title:** {pr.title}",
        f"- **Branch:** {pr.head} → {pr.base}",
        size_line,
    ]
    if role_line:
        lines.append(role_line)
    lines += [
        "",
        "### Description",
        pr.body or "_No description provided._",
        "",
        "### Commits",
        ctx.commits or "_No commits._",
    ]

    if scoped_files is not None:
        sorted_files = sorted(scoped_files, key=lambda f: f["additions"] + f["deletions"], reverse=True)
        file_stats = "\n".join(FILE_STAT_FMT.format(**f) for f in sorted_files)
    else:
        file_stats = pr.file_stats(EFFORT_PRESETS[effort].multi_phase_line_threshold)
    if file_stats:
        lines += ["", "### File breakdown (sorted by churn)", file_stats]

    return "\n".join(lines)


def _format_reviews(raw_json: str) -> str:
    try:
        reviews = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return "_None._"
    if not reviews:
        return "_None._"
    lines = []
    for r in reviews:
        user = r.get("user", "?")
        state = r.get("state", "?")
        body = (r.get("body") or "").replace("\n", " ").strip()
        if len(body) > MAX_REVIEW_BODY_LEN:
            body = body[:MAX_REVIEW_BODY_LEN] + "..."
        entry = f"- @{user}: **{state}**"
        if body:
            entry += f" — {body}"
        lines.append(entry)
    return "\n".join(lines)


def _truncate_body(comment: dict) -> str:
    body = (comment.get("body") or "").replace("\n", " ").strip()
    if len(body) > MAX_REVIEW_BODY_LEN:
        body = body[:MAX_REVIEW_BODY_LEN] + "..."
    return body


def _format_review_comments(raw_json: str) -> str:
    try:
        comments = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return "_None._"
    if not comments:
        return "_None._"

    threads: dict[int, list[dict]] = {}
    roots: list[dict] = []
    for c in comments:
        reply_to = c.get("in_reply_to_id")
        if reply_to:
            threads.setdefault(reply_to, []).append(c)
        else:
            roots.append(c)

    lines = []
    for root in roots:
        cid = root.get("id", 0)
        path = root.get("path", "")
        line_num = root.get("line", "")
        user = root.get("user", "?")
        body = _truncate_body(root)
        loc = f"`{path}:{line_num}`" if path else "(general)"
        lines.append(f"- {loc} @{user}: {body}")
        for reply in threads.get(cid, []):
            ruser = reply.get("user", "?")
            rbody = _truncate_body(reply)
            lines.append(f"  - @{ruser}: {rbody}")

    return "\n".join(lines)


def _format_general_comments(raw_json: str) -> str:
    try:
        comments = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return "_None._"
    if not comments:
        return "_None._"
    lines = []
    for c in comments:
        user = c.get("user", "?")
        body = (c.get("body") or "").replace("\n", " ").strip()
        if len(body) > MAX_REVIEW_BODY_LEN:
            body = body[:MAX_REVIEW_BODY_LEN] + "..."
        lines.append(f"- @{user}: {body}")
    return "\n".join(lines)


def _build_reviews_section(ctx: PRContext) -> str:
    return (
        "\n## Existing reviews and comments\n"
        "Skip these — do NOT re-fetch from the GitHub API. "
        "This data is current as of script invocation.\n\n"
        "### Submitted reviews\n"
        f"{_format_reviews(ctx.reviews)}\n\n"
        "### Inline review comments\n"
        f"{_format_review_comments(ctx.review_comments)}\n\n"
        "### General PR comments\n"
        f"{_format_general_comments(ctx.comments)}"
    )


_THREAD_STATE_ORDER = [
    (ReplyState.CONTESTED, "Contested — re-evaluate in light of the author's explanation"),
    (ReplyState.REPLIED, "Author replied — review the response"),
    (ReplyState.ACKNOWLEDGED, "Acknowledged — verify the fix exists in the diff"),
    (ReplyState.RESOLVED, "Resolved on GitHub — drop from this review"),
    (ReplyState.UNREPLIED, "No reply — carry forward as before"),
]


def _format_thread_item(t: dict, state: str) -> list[str]:
    fid = t.get("finding_id", "")
    loc = t.get("path", "")
    if t.get("line"):
        loc += f":{t['line']}"
    label = f"[{fid}] " if fid else ""
    lines = [f"- {label}`{loc}`" if loc else f"- {label}(general comment)"]
    if state in (ReplyState.CONTESTED, ReplyState.REPLIED):
        for r in t.get("replies", []):
            body = r.get("body", "").replace("\n", " ")[:MAX_REVIEW_BODY_LEN]
            lines.append(f"  > @{r.get('author', '?')}: {body}")
    return lines


def _build_reply_threads_section(
    reply_threads: ReplyThreads | None,
    file_filter: list[str] | None = None,
) -> str:
    threads = reply_threads.threads if reply_threads else []
    if file_filter:
        filter_set = set(file_filter)
        threads = [t for t in threads if t.get("path", "") in filter_set]
    if not threads:
        return ""

    grouped: dict[str, list[dict]] = {}
    for t in threads:
        grouped.setdefault(t["state"], []).append(t)

    parts = [
        "## Reply thread context",
        "",
        "The PR author has replied to some of your prior review comments.",
        "Use this to decide whether to carry forward, drop, or re-evaluate each finding.",
        "",
    ]
    for state, heading in _THREAD_STATE_ORDER:
        items = grouped.get(state, [])
        if not items:
            continue
        parts.append(f"### {heading}")
        parts.append("")
        for t in items:
            parts.extend(_format_thread_item(t, state))
        parts.append("")

    return "\n".join(parts)


def _build_env_section(
    wt_path: str, preflight: PreflightData | None = None,
    files: FileFit | None = None,
) -> str:
    """Where the branch is checked out, and how much of it the prompt carries.

    ``files`` is the budget's fit, if one ran. A fit that dropped every file
    is the budget's first lever having fired all the way: the diffs are still
    inlined but no file contents are, so every changed file is one the agent
    has to open. Telling it otherwise is worse than telling it nothing — an
    agent that reads "file contents are in the Pre-collected data section"
    does not go looking for the ones that are not. A fit that dropped only
    some files, or a preflight with files never collected at all, takes the
    middle wording instead — the two are indistinguishable to the agent, which
    reads "Files not pre-collected" either way.

    A fit with nothing included is not always a fit that dropped everything:
    a `file_filter` scoping the prompt to zero collected files also has an
    empty `included`, with nothing omitted either. Only `files.omitted` being
    non-empty means the budget actually took something away, so that is the
    check — `any_included` alone cannot tell the two apart.
    """
    dropped_everything = bool(files and files.omitted) and not files.any_included
    if preflight and dropped_everything:
        return f"""
## Environment
PR branch checked out at: {wt_path}
Diffs are pre-collected; file contents are not. Read every file listed under "Files not pre-collected" directly from this path."""
    has_omissions = bool(preflight and preflight.omitted_files) or bool(files and files.omitted)
    if preflight and not has_omissions:
        return f"""
## Environment
PR branch checked out at: {wt_path}
File contents and diffs are in the Pre-collected data section. Use Read/Bash only for files NOT in the PR (callers, tests, cross-references)."""
    if preflight and has_omissions:
        return f"""
## Environment
PR branch checked out at: {wt_path}
Diffs and some file contents are pre-collected. Files listed under "Files not pre-collected" must be read directly from this path."""
    return f"""
## Environment
PR branch checked out at: {wt_path}
Read source files directly from this path. Do NOT fetch files via the GitHub API."""


def _build_omitted_guidance(
    preflight: "PreflightData | None", skip_omitted: bool = False,
    files: FileFit | None = None,
) -> str:
    """The sentence that sends the agent to read what the prompt left out.

    ``files`` is the budget's fit, if one ran; a fit that dropped any file
    adds those paths to the "Files not pre-collected" list, so the batch read
    is owed even when ``preflight.omitted_files`` is empty. ``skip_omitted`` is
    the opposite case and suppresses the read, but only while the budget kept
    every file it fit: at an effort level that does not review the large
    files, naming them invites work the run has declined — whereas a file the
    *budget* took out is one the phase does review and now has nowhere else to
    get.
    """
    dropped_any = bool(files and files.omitted)
    if not preflight or not (preflight.omitted_files or dropped_any):
        return ""
    if skip_omitted and not dropped_any:
        return " Some large files were excluded — they are not reviewed at this effort level."
    return (
        ' First, read all files listed under "Files not pre-collected"'
        " in a single parallel batch."
    )


def _build_state_context_section(job: ReviewJob) -> str:
    parts: list[str] = []

    if getattr(job.pr, "is_draft", False):
        parts.append("- **Draft PR** — focus on design and approach, not polish")
    if getattr(job.pr, "labels", None):
        parts.append(f"- **Labels:** {', '.join(job.pr.labels)}")

    state = job.pr_state_data
    if state is not None:
        parts.extend(_state_ci_lines(state.ci, job.pr))
        parts.extend(_state_comment_lines(state.comments))

    if not parts:
        return ""
    return "\n## PR context\n" + "\n".join(parts)


def _state_ci_lines(ci, pr: PRMetadata) -> list[str]:
    if not ci.updated_at or not ci.conclusion or ci.conclusion == "success":
        return []
    ci_line = f"- **CI: {ci.conclusion}**"
    if ci.failure_count:
        kinds = ", ".join(f"{k}: {v}" for k, v in ci.failure_kinds.items())
        ci_line += f" — {ci.failure_count} failure(s)"
        if kinds:
            ci_line += f" ({kinds})"
    return [ci_line] + _build_ci_failure_items(ci, pr)


def _state_comment_lines(comments) -> list[str]:
    if not comments.updated_at:
        return []
    parts: list[str] = []
    contested = comments.by_state.get("contested", 0)
    new_threads = comments.by_state.get("new", 0)
    if contested or new_threads:
        parts.append(f"- **Open review threads:** {new_threads} new, {contested} contested")
    if comments.blocking_reviewers:
        reviewers = ", ".join(f"@{r}" for r in comments.blocking_reviewers)
        parts.append(f"- **Blocking reviewers:** {reviewers}")
    if comments.has_approvals:
        parts.append("- **Has approvals**")
    return parts


_CI_ITEM_CAP = 10


def _build_ci_failure_items(ci, pr: PRMetadata) -> list[str]:
    latest_run_id = ci.latest_run_id or ci.last_run_id
    if not latest_run_id or not ci.runs:
        return []
    run = ci.runs.get(latest_run_id)
    if not run:
        return []

    pr_files = {f["path"] for f in pr.files}
    in_pr = []
    outside_count = 0
    for group in run.failures.values():
        if group.kind.value in ("infra", "flaky"):
            continue
        in_group, out_group = _classify_failure_items(group, pr_files)
        in_pr.extend(in_group)
        outside_count += out_group

    items = in_pr[:_CI_ITEM_CAP]
    remainder = len(in_pr) - _CI_ITEM_CAP + outside_count
    if remainder > 0:
        items.append(f"  - +{remainder} more failure(s) not shown")
    return items


def _classify_failure_items(group, pr_files: set[str]) -> tuple[list[str], int]:
    in_pr: list[str] = []
    outside = 0
    for item in group.items:
        if not item.file:
            continue
        loc = f"{item.file}:{item.line}" if item.line else item.file
        headline = item.headline or item.annotation[:80]
        outcome = item.outcome.value if item.outcome else "new"
        entry = f"  - `{loc}` — {headline} [{group.kind.value}, {outcome}]"
        if item.file in pr_files:
            in_pr.append(entry)
        else:
            outside += 1
    return in_pr, outside


def _build_holistic_block(
    holistic_content: str, changed_files: int,
) -> str:
    if not holistic_content:
        return ""

    if is_scout_output(holistic_content):
        leads, no_scrutiny = parse_scout_output(holistic_content)
        block = format_leads_block(leads, no_scrutiny)
        if block:
            return f"\n## Scout context\n{block}"
        return ""

    return (
        f"\n## Holistic context\n"
        f"The following assessment was produced by scanning all "
        f"{changed_files} files in this PR.\n"
        f"Use it to inform your detailed review — especially the flags section.\n\n"
        f"{holistic_content}"
    )


def _delta_file_list(heading: str, paths: list[str]) -> list[str]:
    shown = sorted(paths)[:MAX_DELTA_LIST_ENTRIES]
    lines = ["", heading] + [f"- `{p}`" for p in shown]
    if len(paths) > len(shown):
        lines.append(f"- _+{len(paths) - len(shown)} more not listed_")
    return lines


def _build_delta_section(
    preflight: PreflightData | None,
    file_filter: list[str] | None = None,
    max_bytes: int | None = None,
) -> str:
    """What changed since the prior review, for an incremental re-review.

    ``file_filter`` narrows it to one group's files. ``max_bytes`` is the
    section's share of the prompt budget: the delta diff shrinks to fit inside
    it and is dropped when what is left will not hold a readable hunk. The
    prose and the two file lists are not budgeted — capped at
    ``MAX_DELTA_LIST_ENTRIES`` they cannot exceed ~20KB, and a section that
    cannot say which commit it is comparing against is worth no bytes at all.
    """
    if not preflight or not preflight.prior_head_sha:
        return ""
    prior = git_client.abbrev(preflight.prior_head_sha)
    delta_files = preflight.delta_files
    if file_filter:
        filter_set = set(file_filter)
        delta_files = [f for f in delta_files if f in filter_set]
        unchanged = sorted(filter_set - set(delta_files))
    else:
        all_pr_files = set(preflight.file_contents.keys()) | set(preflight.omitted_files)
        unchanged = sorted(all_pr_files - set(delta_files))

    head = [
        "## Incremental review context",
        "",
        f"This is an **incremental review**. A prior review exists at commit `{prior}`.",
        f"{len(delta_files)} file(s) changed since the prior review.",
        "",
        "**Focus your review on the delta changes below.** For prior findings on unchanged files,",
        "carry them forward unless you have evidence they were fixed.",
    ]

    if preflight.delta_commit_log and not file_filter:
        head += [
            "",
            "### New commits since prior review",
            "",
            "```",
            preflight.delta_commit_log,
            "```",
        ]

    tail: list[str] = []
    if delta_files:
        tail += _delta_file_list("### Files modified since prior review", delta_files)
    if unchanged:
        tail += _delta_file_list(
            "### Files unchanged since prior review (prior findings still apply)", unchanged,
        )

    diff_text = ""
    if preflight.delta_diff:
        diff_text = (
            scope_diff(preflight.delta_diff, file_filter) if file_filter
            else preflight.delta_diff
        )
    if diff_text and max_bytes is not None:
        fence = ["", "### Delta diff", "", "```diff", "", "```"]
        room = max_bytes - len("\n".join(head + fence + tail).encode())
        diff_text = truncate_diff(diff_text, room).text if room >= MIN_DELTA_DIFF_BYTES else ""

    diff_block = ["", "### Delta diff", "", "```diff", diff_text, "```"] if diff_text else []
    return "\n".join(head + diff_block + tail)


def _is_incremental(job: ReviewJob) -> bool:
    return bool(job.preflight and job.preflight.prior_head_sha)


def _build_issue_section(issue_link: str, issue_context: str) -> str:
    if issue_link:
        return f"\n## Related issue\n{issue_link}"
    if issue_context:
        return f"\n## Related issue\n{issue_context}"
    return ""
