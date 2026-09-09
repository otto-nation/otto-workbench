"""The prompt that asks a model to triage a round of review threads.

The second-largest prompt in the repo, and the only large one that was not in a
module of its own — `review/prompt.py` and `review/prompt_sections.py` are the
precedent. Nothing about it is CLI-specific: it takes the threads, the code
around them, the branch log and any undecomposed top-level comments, and returns
a string.

Two things in it are load-bearing and easy to lose in an edit. The verdict
definitions draw the line between `already_addressed` and `invalid` — the code
context is current HEAD, so a suggestion that looks satisfied is usually one an
earlier round of this same review already acted on, and calling that `invalid`
tells the reviewer they were wrong when they were agreed with. And every verdict
posted back to a reviewer must cite a line: a claim about their code with
nothing to point at is not a claim, which is why `evidence_file` is required for
exactly the two verdicts that are posted outward.
"""

# doc-group: publishing

from __future__ import annotations

import dataclasses
import json

from pr.thread_models import ReportThread


def build_triage_prompt(
    non_resolved: list[ReportThread],
    code_context: str,
    unseen_comments: list[dict] | None = None,
    commit_log: str = "",
) -> str:
    """Build the AI classification prompt for thread triage."""
    thread_json = json.dumps([dataclasses.asdict(t) for t in non_resolved], indent=2)
    context_block = ""
    if code_context:
        context_block = f"\nCode context for referenced files:\n{code_context}\n"

    history_block = ""
    if commit_log:
        history_block = (
            "\nCommits already made on this branch (newest first). A commit whose "
            "subject matches what a thread asks for means that thread was already "
            "acted on — classify it already_addressed, not invalid:\n"
            f"{commit_log}\n"
        )

    comments_block = ""
    comment_items_schema = ""
    if unseen_comments:
        comments_json = json.dumps(unseen_comments, indent=2)
        comments_block = f"""

Additionally, analyze these top-level PR comments. Each comment may contain multiple
actionable points — decompose them into individual items and classify each one separately.
Non-actionable preamble (greetings, praise, general observations) should be skipped.

Top-level comments:
{comments_json}
"""
        comment_items_schema = """
  "comment_items": [
    {{
      "source_id": "id from input comment",
      "source_type": "issue_comment or review_body (from input)",
      "index": 0,
      "reviewer": "user from input comment",
      "classification": "actionable_suggestion|question|approval|conflicting",
      "verification": "valid|already_addressed|invalid|needs_discussion (only for actionable_suggestion, empty string otherwise)",
      "complexity": "low|medium|high (only for actionable_suggestion with verification=valid, empty string otherwise)",
      "reasoning": "brief explanation",
      "summary": "one-line summary of this specific item",
      "file": "file path if referenced in the item (empty string if not)",
      "line": 0,
      "body": "relevant excerpt from the comment for this item",
      "evidence_file": "file proving the verdict (required for already_addressed/invalid)",
      "evidence_line": 0
    }}
  ],"""

    return f"""You are a code review triage assistant. Analyze these PR review threads and classify each one.

For each thread, provide:
1. classification: one of 'actionable_suggestion', 'question', 'approval', 'conflicting'
2. verification (only for actionable_suggestion): one of 'valid', 'already_addressed',
   'invalid', 'needs_discussion'
   - valid: the suggestion is correct and the code does NOT yet do what it asks
   - already_addressed: the code already does what the reviewer asks. The code context
     below is current HEAD, which includes commits made earlier in this same review
     cycle — so a suggestion that looks satisfied is usually one that was ALREADY ACTED
     ON in response to this very thread. This is agreement with the reviewer, not
     rejection of them.
   - invalid: the reviewer's premise is factually wrong — they misread the code, or the
     change they describe would break something. Use this ONLY when you can state the
     specific mistake in `reasoning`. "The code already does this" is NEVER invalid; it
     is already_addressed. When torn between invalid and any other value, do not pick
     invalid.
   - needs_discussion: a judgment call, tradeoff, or design decision the author must make
3. complexity (only for actionable_suggestion with verification=valid): one of 'low', 'medium', 'high'
   - low: rename, remove, import fix, guard/nil check, use existing helper
   - medium: logic change within a single function or file
   - high: cross-file refactor, design decision, or architectural change
4. reasoning: one sentence explaining your classification/verification
5. summary: one-line summary of the thread
6. evidence_file / evidence_line: the file and 1-based line that prove your verdict.
   REQUIRED for already_addressed and invalid — those verdicts get posted back to the
   reviewer as a claim about their code, and a claim with no line to point at is not one
   you can make. Cite the line that already does what the reviewer asked
   (already_addressed) or the line that contradicts their premise (invalid). If you
   cannot name one, the verdict is needs_discussion. Leave both empty otherwise.

Thread data:
{thread_json}
{context_block}{history_block}{comments_block}
Return JSON matching this exact schema:
{{
  "threads": [
    {{
      "id": "id from input",
      "state": "state from input",
      "classification": "actionable_suggestion|question|approval|conflicting",
      "verification": "valid|already_addressed|invalid|needs_discussion (only for actionable_suggestion, empty string otherwise)",
      "complexity": "low|medium|high (only for actionable_suggestion with verification=valid, empty string otherwise)",
      "reasoning": "brief explanation",
      "file": "file from input",
      "line": "line from input",
      "reviewer": "reviewer from input",
      "summary": "one-line summary",
      "evidence_file": "file proving the verdict (required for already_addressed/invalid)",
      "evidence_line": 0
    }}
  ],{comment_items_schema}
  "stats": {{
    "total": "count of threads",
    "actionable": "count of actionable threads",
    "questions": "count of question threads",
    "approvals": "count of approval threads",
    "conflicting": "count of conflicting threads",
    "valid": "count of valid threads",
    "invalid": "count of invalid threads",
    "already_addressed": "count of already-addressed threads",
    "comment_items_total": "count of decomposed comment items (0 if none)",
    "comment_items_actionable": "count of actionable comment items (0 if none)"
  }}
}}

IMPORTANT: Return ONLY the JSON object, no markdown fencing or explanation."""
