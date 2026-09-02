"""What a prompt says about the PR, rendered from what was collected.

Every function here takes what it renders and returns markdown, so a phase
composes the sections it wants without knowing how any of them is built —
nothing in this module decides which sections a phase asks for or what order
they run in.

Which sections a phase asks for is `review_prompt`'s, and how much room they
get is `review_budget`'s.

Scoping the prior review to the files a group is reviewing cuts it a finding
at a time, and where a finding stops is `review_spans`'s `finding_spans` — the
same measure the gates that trim a finished review use. A section that
measured it here would quote an agent evidence belonging to a finding it was
not shown.

Not to be confused with `review_sections`, which is the posting pipeline's
config-driven registry of sections already written to a review document —
that module reads what an agent wrote, this one decides what an agent is
shown before it writes anything.
"""

# doc-group: pipeline

from __future__ import annotations

import bisect
import json

import git_client
from agent_types import EFFORT_PRESETS, Effort
from review_budget import (
    FileFit, MAX_DELTA_LIST_ENTRIES, MAX_REVIEW_BODY_LEN, MIN_DELTA_DIFF_BYTES,
)
from review_collect import scope_diff, truncate_diff
from review_document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS, strip_sections,
)
from review_grammar import BOLD_FINDING_ID_RE, SCOPED_FINDING_RE
from review_merge import annotate_prior_with_stable_ids
from review_reconcile import ReplyThreads
from review_scout import format_leads_block, is_scout_output, parse_scout_output
from review_spans import finding_spans
from review_types import (
    FILE_STAT_FMT, PRContext, PreflightData, PriorDisposition, PriorFinding,
    PRMetadata, ReplyState, ReviewJob,
)


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


def _in_scope(line: str, filter_set: set[str]) -> bool:
    """Whether the finding line names a file the scoped review is about."""
    m = SCOPED_FINDING_RE.match(line)
    return bool(m) and m.group(1) in filter_set


def _collect_scoped_sections(
    prior_text: str, filter_set: set[str],
) -> list[tuple[str, list[str]]]:
    """Each `## ` section of `prior_text` and the in-scope findings under it.

    `SCOPED_FINDING_RE` picks the findings; `finding_spans` says how much of the
    text each one brings with it, so a finding kept for its path keeps the
    evidence quoted under it and nothing quoted under its neighbour. Sections
    come back in document order, empty ones included — the caller decides
    whether a heading with no findings left is worth printing.
    """
    lines = prior_text.split("\n")
    headers = [i for i, line in enumerate(lines) if line.strip().startswith("## ")]
    sections: list[tuple[str, list[str]]] = [(lines[i], []) for i in headers]
    for span in finding_spans(prior_text):
        owner = bisect.bisect_right(headers, span.start) - 1
        if owner < 0 or not _in_scope(span.line, filter_set):
            continue
        sections[owner][1].extend(span.text_of(prior_text).split("\n"))
    return sections


def _scope_prior_review(prior_text: str, file_filter: list[str]) -> str:
    filter_set = set(file_filter)
    sections = _collect_scoped_sections(prior_text, filter_set)

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
    ReplyState.CONTESTED: "[CONTESTED]",
    ReplyState.ACKNOWLEDGED: "[ACKNOWLEDGED]",
    ReplyState.RESOLVED: "[RESOLVED]",
    ReplyState.REPLIED: "[REPLIED]",
}

def _annotate_with_thread_state(review_text: str, reply_threads: ReplyThreads | None) -> str:
    threads = reply_threads.threads if reply_threads else []
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
# `review_grammar.parse_ledger_line` to hold the two together.
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
    reply_threads: ReplyThreads | None = None,
) -> str:
    if not prior_review:
        return ""
    review_text = _strip_internal_sections(annotate_prior_with_stable_ids(prior_review))
    if file_filter:
        review_text = _scope_prior_review(review_text, file_filter)
    if not review_text:
        return ""
    # A frozen dataclass is truthy even with an empty `threads` list, unlike the
    # dict this replaced — so the emptiness has to be asked of the field itself.
    if reply_threads and reply_threads.threads:
        review_text = _annotate_with_thread_state(review_text, reply_threads)
    return f"""
## Prior review
{context}
{_LEDGER_INSTRUCTION}

<prior_review>
{review_text}
</prior_review>"""


# What synthesis is told about the prior findings the group agents passed over.
# It asks for a decision, not for a restatement: a finding whose subject is
# still in the tree may well be right to decline, and the outcome this exists to
# prevent is the third one — the document saying nothing about it at all, which
# the next round cannot tell apart from the finding never having existed.
_UNACCOUNTED_CTX = """These prior findings reached no disposition: the group
agents did not mention them, and nothing in the tree confirmed the issue was
resolved. Decide each one here, on the text below and the merged findings
above. Omitting one is not a third option — a prior finding this document
does not mention is reported as unaccounted for, and comes back unsettled
next round. Your output holds one `## Prior findings` section total: if the
merged findings above already carry one, add these findings' verdicts as
lines to that same section instead of writing a second one; only write it
fresh below if none exists yet."""


def _build_unaccounted_section(findings: list[PriorFinding]) -> str:
    """The prior findings synthesis must dispose of, with the text to judge them on.

    The findings come before `_LEDGER_INSTRUCTION` rather than after it, which
    is the reverse of `_build_prior_section`: the instruction asks for a line
    per finding above it, and here the only findings synthesis has been shown
    are these. Restating the verdict forms in this section's own words would
    make it the second place the ledger's shape is written down.
    """
    if not findings:
        return ""
    reported = "\n\n".join(finding.text.strip() for finding in findings)
    return f"""
## Prior findings awaiting a disposition
{_UNACCOUNTED_CTX}

<unaccounted_findings>
{reported}
</unaccounted_findings>
{_LEDGER_INSTRUCTION}"""
