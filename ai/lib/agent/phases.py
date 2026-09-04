"""What a phase resolves to here: the spec, the config file, the environment.

``agent.registry`` says what a phase's built-in defaults are. This module answers
the question a caller actually has — which model, thinking level, provider and
turn budget *this* invocation runs with — by layering the config file and the
environment over that spec.

One precedence chain, most specific first, for every knob a phase has:

    explicit argument  >  WORKBENCH_AI_<PHASE>_*  >  WORKBENCH_AI_*
                       >  agent.phases.<phase>.*  >  agent.*  >  built-in

The effort preset sits between the config and the built-in for thinking level
only, because it is the one knob a review's depth setting flattens.

**Model aliases.** Whichever layer wins, a bare tier alias (``sonnet``,
``opus``, ``haiku``) is then resolved through ``ANTHROPIC_DEFAULT_SONNET_MODEL``
/ ``ANTHROPIC_DEFAULT_OPUS_MODEL`` / ``ANTHROPIC_DEFAULT_HAIKU_MODEL``. An alias
names a tier, not a deployment — on Vertex and Bedrock the account provisions a
specific model ID, and that is where it lives. A concrete model ID anywhere in
the chain passes through untouched. The Claude CLI does this resolution itself;
the Pi backend does not, so it happens here before dispatch and both backends
land on the same model.
"""

# doc-group: pipeline

from __future__ import annotations

import os
from enum import StrEnum

from config import workbench_config
from agent.registry import PHASES, REVIEW_PHASES
from agent.types import EFFORT_PRESETS
from core.phases import ENV_PREFIX, Effort, Phase, Thinking
from config.workbench_config import WorkbenchConfig

# The keys that cover every phase at once, one layer below the per-phase ones.
GLOBAL_MODEL_ENV = f"{ENV_PREFIX}MODEL"
GLOBAL_THINKING_ENV = f"{ENV_PREFIX}THINKING"
PROVIDER_ENV = f"{ENV_PREFIX}PROVIDER"

# What one file preflight could not fit into the prompt costs a phase that has
# to open it itself: one turn to read, one to act on what it found.
OMITTED_FILE_TURNS = 2


# ── Model aliases ────────────────────────────────────────────────────────────


class ModelAlias(StrEnum):
    """Short model names that resolve to a concrete model id via env override.

    Anything not listed here is already a concrete id and passes through
    untouched.
    """

    SONNET = "sonnet"
    OPUS = "opus"
    HAIKU = "haiku"

    @property
    def env_key(self) -> str:
        return f"ANTHROPIC_DEFAULT_{self.upper()}_MODEL"

    @classmethod
    def parse(cls, model: str) -> ModelAlias | None:
        try:
            return cls(model)
        except ValueError:
            return None


def resolve_alias(model: str) -> str:
    """Swap a tier alias for the provisioned model ID. Concrete IDs pass through."""
    alias = ModelAlias.parse(model)
    if alias is None:
        return model
    return os.environ.get(alias.env_key) or model


# ── Environment layers ───────────────────────────────────────────────────────


def _select_model(explicit: str | None, env_key: str, default: str) -> str:
    """The winning model name by precedence, before any alias resolution."""
    if explicit:
        return explicit
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    global_env = os.environ.get(GLOBAL_MODEL_ENV)
    if global_env:
        return global_env
    return default


def resolve_model(explicit: str | None, env_key: str, default: str) -> str:
    """Pick the model for a phase, then map any tier alias to its provisioned ID.

    Precedence: explicit argument, the phase's own key, WORKBENCH_AI_MODEL, the
    caller's default. Whichever wins is resolved through ANTHROPIC_DEFAULT_* — so
    naming a tier anywhere in the chain honors the deployment configured for it.
    """
    return resolve_alias(_select_model(explicit, env_key, default))


def resolve_thinking(explicit: str | None, env_key: str, default: str | None) -> str | None:
    """The thinking level: explicit, the phase's own key, the global key, default.

    Returns a plain string rather than a ``Thinking``: the env layers are
    operator input and can name a level the enum does not carry (``xhigh``),
    which the backend passes through.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    global_env = os.environ.get(GLOBAL_THINKING_ENV)
    if global_env:
        return global_env
    return default


def resolve_provider() -> str | None:
    """The backend provider the environment asks for, if any."""
    return os.environ.get(PROVIDER_ENV)


def phase_provider(config: WorkbenchConfig | None = None) -> str | None:
    """The backend provider an invocation runs against: the env key, then config.

    Not per-phase: a provider is where the models are served from, and a run
    that reached half its phases on one deployment and half on another would be
    unattributable. The phase argument other resolvers take is deliberately
    absent so no caller can imply otherwise.
    """
    return resolve_provider() or _config(config).agent.provider


# ── Config layers ────────────────────────────────────────────────────────────


def _config(config: WorkbenchConfig | None) -> WorkbenchConfig:
    """The caller's config, or the one on disk.

    Callers resolving several values in a row pass the config they already
    loaded; the default is for the ones resolving a single value.
    """
    return config if config is not None else workbench_config.load_config_or_default()


def _config_model(phase: Phase, config: WorkbenchConfig) -> str | None:
    """The model this phase's config asks for: its own entry, else the section."""
    override = config.agent.phases.get(phase)
    if override is not None and override.model:
        return override.model
    return config.agent.model


def phase_model(
    phase: Phase, explicit: str | None, config: WorkbenchConfig | None = None,
) -> str:
    """Resolve the model for a phase.

    Precedence, highest first: the explicit argument, the phase env key, the
    global env key, the config file (phase entry then section), the phase's
    built-in default. The env layers live in ``resolve_model``; the config and
    built-in layers collapse into the default handed to it.
    """
    phase = Phase(phase)
    cfg = _config(config)
    return resolve_model(
        explicit,
        phase.model_env_key,
        _config_model(phase, cfg) or PHASES[phase].model,
    )


