"""What a phase resolves to here: the spec, the config file, the environment.

``agent.registry`` says what a phase's built-in defaults are. This module answers
the question a caller actually has — which model, thinking level, provider and
turn budget *this* invocation runs with — by layering the config file and the
environment over that spec.

One precedence chain, most specific first, for every knob a phase has:

    explicit argument  >  WORKBENCH_AI_<PHASE>_*  >  WORKBENCH_AI_*
                       >  agent.phases.<phase>.*  >  agent.*  >  built-in

A model is chosen by the same chain, with the phase's tier as its last layer:
``agent.phases.<phase>.model`` beats ``models.<spec.tier>``. Every layer is
written in `agent.models`' grammar, so a value names a provider and a thinking
level as well as an id, and a tier name borrows that tier's model.

Each part of the chosen reference ranks where the layer that wrote it ranks.
Its provider belongs to its id — an id is a name in one provider's catalogue —
so a provider written on the reference is the one it runs on, and
WORKBENCH_AI_PROVIDER only supplies one to a reference that names none. Its
level joins the thinking chain at its layer's height (`phase_settings`).

The effort preset sits between the config and the built-in for thinking level,
the one knob a review's depth setting flattens. It also scales the turn budget,
but multiplicatively rather than as a layer in that chain: a deeper review buys
turns on top of whatever the winning layer said, where a level it flattened
would discard a per-phase override.

**No built-in model.** A phase names a tier, not a model, so when no layer
sets one — the tier unset in ``models`` too — the model is ``None`` and the
backend gets no ``--model``: the Claude CLI and Pi then use their own defaults.
Pi refuses a provider without a model, which
``agent.pi_models.require_model_with_provider`` reports before anything runs.
"""

# doc-group: pipeline

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import config.workbench_config
from agent.models import ModelLayer, ModelRef, ResolvedModel, resolve, tier_ref
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


# ── Environment layers ───────────────────────────────────────────────────────


