"""One round of thread triage: ask the model, then refuse what it cannot back.

The pass that decides what each review thread is — a suggestion, a question, an
approval — and, for a suggestion, whether the code already does what it asks.
Two of those verdicts are posted back to the reviewer as a claim about their
code, so this module's second half exists to stop an unsupported one going out:
:func:`downgrade_unsupported_verdicts` demotes any `already_addressed` or
`invalid` whose cited line is not in the tree, because a claim with a link to
nothing is worse than no claim.

The prompt is `pr.triage_prompt`; the code and history it reads are
`pr.thread_context`. What is here is the round — invoke, parse, assign ids,
verify, record.
"""

# doc-group: publishing

from __future__ import annotations

import json
import re
from pathlib import Path

from agent import invoke as agent_invoke
from core import log
from core import proc
from core.phases import Phase
from core.trail import Trail
from pr import domains as pr_domains
from pr import permalinks
from pr import state as pr_state
from pr import thread_context
from pr import triage_prompt
from pr.comments_state import ThreadState
from pr.thread_models import (
    CommentItem, CommentSourceKind, Complexity, PRReport, TriageResult,
    Verification, triage_result_from_dict,
)

_DOWNGRADE_REASON = (
    "downgraded from {verdict}: triage cited no line in this repo to back the claim"
)


# A model asked for JSON returns it fenced, prefaced, or bare depending on the
# backend and the day. Kept here rather than in `agent` because triage is the
# only caller: a second one is the moment to lift it, not before.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip markdown code fences or preamble from AI output before JSON parsing."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    stripped = text.strip()
    start = stripped.find("{")
    if start > 0:
        end = stripped.rfind("}")
        if end > start:
            return stripped[start:end + 1]
    return stripped


def parses_as_json(text: str) -> bool:
    """Whether triage output can be consumed. Drives the retry-once guard."""
    try:
        json.loads(extract_json(text))
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def collect_unseen_comments(report: PRReport) -> list[dict]:
    """Collect unseen issue and review body comments for triage decomposition."""
    unseen = []
    for c in report.issue_comments:
        if not c.get("seen"):
            unseen.append({
                "id": c["id"],
                "source_type": CommentSourceKind.ISSUE_COMMENT.value,
                "user": c.get("user", ""),
                "body": c.get("body", ""),
            })
    for c in report.review_body_comments:
        if not c.get("seen"):
            unseen.append({
                "id": c["id"],
                "source_type": CommentSourceKind.REVIEW_BODY.value,
                "user": c.get("user", ""),
                "body": c.get("body", ""),
            })
    return unseen


def assign_item_ids(comment_items: list[CommentItem]) -> None:
    """Assign synthetic IDs to decomposed comment items in place.

    The prefix comes off the kind itself, so the id this writes is one
    `permalinks.comment_item_source` can read back. The two-armed ternary this
    replaced called everything that was not an issue comment a review body,
    which gave an entry with a drifted `source_type` an `rb-` id and a
    permalink pointing at a review that does not exist.

    An item whose kind is UNSET gets a bare `-{source_id}-{index}`, which
    parses back to no source and so renders without a permalink. That is the
    honest outcome: nothing here knows which comment it came from.
    """
    for item in comment_items:
        item.id = f"{item.source_type.id_prefix}-{item.source_id}-{item.index}"


def downgrade_unsupported_verdicts(
    entries: list[CommentItem], repo_dir: Path, trail: Trail | None = None,
) -> int:
    """Demote verdicts that cannot cite code to needs_discussion. Returns count.

    `already_addressed` and `invalid` are posted back to the reviewer as claims
    about their code, so each one has to point at a line.  A verdict whose cited
    location does not exist in the tree is no better than one with no location:
    the reader gets a link to nothing.  Rather than post an unsupported claim,
    hand the thread to the author.
    """
    downgraded = 0
    for entry in entries:
        if not entry.verification.needs_evidence:
            continue
        if permalinks.evidence_is_real(repo_dir, entry):
            continue
        reason = _DOWNGRADE_REASON.format(verdict=entry.verification)
        if trail:
            trail.info("triage_downgrade", reason, data={"thread": entry.id})
        log.warn(f"{entry.id}: {reason} — routing to needs_discussion")
        entry.verification = Verification.NEEDS_DISCUSSION
        entry.complexity = Complexity.UNSET
        entry.evidence_file = ""
        entry.evidence_line = 0
        entry.reasoning = f"{entry.reasoning} ({reason})".strip()
        downgraded += 1
    return downgraded


def run_triage(report: PRReport, repo_dir: Path, ctx_args: dict,
               trail: Trail | None = None) -> tuple[TriageResult | None, int]:
    """Classify non-resolved threads and decompose top-level comments via AI.

    Returns (triage_result, exit_code). Caller handles stdout output.
    """
    non_resolved = [
        t for t in report.threads
        if t.state not in (ThreadState.RESOLVED, ThreadState.ADDRESSED)
    ]
    unseen_comments = collect_unseen_comments(report)

    if not non_resolved and not unseen_comments:
        return TriageResult(), 0

    code_context = thread_context.gather_code_context(non_resolved, repo_dir)
    prompt = triage_prompt.build_triage_prompt(
        non_resolved, code_context,
        unseen_comments=unseen_comments or None,
        commit_log=thread_context.branch_commit_log(repo_dir),
    )

    pr_number = ctx_args.get("pr_number")
    answer = agent_invoke.run_prompt(
        Phase.COMMENTS_TRIAGE, prompt,
        cwd=repo_dir, usable=parses_as_json, task="comment-triage",
        repo=ctx_args.get("repo"), pr=str(pr_number) if pr_number else None,
    )
    if answer.exit_code != 0:
        if trail:
            trail.error("triage", "AI prompt failed",
                        data={"exit_code": answer.exit_code})
        log.error("ai prompt failed")
        return None, 1

    stdout = answer.text
    try:
        raw = json.loads(extract_json(stdout))
    except (json.JSONDecodeError, TypeError):
        preview = (stdout or "")[:proc.DETAIL_LIMIT]
        if trail:
            trail.failure("triage", "AI returned non-JSON output",
                          output=stdout or "")
        log.error(f"ai returned non-JSON output ({len(stdout or '')} chars): {preview}")
        return None, 1

    triage_result = triage_result_from_dict(raw)
    assign_item_ids(triage_result.comment_items)

    downgraded = downgrade_unsupported_verdicts(
        triage_result.threads, repo_dir, trail,
    ) + downgrade_unsupported_verdicts(
        triage_result.comment_items, repo_dir, trail,
    )
    if downgraded:
        # The model's own count is stale once a verdict moves.
        triage_result.stats.invalid = sum(
            1 for t in triage_result.threads if t.verification is Verification.INVALID
        )

    # Update triage state
    try:
        stats = triage_result.stats
        st = pr_state.load_or_init(**ctx_args)
        pr_state.apply(st, pr_domains.TriageSummary(
            total=stats.total,
            actionable=stats.actionable,
            valid=stats.valid,
            questions=stats.questions,
            comment_items_total=stats.comment_items_total,
            comment_items_actionable=stats.comment_items_actionable,
            updated_at=pr_state.now_iso(),
        ))
        pr_state.save_state(ctx_args["target_dir"], st)
    except Exception as exc:
        if trail:
            trail.error("triage", f"state update failed: {exc}")
        log.error(f"triage state update failed: {exc}")

    return triage_result, 0
