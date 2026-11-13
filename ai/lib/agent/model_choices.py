"""The models a phase can be set to, as one JSON document.

`pr models --json` prints this. A front end that offers a picker needs, per
phase, the domain and tier it reads, the configured default, whether the
review floor applies, and the catalogue of ids it can be pointed at — and
must not re-implement ``agent.phases.clears_review_floor`` to grey out a
Haiku row, because that copy drifts the moment the floor changes.

So nothing here decides anything. Defaults come from `agent.phase_report`,
enabled ids from `agent.harness_models`, catalogue rows from
`agent.pi_models.listing_rows`, and ``review_ok`` from
`agent.phases.clears_review_floor`. A catalogue that cannot be read is
``ok: false`` with the reason, the same fail-open stance as
`CatalogueUnreadable`; the command still exits 0. An unreadable config
raises `ConfigError`, the one failure the document cannot work around.

**JSON.** ``ModelChoices.as_json`` is the stable shape ``pr models --json``
prints. ``schema`` is 1 and changes only when a field is removed or changes
meaning.

```json
{
  "schema": 1,
  "phases": {
    "synthesis": {
      "domain": "review", "tier": "deep",
      "default": {"ref": "anthropic/claude-opus-5", "source_key": "models.deep"},
      "review_floor": true, "error": null
    }
  },
  "tiers": {
    "deep": {"ref": "anthropic/claude-opus-5", "review_ok": true},
    "standard": {"ref": null, "review_ok": true},
    "light": {"ref": "anthropic/claude-haiku-5", "review_ok": false}
  },
  "enabled": ["anthropic/claude-opus-5", "anthropic/claude-haiku-5"],
  "catalogue": {
    "ok": true, "error": null,
    "providers": [{
      "provider": "anthropic",
      "models": [{
        "id": "claude-opus-5", "ref": "anthropic/claude-opus-5",
        "context": 200000, "review_ok": true
      }]
    }]
  }
}
```

- ``phases`` is keyed by phase, in registry order. ``default`` is null when
  nothing names a model (the backend's default) or when ``error`` is set.
  ``ref`` is ``provider/id`` when the reference names a provider, else the
  bare id — the same string ``--phase-model`` accepts. ``source_key`` is the
  config key or env var that supplied it. ``review_floor`` is true for
  review-domain phases; the picker disables rows with ``review_ok: false``
  only on those. ``error`` holds the message when the phase cannot resolve.
- ``tiers.<tier>.ref`` is ``models.<tier>`` resolved to ``provider/id``, null
  when unset. ``review_ok`` is `clears_review_floor` on that id at that tier
  — the light tier is never ok, a Haiku id on another tier is not either.
- ``enabled`` is Pi's ``enabledModels``: every tier's model and the session's,
  as ``/model`` offers them. Empty when the session setting cannot be read.
- ``catalogue.ok`` is false when ``pi --list-models`` cannot be read; ``error``
  then names the reason and ``providers`` is empty. ``context`` is the listing's
  context cell as an integer token count, or null when it does not parse.
  ``review_ok`` on a catalogue row is `clears_review_floor` on the id alone.
"""

# doc-group: pipeline

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import agent.harness_models
import agent.models
import agent.phase_report
import agent.phases
import agent.pi_binary
import agent.pi_models
import config.workbench_config
import core.errors
from agent.models import ModelRef
from agent.phase_report import PhaseReport, PhaseRow
from config.workbench_config import WorkbenchConfig
from core.phases import PhaseDomain, Tier

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DefaultChoice:
    """A phase's configured model, as ``--phase-model`` would take it, and the key that set it."""

    ref: str
    source_key: str | None


@dataclass(frozen=True)
class PhaseChoice:
    """One phase: its domain, its tier, the configured default, and whether the review floor applies."""

    phase: str
    domain: str
    tier: str
    default: DefaultChoice | None
    review_floor: bool
    error: str | None

    def as_json(self) -> dict:
        default = None
        if self.default is not None:
            default = {"ref": self.default.ref, "source_key": self.default.source_key}
        return {
            "domain": self.domain,
            "tier": self.tier,
            "default": default,
            "review_floor": self.review_floor,
            "error": self.error,
        }


@dataclass(frozen=True)
class TierChoice:
    """One ``models.<tier>`` resolved to a ref, and whether a review phase may use it."""

    tier: str
    ref: str | None
    review_ok: bool

    def as_json(self) -> dict:
        return {"ref": self.ref, "review_ok": self.review_ok}


@dataclass(frozen=True)
class CatalogueModel:
    """One ``pi --list-models`` row: id, ``provider/id`` ref, context window, review floor."""

    id: str
    ref: str
    context: int | None
    review_ok: bool

    def as_json(self) -> dict:
        return {
            "id": self.id, "ref": self.ref,
            "context": self.context, "review_ok": self.review_ok,
        }


