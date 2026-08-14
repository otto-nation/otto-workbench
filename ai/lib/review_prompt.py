"""Prompt construction and template rendering for claude-review.

Handles building prompts for each review template: single, holistic, group,
synthesis, self-review, and self-review-synthesis. Includes section builders,
budget computation, and prompt size logging.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from string import Template

import json
import log
from review_common import (
    EFFORT_PRESETS, Effort,
    FILE_STAT_FMT, FILENAME_PROMPT_STATS,
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, SECTION_STATIC_ANALYSIS,
    PriorDisposition,
    TEMPLATE_DIR_REL,
    TEMPLATE_DISPROVE, TEMPLATE_FIX,
    TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SCOUT, TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path, build_output_block, build_worktree_block,
)
from review_findings import (
    BOLD_FINDING_ID_RE, annotate_prior_with_stable_ids, strip_sections,
)
from review_scout import (
    format_leads_block,
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
        if file_stats:
            lines += ["", "### File breakdown (sorted by churn)", file_stats]
    else:
        file_stats = pr.file_stats(EFFORT_PRESETS[effort].multi_phase_line_threshold)
        if file_stats:
            lines += ["", "### File breakdown (sorted by churn)", file_stats]

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


# Coverage bookkeeping and machine-generated output, not prior reviewer claims.
# The scoped path drops both already (their lines carry no finding ID), so
# stripping here keeps the unscoped prompts consistent with it.
_PRIOR_EXCLUDED_SECTIONS = {
    SECTION_FILE_TRIAGE.lower(),
    SECTION_PRIOR_FINDINGS.lower(),
    SECTION_STATIC_ANALYSIS.lower(),
}


def _strip_internal_sections(prior_text: str) -> str:
    return strip_sections(prior_text, _PRIOR_EXCLUDED_SECTIONS).strip()


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


# The disposition ledger every re-review must emit. Reconciliation matches a
# prior finding on its ID or its path — the two parts an agent restates
# verbatim — so the instruction asks for exactly those, and never for the
# internal sid marker, which nothing downstream requires the agent to echo. The
# two verdict words come from the enum the ledger is parsed with, so asking for
# a word the parser does not know is not expressible here.
_LEDGER_INSTRUCTION = f"""
End your output with a `## {SECTION_PRIOR_FINDINGS}` section listing EVERY prior
finding above, one line each, copying its ID and path exactly as written there:
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.FIXED}` when the change resolves it
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.STILL_OPEN}` when it does not, and
  carry the finding forward into the severity sections as well
This section is bookkeeping — it is stripped before the review is published, and
a prior finding missing from it is reported as unaccounted for."""


def _build_prior_section(
    prior_review: str,
    context: str = "",
    file_filter: list[str] | None = None,
    reply_threads: dict | None = None,
) -> str:
    if not prior_review:
        return ""
    review_text = _strip_internal_sections(annotate_prior_with_stable_ids(prior_review))
    if file_filter:
        review_text = _scope_prior_review(review_text, file_filter)
    if not review_text:
        return ""
    if reply_threads:
        review_text = _annotate_with_thread_state(review_text, reply_threads)
    return f"""
## Prior review
{context}
{_LEDGER_INSTRUCTION}