def resolve_thinking(explicit: str | None, env_key: str, default: str | None) -> str | None:
    """The thinking level: explicit, the phase's own key, the global key, default.

    Returns a plain string rather than a ``Thinking``: the env layers are
    operator input, passed through to the backend as written.
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


# ── Config layers ────────────────────────────────────────────────────────────


def _config(cfg: WorkbenchConfig | None) -> WorkbenchConfig:
    """The caller's config, or the one on disk.

    Callers resolving several values in a row pass the config they already
    loaded; the default is for the ones resolving a single value.
    """
    return cfg if cfg is not None else config.workbench_config.load_config_or_default()


def resolve_phase_model(
    phase: Phase, explicit: str | None, cfg: WorkbenchConfig | None = None,
) -> ResolvedModel | None:
    """The model a phase runs on and the layer that named it; None when none does.

    Precedence, highest first: the explicit argument, the phase env key, the
    global env key, ``agent.phases.<phase>.model``, then ``models.<tier>`` for
    the phase's tier. Any layer but the last may name a tier instead of a model,
    and one naming an unset tier raises `agent.models.UnsetTier` rather than
    letting a lower layer answer. The phase's own tier unset is not an error:
    it runs on the backend's default.
    """
    phase = Phase(phase)
    cfg = _config(cfg)
    override = cfg.agent.phases.get(phase)
    layers = (
        (ModelLayer.EXPLICIT, explicit, "--model"),
        (ModelLayer.PHASE_ENV, os.environ.get(phase.model_env_key), phase.model_env_key),
        (ModelLayer.GLOBAL_ENV, os.environ.get(GLOBAL_MODEL_ENV), GLOBAL_MODEL_ENV),
        (ModelLayer.PHASE_CONFIG, override.model if override is not None else None,
         f"agent.phases.{phase.value}.model"),
    )
    for layer, value, where in layers:
        ref = resolve(value, cfg.models, where=where)
        if ref is not None:
            return ResolvedModel(ref, layer)
    tier = PHASES[phase].tier
    ref = tier_ref(tier, cfg.models) if tier is not None else None
    return ResolvedModel(ref, ModelLayer.TIER) if ref is not None else None


def phase_ref(
    phase: Phase, explicit: str | None, cfg: WorkbenchConfig | None = None,
) -> ModelRef | None:
    """The model a phase runs on, parsed; None when no layer names one (see `resolve_phase_model`)."""
    resolved = resolve_phase_model(phase, explicit, cfg)
    return resolved.ref if resolved is not None else None


def phase_model(
    phase: Phase, explicit: str | None, cfg: WorkbenchConfig | None = None,
) -> str | None:
    """The model id a phase runs on, None when nothing configures one (see `phase_ref`)."""
    ref = phase_ref(phase, explicit, cfg)
    return ref.id if ref is not None else None


@dataclass(frozen=True)
class PhaseSettings:
    """What one invocation of a phase runs with, resolved from one reading of its model."""

    model: str | None
    provider: str | None
    thinking: str | None


def phase_settings(
    phase: Phase, explicit: str | None, cfg: WorkbenchConfig | None = None,
    effort: Effort | None = None,
) -> PhaseSettings:
    """Model, provider and thinking level for one invocation of *phase*.

    The three are read from one `resolve_phase_model`, so a reference's
    provider and level cannot come from a different layer than its id.

    The provider is the reference's, else WORKBENCH_AI_PROVIDER; None leaves
    the backend's own default provider in charge.

    The thinking level, highest first: a level on an explicit ``--model``; the
    thinking env keys; a level on an env-supplied model; then the config
    layers (`phase_thinking_default`), where the reference's level ranks with
    the layer that wrote it.
    """
    cfg = _config(cfg)
    resolved = resolve_phase_model(phase, explicit, cfg)
    ref = resolved.ref if resolved is not None else None
    layer = resolved.layer if resolved is not None else None
    level = ref.level if ref is not None else None
    below_env = phase_thinking_default(phase, effort, cfg, resolved)
    if level and layer in (ModelLayer.PHASE_ENV, ModelLayer.GLOBAL_ENV):
        below_env = level
    thinking = resolve_thinking(
        level if layer is ModelLayer.EXPLICIT else None,
        Phase(phase).thinking_env_key, below_env,
    )
    return PhaseSettings(
        model=ref.id if ref is not None else None,
        provider=(ref.provider if ref is not None else None) or resolve_provider(),
        thinking=thinking,
    )


def collect_phase_models(
    explicit: str | None, project_root: Path | str | None = None,
) -> dict[str, list[Phase]]:
    """Map each model the review pipeline would use to the phases requesting it.

    Callers use this to check every distinct model once up front and to name
    the env keys worth changing when one of them is unusable.

    Scoped to the review phases: preflight fails the run when a model is
    unreachable, and a review has no business refusing to start over the model
    a CI fix pass would have used.

    ``project_root`` is the worktree whose config applies, and a caller
    checking models a review will actually use must pass it. Without one only
    the global scope is read, while the review itself resolves against
    `ReviewJob.config` — project- and container-scoped — so a repo overriding
    a tier in its own `.workbench.yml` would be checked against a model it
    never runs.
    """
    models: dict[str, list[Phase]] = {}
    cfg = config.workbench_config.load_config_or_default(project_root)
    for phase in REVIEW_PHASES:
        model = phase_model(phase, explicit, cfg)
        # A phase with no model runs on the backend's default, which preflight
        # cannot name; `unset_review_phases` reports those for the budget floor.
        if model:
            models.setdefault(model, []).append(phase)
    return models


def unset_review_phases(
    explicit: str | None, project_root: Path | str | None = None,
) -> list[Phase]:
    """The review phases no layer names a model for (see `collect_phase_models`)."""
    cfg = config.workbench_config.load_config_or_default(project_root)
    return [phase for phase in REVIEW_PHASES if not phase_model(phase, explicit, cfg)]


def phase_thinking_default(
    phase: Phase, effort: Effort | None = None,
    cfg: WorkbenchConfig | None = None, resolved: ResolvedModel | None = None,
) -> str | None:
    """The thinking level below the env layers, most specific first.

    ``agent.phases.<phase>.thinking``; a level on the phase's own model
    override; a level on its tier's model (``models.deep: x/y:high``); the
    section-wide ``agent.thinking``; the effort preset; the spec. A setting for
    one phase beats one for its tier, which beats one for every phase.
    ``resolved`` is the phase's model, when the caller has one; a level it
    carries from an env or explicit layer ranks above this function, in
    `phase_settings`. ``effort=None`` is a phase running outside a review,
    where there is no preset to flatten anything.
    """
    cfg = _config(cfg)
    level = resolved.ref.level if resolved is not None else None
    layer = resolved.layer if resolved is not None else None
    override = cfg.agent.phases.get(phase)
    if override is not None and override.thinking is not None:
        return override.thinking
    if level and layer in (ModelLayer.PHASE_CONFIG, ModelLayer.TIER):
        return level
    if cfg.agent.thinking is not None:
        return cfg.agent.thinking
    preset = EFFORT_PRESETS[effort].thinking if effort is not None else None
    return preset if preset is not None else PHASES[phase].thinking


def resolve_effort(
    explicit: Effort | None, cfg: WorkbenchConfig | None = None,
) -> Effort:
    """The effort preset: the flag, the config, then medium."""
    if explicit is not None:
        return explicit
    return _config(cfg).review.effort or Effort.MEDIUM


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

    Three scalings. ``effort`` is how deep a review was asked to go, and its
    preset's ``turn_multiplier`` moves every phase's registry default together —
    a deeper review buys turns, not only thinking level and dollars.
    ``omitted_files`` is what a review's preflight left out of the prompt, which
    a phase that reads branch source has to open itself. ``items`` is the length
    of the checklist a fix pass is handed: each item costs ``turns_per_item``,
    clamped between the phase's flat default as a floor and ``turns_cap`` as the
    most one agent gets.

    The multiplier lands on the registry default before the item clamp, so
    ``turns_cap`` still bounds a fix pass however deep the review around it —
    the cap is what one agent can finish inside, which effort does not change.
    No phase takes both scalings today: the item-scaled phases are the fix
    passes, which run outside a review and so arrive with ``effort=None``.
    """
    spec = PHASES[phase]
    base = spec.max_turns
    if effort is not None:
        base = round(base * EFFORT_PRESETS[effort].turn_multiplier)
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

    The unproductive-path retry (`agent.retry.turns_for`) doubles the original
    budget. This function is the leftovers path, and the two used to disagree:
    a pass that ticked nothing got `2n`, a pass that ticked one box got
    `n + bump`, and at n=30 that is 60 against 50 — progress shrank the second
    attempt. Doubling is therefore in the max here too, so one phase has one
    retry policy and partial progress never gets less than a no-progress retry.

    Floored at the spec's minimum, then capped by the retry ceiling rather than
    the first pass's cap: clamping a retry to the budget that just ran out
    guarantees the same failure, which is what made the bump dead precisely
    when it was warranted.

    Only the four phases a `FixAdapter` names as its own reach here, through
    `fix.engine._retry`: `fix`, `comments_fix`, `ci_fix` and `prepush_fix`. The
    three verify gates are a `verify_phase`, sized by `phase_turns` in
    `fix.verify` and never retried, and the review phases escalate through
    `agent.retry.turns_for` instead. A ceiling at or below a phase's
    `turns_cap` therefore silently disables the bump for that phase, which is
    why every spec that reaches here sets one above its cap.
    """
    retry = PHASES[phase].retry
    return min(
        max(retry.turns_min, original + retry.bump, original * 2),
        retry.ceiling,
    )


def phase_chunk_size(phase: Phase) -> int:
    """How many items one invocation of a phase may be handed at once.

    Derived from the per-item rates and caps, so raising a cap or lowering a
    rate widens the chunk rather than needing a second number kept in step.
    """
    return PHASES[phase].scaling.chunk_size
