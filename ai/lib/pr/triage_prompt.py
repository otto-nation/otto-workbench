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
import textwrap

from pr.thread_models import (
    Classification,
    CommentSourceKind,
    Complexity,
    ReportThread,
    Verification,
)

# The steering prose for each verdict, keyed by the member it explains.
#
# The wording lives here and the vocabulary lives on the enum, which is the
# split `ReviewVerdict` does not make — its `prose` is three words of UI text,
# while this is five lines of prompt engineering and belongs with the prompt.
# Keyed by member rather than listed in order so a new verdict with no guidance
# is a test failure, not a value the model is offered and never told about.
VERIFICATION_GUIDANCE = {
    Verification.VALID: (
        "the suggestion is correct and the code does NOT yet do what it asks"
    ),
    Verification.ALREADY_ADDRESSED: (
        "the code already does what the reviewer asks. The code context\n"
        "     below is current HEAD, which includes commits made earlier in this same review\n"
        "     cycle — so a suggestion that looks satisfied is usually one that was ALREADY ACTED\n"
        "     ON in response to this very thread. This is agreement with the reviewer, not\n"
        "     rejection of them."
    ),
    Verification.INVALID: (
        "the reviewer's premise is factually wrong — they misread the code, or the\n"
        "     change they describe would break something. Use this ONLY when you can state the\n"
        "     specific mistake in `reasoning`. \"The code already does this\" is NEVER invalid; it\n"
        "     is already_addressed. When torn between invalid and any other value, do not pick\n"
        "     invalid."
    ),
    Verification.NEEDS_DISCUSSION: (
        "a judgment call, tradeoff, or design decision the author must make"
    ),
}

# Same split as VERIFICATION_GUIDANCE: the labels live on Complexity, the
# steering prose lives here. Keyed by member so a new level with no prose is a
# test failure rather than a value the model is offered and never told about.
COMPLEXITY_GUIDANCE = {
    Complexity.LOW: (
        "rename, remove, import fix, guard/nil check, use existing helper"
    ),
    Complexity.MEDIUM: "logic change within a single function or file",
    Complexity.HIGH: (
        "cross-file refactor, design decision, or architectural change"
    ),
}


def _members(enum_cls):
    """Non-UNSET members, in definition order.

    UNSET is never offered to the model: the prompt asks for an empty string
    where a field does not apply; it is not a value the model chooses.
    """
    return tuple(m for m in enum_cls if m is not enum_cls.UNSET)


def _options(enum_cls) -> str:
    """The members as the prompt offers them: `'a', 'b', 'c'`."""
    return ", ".join(f"'{m.value}'" for m in _members(enum_cls))


def _vocab_schema_lines() -> str:
    """The three vocabulary lines of the JSON schema, written once.

    Spliced into both the `threads` and `comment_items` blocks, which carried
    a copy each — two literals that had to agree or the model was handed
    contradictory schemas for the two halves of one answer.
    """
    def joined(enum_cls):
        return "|".join(m.value for m in _members(enum_cls))

    return (
        f'      "classification": "{joined(Classification)}",\n'
        f'      "verification": "{joined(Verification)}'
        f' (only for {Classification.ACTIONABLE_SUGGESTION}, empty string otherwise)",\n'
        f'      "complexity": "{joined(Complexity)}'
        f' (only for {Classification.ACTIONABLE_SUGGESTION} with verification={Verification.VALID}, empty string otherwise)",'
    )


def _guidance_lines(enum_cls, guidance: dict) -> str:
    return "\n".join(
        f"   - {member.value}: {guidance[member]}"
        for member in _members(enum_cls)
    )


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
        # Single braces, unlike the outer f-string this is substituted into.
        # Nothing collapses them on the way through, so they are already what
        # the model sees, and must match the `threads` and `stats` blocks the
        # outer template renders beside them.
        # Concatenation, not an f-string: this block's braces are single, and
        # converting it would double them into the schema the model sees.
        comment_items_schema = (
            """
  "comment_items": [
    {
      "source_id": "id from input comment",
      "source_type": \""""
            + " or ".join(m.value for m in _members(CommentSourceKind))
            + """ (from input)",
      "index": 0,
      "reviewer": "user from input comment",
"""
            + _vocab_schema_lines()
            + """
      "reasoning": "brief explanation",
      "summary": "one-line summary of this specific item",
      "file": "file path if referenced in the item (empty string if not)",
      "line": 0,
      "body": "relevant excerpt from the comment for this item",
      "evidence_file": "file proving the verdict (required for """
            + f"{Verification.ALREADY_ADDRESSED}/{Verification.INVALID}"
            + """)",
      "evidence_line": 0
    }
  ],"""
        )

    # The verification sentence is prose the model reads; wrap it to the width
    # the rest of the prompt sits in so a longer option list reflows instead of
    # overflowing the line the rest of the prompt uses.
    verification_line = textwrap.fill(
        f"2. verification (only for {Classification.ACTIONABLE_SUGGESTION}): one of {_options(Verification)}",
        width=88,
        subsequent_indent="   ",
    )

    return f"""You are a code review triage assistant. Analyze these PR review threads and classify each one.

For each thread, provide:
1. classification: one of {_options(Classification)}
{verification_line}
{_guidance_lines(Verification, VERIFICATION_GUIDANCE)}
3. complexity (only for {Classification.ACTIONABLE_SUGGESTION} with verification={Verification.VALID}): one of {_options(Complexity)}
{_guidance_lines(Complexity, COMPLEXITY_GUIDANCE)}
4. reasoning: one sentence explaining your classification/verification
5. summary: one-line summary of the thread
6. evidence_file / evidence_line: the file and 1-based line that prove your verdict.
   REQUIRED for {Verification.ALREADY_ADDRESSED} and {Verification.INVALID} — those verdicts get posted back to the
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
{_vocab_schema_lines()}
      "reasoning": "brief explanation",
      "file": "file from input",
      "line": "line from input",
      "reviewer": "reviewer from input",
      "summary": "one-line summary",
      "evidence_file": "file proving the verdict (required for {Verification.ALREADY_ADDRESSED}/{Verification.INVALID})",
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
