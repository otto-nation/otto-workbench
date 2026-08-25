"""The vocabulary every agent invocation is described in.

A phase is one agent invocation the workbench knows how to size: what model it
runs, how hard it thinks, how many turns it gets, which agent definition it
adopts. This module owns the names for those things — ``Phase``, ``Thinking``,
``AgentKind``, ``Effort`` — and the built-in spec each phase resolves from
(``PhaseSpec``, ``PHASES``).

It imports nothing but the standard library, and that is the point. The
vocabulary used to live in ``review_common``, which reaches the PR state
machine, the usage ledger and the git client; ``ai_backend`` needed one enum
from it and took all of that with it, and ``workbench_config`` needed three.
Anything may depend on the vocabulary, so the vocabulary depends on nothing.

Resolving a spec against the config file and the environment is
``agent_phases``'s job — that layer needs ``workbench_config``, which needs
this one.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Every per-phase and global override key starts here. `WORKBENCH_AI_` rather
# than the old `CLAUDE_REVIEW_`: these keys size agent invocations across the
# whole workbench, not just reviews, and the backend behind them is a choice
# (`_PROVIDER`) rather than a given.
ENV_PREFIX = "WORKBENCH_AI_"

# Turns a second attempt may not exceed when a phase names no ceiling of its
# own. A retry has already proven the first pass's budget insufficient, so the
# ceiling sits above it rather than clamping the retry to what just ran out.
DEFAULT_RETRY_CEILING = 30


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


class Phase(StrEnum):
    """One agent invocation the workbench sizes from a registry entry.

    Both the config key and the override env keys are derived from the member's
    value, so adding a phase means one member here plus one ``PHASES`` entry —
    callers, preflight checks, and failure hints all read the derived keys
    rather than spelling them out. Deriving both from one place is what keeps
    ``agent.phases.<phase>`` and ``WORKBENCH_AI_<PHASE>_*`` naming the same
    phase; the member name is a second spelling that could drift from it.
    """

    SINGLE = "single"
    HOLISTIC = "holistic"
    SCOUT = "scout"
    GROUP = "group"
    SYNTHESIS = "synthesis"
    DISPROVE = "disprove"
    FIX = "fix"
    COMMENTS_FIX = "comments_fix"
    CI_FIX = "ci_fix"

    @property
    def model_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.upper()}_MODEL"

    @property
    def thinking_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.upper()}_THINKING"

    @property
    def domain(self) -> PhaseDomain:
        """The entry point that runs this phase, from its registry entry."""
        return PHASES[self].domain

    @property
    def _stem(self) -> str:
        """The filename stem this phase's artifacts share: the phase's own name.

        ``group`` is the one fan-out phase, so its stem carries the index.
        """
        if self.domain is not PhaseDomain.REVIEW:
            raise ValueError(
                f"{self} runs under the {self.domain} entry point and writes no "
                "review artifact; ask its own domain where its files live"
            )
        return f"{self}-{{}}" if self is Phase.GROUP else str(self)

    @property
    def log_filename(self) -> str:
        """The session log this phase writes into the review directory.

        ``single`` names no file of its own: it writes to the job's session
        log, which ``review-orchestrate --session-log`` may point outside the
        review directory. Raises for a phase outside the review domain, whose
        session log belongs to whichever entry point runs it.
        """
        return "" if self is Phase.SINGLE else f"{self._stem}.jsonl"

    @property
    def output_filename(self) -> str:
        """The findings artifact this phase writes into the review directory.

        Empty for a phase that writes into the review document rather than an
        artifact of its own: ``single`` and ``synthesis`` produce ``review.md``
        and ``fix`` edits it in place. Raises for a phase outside the review
        domain, as ``log_filename`` does.
        """
        return "" if self in _WRITES_REVIEW_FILE else f"{self._stem}.md"


# The phases whose output is the review document itself. Lives below the class
# because it names members; read at call time, so the forward reference in
# `output_filename` resolves.
_WRITES_REVIEW_FILE = frozenset({Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX})


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
    see ``run_fix_pass``.
    """

    REVIEWER = "reviewer"
    REVIEWER_LITE = "reviewer-lite"


