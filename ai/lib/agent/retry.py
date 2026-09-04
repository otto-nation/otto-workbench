"""Shared guard against agents that finish without producing anything.

An agent that runs to its own conclusion having never called a write tool was
thrashing, not working.  The review pipeline learned to diagnose that and give
it one more attempt with a hint naming the write mechanism; every `pr` script
that drives an agent needs the same guard, so it lives here rather than inside
review_retry.

Two shapes are supported, matching the two ways the `pr` scripts call an agent:

  retry_unproductive  — an agent with tools whose work lands in a file or a
                        tracking checklist.  Diagnosed from its session log.
  retry_blank_response — a stateless prompt whose answer must parse.  There is
                        no session log, so the response itself is the signal.

A second attempt writes over the first one's session log, so `preserve_log` and
`restore_preserved` live here too: a retry is the only thing that overwrites a
log, and the pair exists so both attempts' result records survive it.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core import log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.types import DEFAULT_RETRY_CEILING
from agent.session import diagnose_missing_output, try_recover_output

# Kinds a second attempt could plausibly clear. Turn exhaustion and a run that
# never called a write tool are the two the hints address directly; the rest are
# faults in the run's surroundings rather than in the run itself.
_RETRYABLE_KINDS = frozenset({
    DiagnosisKind.MAX_TURNS,
    DiagnosisKind.NO_RESULT_RECORD,
    DiagnosisKind.NO_SESSION_LOG,
    DiagnosisKind.TRANSIENT,
})

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
    "A finding that needs a design decision is not yours to apply — record that "
    "verdict on it the way the instructions below spell it, and move on.\n\n"
)

CI_FIX_RETRY_HINT = (
    "IMPORTANT: A previous attempt ran out of turns investigating without fixing "
    "any failure. Start with the first failing check and apply edits IMMEDIATELY. "
    "A failure that needs a human decision is not yours to apply — record that "
    "verdict on it the way the instructions below spell it, and move on.\n\n"
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


def preserve_log(path: str) -> str:
    """Read session log content before a retry that will overwrite it."""
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def restore_preserved(path: str, prior: str) -> None:
    """Prepend prior log content so both attempts' result records are preserved."""
    if not prior:
        return
    try:
        current = Path(path).read_text()
    except OSError:
        current = ""
    Path(path).write_text(prior + current)


def is_retryable(diagnosis: Diagnosis) -> bool:
    """Whether a second attempt could plausibly do better than the first."""
    # A run that ended on its own terms without ever calling a write tool
    # produced nothing and gave no reason it could not have — the retry hint
    # names the mechanism. `diagnose_missing_output` withholds this flag from
    # crashes, so it never makes a permanent error retryable.
    if diagnosis.no_write_tool:
        return True
    return diagnosis.kind in _RETRYABLE_KINDS


def hint_for(diagnosis: Diagnosis) -> str:
    """The most specific hint the diagnosis supports.

    Checked most-specific first: the no-write flag attaches to turn exhaustion
    and to clean completions alike, and naming the write mechanism beats
    telling the agent to hurry.
    """
    if diagnosis.no_write_tool:
        return NO_WRITE_HINT
    if diagnosis.kind is DiagnosisKind.MAX_TURNS:
        return RETRY_HINT
    return ""


def turns_for(
    diagnosis: Diagnosis, max_turns: int, *, ceiling: int = DEFAULT_RETRY_CEILING,
) -> int:
    """Turn budget for a retry.

    Only turn exhaustion earns a bigger budget — a transient API error or a
    missing result record would fail identically with more turns.

    `ceiling` belongs to the phase, not to this module: the default is sized
    for the review pipeline's group phases, where 15 turns double to 30. A
    phase already operating above that — the comments fix pass runs at 60 —
    would see the doubling silently cancelled, so the result is floored at the
    original budget and the phase's own `retry.ceiling` is passed in instead.
    """
    if diagnosis.kind is not DiagnosisKind.MAX_TURNS:
        return max_turns
    return max(max_turns, min(max_turns * 2, ceiling))


def retry_unproductive(
    invoke: Callable[[str, int], int],
    prompt: str,
    log_path: str,
    *,
    label: str,
    max_turns: int,
    produced: Callable[[], bool],
    recover: Callable[[], None] | None = None,
    hint_select: Callable[[Diagnosis], str] = hint_for,
    ceiling: int = DEFAULT_RETRY_CEILING,
) -> Diagnosis | None:
    """Give an agent that produced nothing a second attempt.

    `invoke(prompt, max_turns)` runs the agent and `produced()` reports whether
    it left anything behind — an output file for a review phase, a checked box
    for a fix pass.  `recover()`, when given, salvages output from the session
    log before the run is written off.  `ceiling` bounds the retry's turn
    budget — see `turns_for`.

    Returns the diagnosis, or None once something was produced.
    """
    if not produced() and recover:
        recover()
    if produced():
        return None

    diagnosis = diagnose_missing_output(log_path)
    if not is_retryable(diagnosis):
        return diagnosis

    turns = turns_for(diagnosis, max_turns, ceiling=ceiling)
    log.warn(
        f"{label} produced no output ({diagnosis.message}) "
        f"— retrying once ({turns} turns)"
    )
    log.blank()
    prior = preserve_log(log_path)
    invoke(hint_select(diagnosis) + prompt, turns)
    log.blank()

    if not produced() and recover:
        recover()
    # Diagnose before restoring: in a merged log the first attempt's tool calls
    # would mask what the retry actually did.
    retry_diagnosis = None if produced() else diagnose_missing_output(log_path)
    restore_preserved(log_path, prior)
    return retry_diagnosis


def run_guarded(
    invoke: Callable[[str, int], int],
    prompt: str,
    log_path: str,
    *,
    label: str,
    max_turns: int,
    produced: Callable[[], bool],
    recover: Callable[[], None] | None = None,
    hint_select: Callable[[Diagnosis], str] = hint_for,
    ceiling: int = DEFAULT_RETRY_CEILING,
) -> Diagnosis | None:
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
        ceiling=ceiling,
    )


def retry_missing_output(
    invoke: Callable[[str, int], int],
    prompt: str, log_path: str, output_path: str,
    *, label: str, max_turns: int,
) -> Diagnosis | None:
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
