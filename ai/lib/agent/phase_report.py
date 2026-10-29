"""What every phase resolves to on this machine, and which setting answered each part.

`otto-workbench config phases` prints this. A front end that lets an operator
set models needs, per phase, the tier it reads, the model and thinking level it
will run on, and the key to edit to change either — and must not re-implement
the precedence chain in `agent.phases` to get them, because that copy drifts the
moment a phase moves tier or a layer is added.

So nothing here decides anything. The model and its layer come from
`agent.phases.resolve_phase_model`, the thinking level and its layer from
`agent.phases.resolve_phase_thinking` — the same rows `phase_settings` runs on.
This module adds only what the resolver cannot know: which file a config key
was read from, taken from `config.workbench_config_report.config_status`, the
owner `config status` and `config get` already report through.

Resolved as a phase outside a review: no ``--model`` and no effort preset, so
the ``explicit*`` and ``effort`` layers never answer here. A phase whose
settings raise an operator error (a tier name naming an unset tier,
WORKBENCH_AI_PROVIDER still set) carries the message instead of a model; an
unreadable config raises `ConfigError`, the one failure the report cannot work
around. ``PhaseReport.as_json`` is the stable shape; ``docs/ai-automation.md``
documents it.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import agent.phases
import config.workbench_config
import config.workbench_config_report
import core.errors
from agent.models import ModelLayer, ModelRef, ResolvedModel
from agent.phases import ResolvedThinking
from agent.registry import PHASES
from config.workbench_config import DEFAULT_SCOPE, WorkbenchConfig
from core.phases import ENV_PREFIX, Phase, PhaseDomain, Tier

# The JSON's own version. Bumped only when a field is removed or changes meaning.
SCHEMA_VERSION = 1
# The scope of a value read from the process environment rather than a file.
ENV_SCOPE = "env"
# The layer a source names when no setting supplied the value.
UNSET_LAYER = "unset"


@dataclass(frozen=True)
class Source:
    """The setting that answered: its layer, its key, its scope, and the tier it borrowed."""

    layer: str
    key: str | None
    scope: str | None
    via: Source | None = None

    def as_json(self) -> dict:
        return {
            "layer": self.layer, "key": self.key, "scope": self.scope,
            "via": self.via.as_json() if self.via is not None else None,
        }


@dataclass(frozen=True)
class TierRow:
    """One ``models.<tier>`` as written, and the file that wrote it; both None when unset."""

    tier: Tier
    value: str | None
    scope: str | None


@dataclass(frozen=True)
class PhaseRow:
    """One phase: its tier, what it runs on, and where each answer came from.

    ``error`` set means the phase's settings could not be resolved, and every
    resolved field is None.
    """

    phase: Phase
    domain: PhaseDomain
    tier: Tier
    model: ModelRef | None = None
    model_source: Source | None = None
    thinking: str | None = None
    thinking_source: Source | None = None
    error: str | None = None

    def as_json(self) -> dict:
        model = self.model
        return {
            "domain": self.domain.value,
            "tier": self.tier.value,
            "model": ({"provider": model.provider, "id": model.id, "level": model.level}
                      if model is not None else None),
            "model_source": self.model_source.as_json() if self.model_source else None,
            "thinking": str(self.thinking) if self.thinking is not None else None,
            "thinking_source": self.thinking_source.as_json() if self.thinking_source else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class PhaseReport:
    """The three tiers, then every registry phase in registry order."""

    tiers: tuple[TierRow, ...]
    phases: tuple[PhaseRow, ...]

    def as_json(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "tiers": {row.tier.value: {"value": row.value, "scope": row.scope} for row in self.tiers},
            "phases": {row.phase.value: row.as_json() for row in self.phases},
        }


def _file_scopes(project_root: Path | str | None) -> dict[str, str]:
    """Each config key a file sets, mapped to the name of the scope that won it."""
    status = config.workbench_config_report.config_status(project_root)
    if not status.ok:
        raise config.workbench_config.ConfigError("; ".join(status.problems))
    return {row.key: row.scope.name for row in status.keys if not row.is_default}


def _scope(key: str | None, scopes: dict[str, str]) -> str:
    """Where *key* was read from: the environment, a config file, or the built-in default."""
    if key is None:
        return DEFAULT_SCOPE
    if key.startswith(ENV_PREFIX):
        return ENV_SCOPE
    return scopes.get(key, DEFAULT_SCOPE)


def _via(tier: Tier | None, scopes: dict[str, str]) -> Source | None:
    if tier is None:
        return None
    key = f"models.{tier.value}"
    return Source(ModelLayer.TIER.name.lower(), key, _scope(key, scopes))


def _model_source(phase: Phase, resolved: ResolvedModel | None, scopes: dict[str, str]) -> Source:
    if resolved is None:
        return Source(UNSET_LAYER, f"models.{PHASES[phase].tier.value}", None)
    return Source(resolved.layer.name.lower(), resolved.key,
                  _scope(resolved.key, scopes), _via(resolved.tier, scopes))


def _thinking_source(thinking: ResolvedThinking | None, scopes: dict[str, str]) -> Source:
    if thinking is None:
        return Source(UNSET_LAYER, None, None)
    return Source(thinking.layer.name.lower(), thinking.key,
                  _scope(thinking.key, scopes), _via(thinking.tier, scopes))


def _phase_row(phase: Phase, cfg: WorkbenchConfig, scopes: dict[str, str]) -> PhaseRow:
    spec = PHASES[phase]
    try:
        resolved = agent.phases.resolve_phase_model(phase, None, cfg)
        thinking = agent.phases.resolve_phase_thinking(phase, resolved, cfg)
    except core.errors.OperatorError as exc:
        return PhaseRow(phase, spec.domain, spec.tier, error=str(exc))
    return PhaseRow(
        phase, spec.domain, spec.tier,
        model=resolved.ref if resolved is not None else None,
        model_source=_model_source(phase, resolved, scopes),
        thinking=thinking.level if thinking is not None else None,
        thinking_source=_thinking_source(thinking, scopes),
    )


def _tier_row(tier: Tier, cfg: WorkbenchConfig, scopes: dict[str, str]) -> TierRow:
    value = getattr(cfg.models, tier.value) or None
    return TierRow(tier, value, scopes.get(f"models.{tier.value}") if value else None)


def phase_report(project_root: Path | str | None = None) -> PhaseReport:
    """Every tier and registry phase as *project_root*'s config resolves them.

    ``project_root`` is a repo's work-tree root, or None for the global scope
    alone. Raises `ConfigError` when any scope cannot be read or typed; an
    operator error resolving one phase stays on that phase's row.
    """
    scopes = _file_scopes(project_root)
    cfg = config.workbench_config.load_config(project_root)
    return PhaseReport(
        tiers=tuple(_tier_row(tier, cfg, scopes) for tier in Tier),
        phases=tuple(_phase_row(phase, cfg, scopes) for phase in PHASES),
    )
