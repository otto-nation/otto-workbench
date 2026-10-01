"""Shared guard against agents that finish without producing anything.

An agent that runs to its own conclusion having never called a write tool was
thrashing, not working.  The review pipeline learned to diagnose that and give
it one more attempt with a hint naming the write mechanism; every `pr` script
that drives an agent needs the same guard, so it lives here rather than inside
review.retry.

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

from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

import core.log
from core.phases import Backend
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.types import DEFAULT_RETRY_CEILING
from agent.session import diagnose_missing_output, try_recover_output

# Kinds a second attempt could plausibly clear. Turn exhaustion and a run that
# never called a write tool are the two the hints address directly; the rest are
# faults in the run's surroundings rather than in the run itself.
#
# A stall belongs here on the same reading: the run was ended by the watch
# rather than by anything it had spent, so a second attempt has its full budget
# and the wedged call is usually not reproduced. It is also the strictly worse
# outcome of the two endings that produce no file, and treating the worse one
# as final while retrying the better one is the asymmetry this set exists to
# avoid.
_RETRYABLE_KINDS = frozenset({
    DiagnosisKind.MAX_TURNS,
    DiagnosisKind.STALLED,
    DiagnosisKind.NO_RESULT_RECORD,
    DiagnosisKind.NO_SESSION_LOG,
    DiagnosisKind.TRANSIENT,
})

RETRY_HINT = (
    "IMPORTANT: A previous attempt ran out of turns before writing output. "
    "Write your findings file IMMEDIATELY as your first action, then verify.\n\n"
)

# The mechanism half of NO_WRITE_HINT, per backend. A retry hint that names the
# other CLI's tools re-issues the recipe that produced the empty file it is
# retrying — see `_WRITE_RECIPES` in `agent.templates`, which this mirrors for
# the shorter hint form.
_NO_WRITE_MECHANISM: Mapping[Backend, str] = MappingProxyType({
    Backend.CLAUDE: (
        "Read it — it already exists and is empty — then Edit it with an empty "
        "`old_string` to insert the complete document. Refine it with further "
        "edits only if turns remain."
    ),
    Backend.PI: (
        "Use the `write` tool to put the complete document into it in one "
        "call. Refine it with `edit` only if turns remain."
    ),
})


def no_write_hint(backend: Backend | None = None) -> str:
    """The retry hint for a run that never called a write tool.

    ``backend=None`` asks the backend layer, the way ``build_output_block``
    does, and falls back to Claude's recipe when nothing names one.
    """
    if backend is None:
        from agent.backend import selected_backend_or_claude

        backend = selected_backend_or_claude()
    return (
        "IMPORTANT: A previous attempt finished without ever calling a "
        "file-writing tool. Write your output file FIRST, before any further "
        f"investigation: {_NO_WRITE_MECHANISM[backend]}\n\n"
    )


# Addressed to an agent that produced a tool call as prose, XML or JSON rather
# than calling one. The ordinary no-write hint fails here on its own terms: it
# tells the agent to write its file first, which is what the agent believes it
# already did, so a second attempt reproduces the first. This names the mistake
# instead of restating the instruction.
_NARRATED_CALL_HINT = (
    "IMPORTANT: A previous attempt wrote out a tool call as text — as prose, "
    "as an XML element, or as a JSON object — instead of calling the tool. "
    "Text that looks like a call does nothing: the file was never written and "
    "the attempt was discarded. Emit an actual tool call this time. If you "
    "find yourself typing the word 'write' followed by a path, or any tag or "
    "brace around one, stop and make the call instead.\n\n"
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

# Addressed to a conflict resolution that came back with the context it was
# shown wrapped around the answer. The blank-response hint above is actively
# wrong here and was what this path used to send: it lectures about emitting
# the markers, and the markers were perfect — the mistake was including lines
# the prompt asked to be left out. A hint that names the wrong mistake buys a
# second identical answer, which is the failure `retry_blank_response`'s own
# docstring warns about.
ECHOED_CONTEXT_HINT = (
    "IMPORTANT: A previous attempt wrapped the surrounding context lines "
    "around its answer. The markers were right; what went in them was not. "
    "Each conflict's replacement is the marker region alone — everything from "
    "the <<<<<<< line through the >>>>>>> line, and not one line above or "
    "below it. The context was shown to you so the merge could be reasoned "
    "about; it is already in the file and will be duplicated if you repeat "
    "it.\n\n"
)

# Addressed to a resolution that left git's own markers in the text it emitted
# — an answer that copied the conflict through instead of merging it. Distinct
# from the echo hint for the same reason that one is distinct from the blank
# one: the two describe opposite mistakes, and the correction for "you included
# too much around the region" tells an agent nothing about "you did not resolve
# the region".
SURVIVING_MARKER_HINT = (
    "IMPORTANT: A previous attempt left git conflict markers in its answer. "
    "A resolution contains no <<<<<<<, =======, ||||||| or >>>>>>> line at "
    "all: it is the single merged version of the code that replaces all of "
    "them. Decide what the merged text should be and emit only that.\n\n"
)

# The same correction for a caller whose answer is a bare JSON object rather
# than a marker-wrapped block. Kept apart rather than folded into the wording
# above: four of the five callers of `retry_blank_response` do wrap their
# answer in markers, so a hint generalised to cover both tells each of them
# about a format it does not use.
JSON_RESPONSE_HINT = (
    "IMPORTANT: A previous attempt replied with prose instead of JSON, and "
    "nothing could be parsed from it. You have no tools and no second turn: "
    "there is no file to open and no command to run, so a reply that announces "
    "one produces nothing. Answer from what is in this prompt, and emit the "
    "JSON object alone — no preamble, no explanation, no code fence.\n\n"
)


# What a caller may pass as a retry hint: the wording itself, or a function
# from the unusable answer to the wording that names what was wrong with it.
RetryHint = str | Callable[[str], str]


def resolve_hint(hint: RetryHint, response: str) -> str:
    """The hint text for *response*, whichever form the caller supplied."""
    return hint(response) if callable(hint) else hint


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

    Narration is checked ahead of the no-write hint for the same reason, one
    step further: both describe a run that wrote nothing, but only this one
    describes an agent that thinks it already did. Naming the mechanism to an
    agent that believes it used the mechanism is the hint that produced a
    second identical failure.
    """
    if diagnosis.narrated_call:
        return _NARRATED_CALL_HINT
    if diagnosis.no_write_tool:
        return no_write_hint()
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
    turns_fn: Callable[[Diagnosis, int], int] | None = None,
    output_path: str = "",
) -> Diagnosis | None:
    """Give an agent that produced nothing a second attempt.

    `invoke(prompt, max_turns)` runs the agent and `produced()` reports whether
    it left anything behind — an output file for a review phase, a checked box
    for a fix pass.  `recover()`, when given, salvages output from the session
    log before the run is written off.  `ceiling` bounds the retry's turn
    budget — see `turns_for`.  `turns_fn`, when given, replaces `turns_for` so
    a phase can keep one retry policy across the unproductive path and the
    leftovers path.

    Returns the diagnosis, or None once something was produced. A caller that
    also needs to know *why* a produced run ended — a fix pass distinguishing
    a finished pass from one that ticked one box and then hit MAX_TURNS —
    diagnoses its own session log after this returns; that is a second,
    independent read (see `agent.invoke._truncation`), not something this
    function threads through, so it does not pay for a diagnosis here that a
    produced-and-satisfied caller would only discard.

    `output_path` is the declared deliverable the diagnosis judges writes
    against. Empty means any write counts — see `session.diagnose_missing_output`.
    """
    if not produced() and recover:
        recover()
    if produced():
        return None

    diagnosis = diagnose_missing_output(log_path, output_path=output_path)
    if not is_retryable(diagnosis):
        return diagnosis

    turns = (
        turns_fn(diagnosis, max_turns) if turns_fn is not None
        else turns_for(diagnosis, max_turns, ceiling=ceiling)
    )
    core.log.warn(
        f"{label} produced no output ({diagnosis.message}) "
        f"— retrying once ({turns} turns)"
    )
    core.log.blank()
    prior = preserve_log(log_path)
    invoke(hint_select(diagnosis) + prompt, turns)
    core.log.blank()

    if not produced() and recover:
        recover()
    # Diagnose before restoring: in a merged log the first attempt's tool calls
    # would mask what the retry actually did.
    retry_diagnosis = (
        None if produced()
        else diagnose_missing_output(log_path, output_path=output_path)
    )
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
    turns_fn: Callable[[Diagnosis, int], int] | None = None,
    output_path: str = "",
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
        ceiling=ceiling, turns_fn=turns_fn, output_path=output_path,
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
        output_path=output_path,
    )


