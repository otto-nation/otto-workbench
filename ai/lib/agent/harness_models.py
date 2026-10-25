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
- An agent declaring ``tier:`` in its source frontmatter is installed with
  that tier's model and level as its ``model:`` and ``effort:``, and with
  ``model: inherit`` when the tier names no Claude model (``tiers``). A tier
  at ``off`` installs ``effort: low`` and still thinks: agent frontmatter has
  no counterpart to the ``MAX_THINKING_TOKENS=0`` a pipeline phase at ``off``
  is spawned with.
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

``models.session: auto`` routes instead of naming one model. Claude Code takes
it as ``opusplan`` — the deep tier (its ``opus`` alias) while planning, the
standard tier (``sonnet``) otherwise. Pi takes it as the ``workbench/auto``
virtual model the ``model-router`` extension registers, which routes each
request to a tier and reads the tiers from ``config models``' ``tiers``. It
needs all three tiers set and each to name its provider: the router looks a
model up by provider and id, and a bare id would be qualified with
``workbench``, the virtual model's provider, on its way into ``enabledModels``.

Not: writing the settings files (``ai/claude/steps.sh``, ``ai/pi/steps.sh``),
the grammar a reference is written in (``agent.models``), routing a request
(``ai/pi/extensions/model-router``).
"""

# doc-group: pipeline

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import agent.backend_claude
from agent.models import ModelRef, resolve
from core.errors import OperatorError
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
# A Claude id as each platform spells it: `claude-…` (Anthropic, Vertex), a
# `<region>.anthropic.claude-…` Bedrock id, or a Bedrock ARN. Anchored so an id
# merely containing the word — a fine-tune named for it — is not taken for one.
_CLAUDE_ID = re.compile(r"^(claude-|([a-z]+\.)?anthropic\.claude-|arn:)")
# The session that routes per request rather than naming one model, and what
# each harness calls it: Claude Code's plan-then-build alias, and the virtual
# model ai/pi/extensions/model-router registers. ROUTER_PROVIDER and ROUTER_ID
# are spelled once more in that extension's route.ts, which tests hold equal.
AUTO_SESSION = "auto"
CLAUDE_AUTO_MODEL = "opusplan"
ROUTER_PROVIDER = "workbench"
ROUTER_ID = "auto"
# The context-window suffix an alias or id may carry, as in `sonnet[1m]`.
_WINDOW_SUFFIX = re.compile(r"\[[^\]]*\]$")


@dataclass(frozen=True)
class ClaudeTier:
    """What an agent on one tier runs: the model id, and the ``--effort`` level or None."""

    model: str
    effort: str | None = None


@dataclass(frozen=True)
class ClaudeModels:
    """Claude Code's model settings: the env block's tier aliases, the session, and each tier."""

    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    effort: str | None = None
    skipped: list[str] = field(default_factory=list)
    tiers: dict[Tier, ClaudeTier] = field(default_factory=dict)


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
    """What the model tiers say each harness's settings should hold, and each tier's reference."""

    claude: ClaudeModels
    pi: PiModels
    tiers: dict[Tier, ModelRef] = field(default_factory=dict)


class AutoNeedsTiers(OperatorError):
    """``models.session: auto`` with a tier unset, or one naming no provider."""

    def __init__(self, missing: list[str]):
        super().__init__(
            "models.session: auto routes between all three tiers, and each must name its "
            f"provider — fix {', '.join(missing)} (e.g. models.deep: anthropic/claude-opus-5-5)"
        )


def _claude_servable(ref: ModelRef) -> bool:
    """Whether the Claude CLI can run *ref*.

    A named provider answers it. A bare id has none to ask, so it has to look
    like a Claude model — a ``claude`` id on any platform, a Bedrock ARN, or one
    of the CLI's aliases — rather than be given to Claude Code because nothing
    said otherwise: a bare ``gemini-2.5-flash`` is a valid Pi tier.
    """
    if ref.provider is not None:
        return ref.provider in agent.backend_claude.SERVED_PROVIDERS
    bare = _WINDOW_SUFFIX.sub("", ref.id.lower())
    return bool(_CLAUDE_ID.match(bare)) or bare in CLAUDE_ALIASES


def _qualified(ref: ModelRef) -> str:
    return f"{ref.provider}/{ref.id}" if ref.provider else ref.id


def _claude(tiers: dict[Tier, ModelRef], session: ModelRef | None) -> ClaudeModels:
    env: dict[str, str] = {}
    skipped: list[str] = []
    claude_tiers: dict[Tier, ClaudeTier] = {}
    for tier, key in CLAUDE_TIER_ENV.items():
        ref = tiers.get(tier)
        if ref is None:
            continue
        if _claude_servable(ref):
            env[key] = ref.id
            claude_tiers[tier] = ClaudeTier(model=ref.id, effort=agent.backend_claude.effort(ref.level))
        else:
            skipped.append(f"models.{tier.value}: {ref.spec()} is not a Claude model, so {key} is left unset")
    model = effort = None
    if session is not None and _claude_servable(session):
        model = session.id
        effort = agent.backend_claude.effort(session.level)
    elif session is not None:
        skipped.append(f"models.session: {session.spec()} is not a Claude model, so Claude Code keeps its own")
    return ClaudeModels(env=env, model=model, effort=effort, skipped=skipped, tiers=claude_tiers)


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


def _auto(tiers: dict[Tier, ModelRef]) -> HarnessModels:
    """The routed session: ``opusplan`` on Claude Code, ``workbench/auto`` on Pi."""
    missing = [f"models.{tier.value}" for tier in Tier if tier not in tiers or not tiers[tier].provider]
    if missing:
        raise AutoNeedsTiers(missing)
    claude = _claude(tiers, None)
    pi = _pi(tiers, None)
    return HarnessModels(
        claude=replace(claude, model=CLAUDE_AUTO_MODEL),
        pi=replace(pi, default_model=ROUTER_ID, default_provider=ROUTER_PROVIDER,
                   enabled=[*pi.enabled, f"{ROUTER_PROVIDER}/{ROUTER_ID}"]),
        tiers=tiers,
    )


def harness_models(models: ModelsConfig) -> HarnessModels:
    """Each harness's model settings, from *models*.

    Raises `agent.models.UnsetTier` when ``models.session`` names a tier that is
    unset, as any other layer naming one does, and `AutoNeedsTiers` when it is
    ``auto`` without all three tiers naming a provider.
    """
    tiers = {tier: ref for tier in Tier if (ref := resolve(getattr(models, tier.value), models,
                                                           where=f"models.{tier.value}"))}
    if models.session == AUTO_SESSION:
        return _auto(tiers)
    session = resolve(models.session, models, where="models.session")
    return HarnessModels(claude=_claude(tiers, session), pi=_pi(tiers, session), tiers=tiers)
