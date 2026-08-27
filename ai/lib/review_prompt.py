"""Prompt construction and template rendering for claude-review.

`build_prompt` renders the prompt for one review phase: a caller names the
`Phase` and hands over what the phase cannot derive for itself. Which template
that renders, and which file the agent is told to write, both come off the
phase's registry entry. Six phases sit in front of eight templates, because two
of them read an open PR and the working branch differently — which template a
mode picks is the spec's answer, not the caller's. Includes section builders,
budget computation, and prompt size logging.

Every phase fits its prompt to the token budget through `PromptBuilder.fit`,
which registers the sections that can shrink — the pre-collected file contents,
the incremental delta, and the full diff — after everything fixed is already
accounted for. It pulls three levers in that order and only as far as the
shortfall requires, rewrites the environment section to send the agent after
whatever it dropped, and reports the cuts in the prompt's size log. A prompt
still over budget once every lever is pulled raises `PromptTooLarge` rather than
being sent: the phase reports it before an agent starts, so it costs nothing.
"""

# doc-group: pipeline

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from datetime import date
from pathlib import Path

import agent_templates
import git_client
import json
import log
from agent_registry import PHASES
from agent_types import EFFORT_PRESETS, Effort, Mode, Phase
from pr_domains import ReviewVerdict
from review_common import (
    FILENAME_PROMPT_STATS,
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, SECTION_STATIC_ANALYSIS,
    _derive_path, build_output_block, build_worktree_block,
    phase_output_path,
)
from review_document import BOLD_FINDING_ID_RE
from review_findings import annotate_prior_with_stable_ids, strip_sections
from review_scout import (
    format_leads_block,
    is_scout_output, parse_scout_output,
)
from review_preflight import (
    MAX_PROMPT_BYTES, MAX_REVIEW_BODY_LEN, MIN_DIFF_BYTES,
    NON_PREFLIGHT_OVERHEAD_BYTES,
    THREAD_ACKNOWLEDGED, THREAD_CONTESTED, THREAD_REPLIED,
    THREAD_RESOLVED, THREAD_UNREPLIED,
    _scope_diff, _truncate_diff, build_project_context, format_preflight_data,
)
from review_types import (
    FILE_STAT_FMT, PRContext, PreflightData, PRMetadata, PriorDisposition,
    ReviewJob,
)

# The verdicts the prompt offers, written from the same members the review's
# `## Verdict` line is parsed against — the wording an agent is asked for cannot
# drift from the wording that is recognised.
VERDICT_OPTIONS = " / ".join(v.prose for v in ReviewVerdict)


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
            body = r.get("body", "").replace("\n", " ")[:MAX_REVIEW_BODY_LEN]
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


def _build_env_section(
    wt_path: str, preflight: PreflightData | None = None,
    skip_file_contents: bool = False,
) -> str:
    """Where the branch is checked out, and how much of it the prompt carries.

    ``skip_file_contents`` is the budget's first lever having fired: the diffs
    are still inlined but no file contents are, so every changed file is one
    the agent has to open. Telling it otherwise is worse than telling it
    nothing — an agent that reads "file contents are in the Pre-collected data
    section" does not go looking for the ones that are not.
    """
    if preflight and skip_file_contents:
        return f"""
## Environment
PR branch checked out at: {wt_path}
Diffs are pre-collected; file contents are not. Read every file listed under "Files not pre-collected" directly from this path."""
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


def _build_omitted_guidance(
    preflight: "PreflightData | None", skip_omitted: bool = False,
    skip_file_contents: bool = False,
) -> str:
    """The sentence that sends the agent to read what the prompt left out.

    ``skip_file_contents`` fills the "Files not pre-collected" list with every
    changed file rather than only the oversized ones, so the batch read is owed
    even when ``omitted_files`` is empty. ``skip_omitted`` is the opposite case
    and suppresses the read, but only while the contents are still in the
    prompt: at an effort level that does not review the large files, naming
    them invites work the run has declined — whereas a file the *budget* took
    out is one the phase does review and now has nowhere else to get.
    """
    if not preflight or not (preflight.omitted_files or skip_file_contents):
        return ""
    if skip_omitted and not skip_file_contents:
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


