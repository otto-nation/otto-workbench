"""The shapes that describe an inventory of phases.

A phase is one agent invocation the workbench knows how to size: what model it
runs, how hard it thinks, how many turns it gets, which agent definition it
adopts, which prompt template it renders. The vocabulary those things are named
in — ``Phase``, ``PhaseShape``, ``Thinking``, ``AgentKind``, ``Effort``,
``Mode`` — lives in ``phases``, below this module. This module owns
``PhaseSpec``, the shape a phase's built-in defaults take, and the presets and
budgets built from it.

Which phases exist, and what each one's defaults are, is ``agent_registry``'s
job. The vocabulary is a closed set of names that grows only when a new kind of
knob appears; the registry is an inventory that grows with the workbench.
Keeping them apart is also what stops the enum reaching back into the registry
to answer questions about itself — a ``PhaseSpec`` answers those now.

It imports nothing but ``phases`` and the standard library. The vocabulary used
to sit here too, alongside the review pipeline's shared-helper module's reach
into the PR state machine, the usage ledger and the git client; ``ai_backend``
needed one enum and took all of that with it, and ``workbench_config`` needed
three. Splitting the vocabulary into its own module below both let each import
only the names, not the shapes built from them.

Resolving a spec against the config file and the environment is
``agent_phases``'s job — that layer needs ``workbench_config``, which needs
this one.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from phases import (
    AgentKind,
    Effort,
    Mode,
    Phase,
    PhaseDomain,
    PhaseShape,
    Thinking,
)

# Turns a second attempt may not exceed when a phase names no ceiling of its
# own. A retry has already proven the first pass's budget insufficient, so the
# ceiling sits above it rather than clamping the retry to what just ran out.
DEFAULT_RETRY_CEILING = 30

# The phases whose output is the review document itself, and the one fan-out
# phase whose artifacts carry an index. Both are read by `PhaseSpec` below.
_WRITES_REVIEW_FILE = frozenset({Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX})
_INDEXED = frozenset({Phase.GROUP})


DEFAULT_MAX_BUDGET_PER_AGENT = 5.0


@dataclass(frozen=True)
class EffortPreset:
    """Budgets, thresholds, and phase skips selected by ``--effort``.

    ``thinking=None`` means the phase's own default stands; a level here
    flattens every phase to it, matching what WORKBENCH_AI_THINKING does.

    ``skips`` names the phases the preset drops, rather than carrying a bool per
    phase: a preset is a statement about how deep a review goes, and a phase
    that becomes skippable should not need a field added to all three presets
    before it can be. Only a phase whose spec is ``optional`` belongs in here —
    ``test_agent_registry`` holds the presets to that.
    """

    thinking: Thinking | None
    agent_budget: float
    max_groups: int
    multi_phase_line_threshold: int
    multi_phase_file_threshold: int
    skips: frozenset[Phase]
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
        # Both scans, so phase 1 drops out entirely rather than falling back to
        # the more expensive of the two.
        skips=frozenset({
            Phase.HOLISTIC, Phase.SCOUT, Phase.SYNTHESIS, Phase.DISPROVE,
        }),
        skip_omitted_files=True,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Effort.MEDIUM: EffortPreset(
        thinking=None,
        agent_budget=DEFAULT_MAX_BUDGET_PER_AGENT,
        max_groups=8,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skips=frozenset(),
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
    Effort.HIGH: EffortPreset(
        thinking=Thinking.HIGH,
        agent_budget=8.0,
        max_groups=16,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skips=frozenset(),
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

    ``shape`` names the backend entry point the phase runs through, and so
    which ``agent_invoke`` function will accept it. ``FIX`` is the shape that
    writes to the branch; every ``AgentKind`` is a reviewer persona instructed
    never to modify source files, so such a phase runs with no agent at all —
    the default agent, which can edit.

    ``scales_with_omitted`` says whether ``max_turns`` grows with the files
    preflight had to leave out of the prompt: a phase that reads branch source
    must open those itself, and that costs turns. It defaults to on because the
    opposite default fails asymmetrically — a phase that silently misses the
    bump is under-budgeted, where one that takes it needlessly finishes early.
    A phase that reasons only over text already in its prompt opts out.

    ``template`` names the prompt file the phase renders, and a mapping names
    one per ``Mode`` for the two phases that read the working branch and an open
    PR differently. Read it through ``template_for``, never directly: the
    mapping is the reason the caller-side ``if mode is SELF`` branches existed,
    and the point of declaring it here is that they do not have to.

    ``optional`` says the phase may be switched off — by an effort preset's
    ``skips`` or by the ``--no-<phase>`` flag generated from this field. It
    defaults to off because that is the answer that cannot mislead: a phase
    nobody marked optional keeps running, where the opposite default would offer
    a flag for a phase the pipeline has no path around.
    """

    phase: Phase
    domain: PhaseDomain
    label: str
    template: str | Mapping[Mode, str] = ""
    optional: bool = False
    model: str = "sonnet"
    thinking: Thinking | None = None
    max_turns: int = 15
    max_budget: float | None = None
    agent: AgentKind | None = None
    shape: PhaseShape = PhaseShape.AGENT
    scales_with_omitted: bool = True
    scaling: ItemScaling = ItemScaling()
    retry: RetryBudget = RetryBudget()

    def __post_init__(self) -> None:
        """Seal a mode-keyed template against writes through the registry.

        ``frozen=True`` stops an attribute being reassigned, not a mapping under
        one being written to, and every spec is a process-wide singleton in
        ``PHASES`` — a caller that mutated one would move every later review's
        prompt. The rest of the class is deep-immutable already, ``scaling`` and
        ``retry`` being frozen dataclasses of their own.
        """
        if not isinstance(self.template, str):
            object.__setattr__(
                self, "template", MappingProxyType(dict(self.template)),
            )


    def template_for(self, mode: Mode = Mode.PR) -> str:
        """The prompt template this phase renders in ``mode``.

        Most phases render one template whatever the review is looking at, and
        those ignore the argument — hence the default, which lets a caller
        outside a review (a fix pass, a triage) ask without inventing a mode it
        has no notion of. Raises for a phase that declares no template at all,
        the way ``_stem`` does for a domain with no review artifacts: a silent
        empty string reaches ``agent_templates.render`` as a missing file, one
        layer further from the declaration that is actually wrong. A mapping
        missing the mode asked for raises the same way rather than as a bare
        ``KeyError``, which names the mode but not the phase that owes it one.
        """
        if not self.template:
            raise ValueError(f"{self.phase} declares no prompt template")
        if isinstance(self.template, str):
            return self.template
        if mode not in self.template:
            raise ValueError(f"{self.phase} declares no prompt template for {mode}")

        return self.template[mode]

    @property
    def _stem(self) -> str:
        """The filename stem this phase's artifacts share: the phase's own name.

        ``group`` is the one fan-out phase, so its stem carries the index.
        """
        if self.domain is not PhaseDomain.REVIEW:
            raise ValueError(
                f"{self.phase} runs under the {self.domain} entry point and "
                "writes no review artifact; ask its own domain where its files "
                "live"
            )
        stem = str(self.phase)
        return f"{stem}-{{}}" if self.phase in _INDEXED else stem

    @property
    def log_filename(self) -> str:
        """The session log this phase writes into the review directory.

        ``single`` names no file of its own: it writes to the job's session
        log, which ``review-orchestrate --session-log`` may point outside the
        review directory. Raises for a phase outside the review domain, whose
        session log belongs to whichever entry point runs it.
        """
        return "" if self.phase is Phase.SINGLE else f"{self._stem}.jsonl"

    @property
    def output_filename(self) -> str:
        """The findings artifact this phase writes into the review directory.

        Empty for a phase that writes into the review document rather than an
        artifact of its own: ``single`` and ``synthesis`` produce ``review.md``
        and ``fix`` edits it in place. Raises for a phase outside the review
        domain, as ``log_filename`` does.
        """
        return "" if self.phase in _WRITES_REVIEW_FILE else f"{self._stem}.md"
