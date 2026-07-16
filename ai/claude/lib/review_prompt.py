"""Prompt construction and template rendering for claude-review.

Handles building prompts for each review template: single, holistic, group,
synthesis, self-review, and self-review-synthesis. Includes section builders,
budget computation, and prompt size logging.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from string import Template

import json
import log
from review_common import (
    FILE_STAT_FMT, FILENAME_PROMPT_STATS,
    TEMPLATE_DIR_REL,
    TEMPLATE_ANGLES, TEMPLATE_DISPROVE, TEMPLATE_FIX,
    TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SCOUT, TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path,
)
from review_findings import BOLD_FINDING_ID_RE, annotate_prior_with_stable_ids
from review_scout import (
    filter_leads_for_group, format_leads_block,
    is_scout_output, parse_scout_output,
)
from review_preflight import (
    MAX_PROMPT_BYTES, MIN_DIFF_BYTES, NON_PREFLIGHT_OVERHEAD_BYTES,
    PRContext, PRMetadata, PreflightData, ReviewJob,
    THREAD_ACKNOWLEDGED, THREAD_CONTESTED, THREAD_REPLIED,
    THREAD_RESOLVED, THREAD_UNREPLIED,
    _scope_diff, build_project_context, format_preflight_data,
)

# ── Template rendering ────────────────────────────────────────────────────────

def _template_dir() -> Path:
    return Path(__file__).resolve().parent.parent / TEMPLATE_DIR_REL


def _build_pr_header(
    pr: PRMetadata, ctx: PRContext,
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
        if file_stats:
            lines += ["", "### File breakdown (sorted by churn)", file_stats]
    elif pr.file_stats:
        lines += ["", "### File breakdown (sorted by churn)", pr.file_stats]

    return "\n".join(lines)


MAX_REVIEW_BODY_LEN = 200


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
    (THREAD_CONTESTED, "Contested — re-evaluate in light of the author's explanation"),
    (THREAD_REPLIED, "Author replied — review the response"),
    (THREAD_ACKNOWLEDGED, "Acknowledged — verify the fix exists in the diff"),
    (THREAD_RESOLVED, "Resolved on GitHub — drop from this review"),
    (THREAD_UNREPLIED, "No reply — carry forward as before"),
]


def _format_thread_item(t: dict, state: str) -> list[str]:
    fid = t.get("finding_id", "")
    loc = t.get("path", "")
    if t.get("line"):
        loc += f":{t['line']}"
    label = f"[{fid}] " if fid else ""
    lines = [f"- {label}`{loc}`" if loc else f"- {label}(general comment)"]
    if state in (THREAD_CONTESTED, THREAD_REPLIED):
        for r in t.get("replies", []):
            body = r.get("body", "").replace("\n", " ")[:200]
            lines.append(f"  > @{r.get('author', '?')}: {body}")
    return lines


def _build_reply_threads_section(
    reply_threads: dict,
    file_filter: list[str] | None = None,
) -> str:
    threads = reply_threads.get("threads", [])
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


def _build_env_section(wt_path: str, preflight: PreflightData | None = None) -> str:
    if preflight and not preflight.omitted_files:
        return f"""
## Environment
PR branch checked out at: {wt_path}
File contents and diffs are in the Pre-collected data section. Use Read/Bash only for files NOT in the PR (callers, tests, cross-references)."""
    if preflight and preflight.omitted_files:
        return f"""
## Environment
PR branch checked out at: {wt_path}
Diffs and some file contents are pre-collected. Files listed under "Files not pre-collected" must be read directly from this path."""
    return f"""