# How many paths either file list in the delta section spells out before it
# summarises the rest. A list is orientation, not content — the diff above it is
# what the agent reviews — so the tail costs bytes no reader spends. Both lists
# were uncapped until a rebased branch produced 4,974 delta files for a 107-file
# PR and 260KB of `- \`path\`` lines pushed the synthesis prompt 75% past its
# budget. `_scope_to_surface` bounds the count itself now; this bounds the
# rendering, so no future way of over-counting can spend the whole budget on it.
MAX_DELTA_LIST_ENTRIES = 200

# Below this the diff fence holds a fragment of one hunk, which reads as
# corruption rather than as context. The delta section drops its diff entirely
# at that point and the full diff — which covers the same files from the base —
# is what the agent reviews from.
MIN_DELTA_DIFF_BYTES = 2_048


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
            _scope_diff(preflight.delta_diff, file_filter) if file_filter
            else preflight.delta_diff
        )
    if diff_text and max_bytes is not None:
        fence = ["", "### Delta diff", "", "```diff", "", "```"]
        room = max_bytes - len("\n".join(head + fence + tail).encode())
        diff_text = _truncate_diff(diff_text, room)[0] if room >= MIN_DELTA_DIFF_BYTES else ""

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
# verdict words come from the enum the ledger is parsed with, so asking for
# a word the parser does not know is not expressible here.
#
# Where the verdict sits in the line is not so protected: an example the parser
# rejects is expressible, and stays invisible until a re-review's bookkeeping
# is lost. So the instruction says where the verdict goes as well as what it
# is, and `TestLedgerInstructionParses` reads every example back through
# `_parse_ledger_line` to hold the two together.
_LEDGER_INSTRUCTION = f"""
End your output with a `## {SECTION_PRIOR_FINDINGS}` section listing EVERY prior
finding above, one line each, copying its ID and path exactly as written there:
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.FIXED}` when the change resolves it
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.STILL_OPEN}` when it does not, and
  carry the finding forward into the severity sections as well
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.DECLINED}` when it was considered and
  rejected on the merits — a documented tradeoff (a `ceiling:` marker, a commit
  message or a prior reply explaining the choice), or something the prior review
  itself already recorded as declined. Carry it forward too, but annotated
  `*(declined — one-line reason)*` so it is not raised or auto-fixed again. A
  declined finding stays declined: never downgrade one to {PriorDisposition.STILL_OPEN}
Write the verdict word first, before any explanation of it, and let it end the
line or be followed by a dash, a colon or a full stop. A verdict qualified in
the same breath ("{PriorDisposition.FIXED}, but only on the happy path") is not
read as a verdict at all.
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


@dataclass(frozen=True)
class CommonSections:
    """Sections shared by every template, built once per prompt.

    The incremental delta is not among them: it is budgeted, and a phase that
    scopes itself to one group budgets a different section from a phase that
    reads the whole PR. `PromptBuilder.fit` builds and registers it instead.
    """

    today: str
    generator_version: str
    pr_header: str
    state_context: str
    reviews_section: str
    reply_threads: str
    env_section: str
    issue_section: str
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
        self._plan: BudgetPlan | None = None

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

    def fit(
        self, job: ReviewJob, *,
        file_filter: list[str] | None = None,
        skip_file_contents: bool = False,
        skip_project_context: bool = False,
        min_diff: int = MIN_DIFF_BYTES,
    ) -> "PromptBuilder":
        """Register the budgeted sections, shrunk to whatever room is left.

        Everything registered before this call is fixed overhead; call it once,
        last, after every other variable. `file_filter` scopes the prompt to one
        group's files, `skip_file_contents` drops the pre-collected contents
        outright rather than waiting for the ladder to reach them, and
        `min_diff` is the floor the full diff will not shrink below — synthesis
        passes 0, having the findings already.

        Four sections are registered here and nowhere else, because this is the
        only place that knows what the budget cut: `preflight_data` and
        `delta_section`, which are what shrink, and `env_section` and
        `omitted_guidance`, which describe them and would otherwise promise the
        agent data the prompt no longer carries. The latter two are rewritten
        only if the caller registered them.
        """
        plan = _fit_budget(
            job, self._vars, file_filter=file_filter,
            skip_file_contents=skip_file_contents, min_diff=min_diff,
        )
        self._plan = plan
        self.set("delta_section", plan.delta_section)
        self.set("preflight_data", _build_preflight_section(
            job, file_filter=file_filter,
            skip_file_contents=plan.skip_file_contents,
            skip_project_context=skip_project_context,
            max_diff_bytes=plan.diff_bytes,
        ))
        if "env_section" in self._vars:
            self.set("env_section", _build_env_section(
                job.wt_path, preflight=job.preflight,
                skip_file_contents=plan.skip_file_contents,
            ))
        if "omitted_guidance" in self._vars:
            self.set("omitted_guidance", _build_omitted_guidance(
                job.preflight,
                skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
                skip_file_contents=plan.skip_file_contents,
            ))
        return self

    @property
    def cuts(self) -> tuple[Cut, ...]:
        """What `fit` dropped to make the prompt fit, in the order it went."""
        return self._plan.cuts if self._plan else ()

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


class BudgetLever(StrEnum):
    """Which section the budget ladder cut, named in the order it pulls them."""

    FILE_CONTENTS = "file_contents"
    DELTA = "delta"
    DIFF_FLOOR = "diff_floor"


@dataclass(frozen=True)
class Cut:
    """One lever the ladder pulled, and what it bought.

    `freed_bytes` is what the section gave back. `DIFF_FLOOR` gives nothing
    back — it is the ladder refusing to shrink the diff any further — so it
    carries `shortfall_bytes`, what the prompt is still over by, and
    `floor_bytes`, the size the diff was held at. A phase reviewing from
    findings it already has passes no floor, and then there is no diff left at
    all rather than a floor to report.

    Structured rather than pre-rendered because `prompt-stats.json` is the
    artifact an over-budget run is diagnosed from, and asking it which lever
    fired on which phase should not mean parsing the sentence written for the
    log. `describe` is that sentence, and the only place it is spelled out.
    """

    lever: BudgetLever
    freed_bytes: int = 0
    shortfall_bytes: int = 0
    floor_bytes: int = 0

    def describe(self) -> str:
        """How the cut reads in the prompt's size log."""
        if self.lever is BudgetLever.FILE_CONTENTS:
            return f"{self.freed_bytes // 1024}KB of pre-collected file contents"
        if self.lever is BudgetLever.DELTA:
            return f"{self.freed_bytes // 1024}KB of incremental delta"
        still_over = f"{self.shortfall_bytes // 1024}KB still over"
        if self.floor_bytes:
            return f"the full diff, floored at {self.floor_bytes // 1024}KB and {still_over}"
        return f"the full diff entirely, {still_over}"


