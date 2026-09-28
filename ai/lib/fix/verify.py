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

from agent import invoke as agent_invoke
from agent import phases as agent_phases
from agent import templates as agent_templates
from fix import tracking as fix_tracking
from agent.registry import PHASES
from core import log
from core.phases import Phase
from fix.gate import Verdict
from fix.types import FixItem

# What the gate calls one unit of its work, in the prompt's own words.
_NOUN = "fix"


def _chunks(items: list[FixItem], size: int) -> list[list[FixItem]]:
    """Split claimed fixes so one gate invoke stays inside the phase's cap."""
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


def _run_chunk(
    phase: Phase, items: list[FixItem], adapter, label: str, chunk: int,
) -> dict[str, Verdict]:
    """One gate invoke, budgeted for this chunk's size."""
    path = adapter.verify_tracking_path(chunk)
    fix_tracking.write(path, "Verify Fixes", items, fix_tracking.VERIFY_BOXES)

    turns = agent_phases.phase_turns(phase, items=len(items))
    prompt = agent_templates.render(
        PHASES[phase].template_for(),
        branch_name=adapter.branch,
        repo=adapter.repo,
        tracking_content=path.read_text(),
        tracking_file=str(path),
        answer_format=fix_tracking.verify_instructions(_NOUN),
        worktree_block=agent_templates.build_worktree_block(str(adapter.workdir)),
        max_turns=str(turns),
    )

    log.info(
        f"{label} — checking {len(items)} claimed fix"
        f"{'es' if len(items) != 1 else ''}..."
    )
    agent_invoke.run_fix(
        phase, prompt,
        cwd=adapter.workdir,
        session_log=str(adapter.verify_session_log(chunk)),
        # Any verdict at all is production. A gate that reached none is the
        # unproductive case the retry exists for, and a gate that answered
        # "not verified" everywhere did its job.
        produced=lambda: bool(fix_tracking.parse_verdicts(path)),
        add_dirs=adapter.add_dirs(),
        max_turns=turns,
        max_budget=agent_phases.phase_budget(
            phase, adapter.effort, items=len(items),
        ),
        label=label,
        repo=adapter.repo or None,
        pr=adapter.pr or None,
        config=adapter.config,
        effort=adapter.effort,
        model=adapter.model or None,
    )
    log.blank()

    return {
        item_id: Verdict(ok=ok, detail=detail)
        for item_id, (ok, detail) in fix_tracking.parse_verdicts(path).items()
    }


def run(
    phase: Phase, _prompt_unused: str, *, items: list[FixItem], adapter,
) -> dict[str, Verdict]:
    """Ask the gate about `items` and hand back a verdict per item id.

    Signature is the engine's `VerifyFn`: the engine supplies the phase and the
    items, and the adapter is how a domain's own branch, repo and worktree reach
    the prompt. The unused prompt argument keeps the shape identical to
    `agent_invoke.run_fix`, so a test can substitute one for the other.

    Chunked at `phase_chunk_size` so a pass that claimed more fixes than the
    cap covers still gets five turns an item, rather than one invoke squeezing
    thirty-nine claims into a 40-turn budget.

    An id the agent did not answer is simply absent from the result. The engine
    reads that as unverified rather than as falsified — see `gate._verify`,
    which is where the decision not to demote on silence is argued.
    """
    chunk_size = agent_phases.phase_chunk_size(phase)
    batched = _chunks(items, chunk_size)
    name = "Verify gate"
    verdicts: dict[str, Verdict] = {}
    for n, chunk in enumerate(batched, start=1):
        label = name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})"
        verdicts.update(_run_chunk(phase, chunk, adapter, label, n))
    return verdicts