<prior_review>
{review_text}
</prior_review>"""


def render_template(name: str, **kwargs) -> str:
    path = _template_dir() / name
    tmpl = Template(path.read_text())
    return tmpl.safe_substitute(**kwargs)


@dataclass(frozen=True)
class CommonSections:
    """Sections shared by every template, built once per prompt."""

    today: str
    generator_version: str
    pr_header: str
    state_context: str
    reviews_section: str
    reply_threads: str
    env_section: str
    issue_section: str
    delta_section: str
    omitted_guidance: str
    max_turns: int


COMMON_SECTION_NAMES = frozenset(f.name for f in fields(CommonSections))


class PromptBuilder:
    """Collects the variables a template is rendered with.

    One registry feeds both `safe_substitute` and the byte accounting, so a
    value cannot be interpolated into a prompt without also counting against
    the diff budget and appearing in the prompt-size stats.
    """

    def __init__(self, common: CommonSections):
        self._common = common
        self._vars: dict[str, object] = {}

    def set(self, key: str, value) -> "PromptBuilder":
        self._vars[key] = value
        return self

    def shared(self, *keys: str) -> "PromptBuilder":
        """Register sections from `common` under their own names."""
        unknown = sorted(set(keys) - COMMON_SECTION_NAMES)
        if unknown:
            raise KeyError(
                f"not valid CommonSections fields: {', '.join(unknown)} — "
                f"valid names are {', '.join(sorted(COMMON_SECTION_NAMES))}"
            )
        for key in keys:
            self._vars[key] = getattr(self._common, key)
        return self

    def output(self, output_path: str, *, stdout_warning: bool = False) -> "PromptBuilder":
        return self.set(
            "output_block", build_output_block(output_path, stdout_warning=stdout_warning),
        )

    def worktree(self, wt_path: str) -> "PromptBuilder":
        return self.set("worktree_block", build_worktree_block(wt_path))

    def diff_budget(
        self, job: ReviewJob, *,
        file_filter: list[str] | None = None,
        skip_file_contents: bool = False,
        min_diff: int = MIN_DIFF_BYTES,
    ) -> int:
        """Bytes left for the diff, given everything registered so far.

        Call this only after every non-preflight variable is registered —
        anything set afterwards is not counted against the budget.
        """
        return _compute_diff_budget(
            job, self._vars, file_filter=file_filter,
            skip_file_contents=skip_file_contents, min_diff=min_diff,
        )

    @property
    def vars(self) -> dict[str, object]:
        return dict(self._vars)


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
    known_sections: dict[str, object],
    skip_file_contents: bool = False,
    file_filter: list[str] | None = None,
    min_diff: int = MIN_DIFF_BYTES,
) -> int:
    # `is not None`, not truthiness — a falsy value (0, False) still renders
    # into the prompt and must count against the budget.
    known_bytes = sum(
        len(str(v).encode()) for v in known_sections.values() if v is not None
    )

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
    if remaining < min_diff:
        log.warn(
            f"Prompt budget tight: {non_diff_total // 1024}KB non-diff vs "
            f"{MAX_PROMPT_BYTES // 1024}KB limit — diff capped to {min_diff // 1024}KB"
        )
    return max(min_diff, remaining)


def _log_prompt_size(template_name: str, prompt: str, sections: dict[str, object], job: ReviewJob, label: str = "") -> str:
    prompt_bytes = len(prompt.encode())
    prompt_kb = prompt_bytes // 1024
    budget_kb = MAX_PROMPT_BYTES // 1024

    section_sizes = {}
    parts = []
    for name, value in sections.items():
        size = len(str(value).encode()) if value is not None else 0
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
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ))
    prior_section = _build_prior_section(job.prior_review, prior_ctx, reply_threads=job.reply_threads)
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "delta_section", "reply_threads",
        "env_section", "issue_section", "generator_version",
        "omitted_guidance", "max_turns",
    )
    b.set("branch_name", extra.get("branch_name", job.pr.head))
    b.set("repo", job.repo)
    b.set("prior_section", prior_section)
    b.output(job.review_file, stdout_warning=True)
    b.set("preflight_data", _build_preflight_section(job))
    return b, ""


def _synthesis_prompt(job, common, extra, *, shared: tuple[str, ...], ident: dict):
    """Shared body of the PR and self-review synthesis prompts.

    The two differ only in how the review is identified — PR number and title
    vs branch name — and whether prior reviews are in scope.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "delta_section", "reply_threads",
        "today", "generator_version", "max_turns", *shared,
    )
    for key, value in ident.items():
        b.set(key, value)
    b.set("repo", job.repo)
    b.set("pr_head_sha", job.pr.head_sha)
    b.set("wt_path", job.wt_path)
    b.set("prior_section", "")
    b.set("group_count", extra["group_count"])
    b.set("holistic_content", extra.get("holistic_content") or "_No holistic assessment available._")
    b.set("merged_content", extra["merged_content"])
    b.output(job.review_file)
    # Synthesis has all findings in merged_content — diff is supplementary,
    # so allow it to shrink to 0 rather than blowing the budget.
    diff_budget = b.diff_budget(job, skip_file_contents=True, min_diff=0)
    b.set("preflight_data", _build_preflight_section(
        job, skip_file_contents=True, max_diff_bytes=diff_budget,
    ))
    return b, ""


def _prompt_self_synthesis(job, common, extra):
    return _synthesis_prompt(
        job, common, extra, shared=(),
        ident={"branch_name": extra.get("branch_name", job.pr.head)},
    )


def _prompt_single(job, common, extra):
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the findings from the previous review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ))
    prior_section = _build_prior_section(job.prior_review, prior_ctx, reply_threads=job.reply_threads)
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "delta_section", "reviews_section",
        "reply_threads", "env_section", "issue_section", "generator_version",
        "omitted_guidance", "max_turns",
    )
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("prior_section", prior_section)
    b.output(job.review_file, stdout_warning=True)
    b.set("preflight_data", _build_preflight_section(job))
    return b, ""