@dataclass(frozen=True)
class BudgetPlan:
    """How much of the prompt each variable-size section gets, and what was cut.

    `delta_section` is the rendered incremental context, already shrunk;
    `diff_bytes` is the cap the full diff is truncated to; `skip_file_contents`
    says whether the pre-collected contents survived. `cuts` holds one `Cut` per
    lever the ladder had to pull, in the order it pulled them, and is empty on
    the ordinary path where everything fit.
    """

    delta_section: str
    diff_bytes: int
    skip_file_contents: bool
    cuts: tuple[Cut, ...]


def _fixed_preflight_bytes(pf: PreflightData | None) -> int:
    if not pf:
        return 0
    return (
        len(pf.commit_log.encode())
        + len(pf.claude_md.encode())
        + len(pf.architecture_md.encode())
        + sum(len(v.encode()) for v in pf.review_checklists.values())
    )


def _file_contents_bytes(
    pf: PreflightData | None, file_filter: list[str] | None,
) -> int:
    if not pf:
        return 0
    filter_set = set(file_filter) if file_filter else None
    return sum(
        len(v.encode()) for k, v in pf.file_contents.items()
        if filter_set is None or k in filter_set
    )


def _fit_budget(
    job: ReviewJob,
    known_sections: dict[str, object],
    *,
    skip_file_contents: bool = False,
    file_filter: list[str] | None = None,
    min_diff: int = MIN_DIFF_BYTES,
) -> BudgetPlan:
    """Fit the variable sections into what `known_sections` leaves of the budget.

    Three levers, pulled in this order and only as far as the shortfall
    requires: drop the pre-collected file contents, shrink the incremental
    delta, then floor the full diff at `min_diff`. Contents go first because
    they are the only section the agent can recover on its own — the worktree
    is checked out and `fit` rewrites the environment section to send it there
    — while a diff it is not shown is a change it does not know happened.

    Pulling every lever is not a guarantee of fitting: the fixed overhead alone
    can exceed the budget. The plan then reports the cuts it made and
    `build_prompt` raises `PromptTooLarge` on the rendered result, rather than
    logging past a prompt the model will reject.
    """
    # `is not None`, not truthiness — a falsy value (0, False) still renders
    # into the prompt and must count against the budget.
    known_bytes = sum(
        len(str(v).encode()) for v in known_sections.values() if v is not None
    )
    fixed = NON_PREFLIGHT_OVERHEAD_BYTES + known_bytes + _fixed_preflight_bytes(job.preflight)
    contents = 0 if skip_file_contents else _file_contents_bytes(job.preflight, file_filter)
    delta = _build_delta_section(job.preflight, file_filter=file_filter)
    cuts: list[Cut] = []

    if contents and fixed + contents + len(delta.encode()) + min_diff > MAX_PROMPT_BYTES:
        cuts.append(Cut(BudgetLever.FILE_CONTENTS, freed_bytes=contents))
        skip_file_contents, contents = True, 0

    delta_room = max(0, MAX_PROMPT_BYTES - fixed - contents - min_diff)
    if len(delta.encode()) > delta_room:
        shrunk = _build_delta_section(
            job.preflight, file_filter=file_filter, max_bytes=delta_room,
        )
        cuts.append(Cut(
            BudgetLever.DELTA,
            freed_bytes=len(delta.encode()) - len(shrunk.encode()),
        ))
        delta = shrunk

    diff_bytes = MAX_PROMPT_BYTES - fixed - contents - len(delta.encode())
    if diff_bytes < min_diff:
        # Recorded as a shortfall rather than as bytes freed, because the floor
        # frees nothing: it is what the ladder could not absorb, and so is also
        # what the rendered prompt will be over by.
        cuts.append(Cut(
            BudgetLever.DIFF_FLOOR,
            shortfall_bytes=min_diff - diff_bytes,
            floor_bytes=min_diff,
        ))
        diff_bytes = min_diff

    return BudgetPlan(
        delta_section=delta,
        diff_bytes=diff_bytes,
        skip_file_contents=skip_file_contents,
        cuts=tuple(cuts),
    )