## Environment
PR branch checked out at: {wt_path}
Read source files directly from this path. Do NOT fetch files via the GitHub API."""


def _build_omitted_guidance(preflight: "PreflightData | None", skip_omitted: bool = False) -> str:
    if not preflight or not preflight.omitted_files:
        return ""
    if skip_omitted:
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
    run = ci.runs.get(str(latest_run_id))
    if not run or not hasattr(run, "failures"):
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
    group_files: list[str] | None = None,
) -> str:
    if not holistic_content:
        return ""

    if is_scout_output(holistic_content) and group_files:
        leads, no_scrutiny = parse_scout_output(holistic_content)
        block = format_leads_block(leads, no_scrutiny, group_files=group_files)
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


def _build_delta_section(
    preflight: PreflightData | None,
    file_filter: list[str] | None = None,
) -> str:
    if not preflight or not preflight.prior_head_sha:
        return ""
    prior = preflight.prior_head_sha[:7]
    delta_files = preflight.delta_files
    if file_filter:
        filter_set = set(file_filter)
        delta_files = [f for f in delta_files if f in filter_set]
        unchanged = sorted(filter_set - set(delta_files))
    else:
        all_pr_files = set(preflight.file_contents.keys()) | set(preflight.omitted_files)
        unchanged = sorted(all_pr_files - set(delta_files))

    parts = [
        "## Incremental review context",
        "",
        f"This is an **incremental review**. A prior review exists at commit `{prior}`.",
        f"{len(delta_files)} file(s) changed since the prior review.",
        "",
        "**Focus your review on the delta changes below.** For prior findings on unchanged files,",
        "carry them forward unless you have evidence they were fixed.",
    ]

    if preflight.delta_commit_log and not file_filter:
        parts += [
            "",
            "### New commits since prior review",
            "",
            "```",
            preflight.delta_commit_log,
            "```",
        ]

    if preflight.delta_diff:
        diff_text = _scope_diff(preflight.delta_diff, file_filter) if file_filter else preflight.delta_diff
        if diff_text:
            parts += [
                "",
                "### Delta diff",
                "",
                "```diff",
                diff_text,
                "```",
            ]

    if delta_files:
        parts += ["", "### Files modified since prior review"]
        for f in sorted(delta_files):
            parts.append(f"- `{f}`")

    if unchanged:
        parts += ["", "### Files unchanged since prior review (prior findings still apply)"]
        for f in unchanged:
            parts.append(f"- `{f}`")

    return "\n".join(parts)


def _is_incremental(job: ReviewJob) -> bool:
    return bool(job.preflight and job.preflight.prior_head_sha)


def _build_issue_section(issue_link: str, issue_context: str) -> str:
    if issue_link:
        return f"\n## Related issue\n{issue_link}"
    if issue_context:
        return f"\n## Related issue\n{issue_context}"
    return ""


_FINDING_LINE_RE = re.compile(
    r"- (?:\[[ x]\] )?"                       # optional checkbox
    r"\*\*\[[A-Z]\d+\]\*\*"                   # finding ID
    r"\s+(?:<!-- sid:\w+ -->\s+)?"             # optional stable ID
    r"(?:\*\*)?[`]?(\S+?)[`]?(?:\*\*)?:\d+"   # path with optional bold/backtick wrapping
)


def _classify_prior_line(stripped: str, filter_set: set[str], in_matched: bool) -> bool:
    m = _FINDING_LINE_RE.match(stripped)
    if m:
        return m.group(1) in filter_set
    return in_matched


def _collect_scoped_sections(
    lines: list[str], filter_set: set[str],
) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current_header = ""
    current_lines: list[str] = []
    in_matched_finding = False

    for line in lines:
        stripped = line.strip()
        is_header = stripped.startswith("## ")
        if is_header and current_header:
            sections.append((current_header, current_lines))
        if is_header:
            current_header = line
            current_lines = []
            in_matched_finding = False
            continue
        in_matched_finding = _classify_prior_line(stripped, filter_set, in_matched_finding)
        if in_matched_finding:
            current_lines.append(line)

    if current_header:
        sections.append((current_header, current_lines))
    return sections


def _scope_prior_review(prior_text: str, file_filter: list[str]) -> str:
    filter_set = set(file_filter)
    sections = _collect_scoped_sections(prior_text.split("\n"), filter_set)

    parts: list[str] = []
    for header, lines in sections:
        if lines:
            parts.append(header)
            parts.extend(lines)
    return "\n".join(parts).strip()


_STATE_LABELS = {
    THREAD_CONTESTED: "[CONTESTED]",
    THREAD_ACKNOWLEDGED: "[ACKNOWLEDGED]",
    THREAD_RESOLVED: "[RESOLVED]",
    THREAD_REPLIED: "[REPLIED]",
}

def _annotate_with_thread_state(review_text: str, reply_threads: dict) -> str:
    threads = reply_threads.get("threads", [])
    if not threads:
        return review_text
    id_to_state = {}
    for t in threads:
        fid = t.get("finding_id", "")
        if fid and t["state"] in _STATE_LABELS:
            id_to_state[fid] = _STATE_LABELS[t["state"]]
    if not id_to_state:
        return review_text
    lines = review_text.split("\n")
    result = []
    for line in lines:
        m = BOLD_FINDING_ID_RE.search(line)
        if m and m.group(1) in id_to_state:
            label = id_to_state[m.group(1)]
            line = f"{line}  {label}"
        result.append(line)
    return "\n".join(result)


def _build_prior_section(
    prior_review: str,
    context: str = "",
    file_filter: list[str] | None = None,
    reply_threads: dict | None = None,
) -> str:
    if not prior_review:
        return ""
    review_text = annotate_prior_with_stable_ids(prior_review)
    if file_filter:
        review_text = _scope_prior_review(review_text, file_filter)
        if not review_text:
            return ""
    if reply_threads:
        review_text = _annotate_with_thread_state(review_text, reply_threads)
    default = (
        "This is a re-review. Each prior finding has a stable ID (<!-- sid:XXXXXXXX -->).\n"
        "For each prior finding:\n"
        "- If fixed: note as fixed in the Prior findings section\n"
        "- If still open: reference in the Prior findings section with disposition and thread link — do not repeat in severity sections\n"
        "- Severity sections (Must fix, Should fix, etc.) are for genuinely new findings only\n"
        "- New findings get no stable ID — one will be assigned automatically"
    )
    return f"""
