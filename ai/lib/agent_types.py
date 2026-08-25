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


class Phase(StrEnum):
    """One agent invocation the workbench sizes from a registry entry.

    Override env keys are derived from the member name and config keys from its
    value, so adding a phase means one member here plus one ``PHASES`` entry —
    callers, preflight checks, and failure hints all read the derived keys
    rather than spelling them out.
    """

    SINGLE = "single"
    HOLISTIC = "holistic"
    SCOUT = "scout"
    GROUP = "group"
    SYNTHESIS = "synthesis"
    DISPROVE = "disprove"
    FIX = "fix"

    @property
    def model_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.name}_MODEL"

    @property
    def thinking_env_key(self) -> str:
        return f"{ENV_PREFIX}{self.name}_THINKING"

    @property
    def _stem(self) -> str:
        """The filename stem this phase's artifacts share: the phase's own name.

        ``group`` is the one fan-out phase, so its stem carries the index.
        """
        return f"{self}-{{}}" if self is Phase.GROUP else str(self)

    @property
    def log_filename(self) -> str:
        """The session log this phase writes, named after the phase.

        ``single`` names no file of its own: it writes to the job's session
        log, which ``review-orchestrate --session-log`` may point outside the
        review directory.
        """
        return "" if self is Phase.SINGLE else f"{self._stem}.jsonl"

    @property
    def output_filename(self) -> str:
        """The findings artifact this phase writes, named after the phase.

        Empty for a phase that writes into the review document rather than an
        artifact of its own: ``single`` and ``synthesis`` produce ``review.md``
        and ``fix`` edits it in place.
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
class PhaseSpec:
    """Built-in defaults for one phase.

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
    model: str = "sonnet"
    thinking: Thinking | None = None
    max_turns: int = 15
    agent: AgentKind | None = None
    edits: bool = False
    scales_with_omitted: bool = True


PHASES: dict[Phase, PhaseSpec] = {
    Phase.SINGLE: PhaseSpec(
        Phase.SINGLE, thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.HOLISTIC: PhaseSpec(
        Phase.HOLISTIC, thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.SCOUT: PhaseSpec(
        Phase.SCOUT, thinking=Thinking.LOW, max_turns=10,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Phase.GROUP: PhaseSpec(
        Phase.GROUP, thinking=Thinking.LOW, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    # Synthesis and disprove are handed the findings they judge, so an omitted
    # file costs them nothing. Fix takes its budget from _fix_turn_budget, which
    # scales with unchecked findings instead.
    Phase.SYNTHESIS: PhaseSpec(
        Phase.SYNTHESIS, thinking=Thinking.MEDIUM, max_turns=15,
        scales_with_omitted=False,
    ),
    Phase.DISPROVE: PhaseSpec(
        Phase.DISPROVE, thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
        scales_with_omitted=False,
    ),
    Phase.FIX: PhaseSpec(
        Phase.FIX, thinking=Thinking.LOW, max_turns=20,
        edits=True,
        scales_with_omitted=False,
    ),
}