def _log_prompt_size(
    template_name: str, prompt: str, sections: dict[str, object], job: ReviewJob,
    label: str = "", cuts: tuple[Cut, ...] = (),
) -> str:
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
    if cuts:
        msg += " — dropped " + ", ".join(c.describe() for c in cuts)
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
        "cuts": [asdict(c) for c in cuts],
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
    prior_sha = git_client.abbrev(pf.prior_head_sha)
    head_sha = git_client.abbrev(job.pr.head_sha)
    n_files = len(pf.delta_files)
    incremental_note = (
        f"\n\n**Incremental review note:** {n_files} file(s) changed since the "
        f"prior review ({prior_sha}..{head_sha}). Focus on changes in the "
        f"'Incremental review context' section."
    )
    return base_ctx + incremental_note


# ── Per-phase prompt builders ────────────────────────────────────────────────
#
# One builder per phase, registered in `_PROMPT_BUILDERS` below. Each is handed
# the job, the sections every prompt shares, the phase's own extras, and the
# path its agent writes to — which `build_prompt` derives from the phase spec,
# so no caller passes an output path in. Two of them serve both review modes;
# `job.mode` is what they read to tell the modes apart.


# The re-review preamble the single-agent prompt opens its prior findings with.
# What follows it is the same either way — only what is being re-read differs,
# which is why the two are spelled out in full rather than assembled from a
# shared tail and a per-mode head.
_REREVIEW_CTX: dict[Mode, str] = {
    Mode.PR: (
        "This is a re-review. Below are the findings from the previous review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ),
    Mode.SELF: (
        "This is a re-review of your own code. Below are the findings from the previous self-review. "
        "For each prior finding:\n"
        "- If the issue is still present, carry it forward\n"
        "- If the issue has been fixed, leave it out of the severity sections\n"
        "- Add any new findings from changes since the last review"
    ),
}


def _identify_review(b: PromptBuilder, job: ReviewJob, **pr_only) -> None:
    """Register what names the review — the only thing the two modes split on.

    A self-review has no PR to point at, so it is identified by its branch and
    has no reviews on it to read. `pr_only` carries whatever else belongs to the
    PR side of one template: the verdict wording for the single-agent prompt,
    the title for synthesis. A caller states that difference rather than
    restating the split it hangs off.
    """
    if job.mode is Mode.SELF:
        b.set("branch_name", job.pr.head)
        return
    b.shared("reviews_section")
    b.set("pr_number", job.pr_number)
    for key, value in pr_only.items():
        b.set(key, value)


def _prompt_single(job, common, extra, output):
    """The one-agent review, of an open PR or of the working branch.

    The modes differ in three places, all of them consequences of there being
    no PR: a self-review is identified by its branch, has no reviews on it to
    read, and is not asked for a verdict.
    """
    prior_section = _build_prior_section(
        job.prior_review,
        _incremental_prior_ctx(job, _REREVIEW_CTX[job.mode]),
        reply_threads=job.reply_threads,
    )
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context",
        "reply_threads", "env_section", "issue_section", "generator_version",
        "omitted_guidance", "max_turns",
    )
    _identify_review(b, job, verdict_options=VERDICT_OPTIONS)
    b.set("repo", job.repo)
    b.set("prior_section", prior_section)
    b.output(output, stdout_warning=True)
    b.fit(job)
    return b, ""