def collect_phase_models(explicit: str | None) -> dict[str, list[Phase]]:
    """Map each model the review pipeline would use to the phases requesting it.

    Callers use this to check every distinct model once up front and to name
    the env keys worth changing when one of them is unusable.

    Scoped to the review phases: preflight fails the run when a model is
    unreachable, and a review has no business refusing to start over the model
    a CI fix pass would have used.
    """
    models: dict[str, list[Phase]] = {}
    cfg = workbench_config.load_config_or_default()
    for phase in REVIEW_PHASES:
        models.setdefault(phase_model(phase, explicit, cfg), []).append(phase)
    return models


def phase_thinking_default(
    phase: Phase, effort: Effort | None = None,
    config: WorkbenchConfig | None = None,
) -> Thinking | None:
    """The thinking level below the env layers: config, effort preset, spec.

    A phase entry beats the section, and both beat the effort preset — a level
    written for one phase is more specific than one the preset flattens
    everything to. ``effort=None`` is a phase running outside a review, where
    there is no preset to flatten anything and the spec stands under the config.
    """
    cfg = _config(config)
    override = cfg.agent.phases.get(phase)
    if override is not None and override.thinking is not None:
        return override.thinking
    if cfg.agent.thinking is not None:
        return cfg.agent.thinking
    preset = EFFORT_PRESETS[effort].thinking if effort is not None else None
    return preset if preset is not None else PHASES[phase].thinking


def phase_thinking(
    phase: Phase, effort: Effort | None = None,
    config: WorkbenchConfig | None = None,
) -> str | None:
    """The thinking level a phase runs at, env layers included."""
    return resolve_thinking(
        None, phase.thinking_env_key, phase_thinking_default(phase, effort, config),
    )


def resolve_effort(
    explicit: Effort | None, config: WorkbenchConfig | None = None,
) -> Effort:
    """The effort preset: the flag, the config, then medium."""
    if explicit is not None:
        return explicit
    return _config(config).review.effort or Effort.MEDIUM


# ── Turn and dollar budgets ──────────────────────────────────────────────────


def omitted_turns(effort: Effort, omitted_files: int) -> int:
    """What files left out of the prompt cost a phase that has to open them.

    Zero when the effort preset drops omitted files from the prompt's remit
    altogether — no phase is expected to go looking for them.
    """
    if EFFORT_PRESETS[effort].skip_omitted_files:
        return 0
    return omitted_files * OMITTED_FILE_TURNS


def phase_omitted_bump(
    phase: Phase, effort: Effort | None, omitted_files: int,
) -> int:
    """The omitted-file bump this phase takes, zero when its spec opts out.

    Separate from ``phase_turns`` because a caller escalating past the registry
    default still owes the phase its bump — the retry ceiling is not a default,
    but whether the phase scales at all is still the spec's answer.

    Zero without an effort too: the bump measures what a review's preflight had
    to leave out of the prompt, and a phase running outside a review has no
    preflight behind it.
    """
    if effort is None or not PHASES[phase].scales_with_omitted:
        return 0
    return omitted_turns(effort, omitted_files)


def phase_turns(
    phase: Phase, effort: Effort | None = None, omitted_files: int = 0,
    *, items: int = 0,
) -> int:
    """A phase's turn budget: its registry default, scaled by the work it faces.

    Two scalings, and no phase takes both. ``omitted_files`` is what a review's
    preflight left out of the prompt, which a phase that reads branch source has
    to open itself. ``items`` is the length of the checklist a fix pass is
    handed: each item costs ``turns_per_item``, clamped between the phase's flat
    default as a floor and ``turns_cap`` as the most one agent gets.
    """
    spec = PHASES[phase]
    base = spec.max_turns
    if spec.scaling.turns_per_item:
        base = min(
            max(base, items * spec.scaling.turns_per_item), spec.scaling.turns_cap,
        )
    return base + phase_omitted_bump(phase, effort, omitted_files)


def phase_budget(
    phase: Phase, effort: Effort | None = None, *, items: int = 0,
) -> float | None:
    """The dollar cap on one invocation of a phase.

    ``max_budget=None`` in the spec defers to the effort preset's per-agent
    budget, which is how every review phase is sized. A phase that pins its own
    takes it flat, or scales it with ``items`` the way ``phase_turns`` does when
    its spec names a per-item rate.
    """
    spec = PHASES[phase]
    if spec.max_budget is None:
        return EFFORT_PRESETS[effort].agent_budget if effort is not None else None
    if not spec.scaling.budget_per_item:
        return spec.max_budget
    return min(
        max(spec.max_budget, items * spec.scaling.budget_per_item),
        spec.scaling.budget_cap,
    )


def phase_retry_turns(phase: Phase, original: int) -> int:
    """Turns for a second attempt at a phase whose first came back empty.

    Bumped above whatever the original pass was given and floored at the spec's
    minimum, then capped by the retry ceiling rather than the first pass's cap:
    clamping a retry to the budget that just ran out guarantees the same
    failure, which is what made the bump dead precisely when it was warranted.
    """
    retry = PHASES[phase].retry
    return min(max(retry.turns_min, original + retry.bump), retry.ceiling)


def phase_chunk_size(phase: Phase) -> int:
    """How many items one invocation of a phase may be handed at once.

    Derived from the per-item rates and caps, so raising a cap or lowering a
    rate widens the chunk rather than needing a second number kept in step.
    """
    return PHASES[phase].scaling.chunk_size
