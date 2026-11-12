"""The agent behind the verify gate: does a claimed fix actually work?

`fix.gate` owns when the gate runs and what its verdicts mean; this owns the
one call that produces them. The split is the same one the engine already makes
for the fix pass itself — the pipeline is domain-neutral, and what it dispatches
is swappable, which is what lets `engine.run(verify=...)` be a stub in a test
and an agent in production.

The gate exists because a ticked `fixed` box is a claim that an edit was made,
not a claim that the edit works. Those are different claims and a fix pass
publishes the second while only ever establishing the first.

Why an agent rather than a fixed command: what verifies a fix is not knowable
in advance. Sometimes it is the reviewer's own repro in the comment body,
sometimes calling the changed function, sometimes the project's suite — and on
a path nothing covers, honestly nothing. A hardcoded `run the tests` goes green
on precisely the case that motivated this (a suite whose tests mock the thing
that was fixed), which is worse than no gate: it launders an unverified fix as a
checked one.

Nothing here executes anything from a PR comment. The agent reads the fix and
decides what to run in the repo; reviewer text is context it reasons about, not
a script the host shells out to. That distinction is the whole security story —
running a fenced block out of a public PR comment would be arbitrary code
execution as the operator, with their credentials and their network.
"""

# doc-group: pipeline

from __future__ import annotations

import textwrap
from dataclasses import replace

import agent.backend_events
import agent.invoke
import agent.phases
import agent.templates
import fix.tracking
from agent.registry import PHASES
import core.log
from core.phases import Phase
from fix.gate import Verdict
from fix.types import FixItem

# What the gate calls one unit of its work, in the prompt's own words.
_NOUN = "fix"

# How much of the first refusal reason a verdict quotes. `test-pipe-guard`'s
# reason is several hundred characters of remedy; the verdict needs the gist.
REFUSAL_SHOWN = 160


def _chunks(items: list[FixItem], size: int) -> list[list[FixItem]]:
    """Split claimed fixes so one gate invoke stays inside the phase's cap."""
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


_REWRITE_HINT = (
    "Your previous edits to this checklist failed. Rewrite the whole file "
    "in one call with the file-write tool, not edit."
)


def _run_chunk(
    phase: Phase, items: list[FixItem], adapter, label: str, chunk: int,
) -> dict[str, Verdict]:
    """One gate invoke, budgeted for this chunk's size."""
    path = adapter.verify_tracking_path(chunk)
    fix.tracking.write(path, "Verify Fixes", items, fix.tracking.VERIFY_BOXES)
    session = adapter.verify_session_log(chunk)

    core.log.info(
        f"{label} — checking {len(items)} claimed fix"
        f"{'es' if len(items) != 1 else ''}..."
    )
    verdicts, failed = _invoke_gate(
        phase, items, adapter, label, path, session)
    unanswered = [item for item in items if item.id not in verdicts]
    blocked = _blocked_writes(failed, str(path))
    last_failed = failed
    suspect: set[str] = set()
    if blocked and unanswered:
        fix.tracking.write(
            path, "Verify Fixes", unanswered, fix.tracking.VERIFY_BOXES)
        retry_log = adapter.artifacts / f"verify-session-{chunk}-retry.jsonl"
        retry_verdicts, retry_failed = _invoke_gate(
            phase, unanswered, adapter, label, path, retry_log,
            extra=_REWRITE_HINT)
        verdicts = {**verdicts, **retry_verdicts}
        last_failed = retry_failed
        # Only verdicts the failing last attempt produced are in doubt.
        if _blocked_writes(retry_failed, str(path)):
            suspect = set(retry_verdicts)
    core.log.blank()

    # Guard blocks only: argument errors are the write-failure path below.
    guard = agent.backend_events.guard_refusals(session)
    retry_session = adapter.artifacts / f"verify-session-{chunk}-retry.jsonl"
    if retry_session.is_file():
        guard = guard + agent.backend_events.guard_refusals(retry_session)
    result = _with_unrecorded(
        _with_refusals(verdicts, guard), items, last_failed, str(path))
    if suspect:
        result = {
            item_id: (replace(v, ok=None, recorded=False)
                      if v.ok is False and item_id in suspect else v)
            for item_id, v in result.items()
        }
    return result


def _blocked_writes(failed: tuple[str, ...], tracking_file: str) -> tuple[str, ...]:
    return tuple(reason for reason in failed if tracking_file in reason)


