"""The vocabulary a phase is named in: what runs, at what depth, in what mode.

Enums and the prefix their keys are built from. The shapes that describe an
inventory of phases — `PhaseSpec`, `EffortPreset`, `ItemScaling`,
`RetryBudget` — live one layer up, in the module that owns the phase
inventory's shapes.

Split from them because the config types its fields with these names: with the
vocabulary in the agent layer, the config would import the agent layer while the
agent layer imports the config. A vocabulary has no dependencies, so it goes
below both.
"""

# doc-group: pipeline

from __future__ import annotations

from enum import StrEnum

# Prefix on every per-phase override env key. Read by `Phase`'s two derived
# keys and by `agent_phases`' three global ones.
#
# ``WORKBENCH_AI_`` rather than the old ``CLAUDE_REVIEW_``: these keys size
# agent invocations across the whole workbench, not just reviews, and the
# backend behind them is a choice (``_PROVIDER``) rather than a given.
ENV_PREFIX = "WORKBENCH_AI_"


class PhaseDomain(StrEnum):
    """Which entry point runs a phase.

    A phase belongs to exactly one `pr` domain, and the domain is what says
    where its artifacts live: only ``REVIEW`` phases write into the review
    directory, so only they name a log or a findings file. Without it, adding a
    fix phase for another domain would silently mint review artifact names for
    a phase that never enters a review.
    """

    REVIEW = "review"
    COMMENTS = "comments"
    CI = "ci"
    REBASE = "rebase"
    DESCRIBE = "describe"


class PhaseShape(StrEnum):
    """Which backend entry point a phase is run through.

    The three shapes are the three things ``ai_backend`` can do, so a phase's
    shape decides which ``agent_invoke`` function will accept it. A read-only
    reviewer handed to the fix runner, or a stateless prompt handed a session
    log it will never write, is a mistake the shape catches at the owner rather
    than in a backend argument that is silently ignored.
    """

    PROMPT = "prompt"
    AGENT = "agent"
    FIX = "fix"


class Phase(StrEnum):
    """One agent invocation the workbench sizes from a registry entry.

    Both the config key and the override env keys are derived from the member's
    value, so adding a phase means one member here plus one ``agent_registry``
    entry — callers, preflight checks, and failure hints all read the derived
    keys rather than spelling them out. Deriving both from one place is what
    keeps ``agent.phases.<phase>`` and ``WORKBENCH_AI_<PHASE>_*`` naming the
    same phase; the member name is a second spelling that could drift from it.

    A member is a name and nothing more. Everything else about a phase — the
    entry point that runs it, its defaults, the files it writes — lives on its
    ``PhaseSpec``, so the vocabulary answers no question that needs the
    inventory.
    """

    SINGLE = "single"
    HOLISTIC = "holistic"
    SCOUT = "scout"
    GROUP = "group"
    SYNTHESIS = "synthesis"
    DISPROVE = "disprove"
    FIX = "fix"
    COMMENTS_FIX = "comments_fix"
    COMMENTS_TRIAGE = "comments_triage"
    CI_FIX = "ci_fix"
    REBASE = "rebase"
    DESCRIBE = "describe"

    @property
    def model_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.upper()}_MODEL"

    @property
    def thinking_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.upper()}_THINKING"


class Mode(StrEnum):
    """What the review is reviewing: an open PR or the working branch.

    Vocabulary rather than review state: two phases render a different prompt
    template per mode, and ``PhaseSpec`` is what says which. Owning it here is
    what lets the spec answer that without importing the review layer.
    """

    PR = "pr"
    SELF = "self"


class Effort(StrEnum):
    """Review depth. Selects a preset of budgets, thresholds, and phase skips."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Thinking(StrEnum):
    """Extended-thinking level passed through to the backend."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentKind(StrEnum):
    """Which reviewer agent definition a phase runs under.

    ``REVIEWER_LITE`` skips context gathering, so it only suits phases that are
    handed everything they need up front.

    Every member here is a review persona forbidden from editing the workspace.
    Phases that write to the branch pass ``None`` instead of an ``AgentKind`` —
    see ``agent_invoke.run_fix``.
    """

    REVIEWER = "reviewer"
    REVIEWER_LITE = "reviewer-lite"
