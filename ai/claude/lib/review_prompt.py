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
from review_common import (
    FILENAME_PROMPT_STATS,
    TEMPLATE_DIR_REL,
    TEMPLATE_ANGLES, TEMPLATE_FIX,
    TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path, _warn,
)
from review_findings import annotate_prior_with_stable_ids
from review_preflight import (
    MAX_PROMPT_BYTES, MIN_DIFF_BYTES, NON_PREFLIGHT_OVERHEAD_BYTES,
    PRContext, PRMetadata, PreflightData, ReviewJob,
    format_preflight_data,
)

# ── Template rendering ────────────────────────────────────────────────────────

def _template_dir() -> Path:
    return Path(__file__).resolve().parent.parent / TEMPLATE_DIR_REL


def _build_pr_header(pr: PRMetadata, ctx: PRContext) -> str:
    lines = [
        "## PR metadata",
        f"- **Title:** {pr.title}",
        f"- **Branch:** {pr.head} → {pr.base}",
        f"- **Size:** +{pr.additions} -{pr.deletions} across {pr.changed_files} files",
        "",
        "### Description",
        pr.body or "_No description provided._",
        "",
        "### Commits",
        ctx.commits or "_No commits._",
    ]
    if pr.file_stats:
        lines += ["", "### File breakdown (sorted by churn)", pr.file_stats]
    return "\n".join(lines)


def _build_reviews_section(ctx: PRContext) -> str:
    return f"""
## Existing reviews and comments
Skip these — do NOT re-fetch from the GitHub API. This data is current as of script invocation.

### Submitted reviews
{ctx.reviews}

### Inline review comments
{ctx.review_comments}

### General PR comments
{ctx.comments}"""


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


def _build_holistic_block(holistic_content: str, changed_files: int) -> str:
    if not holistic_content:
        return ""
    return (
        f"\n## Holistic context\n"
        f"The following assessment was produced by scanning all "
        f"{changed_files} files in this PR.\n"
        f"Use it to inform your detailed review — especially the flags section.\n\n"
        f"{holistic_content}"
    )