def retry_blank_response(
    call: Callable[[str], tuple[str, int]],
    prompt: str,
    *,
    label: str,
    usable: Callable[[str], bool],
    hint: RetryHint = BLANK_RESPONSE_HINT,
) -> tuple[str, int]:
    """Give a stateless prompt one more attempt when its answer will not parse.

    `call(prompt)` returns `(response, exit_code)` and `usable(response)` says
    whether the response can be consumed.  A non-zero exit code is returned
    as-is: the backend already reported why, and the same call would reproduce
    it.  There is no session log here, so an unusable answer is the only signal
    that the agent spent a turn without doing the job.

    `hint` is what the second attempt is told it got wrong, and defaults to the
    marker wording the majority of callers want.  A retry is only worth a turn
    if it corrects the actual mistake: telling a caller that asked for a bare
    JSON object to "emit the requested markers" names a format its prompt never
    mentioned, and the second attempt fails the way the first did.

    A caller whose answer can fail in several distinct ways passes a callable
    instead, and it is handed the unusable answer to choose from.  A fixed
    string cannot express that: the failure is not known until the first
    attempt comes back, so a caller resolving the hint up front has to pick one
    wording for every way its contract can be broken — which is how a
    resolution that echoed its context, with faultless markers, was sent a
    lecture about emitting markers.
    """
    response, rc = call(prompt)
    if rc != 0 or usable(response):
        return response, rc
    core.log.warn(f"{label} returned an unparseable response — retrying once")
    return call(resolve_hint(hint, response) + prompt)
