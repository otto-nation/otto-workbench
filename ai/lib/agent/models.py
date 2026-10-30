"""What a model setting names: a provider, a model id, and a thinking level.

Every layer that names a model — ``--model``, ``WORKBENCH_AI_*_MODEL``,
``agent.phases.<p>.model`` and ``models.<tier>`` — is written in one grammar,
``[provider/]id[:level]`` or a tier name, and read here. It is Pi's own
``--model`` grammar. A value is parsed once and everything downstream takes the
parts it needs: the review budget wants the id, Pi wants the provider as
``--provider``, the thinking resolver wants the level.

Two separators, each read so that a real id cannot be misread:

- ``provider/`` is split at the *first* slash, and only when what precedes it
  is shaped like a provider id — letters, digits, ``.``, ``_`` and ``-``. A
  later slash is the id's: OpenRouter ids carry their own
  (``openrouter/anthropic/claude-x``). A first segment holding a ``:`` is no
  provider, so a Bedrock ARN (``arn:aws:bedrock:…:inference-profile/abc``) is a
  bare id. The cost: an id whose first segment *is* provider-shaped always
  names its provider, even the default one — ``anthropic/claude-x`` is
  provider ``anthropic``.
- ``:level`` is a suffix only when it is one of ``Thinking``'s values. Bedrock
  ids end ``:0`` and some local ones ``:latest``; those stay part of the id.

A tier name takes a level too: ``deep:high`` is the deep tier's model at high.

Not: which layer wins (``agent.phases``), what a backend does with the parts.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TypeAlias

from config.workbench_config import ModelsConfig
from core.errors import OperatorError
from core.phases import Phase, Thinking, Tier

_LEVELS = frozenset(level.value for level in Thinking)
_TIERS = frozenset(tier.value for tier in Tier)
# What a provider id looks like, so a first segment that is not one — an ARN's,
# which carries colons — stays part of the id.
_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


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


# What a preflight is handed: each model reference a run would use, with the
# phases that request it. Keyed by reference rather than id because an id is a
# name in one provider's catalogue.
PhaseModels: TypeAlias = Mapping[ModelRef, Sequence[Phase]]


class ModelLayer(Enum):
    """Which layer of the model chain supplied a phase's model, highest first.

    A reference carries a provider and a level as well as an id, and each part
    ranks where the layer that wrote it ranks; `agent.phases.phase_settings`
    states the thinking chain those levels join.
    """

    EXPLICIT = auto()
    PHASE_ENV = auto()
    GLOBAL_ENV = auto()
    PHASE_CONFIG = auto()
    TIER = auto()


@dataclass(frozen=True)
class ResolvedModel:
    """A phase's model reference, the layer that supplied it, and the setting it was read from.

    ``key`` is that setting as a reader would edit it: ``--model``, an env var
    name, ``agent.phases.<phase>.model``, or ``models.<tier>`` for the tier
    layer. ``tier`` is the tier a value borrowed by name (``synthesis: {model:
    deep}``), and None when the value named a model itself — including the tier
    layer, whose ``key`` already names it.
    """

    ref: ModelRef
    layer: ModelLayer
    key: str | None = None
    tier: Tier | None = None


class UnsetTier(OperatorError):
    """A layer names a tier by name, and ``models.<tier>`` does not set it."""

    def __init__(self, value: str, where: str):
        super().__init__(
            f"{where} names the {value} tier, but models.{value} is not set — "
            f"set models.{value} or name a model instead"
        )


def _split_level(value: str) -> ModelRef:
    """*value* with a trailing ``:level`` split off: the rest as ``id``, unparsed."""
    head, sep, tail = value.rpartition(":")
    return ModelRef(id=head, level=tail) if sep and tail in _LEVELS else ModelRef(id=value)


def parse_ref(value: str) -> ModelRef:
    """``[provider/]id[:level]`` as a `ModelRef`. A tier name is not a ref; see `resolve`."""
    split = _split_level(value)
    provider, sep, model_id = split.id.partition("/")
    if sep and _PROVIDER_ID.fullmatch(provider):
        return ModelRef(id=model_id, provider=provider, level=split.level)
    return split


def tier_ref(tier: Tier, models: ModelsConfig) -> ModelRef | None:
    """The model ``models.<tier>`` names, or None when the tier is unset."""
    value = getattr(models, tier.value)
    return parse_ref(value) if value else None


def named_tier(value: str | None) -> Tier | None:
    """The tier *value* names — ``deep``, ``deep:high`` — or None when it names a model or nothing."""
    if not value:
        return None
    split = _split_level(value)
    return Tier(split.id) if split.id in _TIERS else None


def resolve(value: str | None, models: ModelsConfig, *, where: str = "a model setting") -> ModelRef | None:
    """A layer's value as a `ModelRef`: a tier name read through *models*, else parsed.

    None for an empty value — that layer names nothing, and the next one
    answers. A tier name is never read as a model id: ``deep`` is reserved, which
    is what lets a setting borrow another tier's model without spelling it out.
    A level on a tier name replaces the one its model carries. One naming a
    tier that is unset raises `UnsetTier`, naming *where* it was written: asking for a tier is asking for that model, and silently running
    the next layer's instead is a different choice than the one made.
    """
    if not value:
        return None
    tier = named_tier(value)
    if tier is None:
        return parse_ref(value)
    ref = tier_ref(tier, models)
    if ref is None:
        raise UnsetTier(tier.value, where)
    level = _split_level(value).level
    return replace(ref, level=level) if level else ref
