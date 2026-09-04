"""Every phase the workbench knows how to run, and what each one defaults to.

``phases`` says what a phase is *named*; ``agent.types`` says what a phase's
*shape* is; this module says which ones there are.
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

The ``--no-<phase>`` flags at the bottom are the registry read from the command
line: which phases may be switched off is a property of the specs above, so the
flags are generated from them rather than listed a second time in each script
that offers them.
"""

# doc-group: pipeline

from __future__ import annotations

import argparse

from agent.types import ItemScaling, PhaseSpec, RetryBudget
from core.phases import AgentKind, Mode, Phase, PhaseDomain, PhaseShape, Thinking

_SPECS: tuple[PhaseSpec, ...] = (
    # `single` and `synthesis` are the two phases a self-review prompts
    # differently: there is no PR description to read and no author to address,
    # so each names a template per mode rather than one for both.
    PhaseSpec(
        Phase.SINGLE, PhaseDomain.REVIEW, "Review",
        template={Mode.PR: "single-agent.md", Mode.SELF: "self-review.md"},
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    # The five multi-phase steps below are `optional`: each has a path around it
    # in the pipeline, so each earns a `--no-<phase>` flag and may appear in an
    # effort preset's `skips`. `single` does not — a review with no reviewing
    # phase is not a shallower review, it is no review.
    PhaseSpec(
        Phase.HOLISTIC, PhaseDomain.REVIEW, "Holistic scan",
        template="holistic.md", optional=True,
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    PhaseSpec(
        Phase.SCOUT, PhaseDomain.REVIEW, "Scout",
        template="scout.md", optional=True,
        thinking=Thinking.LOW, max_turns=10,
        agent=AgentKind.REVIEWER_LITE,
    ),
    PhaseSpec(
        Phase.GROUP, PhaseDomain.REVIEW, "Group review",
        template="group.md", optional=True,
        thinking=Thinking.LOW, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    # Synthesis and disprove are handed the findings they judge, so an omitted
    # file costs them nothing. The three fix phases scale with the items on
    # their checklist instead, through `scaling`.
    PhaseSpec(
        Phase.SYNTHESIS, PhaseDomain.REVIEW, "Synthesis",
        template={Mode.PR: "synthesis.md",
                  Mode.SELF: "self-review-synthesis.md"},
        optional=True,
        thinking=Thinking.MEDIUM, max_turns=15,
        scales_with_omitted=False,
    ),
    PhaseSpec(
        Phase.DISPROVE, PhaseDomain.REVIEW, "Disprove gate",
        template="disprove.md", optional=True,
        thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
        scales_with_omitted=False,
    ),
    PhaseSpec(
        Phase.FIX, PhaseDomain.REVIEW, "Fix pass",
        template="fix-findings.md",
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
        template="fix-comments.md",
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
        template="fix-ci.md",
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

# A review's phase 1 is one scan chosen from these two, so every question about
# phase 1 — did it run, was it switched off, is it behind a resumed run — is a
# question about the pair rather than about either member. Listed rather than
# derived: nothing on a `PhaseSpec` says "candidate for phase 1", and inventing
# a field read by one frozenset would be the parallel table this epic removes.
SCAN_PHASES: frozenset[Phase] = frozenset({Phase.SCOUT, Phase.HOLISTIC})


# ── Switching a phase off from the command line ──────────────────────────────
#
# `claude-review` and `review-orchestrate` both offer the flags and one forwards
# them to the other, so all three sides are generated from the same registry
# read: a phase declared `optional` gets its flag, its parse and its argv entry
# at once, and nothing can offer a flag the pipeline has no path around.

def _switchable() -> tuple[Phase, ...]:
    return tuple(p for p in REVIEW_PHASES if PHASES[p].optional)


def add_phase_skip_flags(parser: argparse.ArgumentParser) -> None:
    """Add a ``--no-<phase>`` for every review phase that may be switched off."""
    for phase in _switchable():
        parser.add_argument(
            f"--no-{phase}", action="store_true",
            help=f"Skip the {PHASES[phase].label.lower()} phase",
        )


def phase_skips(args: argparse.Namespace) -> frozenset[Phase]:
    """The phases ``--no-<phase>`` switched off on this command line."""
    return frozenset(p for p in _switchable() if getattr(args, f"no_{p}", False))


def phase_skip_argv(skips: frozenset[Phase]) -> list[str]:
    """``skips`` as the flags that reproduce it on a child process's argv."""
    return [f"--no-{p}" for p in _switchable() if p in skips]
