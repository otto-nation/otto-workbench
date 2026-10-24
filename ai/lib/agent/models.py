"""What a model setting names: a provider, a model id, and a thinking level.

Every layer that names a model — ``--model``, ``WORKBENCH_AI_*_MODEL``,
``agent.phases.<p>.model`` and ``models.<tier>`` — is written in one grammar,
``[provider/]id[:level]`` or a tier name, and read here. A value is parsed once
and everything downstream takes the parts it needs: the review budget wants the
id, Pi wants the provider as ``--provider``, the thinking resolver wants the
level.

Two separators, each read so that a real id cannot be misread:

- ``provider/`` is split at the *first* slash. OpenRouter ids carry slashes of
  their own (``openrouter/anthropic/claude-x``), so a later slash is the id's.
- ``:level`` is a suffix only when it is one of ``Thinking``'s values. Bedrock
  ids end ``:0`` and some local ones ``:latest``; those stay part of the id.

Not: which layer wins (``agent.phases``), what a backend does with the parts.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

from config.workbench_config import ModelsConfig
from core.phases import Thinking, Tier

_LEVELS = frozenset(level.value for level in Thinking)
_TIERS = frozenset(tier.value for tier in Tier)


@dataclass(frozen=True)
class ModelRef:
    """One model setting, parsed. ``provider`` and ``level`` are None when unwritten."""

    id: str
    provider: str | None = None
    level: str | None = None

    def spec(self) -> str:
        """The reference written back out — ``[provider/]id[:level]``, as `parse_ref` reads it."""
        provider = f"{self.provider}/" if self.provider else ""
        level = f":{self.level}" if self.level else ""
        return f"{provider}{self.id}{level}"


def parse_ref(value: str) -> ModelRef:
    """``[provider/]id[:level]`` as a `ModelRef`. A tier name is not a ref; see `resolve`."""
    rest, level = value, None
    head, sep, tail = value.rpartition(":")
    if sep and tail in _LEVELS:
        rest, level = head, tail
    provider, sep, model_id = rest.partition("/")
    if not sep:
        return ModelRef(id=rest, level=level)
    return ModelRef(id=model_id, provider=provider or None, level=level)


def tier_value(tier: Tier, models: ModelsConfig) -> str | None:
    """What ``models.<tier>`` holds, or None when the tier is unset."""
    return getattr(models, tier.value)


def resolve(value: str | None, models: ModelsConfig) -> ModelRef | None:
    """A layer's value as a `ModelRef`: a tier name read through *models*, else parsed.

    None for an empty value, and for a tier name whose tier is unset — that
    layer names nothing, so the next one down answers. A tier name is never
    read as a model id: ``deep`` is reserved, which is what lets a phase
    override borrow another tier's model without spelling it out.
    """
    if not value:
        return None
    if value in _TIERS:
        target = tier_value(Tier(value), models)
        return parse_ref(target) if target else None
    return parse_ref(value)