@dataclass(frozen=True)
class CatalogueProvider:
    """The models one provider lists, in listing order."""

    provider: str
    models: tuple[CatalogueModel, ...]

    def as_json(self) -> dict:
        return {
            "provider": self.provider,
            "models": [row.as_json() for row in self.models],
        }


@dataclass(frozen=True)
class Catalogue:
    """The listing, or why it could not be read."""

    ok: bool
    error: str | None
    providers: tuple[CatalogueProvider, ...]

    def as_json(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "providers": [row.as_json() for row in self.providers],
        }


@dataclass(frozen=True)
class ModelChoices:
    """Every registry phase, the three tiers, Pi's enabled list, and the catalogue."""

    phases: tuple[PhaseChoice, ...]
    tiers: tuple[TierChoice, ...]
    enabled: tuple[str, ...]
    catalogue: Catalogue

    def as_json(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "phases": {row.phase: row.as_json() for row in self.phases},
            "tiers": {row.tier: row.as_json() for row in self.tiers},
            "enabled": list(self.enabled),
            "catalogue": self.catalogue.as_json(),
        }


def _qualified(ref: ModelRef) -> str:
    return f"{ref.provider}/{ref.id}" if ref.provider else ref.id


def _default(row: PhaseRow) -> DefaultChoice | None:
    if row.model is None:
        return None
    key = row.model_source.key if row.model_source is not None else None
    return DefaultChoice(_qualified(row.model), key)


def _phase_choice(row: PhaseRow) -> PhaseChoice:
    return PhaseChoice(
        phase=row.phase.value,
        domain=row.domain.value,
        tier=row.tier.value,
        default=_default(row),
        review_floor=row.domain is PhaseDomain.REVIEW,
        error=row.error,
    )


def _tier_model(value: str | None, cfg: WorkbenchConfig, tier: Tier) -> ModelRef | None:
    if not value:
        return None
    try:
        return agent.models.resolve(value, cfg.models, where=f"models.{tier.value}")
    except core.errors.OperatorError:
        return None


def _tier_choice(row: agent.phase_report.TierRow, cfg: WorkbenchConfig) -> TierChoice:
    parsed = _tier_model(row.value, cfg, row.tier)
    ref = _qualified(parsed) if parsed is not None else None
    model_id = parsed.id if parsed is not None else ""
    return TierChoice(row.tier.value, ref, agent.phases.clears_review_floor(model_id, row.tier))


def _enabled(cfg: WorkbenchConfig) -> tuple[str, ...]:
    try:
        models = agent.harness_models.harness_models(cfg.models)
    except core.errors.OperatorError:
        return ()
    return tuple(models.pi.enabled)


def _providers(text: str) -> tuple[CatalogueProvider, ...]:
    grouped: dict[str, list[CatalogueModel]] = {}
    order: list[str] = []
    for provider, model, rest in agent.pi_models.listing_rows(text):
        if provider not in grouped:
            grouped[provider] = []
            order.append(provider)
        context = agent.pi_models.parse_context_tokens(rest[0]) if rest else None
        grouped[provider].append(CatalogueModel(
            id=model,
            ref=f"{provider}/{model}",
            context=context,
            review_ok=agent.phases.clears_review_floor(model),
        ))
    return tuple(CatalogueProvider(name, tuple(grouped[name])) for name in order)


def _catalogue(cwd: str) -> Catalogue:
    try:
        spawn = agent.pi_binary.resolve(cwd).spawn(os.environ)
        text = agent.pi_models.catalogue_text(spawn.argv)
    except (agent.pi_binary.PiUnresolvable, agent.pi_models.CatalogueUnreadable) as exc:
        return Catalogue(ok=False, error=str(exc), providers=())
    return Catalogue(ok=True, error=None, providers=_providers(text))


def model_choices(project_root: Path | str | None = None) -> ModelChoices:
    """The picker document as *project_root*'s config and this machine's catalogue resolve it.

    ``project_root`` is a repo's work-tree root, or None for the global scope
    alone. Raises `ConfigError` when any scope cannot be read or typed; a
    catalogue that cannot be listed stays on the document as ``ok: false``.
    """
    report: PhaseReport = agent.phase_report.phase_report(project_root)
    cfg = config.workbench_config.load_config(project_root)
    cwd = os.fspath(project_root) if project_root is not None else os.getcwd()
    return ModelChoices(
        phases=tuple(_phase_choice(row) for row in report.phases),
        tiers=tuple(_tier_choice(row, cfg) for row in report.tiers),
        enabled=_enabled(cfg),
        catalogue=_catalogue(cwd),
    )