DEFAULT_MAX_BUDGET_PER_AGENT = 5.0


@dataclass(frozen=True)
class EffortPreset:
    """Budgets, thresholds, and phase skips selected by ``--effort``.

    ``thinking=None`` means the phase's own default stands; a level here
    flattens every phase to it, matching what WORKBENCH_AI_THINKING does.
    """

    thinking: Thinking | None
    agent_budget: float
    max_groups: int
    multi_phase_line_threshold: int
    multi_phase_file_threshold: int
    skip_synthesis: bool
    skip_holistic: bool
    skip_scout: bool
    skip_disprove: bool
    skip_omitted_files: bool
    agent: AgentKind


# Lives here rather than beside the pipeline that reads it most: every layer
# down to prompt building needs a threshold from it, and the pipeline imports
# those layers, so owning it there would make the lookup a circular import.
EFFORT_PRESETS: dict[Effort, EffortPreset] = {
    Effort.LOW: EffortPreset(
        thinking=Thinking.LOW,
        agent_budget=3.0,
        max_groups=6,
        multi_phase_line_threshold=1000,
        multi_phase_file_threshold=15,
        skip_synthesis=True,
        skip_holistic=True,
        skip_scout=True,
        skip_disprove=True,
        skip_omitted_files=True,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Effort.MEDIUM: EffortPreset(
        thinking=None,
        agent_budget=DEFAULT_MAX_BUDGET_PER_AGENT,
        max_groups=8,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
    Effort.HIGH: EffortPreset(
        thinking=Thinking.HIGH,
        agent_budget=8.0,
        max_groups=16,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
}


@dataclass(frozen=True)
class ItemScaling:
    """How a phase's budget grows with the number of items it is handed.

    A fix pass is sized by the work in front of it rather than by a flat
    number: each item costs turns and dollars, and both clamp to a cap the
    agent has to finish inside. A rate of zero means that resource does not
    scale — the phase's flat ``max_turns`` or ``max_budget`` stands.
    """

    turns_per_item: int = 0
    turns_cap: int = 0
    budget_per_item: float = 0.0
    budget_cap: float = 0.0

    @property
    def chunk_size(self) -> int:
        """Largest item count that fits every cap at the per-item rates.

        Derived, not chosen: raising a cap or lowering a rate widens the chunk.
        A phase that scales with neither resource bounds no chunk and answers
        zero, which is a loud failure at the chunker rather than a silent one.
        """
        bounds = []
        if self.turns_per_item:
            bounds.append(self.turns_cap // self.turns_per_item)
        if self.budget_per_item:
            bounds.append(int(self.budget_cap // self.budget_per_item))
        return min(bounds) if bounds else 0


@dataclass(frozen=True)
class RetryBudget:
    """What a second attempt gets after the first came back having done nothing.

    ``ceiling`` is the hard limit on turns, above the first pass's cap: a retry
    clamped to the budget that just ran out is guaranteed the same failure,
    which is what made the bump dead precisely when a retry was warranted.
    ``turns_min`` is the floor a retry starts from and ``bump`` what it adds to
    whatever the original pass was given.
    """

    ceiling: int = DEFAULT_RETRY_CEILING
    turns_min: int = 0
    bump: int = 0


@dataclass(frozen=True)
class PhaseSpec:
    """Built-in defaults for one phase.

    ``domain`` names the entry point that runs the phase and ``label`` is how
    that entry point announces it, so a stage has one name whether it is being
    scheduled, logged, or reported.

    ``max_budget=None`` means the phase takes the effort preset's per-agent
    budget; a number pins it regardless of effort, which is what a phase
    outside a review — where there is no ``--effort`` — needs.

    ``agent=None`` means the phase takes whichever agent the effort preset
    selects. A concrete ``AgentKind`` pins the phase regardless of effort:
    those phases are handed everything they need up front and do no context
    gathering, so a higher effort has nothing to buy them.

    ``edits=True`` marks a phase that writes to the branch. Every ``AgentKind``
    is a reviewer persona instructed never to modify source files, so such a
    phase runs with no agent at all — the default agent, which can edit.

    ``scales_with_omitted`` says whether ``max_turns`` grows with the files
    preflight had to leave out of the prompt: a phase that reads branch source
    must open those itself, and that costs turns. It defaults to on because the
    opposite default fails asymmetrically — a phase that silently misses the
    bump is under-budgeted, where one that takes it needlessly finishes early.
    A phase that reasons only over text already in its prompt opts out.
    """

    phase: Phase
    domain: PhaseDomain
    label: str
    model: str = "sonnet"
    thinking: Thinking | None = None
    max_turns: int = 15
    max_budget: float | None = None
    agent: AgentKind | None = None
    edits: bool = False
    scales_with_omitted: bool = True
    scaling: ItemScaling = ItemScaling()
    retry: RetryBudget = RetryBudget()


PHASES: dict[Phase, PhaseSpec] = {
    Phase.SINGLE: PhaseSpec(
        Phase.SINGLE, PhaseDomain.REVIEW, "Review",
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.HOLISTIC: PhaseSpec(
        Phase.HOLISTIC, PhaseDomain.REVIEW, "Holistic scan",
        thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.SCOUT: PhaseSpec(
        Phase.SCOUT, PhaseDomain.REVIEW, "Scout",
        thinking=Thinking.LOW, max_turns=10,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Phase.GROUP: PhaseSpec(
        Phase.GROUP, PhaseDomain.REVIEW, "Group review",
        thinking=Thinking.LOW, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    # Synthesis and disprove are handed the findings they judge, so an omitted
    # file costs them nothing. The three fix phases scale with the items on
    # their checklist instead, through `scaling`.
    Phase.SYNTHESIS: PhaseSpec(
        Phase.SYNTHESIS, PhaseDomain.REVIEW, "Synthesis",
        thinking=Thinking.MEDIUM, max_turns=15,
        scales_with_omitted=False,
    ),
    Phase.DISPROVE: PhaseSpec(
        Phase.DISPROVE, PhaseDomain.REVIEW, "Disprove",
        thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
        scales_with_omitted=False,
    ),
    Phase.FIX: PhaseSpec(
        Phase.FIX, PhaseDomain.REVIEW, "Fix pass",
        thinking=Thinking.LOW, max_turns=20,
        edits=True,
        scales_with_omitted=False,
        scaling=ItemScaling(turns_per_item=2, turns_cap=60),
        retry=RetryBudget(ceiling=60, turns_min=40, bump=20),
    ),
    # The comments fix pass runs outside a review, so no effort preset sets its
    # dollar cap: it scales with the checklist it is handed, between a floor
    # that covers a single item and a cap one agent can finish inside.
    Phase.COMMENTS_FIX: PhaseSpec(
        Phase.COMMENTS_FIX, PhaseDomain.COMMENTS, "Fix pass",
        max_turns=20, max_budget=2.0,
        edits=True,
        scales_with_omitted=False,
        scaling=ItemScaling(turns_per_item=5, turns_cap=60,
                            budget_per_item=0.5, budget_cap=5.0),
        retry=RetryBudget(ceiling=120, turns_min=30, bump=15),
    ),
    Phase.CI_FIX: PhaseSpec(
        Phase.CI_FIX, PhaseDomain.CI, "CI fix pass",
        max_turns=20, max_budget=3.0,
        edits=True,
        scales_with_omitted=False,
    ),
}

# The phases a review runs, in registry order. Preflight, artifact globbing and
# the review directory's filenames all cover exactly these — derived from the
# domain so a phase added for another entry point joins neither by accident.
REVIEW_PHASES: tuple[Phase, ...] = tuple(
    p for p, s in PHASES.items() if s.domain is PhaseDomain.REVIEW
)
