"""Shared guard against agents that finish without producing anything.

An agent that runs to its own conclusion having never called a write tool was
thrashing, not working.  The review pipeline learned to diagnose that and give
it one more attempt with a hint naming the write mechanism; every `pr` script
that drives an agent needs the same guard, so it lives here rather than inside
review_pipeline.

Two shapes are supported, matching the two ways the `pr` scripts call an agent:

  retry_unproductive  — an agent with tools whose work lands in a file or a
                        tracking checklist.  Diagnosed from its session log.
  retry_blank_response — a stateless prompt whose answer must parse.  There is
                        no session log, so the response itself is the signal.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import log
from review_agent import (
    DIAG_NO_RESULT_RECORD, DIAG_NO_SESSION_LOG, DIAG_NO_WRITE_TOOL_CALL,
    diagnose_missing_output, is_transient_error, try_recover_output,
)
from review_common import preserve_log, restore_preserved

MAX_TURNS_REASON = "agent hit max turns"

# Ceiling for a retry's turn budget. Group reviews arrived at this number and
# every other phase inherited it; a single shared cap keeps the guard uniform.
RETRY_MAX_TURNS = 30

NON_RECOVERABLE_REASONS = ("permission denied",)

RETRY_HINT = (
    "IMPORTANT: A previous attempt ran out of turns before writing output. "
    "Write your findings file IMMEDIATELY as your first action, then verify.\n\n"
)

NO_WRITE_HINT = (
    "IMPORTANT: A previous attempt finished without ever calling a "
    "file-writing tool. Write your output file FIRST, before any further "
    "investigation: Read it — it already exists and is empty — then Edit it "
    "with an empty `old_string` to insert the complete document. Refine it "
    "with further edits only if turns remain.\n\n"
)

FIX_RETRY_HINT = (
    "IMPORTANT: A previous attempt ran out of turns reading files without applying any fixes. "
    "Start with the highest-severity fixable findings and apply edits IMMEDIATELY. "
    "Skip findings that require design decisions — annotate them with *(skipped — reason)* "
    "and move on.\n\n"
)

BLANK_RESPONSE_HINT = (
    "IMPORTANT: A previous attempt returned an answer that could not be parsed. "
    "Emit the requested markers exactly as specified and put nothing outside "
    "them.\n\n"
)


def has_output(path: str) -> bool:
    """Check if a file exists and has content (not just pre-created empty)."""
    p = Path(path)
    return p.exists() and p.stat().st_size > 0


def is_retryable(reason: str) -> bool:
    """Whether a second attempt could plausibly do better than the first."""
    if reason.startswith("skipped: "):
        return False
    if MAX_TURNS_REASON in reason:
        return True
    # A run that ended on its own terms without ever calling a write tool
    # produced nothing and gave no reason it could not have — the retry hint
    # names the mechanism. `diagnose_missing_output` withholds this label from
    # crashes, so it never makes a permanent error retryable.
    if DIAG_NO_WRITE_TOOL_CALL in reason:
        return True
    if reason in (DIAG_NO_RESULT_RECORD, DIAG_NO_SESSION_LOG):
        return True
    if is_transient_error(reason):
        return True
    return False


def hint_for(reason: str) -> str:
    """The most specific hint the diagnosis supports.

    Checked most-specific first: the no-write label attaches to turn exhaustion
    and to clean completions alike, and naming the write mechanism beats
    telling the agent to hurry.
    """
    if DIAG_NO_WRITE_TOOL_CALL in reason:
        return NO_WRITE_HINT
    if MAX_TURNS_REASON in reason:
        return RETRY_HINT
    return ""


def turns_for(reason: str, max_turns: int) -> int:
    """Turn budget for a retry.

    Only turn exhaustion earns a bigger budget — a transient API error or a
    missing result record would fail identically with more turns. Doubling is
    capped at the shared ceiling, but never lowers a budget that was already
    scaled above it.
    """
    if MAX_TURNS_REASON not in reason:
        return max_turns
    return min(max_turns * 2, max(RETRY_MAX_TURNS, max_turns))


def retry_unproductive(
    invoke: Callable[[str, int], int],
    prompt: str,
    log_path: str,
    *,
    label: str,
    max_turns: int,
    produced: Callable[[], bool],
    recover: Callable[[], None] | None = None,
    hint_select: Callable[[str], str] = hint_for,
) -> str:
    """Give an agent that produced nothing a second attempt.

    `invoke(prompt, max_turns)` runs the agent and `produced()` reports whether
    it left anything behind — an output file for a review phase, a checked box
    for a fix pass.  `recover()`, when given, salvages output from the session
    log before the run is written off.

    Returns the diagnosed failure reason, or "" once something was produced.
    """
    if not produced() and recover:
        recover()
    if produced():
        return ""

    reason = diagnose_missing_output(log_path)
    if not is_retryable(reason):
        return reason

    turns = turns_for(reason, max_turns)
    log.warn(f"{label} produced no output ({reason}) — retrying once ({turns} turns)")
    log.blank()
    prior = preserve_log(log_path)
    invoke(hint_select(reason) + prompt, turns)
    log.blank()

    if not produced() and recover:
        recover()
    # Diagnose before restoring: in a merged log the first attempt's tool calls
    # would mask what the retry actually did.
    retry_reason = "" if produced() else diagnose_missing_output(log_path)
    restore_preserved(log_path, prior)
    return retry_reason


def run_guarded(
    invoke: Callable[[str, int], int],
    prompt: str,
    log_path: str,
    *,
    label: str,
    max_turns: int,
    produced: Callable[[], bool],
    recover: Callable[[], None] | None = None,
    hint_select: Callable[[str], str] = hint_for,
) -> str:
    """Run an agent and guard the result with `retry_unproductive`.

    `retry_unproductive` is post-hoc: it asks `produced()` before it invokes
    anything, so handing it the first attempt would let a leftover artifact from
    an earlier pass satisfy the predicate and skip the run entirely.  Callers
    that have not already run the agent want this instead.
    """
    invoke(prompt, max_turns)
    return retry_unproductive(
        invoke, prompt, log_path,
        label=label, max_turns=max_turns,
        produced=produced, recover=recover, hint_select=hint_select,
    )


def retry_missing_output(
    invoke: Callable[[str, int], int],
    prompt: str, log_path: str, output_path: str,
    *, label: str, max_turns: int,
) -> str:
    """`retry_unproductive` for an agent whose output is a single file."""
    return retry_unproductive(
        invoke, prompt, log_path,
        label=label, max_turns=max_turns,
        produced=lambda: has_output(output_path),
        recover=lambda: try_recover_output(log_path, output_path),
    )


def retry_blank_response(
    call: Callable[[str], tuple[str, int]],
    prompt: str,
    *,
    label: str,
    usable: Callable[[str], bool],
) -> tuple[str, int]:
    """Give a stateless prompt one more attempt when its answer will not parse.

    `call(prompt)` returns `(response, exit_code)` and `usable(response)` says
    whether the response can be consumed.  A non-zero exit code is returned
    as-is: the backend already reported why, and the same call would reproduce
    it.  There is no session log here, so an unusable answer is the only signal
    that the agent spent a turn without doing the job.
    """
    response, rc = call(prompt)
    if rc != 0 or usable(response):
        return response, rc
    log.warn(f"{label} returned an unparseable response — retrying once")
    return call(BLANK_RESPONSE_HINT + prompt)