def _invoke_gate(
    phase: Phase, items: list[FixItem], adapter, label: str, path, session,
    extra: str = "",
) -> tuple[dict[str, Verdict], tuple[str, ...]]:
    """One agent invoke over *items*, plus every untimed failure in its log."""
    turns = agent.phases.phase_turns(phase, items=len(items))
    answer = fix.tracking.verify_instructions(_NOUN)
    if extra:
        answer = f"{answer}\n\n{extra}"
    prompt = agent.templates.render(
        PHASES[phase].template_for(),
        branch_name=adapter.branch,
        repo=adapter.repo,
        tracking_content=path.read_text(),
        tracking_file=str(path),
        answer_format=answer,
        execution_claim_guard=agent.templates.build_execution_claim_guard(
            occasion=agent.templates.ClaimOccasion.FIX_EVIDENCE,
        ),
        worktree_block=agent.templates.build_worktree_block(str(adapter.workdir)),
        max_turns=str(turns),
    )
    agent.invoke.run_fix(
        phase, prompt,
        cwd=adapter.workdir,
        session_log=str(session),
        # Any verdict at all is production. A gate that reached none is the
        # unproductive case the retry exists for, and a gate that answered
        # "not verified" everywhere did its job.
        produced=lambda: bool(fix.tracking.parse_verdicts(path)),
        add_dirs=adapter.add_dirs(),
        max_turns=turns,
        max_budget=agent.phases.phase_budget(
            phase, adapter.effort, items=len(items),
        ),
        label=label,
        repo=adapter.repo or None,
        pr=adapter.pr or None,
        branch=adapter.branch or None,
        config=adapter.config,
        effort=adapter.effort,
        model=adapter.model or None,
    )
    verdicts = {
        item_id: Verdict(ok=ok, detail=detail)
        for item_id, (ok, detail) in fix.tracking.parse_verdicts(path).items()
    }
    failed = (
        agent.backend_events.guard_refusals(session)
        + agent.backend_events.failed_calls(session)
    )
    return verdicts, failed


def _with_refusals(
    verdicts: dict[str, Verdict], refusals: tuple[str, ...],
) -> dict[str, Verdict]:
    """*verdicts*, each one the gate did not verify naming the refusals it hit.

    A gate refused its test runs answers "not verified — pytest unavailable",
    which reads as a machine missing pytest. The refusal is the real cause, and
    it is a fact about the chunk rather than about any item, so it is appended
    to every unverified verdict in the chunk and to none that verified.
    Nothing is added for an item the gate never answered: an added verdict
    would be read as one, and silence must stay unverified.
    """
    if not refusals:
        return verdicts
    first = textwrap.shorten(refusals[0], REFUSAL_SHOWN)
    core.log.warn(
        f"Verify gate: {len(refusals)} tool call(s) refused by a guard — first: {first}")
    note = f"the gate's tool calls were refused {len(refusals)}× (first: {first})"
    return {
        item_id: v if v.ok is True else replace(
            v, detail=f"{v.detail}; {note}" if v.detail else note)
        for item_id, v in verdicts.items()
    }


def _with_unrecorded(
    verdicts: dict[str, Verdict], items: list[FixItem],
    refusals: tuple[str, ...], tracking_file: str,
) -> dict[str, Verdict]:
    """*verdicts*, plus an unverified one for each item a guard kept off the checklist.

    An item the gate did not answer stays silent, because silence is no verdict
    at all. One case is different: a guard refusing the gate's writes to its own
    checklist. Then the answer was never recordable, and silence would read as
    an ordinary "not verified" with the cause nowhere. Matched on the checklist
    path in the refusal, so only a refusal of that file speaks for every item in
    the chunk. A refusal carries only its reason, not the tool, so this cannot
    tell a refused write from a refused read; it does not need to while guards
    allow every read, which makes a refused call naming the checklist an attempt
    to write it. It touches only items the gate left unanswered. A refusal
    elsewhere is left to `_with_refusals`. The verdict is
    None, "nothing could be established", which is what the gate actually knows.
    """
    blocked = [r for r in refusals if tracking_file in r]
    if not blocked:
        return verdicts
    first = textwrap.shorten(blocked[0], REFUSAL_SHOWN)
    core.log.warn(
        f"Verify gate: {len(blocked)} write(s) to its checklist failed "
        f"(refused by a guard or malformed) — answers could not be recorded "
        f"(first: {first})")
    note = (f"the gate could not record its answer: {len(blocked)} write(s) to "
            f"{tracking_file} failed (refused by a guard or malformed) "
            f"(first: {first})")
    return {
        **{item.id: Verdict(ok=None, detail=note, recorded=False) for item in items},
        **verdicts,
    }


def run(
    phase: Phase, _prompt_unused: str, *, items: list[FixItem], adapter,
) -> dict[str, Verdict]:
    """Ask the gate about `items` and hand back a verdict per item id.

    Signature is the engine's `VerifyFn`: the engine supplies the phase and the
    items, and the adapter is how a domain's own branch, repo and worktree reach
    the prompt. The unused prompt argument keeps the shape identical to
    `agent.invoke.run_fix`, so a test can substitute one for the other.

    Chunked at `phase_chunk_size` so a pass that claimed more fixes than the
    cap covers still gets five turns an item, rather than one invoke squeezing
    thirty-nine claims into a 40-turn budget.

    An id the agent did not answer is simply absent from the result. The engine
    reads that as unverified rather than as falsified — see `gate._verify`,
    which is where the decision not to demote on silence is argued.
    """
    chunk_size = agent.phases.phase_chunk_size(phase)
    batched = _chunks(items, chunk_size)
    name = "Verify gate"
    verdicts: dict[str, Verdict] = {}
    for n, chunk in enumerate(batched, start=1):
        label = name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})"
        verdicts.update(_run_chunk(phase, chunk, adapter, label, n))
    return verdicts