def _survey_prompt(job, common, extra, output_key: str):
    """Shared body of the holistic and scout prompts.

    Both survey the whole PR from identical inputs; only the output file
    differs.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "delta_section", "reviews_section",
        "issue_section", "env_section", "omitted_guidance", "max_turns",
    )
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("all_files_formatted", job.pr.all_files_formatted)
    b.output(extra[output_key])
    b.set("preflight_data", _build_preflight_section(
        job, max_diff_bytes=b.diff_budget(job),
    ))
    return b, ""


def _prompt_holistic(job, common, extra):
    return _survey_prompt(job, common, extra, "holistic_output")


def _prompt_group(job, common, extra):
    group_files = extra.get("group_file_paths", [])
    file_filter = group_files or None
    prior_ctx = _incremental_prior_ctx(job, (
        "This is a re-review. Below are the prior findings for YOUR files. "
        "Carry forward the ones still present; leave fixed ones out of the "
        "severity sections."
    ))
    prior_section = _build_prior_section(
        job.prior_review, prior_ctx,
        file_filter=file_filter,
        reply_threads=job.reply_threads,
    )
    holistic_block = _build_holistic_block(
        extra.get("holistic_content", ""), job.pr.changed_files,
    )
    b = PromptBuilder(common)
    b.shared("issue_section", "env_section", "omitted_guidance", "max_turns")
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("pr_header", _build_pr_header(
        job.pr, job.ctx, job.effort, file_filter=file_filter,
    ))
    b.set("delta_section", _build_delta_section(job.preflight, file_filter=file_filter))
    b.set("reply_threads", _build_reply_threads_section(job.reply_threads, file_filter=file_filter))
    b.set("project_context", build_project_context(job.preflight, file_filter=file_filter) if job.preflight else "")
    b.set("holistic_block", holistic_block)
    b.set("prior_section", prior_section)
    b.set("group_idx", extra["group_idx"])
    b.set("group_count", extra["group_count"])
    b.set("group_name", extra["group_name"])
    b.set("group_files_formatted", extra["group_files_formatted"])
    b.output(extra["group_output"])
    diff_budget = b.diff_budget(job, file_filter=file_filter)

    # When file contents blow the budget (common for generated-code groups),
    # drop them so the diff gets adequate space.
    skip_file_contents = False
    if diff_budget <= MIN_DIFF_BYTES and job.preflight:
        filter_set = set(file_filter) if file_filter else None
        fc_bytes = sum(
            len(v.encode()) for k, v in job.preflight.file_contents.items()
            if filter_set is None or k in filter_set
        )
        if fc_bytes > 0:
            log.info(f"Dropping {fc_bytes // 1024}KB file contents for group to fit diff budget")
            skip_file_contents = True
            diff_budget = b.diff_budget(job, file_filter=file_filter, skip_file_contents=True)

    b.set("preflight_data", _build_preflight_section(
        job, file_filter=file_filter, skip_project_context=True,
        skip_file_contents=skip_file_contents,
        max_diff_bytes=diff_budget,
    ))
    return b, str(extra["group_idx"])


def _prompt_synthesis(job, common, extra):
    return _synthesis_prompt(
        job, common, extra, shared=("reviews_section",),
        ident={"pr_number": job.pr_number, "pr_title": job.pr.title},
    )


def _prompt_fix(job, common, extra):
    review_content = ""
    if Path(job.review_file).exists():
        review_content = Path(job.review_file).read_text()
    b = PromptBuilder(common)
    b.shared("max_turns")
    b.set("branch_name", job.pr.head)
    b.set("repo", job.repo)
    b.set("review_content", review_content)
    b.set("review_file", job.review_file)
    b.worktree(job.wt_path)
    return b, ""


def _prompt_scout(job, common, extra):
    return _survey_prompt(job, common, extra, "scout_output")


def _prompt_disprove(job, common, extra):
    b = PromptBuilder(common)
    b.shared("max_turns")
    b.set("review_content", extra.get("review_content", ""))
    b.output(extra["disprove_output"])
    return b, ""


_PROMPT_HANDLERS = {
    TEMPLATE_SELF_REVIEW: _prompt_self_review,
    TEMPLATE_SELF_SYNTHESIS: _prompt_self_synthesis,
    TEMPLATE_SINGLE: _prompt_single,
    TEMPLATE_HOLISTIC: _prompt_holistic,
    TEMPLATE_SCOUT: _prompt_scout,
    TEMPLATE_GROUP: _prompt_group,
    TEMPLATE_SYNTHESIS: _prompt_synthesis,
    TEMPLATE_DISPROVE: _prompt_disprove,
    TEMPLATE_FIX: _prompt_fix,
}


def _build_common_sections(job: ReviewJob, *, max_turns: int) -> CommonSections:
    return CommonSections(
        today=date.today().isoformat(),
        generator_version=job.generator_version,
        pr_header=_build_pr_header(
            job.pr, job.ctx, job.effort, viewer_role=job.viewer_role,
        ),
        state_context=_build_state_context_section(job),
        reviews_section=_build_reviews_section(job.ctx),
        reply_threads=_build_reply_threads_section(job.reply_threads),
        env_section=_build_env_section(job.wt_path, preflight=job.preflight),
        issue_section=_build_issue_section(job.issue_link, job.issue_context),
        delta_section=_build_delta_section(job.preflight),
        omitted_guidance=_build_omitted_guidance(
            job.preflight,
            skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
        ),
        max_turns=max_turns,
    )


def build_prompt(template_name: str, job: ReviewJob, *, max_turns: int, **extra) -> str:
    handler = _PROMPT_HANDLERS.get(template_name)
    if handler is None:
        raise ValueError(f"Unknown template: {template_name}")

    common = _build_common_sections(job, max_turns=max_turns)
    builder, label = handler(job, common, extra)
    template_vars = builder.vars
    rendered = render_template(template_name, **template_vars)
    return _log_prompt_size(template_name, rendered, template_vars, job, label=label)


