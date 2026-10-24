"""What a model setting names: a provider, a model id, and a thinking level.

Every layer that names a model — ``--model``, ``WORKBENCH_AI_*_MODEL``,
``agent.phases.<p>.model`` and ``models.<tier>`` — is written in one grammar,
``[provider/]id[:level]`` or a tier name, and read here. It is Pi's own
``--model`` grammar. A value is parsed once and everything downstream takes the
parts it needs: the review budget wants the id, Pi wants the provider as
``--provider``, the thinking resolver wants the level.

Two separators, each read so that a real id cannot be misread:

- ``provider/`` is split at the *first* slash. OpenRouter ids carry slashes of
  their own (``openrouter/anthropic/claude-x``), so a later slash is the id's.
  The cost: an id containing a slash always names its provider, even the
  default one — ``anthropic/claude-x`` is provider ``anthropic``.
- ``:level`` is a suffix only when it is one of ``Thinking``'s values. Bedrock
  ids end ``:0`` and some local ones ``:latest``; those stay part of the id.

Not: which layer wins (``agent.phases``), what a backend does with the parts.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from config.workbench_config import ModelsConfig
from core.errors import OperatorError
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


class ModelLayer(Enum):
    """Which layer of the model chain supplied a phase's model, highest first.

    A reference carries a provider and a level as well as an id, and each part
    ranks where the layer that wrote it ranks: a level typed on ``--model``
    beats a thinking level in the config file, where one on ``models.<tier>``
    does not.
    """

    EXPLICIT = auto()
    PHASE_ENV = auto()
    GLOBAL_ENV = auto()
    PHASE_CONFIG = auto()
    TIER = auto()


@dataclass(frozen=True)
class ResolvedModel:
    """A phase's model reference, and the layer that supplied it."""

    ref: ModelRef
    layer: ModelLayer


class UnsetTier(OperatorError):
    """A layer names a tier by name, and ``models.<tier>`` does not set it."""

    def __init__(self, value: str, where: str):
        super().__init__(
            f"{where} names the {value} tier, but models.{value} is not set — "
            f"set models.{value} or name a model instead"
        )


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


def tier_ref(tier: Tier, models: ModelsConfig) -> ModelRef | None:
    """The model ``models.<tier>`` names, or None when the tier is unset."""
    value = getattr(models, tier.value)
    return parse_ref(value) if value else None


def resolve(value: str | None, models: ModelsConfig, *, where: str = "a model setting") -> ModelRef | None:
    """A layer's value as a `ModelRef`: a tier name read through *models*, else parsed.

    None for an empty value — that layer names nothing, and the next one
    answers. A tier name is never read as a model id: ``deep`` is reserved, which
    is what lets a setting borrow another tier's model without spelling it out.
    One naming a tier that is unset raises `UnsetTier`, naming *where* it was
    written: asking for a tier is asking for that model, and silently running
    the next layer's instead is a different choice than the one made.
    """
    if not value:
        return None
    if value not in _TIERS:
        return parse_ref(value)
    ref = tier_ref(Tier(value), models)
    if ref is None:
        raise UnsetTier(value, where)
    return ref
