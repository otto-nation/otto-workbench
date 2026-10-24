"""The model tiers as each harness's own settings read them.

``models.*`` in the config is the one place a model is named. Claude Code and
Pi each keep model settings of their own, which the workbench's sync writes
from that one place; this module is what both syncs ask, so neither re-parses
a reference in bash and the two cannot read one differently.

Claude Code:

- ``ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`` take the deep, standard and
  light tiers, so the CLI's own ``opus``/``sonnet``/``haiku`` aliases — and the
  subagents and ``opusplan`` that use them — follow the tiers.
- ``model`` and ``effortLevel`` take ``models.session``.
- A tier naming a provider the Claude CLI cannot be pointed at, or a bare id
  that is not a Claude model, has no Claude model, and is reported rather than
  written: the CLI runs Claude models only.

Pi:

- ``defaultModel``, ``defaultProvider`` and ``defaultThinkingLevel`` take
  ``models.session``; each is left out when the session does not name it, so
  Pi keeps the value it has.
- ``enabledModels`` lists every tier's model and the session's, as ``/model``
  offers them; ``modelThinkingLevels`` carries each one's level. Both are keyed
  ``provider/id`` where the reference names a provider and by the bare id where
  it does not: Pi's sync qualifies a bare id with the ``defaultProvider`` the
  settings end up with, which only the live file knows.

Not: writing the settings files (``ai/claude/steps.sh``, ``ai/pi/steps.sh``),
the grammar a reference is written in (``agent.models``).
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass, field

import agent.backend_claude
from agent.models import ModelRef, resolve
from config.workbench_config import ModelsConfig
from core.phases import Tier

# Which Claude CLI alias each tier stands behind.
CLAUDE_TIER_ENV = {
    Tier.DEEP: "ANTHROPIC_DEFAULT_OPUS_MODEL",
    Tier.STANDARD: "ANTHROPIC_DEFAULT_SONNET_MODEL",
    Tier.LIGHT: "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
# Every env key the Claude view manages, including the one it no longer
# writes: `ANTHROPIC_MODEL` was the session model's name in the env block
# before `model` carried it, and a sync clears it so the two cannot disagree.
CLAUDE_MANAGED_ENV = (*CLAUDE_TIER_ENV.values(), "ANTHROPIC_MODEL")
# The model aliases the Claude CLI resolves itself.
CLAUDE_ALIASES = frozenset({"opus", "sonnet", "haiku", "opusplan", "default"})
# The levels `claude --effort` takes.
CLAUDE_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ClaudeModels:
    """Claude Code's model settings: the env block's tier aliases, and the session."""

    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    effort: str | None = None
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PiModels:
    """Pi's model settings, each None or empty when nothing names it."""

    default_model: str | None = None
    default_provider: str | None = None
    default_thinking: str | None = None
    enabled: list[str] = field(default_factory=list)
    thinking_levels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessModels:
    """What the model tiers say each harness's settings should hold."""

    claude: ClaudeModels
    pi: PiModels


def _claude_servable(ref: ModelRef) -> bool:
    """Whether the Claude CLI can run *ref*.

    A named provider answers it. A bare id has none to ask, so it has to look
    like a Claude model — a ``claude`` id on any platform, a Bedrock ARN, or one
    of the CLI's aliases — rather than be given to Claude Code because nothing
    said otherwise: a bare ``gemini-2.5-flash`` is a valid Pi tier.
    """
    if ref.provider is not None:
        return ref.provider in agent.backend_claude.SERVED_PROVIDERS
    return "claude" in ref.id.lower() or ref.id.startswith("arn:") or ref.id in CLAUDE_ALIASES


def _qualified(ref: ModelRef) -> str:
    return f"{ref.provider}/{ref.id}" if ref.provider else ref.id


def _claude(tiers: dict[Tier, ModelRef], session: ModelRef | None) -> ClaudeModels:
    env: dict[str, str] = {}
    skipped: list[str] = []
    for tier, key in CLAUDE_TIER_ENV.items():
        ref = tiers.get(tier)
        if ref is None:
            continue
        if _claude_servable(ref):
            env[key] = ref.id
        else:
            skipped.append(f"models.{tier.value}: {ref.spec()} is not a Claude model, so {key} is left unset")
    model = effort = None
    if session is not None and _claude_servable(session):
        model = session.id
        effort = session.level if session.level in CLAUDE_EFFORT_LEVELS else None
    elif session is not None:
        skipped.append(f"models.session: {session.spec()} is not a Claude model, so Claude Code keeps its own")
    return ClaudeModels(env=env, model=model, effort=effort, skipped=skipped)


def _pi(tiers: dict[Tier, ModelRef], session: ModelRef | None) -> PiModels:
    refs = [*tiers.values(), *([session] if session is not None else [])]
    enabled = list(dict.fromkeys(_qualified(ref) for ref in refs))
    levels = {_qualified(ref): ref.level for ref in refs if ref.level}
    if session is None:
        return PiModels(enabled=enabled, thinking_levels=levels)
    return PiModels(
        default_model=session.id, default_provider=session.provider,
        default_thinking=session.level, enabled=enabled, thinking_levels=levels,
    )


def harness_models(models: ModelsConfig) -> HarnessModels:
    """Each harness's model settings, from *models*.

    Raises `agent.models.UnsetTier` when ``models.session`` names a tier that is
    unset, as any other layer naming one does.
    """
    tiers = {tier: ref for tier in Tier if (ref := resolve(getattr(models, tier.value), models,
                                                           where=f"models.{tier.value}"))}
    session = resolve(models.session, models, where="models.session")
    return HarnessModels(claude=_claude(tiers, session), pi=_pi(tiers, session))