def _build_delta_section(preflight: PreflightData | None) -> str:
    if not preflight or not preflight.prior_head_sha:
        return ""
    prior = preflight.prior_head_sha[:7]
    delta_files = preflight.delta_files
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

    if preflight.delta_commit_log:
        parts += [
            "",
            "### New commits since prior review",
            "",
            "```",
            preflight.delta_commit_log,
            "```",
        ]

    if preflight.delta_diff:
        parts += [
            "",
            "### Delta diff",
            "",
            "```diff",
            preflight.delta_diff,
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


def _build_prior_section(prior_review: str, context: str = "", file_filter: list[str] | None = None) -> str:
    if not prior_review:
        return ""
    review_text = annotate_prior_with_stable_ids(prior_review)
    if file_filter:
        review_text = _scope_prior_review(review_text, file_filter)
        if not review_text:
            return ""
    default = (
        "This is a re-review. Each prior finding has a stable ID (<!-- sid:XXXXXXXX -->).\n"
        "For each prior finding:\n"
        "- If fixed: omit from new review\n"
        "- If still open: carry forward, preserving the stable ID comment\n"
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
    max_diff_bytes: int | None = None,
) -> str:
    if not job.preflight:
        return ""
    return format_preflight_data(
        job.preflight, file_filter=file_filter,
        skip_file_contents=skip_file_contents,
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
            + len(pf.context_md.encode())
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
        _warn(
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
    print(msg, file=sys.stderr, flush=True)

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
    stats_file = _derive_path(job.review_file, FILENAME_PROMPT_STATS)
    try:
        existing: list = []
        if Path(stats_file).exists():
            existing = json.loads(Path(stats_file).read_text())
            if not isinstance(existing, list):
                existing = [existing]
        existing.append(stats)
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
    prior_section = _build_prior_section(job.prior_review, prior_ctx)
    preflight = _build_preflight_section(job)
    sections = {"pr_header": common["pr_header"], "preflight_data": preflight, "delta_section": common["delta_section"], "prior_section": prior_section}
    kwargs = {
        "branch_name": extra.get("branch_name", job.pr.head),
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "env_section": common["env_section"],
        "issue_section": common["issue_section"],
        "prior_section": prior_section,
        "review_file": job.review_file,
        "generator_version": common["generator_version"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_self_synthesis(job, common, extra):
    holistic_content = extra.get("holistic_content") or "_No holistic assessment available._"
    merged_content = extra["merged_content"]
    prior_ctx = _incremental_prior_ctx(job,
        "Omit prior findings that have been fixed. Carry forward open ones.",
    )
    prior_section = _build_prior_section(job.prior_review, prior_ctx)
    sections = {
        "pr_header": common["pr_header"],
        "holistic_content": holistic_content,
        "merged_content": merged_content,
        "delta_section": common["delta_section"],
        "prior_section": prior_section,
    }
    diff_budget = _compute_diff_budget(job, sections, skip_file_contents=True)
    preflight = _build_preflight_section(job, skip_file_contents=True, max_diff_bytes=diff_budget)
    sections["preflight_data"] = preflight
    kwargs = {
        "branch_name": extra.get("branch_name", job.pr.head),
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "pr_head_sha": job.pr.head_sha,
        "holistic_content": holistic_content,
        "group_count": extra["group_count"],
        "merged_content": merged_content,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "review_file": job.review_file,
        "wt_path": job.wt_path,
        "today": common["today"],
        "prior_section": prior_section,
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
    prior_section = _build_prior_section(job.prior_review, prior_ctx)
    preflight = _build_preflight_section(job)
    sections = {
        "pr_header": common["pr_header"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "prior_section": prior_section,
    }
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "env_section": common["env_section"],
        "issue_section": common["issue_section"],
        "prior_section": prior_section,
        "review_file": job.review_file,
        "generator_version": common["generator_version"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_holistic(job, common, extra):
    sections = {
        "pr_header": common["pr_header"],
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
        "all_files_formatted": job.pr.all_files_formatted,
        "preflight_data": preflight,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "holistic_output": extra["holistic_output"],
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, ""


def _prompt_group(job, common, extra):
    group_files = extra.get("group_file_paths", [])
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the prior findings. "
        "Carry forward findings relevant to YOUR files only. "
        "Mark fixed findings as resolved."
    ))
    prior_section = _build_prior_section(
        job.prior_review, prior_ctx,
        file_filter=group_files or None,
    )
    holistic_block = _build_holistic_block(
        extra.get("holistic_content", ""), job.pr.changed_files,
    )
    sections = {
        "pr_header": common["pr_header"],
        "holistic_block": holistic_block,
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "delta_section": common["delta_section"],
        "prior_section": prior_section,
    }
    diff_budget = _compute_diff_budget(job, sections, file_filter=group_files or None)
    group_preflight = _build_preflight_section(
        job, file_filter=group_files or None, max_diff_bytes=diff_budget,
    )
    sections["preflight_data"] = group_preflight
    kwargs = {
        "pr_number": job.pr_number,
        "repo": job.repo,
        "pr_header": common["pr_header"],
        "group_idx": extra["group_idx"],
        "group_count": extra["group_count"],
        "group_name": extra["group_name"],
        "holistic_block": holistic_block,
        "group_files_formatted": extra["group_files_formatted"],
        "preflight_data": group_preflight,
        "delta_section": common["delta_section"],
        "issue_section": common["issue_section"],
        "env_section": common["env_section"],
        "group_output": extra["group_output"],
        "prior_section": prior_section,
        "max_turns": common["max_turns"],
    }
    return sections, kwargs, str(extra["group_idx"])


def _prompt_synthesis(job, common, extra):
    holistic_content = extra.get("holistic_content") or "_No holistic assessment available._"
    merged_content = extra["merged_content"]
    prior_ctx = _incremental_prior_ctx(job,
        "Include a ## Resolved section noting which prior findings were fixed.",
    )
    prior_section = _build_prior_section(job.prior_review, prior_ctx)
    sections = {
        "pr_header": common["pr_header"],
        "holistic_content": holistic_content,
        "merged_content": merged_content,
        "delta_section": common["delta_section"],
        "reviews_section": common["reviews_section"],
        "prior_section": prior_section,
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
        "prior_section": prior_section,
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


_PROMPT_HANDLERS = {
    TEMPLATE_SELF_REVIEW: _prompt_self_review,
    TEMPLATE_SELF_SYNTHESIS: _prompt_self_synthesis,
    TEMPLATE_SINGLE: _prompt_single,
    TEMPLATE_HOLISTIC: _prompt_holistic,
    TEMPLATE_GROUP: _prompt_group,
    TEMPLATE_SYNTHESIS: _prompt_synthesis,
    TEMPLATE_ANGLES: _prompt_angles,
    TEMPLATE_FIX: _prompt_fix,
}


def build_prompt(template_name: str, job: ReviewJob, *, max_turns: int, **extra) -> str:
    handler = _PROMPT_HANDLERS.get(template_name)
    if handler is None:
        raise ValueError(f"Unknown template: {template_name}")

    common = {
        "today": date.today().isoformat(),
        "generator_version": job.generator_version,
        "pr_header": _build_pr_header(job.pr, job.ctx),
        "reviews_section": _build_reviews_section(job.ctx),
        "env_section": _build_env_section(job.wt_path, preflight=job.preflight),
        "issue_section": _build_issue_section(job.issue_link, job.issue_context),
        "delta_section": _build_delta_section(job.preflight),
        "max_turns": max_turns,
    }

    sections, kwargs, label = handler(job, common, extra)
    rendered = render_template(template_name, **kwargs)
    return _log_prompt_size(template_name, rendered, sections, job, label=label)


