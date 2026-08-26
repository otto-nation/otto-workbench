"""Every phase the workbench knows how to run, and what each one defaults to.

``agent_types`` says what a phase *is*; this module says which ones there are.
One entry per phase, and the entry is the whole declaration — the config key,
the ``WORKBENCH_AI_*`` override keys, the review directory's filenames and the
preflight model list are all derived from it, so adding a phase is a member on
``Phase`` and a spec here.

The registry is a dict keyed by phase, but it is written as a tuple and keyed
afterwards: a literal keyed by hand spells every phase name twice and can drift
between the two spellings, which is a whole class of bug that no longer has
anywhere to live.

Nothing here is required to reach a caller through ``PHASES``. A ``PhaseSpec``
is an ordinary value, and an invocation that must not be operator-tunable —
a benchmark whose numbers stop comparing if a config file can move them — is
better served by a spec of its own than by an entry here that quietly ignores
the keys it advertises.
"""

# doc-group: pipeline

from __future__ import annotations

from agent_types import (
    AgentKind, ItemScaling, Phase, PhaseDomain, PhaseShape, PhaseSpec,
    RetryBudget, Thinking,
)

_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        Phase.SINGLE, PhaseDomain.REVIEW, "Review",
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    PhaseSpec(
        Phase.HOLISTIC, PhaseDomain.REVIEW, "Holistic scan",
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    PhaseSpec(
        Phase.SCOUT, PhaseDomain.REVIEW, "Scout",
        thinking=Thinking.LOW, max_turns=10,
        agent=AgentKind.REVIEWER_LITE,
    ),
    PhaseSpec(
        Phase.GROUP, PhaseDomain.REVIEW, "Group review",
        thinking=Thinking.LOW, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    # Synthesis and disprove are handed the findings they judge, so an omitted
    # file costs them nothing. The three fix phases scale with the items on
    # their checklist instead, through `scaling`.
    PhaseSpec(
        Phase.SYNTHESIS, PhaseDomain.REVIEW, "Synthesis",
        thinking=Thinking.MEDIUM, max_turns=15,
        scales_with_omitted=False,
    ),
    PhaseSpec(
        Phase.DISPROVE, PhaseDomain.REVIEW, "Disprove",
        thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
        scales_with_omitted=False,
    ),
    PhaseSpec(
        Phase.FIX, PhaseDomain.REVIEW, "Fix pass",
        thinking=Thinking.LOW, max_turns=20,
        shape=PhaseShape.FIX,
        scales_with_omitted=False,
        scaling=ItemScaling(turns_per_item=2, turns_cap=60),
        retry=RetryBudget(ceiling=60, turns_min=40, bump=20),
    ),
    # The comments fix pass runs outside a review, so no effort preset sets its
    # dollar cap: it scales with the checklist it is handed, between a floor
    # that covers a single item and a cap one agent can finish inside.
    PhaseSpec(
        Phase.COMMENTS_FIX, PhaseDomain.COMMENTS, "Fix pass",
        max_turns=20, max_budget=2.0,
        shape=PhaseShape.FIX,
        scales_with_omitted=False,
        scaling=ItemScaling(turns_per_item=5, turns_cap=60,
                            budget_per_item=0.5, budget_cap=5.0),
        retry=RetryBudget(ceiling=120, turns_min=30, bump=15),
    ),
    # The CI fix pass gets the same 20 turns and $3 whatever it is handed: the
    # rates below sit at exactly that flat budget divided by the ten failures it
    # was always implicitly sized for, and the caps match the flat numbers, so
    # `phase_turns` and `phase_budget` answer what they always did. Naming the
    # ten is what buys the chunk — a run with forty failures now gets four
    # passes of that budget rather than one prompt holding all forty.
    PhaseSpec(
        Phase.CI_FIX, PhaseDomain.CI, "CI fix pass",
        max_turns=20, max_budget=3.0,
        shape=PhaseShape.FIX,
        scales_with_omitted=False,
        scaling=ItemScaling(turns_per_item=2, turns_cap=20,
                            budget_per_item=0.25, budget_cap=3.0),
    ),
    # The prompt-shaped phases below are one stateless call each: no agent
    # loop, so no turn budget, no dollar cap and no agent persona to pick.
    # What they have that a hardcoded call did not is a model and a thinking
    # level an operator can move, which is the whole reason they are phases.
    PhaseSpec(
        Phase.COMMENTS_TRIAGE, PhaseDomain.COMMENTS, "Triage",
        shape=PhaseShape.PROMPT,
        scales_with_omitted=False,
    ),
    # One phase, six ledger labels: pr-rebase asks for conflict resolutions,
    # chunked resolutions, stash resolutions, lockfile commands and push-check
    # fixes. They are the same call sized the same way, and an operator moving
    # the rebase model means all of them.
    PhaseSpec(
        Phase.REBASE, PhaseDomain.REBASE, "Rebase assist",
        shape=PhaseShape.PROMPT,
        scales_with_omitted=False,
    ),
    PhaseSpec(
        Phase.DESCRIBE, PhaseDomain.DESCRIBE, "Describe",
        shape=PhaseShape.PROMPT,
        scales_with_omitted=False,
    ),
)

PHASES: dict[Phase, PhaseSpec] = {s.phase: s for s in _SPECS}

# The phases a review runs, in registry order. Preflight, artifact globbing and
# the review directory's filenames all cover exactly these — derived from the
# domain so a phase added for another entry point joins neither by accident.
REVIEW_PHASES: tuple[Phase, ...] = tuple(
    p for p, s in PHASES.items() if s.domain is PhaseDomain.REVIEW
)