def _prompt_synthesis(job, common, extra, output):
    """The group findings written up as the review document.

    Same split as `_prompt_single`, minus the verdict: synthesis asks for one in
    either mode, each template in its own words.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "reply_threads",
        "today", "generator_version", "max_turns",
    )
    _identify_review(b, job, pr_title=job.pr.title)
    b.set("repo", job.repo)
    b.set("pr_head_sha", job.pr.head_sha)
    b.set("wt_path", job.wt_path)
    b.set("prior_section", "")
    b.set("group_count", extra["group_count"])
    b.set("verdict_options", VERDICT_OPTIONS)
    b.set("holistic_content", extra.get("holistic_content") or "_No holistic assessment available._")
    b.set("merged_content", extra["merged_content"])
    b.output(output)
    # Synthesis has all findings in merged_content — diff is supplementary,
    # so allow it to shrink to 0 rather than blowing the budget.
    b.fit(job, skip_file_contents=True, min_diff=0)
    return b, ""


def _survey_prompt(job, common, extra, output):
    """The survey of the whole PR — the holistic scan and the scout both.

    The two are alternative first passes over identical inputs. Only the file
    they write differs, and that is the phase spec's answer rather than
    anything this builder has to know.
    """
    b = PromptBuilder(common)
    b.shared(
        "pr_header", "state_context", "reviews_section",
        "issue_section", "env_section", "omitted_guidance", "max_turns",
    )
    b.set("pr_number", job.pr_number)
    b.set("repo", job.repo)
    b.set("all_files_formatted", job.pr.all_files_formatted)
    b.output(output)
    b.fit(job)
    return b, ""


def _prompt_group(job, common, extra, output):
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
    b.set("reply_threads", _build_reply_threads_section(job.reply_threads, file_filter=file_filter))
    b.set("project_context", build_project_context(job.preflight, file_filter=file_filter) if job.preflight else "")
    b.set("holistic_block", holistic_block)
    b.set("prior_section", prior_section)
    b.set("group_idx", extra["group_idx"])
    b.set("group_count", extra["group_count"])
    b.set("group_name", extra["group_name"])
    b.set("group_files_formatted", extra["group_files_formatted"])
    b.output(output)
    b.fit(job, file_filter=file_filter, skip_project_context=True)
    return b, str(extra["group_idx"])


def _prompt_disprove(job, common, extra, output):
    b = PromptBuilder(common)
    b.shared("max_turns")
    b.set("review_content", extra.get("review_content", ""))
    b.output(output)
    return b, ""


# Every phase that renders a review prompt, and the builder that fills it.
# Keyed by `Phase` rather than by template filename: the filename is the spec's
# answer to a question this table does not ask, and keying on it left the two
# registries with no name in common to check each other against — a phase could
# gain a template and never gain a builder. `test_review_contracts` holds this
# table to the review phases the registry declares.
_PROMPT_BUILDERS = {
    Phase.SINGLE: _prompt_single,
    Phase.HOLISTIC: _survey_prompt,
    Phase.SCOUT: _survey_prompt,
    Phase.GROUP: _prompt_group,
    Phase.SYNTHESIS: _prompt_synthesis,
    Phase.DISPROVE: _prompt_disprove,
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
        omitted_guidance=_build_omitted_guidance(
            job.preflight,
            skip_omitted=EFFORT_PRESETS[job.effort].skip_omitted_files,
        ),
        max_turns=max_turns,
    )


class PromptTooLarge(RuntimeError):
    """A rendered prompt that exceeds the budget with every lever already pulled.

    Raised by `build_prompt` after the prompt and its stats are written, so the
    oversized prompt is on disk to look at. The alternative — logging "EXCEEDS
    budget" and sending it anyway — spends a phase's cost on a request the model
    truncates or rejects, and reports whatever comes back as the phase's finding.
    """

    def __init__(self, template: str, prompt_bytes: int):
        self.template = template
        self.prompt_bytes = prompt_bytes
        super().__init__(
            f"{template} prompt is {prompt_bytes // 1024}KB against a "
            f"{MAX_PROMPT_BYTES // 1024}KB budget, with every lever already pulled"
        )


def build_prompt(phase: Phase, job: ReviewJob, *, max_turns: int, **extra) -> str:
    """Render ``phase``'s prompt for ``job``, with ``max_turns`` turns to spend.

    The template and the file the agent is told to write both come off the
    phase's registry entry, so a caller names the phase and nothing else about
    it. ``extra`` carries only what the phase cannot derive — the group's
    identity and the content a later phase reasons over.

    Raises `PromptTooLarge` when the result exceeds `MAX_PROMPT_BYTES` even
    after the budget ladder has cut everything it can.
    """
    builder = _PROMPT_BUILDERS.get(phase)
    if builder is None:
        raise ValueError(f"{phase} renders no review prompt")

    spec = PHASES[phase]
    # A phase that names an artifact of its own is told that path; the rest
    # write the review document. `group_idx` is the only index in play, and
    # `phase_output_path` rejects it for a phase that writes one artifact.
    output = (
        phase_output_path(job.review_file, phase, extra.get("group_idx"))
        if spec.output_filename else job.review_file
    )
    template_name = spec.template_for(job.mode)

    common = _build_common_sections(job, max_turns=max_turns)
    prompt_builder, label = builder(job, common, extra, output)
    template_vars = prompt_builder.vars
    rendered = agent_templates.render(template_name, **template_vars)
    prompt = _log_prompt_size(
        template_name, rendered, template_vars, job,
        label=label, cuts=prompt_builder.cuts,
    )
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise PromptTooLarge(template_name, len(prompt.encode()))
    return prompt