## Prior review
{context or default}

<prior_review>
{review_text}
</prior_review>"""


def render_template(name: str, **kwargs) -> str:
    path = _template_dir() / name
    tmpl = Template(path.read_text())
    return tmpl.safe_substitute(**kwargs)


def _build_preflight_section(
    job: ReviewJob, file_filter: list[str] | None = None,
    skip_file_contents: bool = False,
    skip_project_context: bool = False,
    max_diff_bytes: int | None = None,
) -> str:
    if not job.preflight:
        return ""
    return format_preflight_data(
        job.preflight, file_filter=file_filter,
        skip_file_contents=skip_file_contents,
        skip_project_context=skip_project_context,
        max_diff_bytes=max_diff_bytes,
    )


def _compute_diff_budget(
    job: ReviewJob,
    known_sections: dict[str, str],
    skip_file_contents: bool = False,
    file_filter: list[str] | None = None,
) -> int:
    known_bytes = sum(len(v.encode()) for v in known_sections.values())

    pf = job.preflight
    non_diff_preflight = 0
    if pf:
        non_diff_preflight = (
            len(pf.commit_log.encode())
            + len(pf.claude_md.encode())
            + len(pf.architecture_md.encode())
            + sum(len(v.encode()) for v in pf.review_checklists.values())
        )
        if not skip_file_contents:
            filter_set = set(file_filter) if file_filter else None
            non_diff_preflight += sum(
                len(v.encode()) for k, v in pf.file_contents.items()
                if filter_set is None or k in filter_set
            )

    non_diff_total = NON_PREFLIGHT_OVERHEAD_BYTES + known_bytes + non_diff_preflight
    remaining = MAX_PROMPT_BYTES - non_diff_total
    if remaining < MIN_DIFF_BYTES:
        log.warn(
            f"Prompt budget tight: {non_diff_total // 1024}KB non-diff vs "
            f"{MAX_PROMPT_BYTES // 1024}KB limit — diff capped to {MIN_DIFF_BYTES // 1024}KB"
        )
    return max(MIN_DIFF_BYTES, remaining)


def _log_prompt_size(template_name: str, prompt: str, sections: dict[str, str], job: ReviewJob, label: str = "") -> str:
    prompt_bytes = len(prompt.encode())
    prompt_kb = prompt_bytes // 1024
    budget_kb = MAX_PROMPT_BYTES // 1024

    section_sizes = {}
    parts = []
    for name, value in sections.items():
        size = len(value.encode()) if value else 0
        section_sizes[name] = size
        if size > 1024:
            parts.append(f"{name}={size // 1024}KB")
    section_summary = ", ".join(parts) if parts else "all <1KB"

    msg = f"Prompt [{template_name}]: {prompt_kb}KB / {budget_kb}KB ({section_summary})"
    if prompt_bytes > MAX_PROMPT_BYTES:
        msg += f" — EXCEEDS budget by {(prompt_bytes - MAX_PROMPT_BYTES) // 1024}KB"
    log.info(msg)

    suffix = f"-{label}" if label else ""
    prompt_file = _derive_path(job.review_file, f"prompt-{template_name}{suffix}")
    try:
        Path(prompt_file).write_text(prompt)
    except OSError:
        pass

    stats: dict = {
        "template": f"{template_name}{suffix}",
        "prompt_bytes": prompt_bytes,
        "budget_bytes": MAX_PROMPT_BYTES,
        "utilization_pct": round(prompt_bytes / MAX_PROMPT_BYTES * 100, 1),
        "sections": section_sizes,
    }
    if job.preflight:
        pf = job.preflight
        stats["file_contents"] = {
            "included": {p: len(c.encode()) for p, c in pf.file_contents.items()},
            "omitted": pf.omitted_files,
        }
        stats["file_count"] = {
            "included": len(pf.file_contents),
            "omitted": len(pf.omitted_files),
        }
    # Read existing stats — corrupt files from concurrent writes are discarded
    stats_file = _derive_path(job.review_file, FILENAME_PROMPT_STATS)
    existing: list = []
    try:
        parsed = json.loads(Path(stats_file).read_text())
        existing = parsed if isinstance(parsed, list) else [parsed]
    except (OSError, json.JSONDecodeError):
        pass
    existing.append(stats)
    try:
        Path(stats_file).write_text(json.dumps(existing, indent=2))
    except OSError:
        pass

    return prompt


def _incremental_prior_ctx(job: ReviewJob, base_ctx: str) -> str:
    """Return incremental-aware prior context when delta data is available."""
    if not _is_incremental(job):
        return base_ctx
    pf = job.preflight
    prior_sha = pf.prior_head_sha[:7]
    head_sha = job.pr.head_sha[:7]
    n_files = len(pf.delta_files)
    incremental_note = (
        f"\n\n**Incremental review note:** {n_files} file(s) changed since the "
        f"prior review ({prior_sha}..{head_sha}). Focus on changes in the "
        f"'Incremental review context' section."
    )
    return base_ctx + incremental_note


def _prompt_self_review(job, common, extra):
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review of your own code. Below are the findings from the previous self-review. "
        "For each prior finding:\n"
        "- If the issue has been fixed, omit it from the new review\n"
        "- If the issue is still present, carry it forward\n"
        "- Add any new findings from changes since the last review"
    ))
    prior_section = _build_prior_section(job.prior_review, prior_ctx, reply_threads=job.reply_threads)
    preflight = _build_preflight_section(job)
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "prior_section": prior_section,
        "reply_threads": common["reply_threads"],
    }
    kwargs = {
        "branch_name": extra.get("branch_name", job.pr.head),
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "env_section": common["env_section"],
        "issue_section": common["issue_section"],
        "prior_section": prior_section,
        "reply_threads": common["reply_threads"],
        "review_file": job.review_file,
        "generator_version": common["generator_version"],
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_self_synthesis(job, common, extra):
    holistic_content = extra.get("holistic_content") or "_No holistic assessment available._"
    merged_content = extra["merged_content"]
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "holistic_content": holistic_content,
        "merged_content": merged_content,
        "delta_section": common["delta_section"],
        "reply_threads": common["reply_threads"],
    }
    diff_budget = _compute_diff_budget(job, sections, skip_file_contents=True)
    preflight = _build_preflight_section(job, skip_file_contents=True, max_diff_bytes=diff_budget)
    sections["preflight_data"] = preflight
    kwargs = {
        "branch_name": extra.get("branch_name", job.pr.head),
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "pr_head_sha": job.pr.head_sha,
        "holistic_content": holistic_content,
        "group_count": extra["group_count"],
        "merged_content": merged_content,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "review_file": job.review_file,
        "wt_path": job.wt_path,
        "today": common["today"],
        "prior_section": "",
        "reply_threads": common["reply_threads"],
        "generator_version": common["generator_version"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_single(job, common, extra):
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the findings from the previous review. "
        "For each prior finding:\n"
        "- If the issue has been fixed, note it as resolved and omit from the new review\n"
        "- If the issue is still present, carry it forward\n"
        "- Add any new findings from changes since the last review"
    ))
    prior_section = _build_prior_section(job.prior_review, prior_ctx, reply_threads=job.reply_threads)
    preflight = _build_preflight_section(job)
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "prior_section": prior_section,
        "reply_threads": common["reply_threads"],
    }
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "env_section": common["env_section"],
        "issue_section": common["issue_section"],
        "prior_section": prior_section,
        "reply_threads": common["reply_threads"],
        "review_file": job.review_file,
        "generator_version": common["generator_version"],
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_holistic(job, common, extra):
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "all_files_formatted": job.pr.all_files_formatted,
        "reviews_section": common["reviews_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "delta_section": common["delta_section"],
    }
    diff_budget = _compute_diff_budget(job, sections)
    preflight = _build_preflight_section(job, max_diff_bytes=diff_budget)
    sections["preflight_data"] = preflight
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "all_files_formatted": job.pr.all_files_formatted,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "holistic_output": extra["holistic_output"],
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_group(job, common, extra):
    group_files = extra.get("group_file_paths", [])
    file_filter = group_files or None
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the prior findings. "
        "Carry forward findings relevant to YOUR files only. "
        "Mark fixed findings as resolved."
    ))
    prior_section = _build_prior_section(
        job.prior_review, prior_ctx,
        file_filter=file_filter,
        reply_threads=job.reply_threads,
    )
    holistic_block = _build_holistic_block(
        extra.get("holistic_content", ""), job.pr.changed_files,
        group_files=group_files or None,
    )
    pr_header = _build_pr_header(job.pr, job.ctx, file_filter=file_filter)
    delta_section = _build_delta_section(job.preflight, file_filter=file_filter)
    reply_threads = _build_reply_threads_section(job.reply_threads, file_filter=file_filter)
    project_context = build_project_context(job.preflight, file_filter=group_files or None) if job.preflight else ""
    sections = {
        "pr_header": pr_header,
        "holistic_block": holistic_block,
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "delta_section": delta_section,
        "prior_section": prior_section,
        "reply_threads": reply_threads,
    }
    diff_budget = _compute_diff_budget(job, sections, file_filter=file_filter)
    sections["project_context"] = project_context
    group_preflight = _build_preflight_section(
        job, file_filter=file_filter, skip_project_context=True,
        max_diff_bytes=diff_budget,
    )
    sections["preflight_data"] = group_preflight
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": pr_header,
        "group_idx": extra["group_idx"],
        "group_count": extra["group_count"],
        "group_name": extra["group_name"],
        "holistic_block": holistic_block,
        "project_context": project_context,
        "group_files_formatted": extra["group_files_formatted"],
        "preflight_data": group_preflight,
        "delta_section": delta_section,
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "group_output": extra["group_output"],
        "prior_section": prior_section,
        "reply_threads": reply_threads,
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, str(extra["group_idx"])


def _prompt_synthesis(job, common, extra):
    holistic_content = extra.get("holistic_content") or "_No holistic assessment available._"
    merged_content = extra["merged_content"]
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "holistic_content": holistic_content,
        "merged_content": merged_content,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "reply_threads": common["reply_threads"],
    }
    diff_budget = _compute_diff_budget(job, sections, skip_file_contents=True)
    preflight = _build_preflight_section(
        job, skip_file_contents=True, max_diff_bytes=diff_budget,
    )
    sections["preflight_data"] = preflight
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "pr_title": job.pr.title,
        "pr_head_sha": job.pr.head_sha,
        "holistic_content": holistic_content,
        "group_count": extra["group_count"],
        "merged_content": merged_content,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "review_file": job.review_file,
        "wt_path": job.wt_path,
        "today": common["today"],
        "prior_section": "",
        "reply_threads": common["reply_threads"],
        "generator_version": common["generator_version"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_angles(job, common, extra):
    holistic_content = extra.get("holistic_content") or "_No holistic assessment available._"
    holistic_block = _build_holistic_block(holistic_content, job.pr.changed_files)
    sections = {
        "pr_header": common["pr_header"],
        "holistic_block": holistic_block,
        "env_section": common["env_section"],
        "delta_section": common["delta_section"],
    }
    diff_budget = _compute_diff_budget(job, sections)
    preflight = _build_preflight_section(job, max_diff_bytes=diff_budget)
    sections["preflight_data"] = preflight
    kwargs = {
        "branch_name": job.pr.head,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "holistic_block": holistic_block,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "env_section": common["env_section"],
        "angles_output": extra["angles_output"],
        "wt_path": job.wt_path,
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_fix(job, common, extra):
    review_content = ""
    if Path(job.review_file).exists():
        review_content = Path(job.review_file).read_text()
    sections = {
        "review_content": review_content,
    }
    kwargs = {
        "branch_name": job.pr.head,
        "repo": job.repo,
        "review_content": review_content,
        "review_file": job.review_file,
        "wt_path": job.wt_path,
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_scout(job, common, extra):
    sections = {
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "all_files_formatted": job.pr.all_files_formatted,
        "reviews_section": common["reviews_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "delta_section": common["delta_section"],
    }
    diff_budget = _compute_diff_budget(job, sections)
    preflight = _build_preflight_section(job, max_diff_bytes=diff_budget)
    sections["preflight_data"] = preflight
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "state_context": common["state_context"],
        "all_files_formatted": job.pr.all_files_formatted,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "scout_output": extra["scout_output"],
        "omitted_guidance": common["omitted_guidance"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_disprove(job, common, extra):
    review_content = extra.get("review_content", "")
    sections = {
        "review_content": review_content,
    }
    kwargs = {
        "review_content": review_content,
        "disprove_output": extra["disprove_output"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


_PROMPT_HANDLERS = {
    TEMPLATE_SELF_REVIEW: _prompt_self_review,
    TEMPLATE_SELF_SYNTHESIS: _prompt_self_synthesis,
    TEMPLATE_SINGLE: _prompt_single,
    TEMPLATE_HOLISTIC: _prompt_holistic,
    TEMPLATE_SCOUT: _prompt_scout,
    TEMPLATE_GROUP: _prompt_group,
    TEMPLATE_SYNTHESIS: _prompt_synthesis,
    TEMPLATE_DISPROVE: _prompt_disprove,
    TEMPLATE_ANGLES: _prompt_angles,
    TEMPLATE_FIX: _prompt_fix,
}


def build_prompt(template_name: str, job: ReviewJob, *, max_turns: int, **extra) -> str:
    handler = _PROMPT_HANDLERS.get(template_name)
    if handler is None:
        raise ValueError(f"Unknown template: {template_name}")

    reply_threads_section = _build_reply_threads_section(job.reply_threads)
    common = {
        "today": date.today().isoformat(),
        "generator_version": job.generator_version,
        "pr_header": _build_pr_header(job.pr, job.ctx, viewer_role=job.viewer_role),
        "state_context": _build_state_context_section(job),
        "reviews_section": _build_reviews_section(job.ctx),
        "reply_threads": reply_threads_section,
        "env_section": _build_env_section(job.wt_path, preflight=job.preflight),
        "issue_section": _build_issue_section(job.issue_link, job.issue_context),
        "delta_section": _build_delta_section(job.preflight),
        "omitted_guidance": _build_omitted_guidance(job.preflight, skip_omitted=(job.effort == "low")),
        "max_turns": max_turns,
    }

    sections, kwargs, label = handler(job, common, extra)
    rendered = render_template(template_name, **kwargs)
    return _log_prompt_size(template_name, rendered, sections, job, label=label)


