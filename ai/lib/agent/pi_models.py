"""Map a requested model id onto one `pi --list-models` actually serves.

``AI_*_MODEL`` is the operator's id, often the Anthropic / Claude Code spelling
(``claude-sonnet-5-5``). Pi's ``google-vertex-claude`` provider lists Vertex
publisher ids, which drop a generation segment when Google's catalogue does
(``claude-sonnet-5``). Passing the env value through unchanged is how a review
dies with ``Model "claude-sonnet-5-5" not found`` and the RPC path reports
that as stdin already closed.

The catalogue is the source of truth for what this process can spawn. An exact
hit is kept. Otherwise one trailing ``-N`` segment is stripped when that parent
is listed — ``claude-sonnet-5-5`` → ``claude-sonnet-5`` — and nothing else is
invented. An unreadable catalogue leaves the requested id alone.
"""

# doc-group: backend

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import core.proc
import core.timeouts

# Matches ai/pi/settings.json and `_pi_default_provider` in ai/pi/steps.sh.
DEFAULT_PROVIDER = "google-vertex-claude"

_PI_SETTINGS = Path.home() / ".pi" / "agent" / "settings.json"

# One generation segment: `claude-sonnet-5-5` → `claude-sonnet-5`. A date
# suffix (`-20251001`) is the same shape; stripping it is the right fallback
# when the catalogue lists the undated alias. Applied only when the parent is
# itself listed, so `claude-sonnet-4-5` does not become `claude-sonnet-4`.
_GEN_SUFFIX = re.compile(r"-\d+$")


def parent_model_id(model: str) -> str | None:
    """The id with one trailing ``-N`` segment stripped, or None."""
    stripped = _GEN_SUFFIX.sub("", model, count=1)
    return stripped if stripped and stripped != model else None


def parse_listed_ids(text: str, provider: str | None = None) -> frozenset[str]:
    """Model ids from a `pi --list-models` listing, optionally one provider.

    The header row is skipped. A provider filter is exact on column 1, so a
    ``google-vertex`` row cannot satisfy a ``google-vertex-claude`` request.
    """
    ids: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].lower() == "provider":
            continue
        if provider and parts[0] != provider:
            continue
        ids.add(parts[1])
    return frozenset(ids)


def resolve_served_model(model: str, listed: frozenset[str]) -> str:
    """The listed id to spawn, or *model* when the catalogue cannot help.

    An empty *listed* is an unreadable catalogue, not an empty provider: the
    requested id is returned unchanged so a missing ``pi`` cannot rewrite a
    model. A non-empty catalogue that contains *model* keeps it. Otherwise the
    parent id is used when that parent is listed.
    """
    if not model or not listed:
        return model
    if model in listed:
        return model
    parent = parent_model_id(model)
    if parent and parent in listed:
        return parent
    return model


def not_served_message(model: str, stderr: str) -> str | None:
    """An override hint when Pi's stderr says the model is unknown, else None."""
    if not stderr:
        return None
    lowered = stderr.lower()
    if "not found" not in lowered and "no models match" not in lowered:
        return None
    named = model or "the requested model"
    return (
        f"pi does not serve model {named!r} — set --model or agent.model "
        "to an id listed by `pi --list-models`"
    )


def default_provider() -> str:
    """Pi's defaultProvider, or the workbench template's fallback."""
    try:
        data = json.loads(_PI_SETTINGS.read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PROVIDER
    value = data.get("defaultProvider")
    return value if isinstance(value, str) and value else DEFAULT_PROVIDER


def effective_provider(explicit: str | None) -> str:
    """The provider a spawn will query: the flag, else Pi's default."""
    return explicit or default_provider()


@functools.cache
def _catalogue_text() -> str:
    """Stdout of ``pi --list-models``, or empty when the catalogue cannot be read."""
    try:
        result = core.proc.run(
            ["pi", "--list-models"], timeout=core.timeouts.LOCAL,
        )
    except OSError:
        return ""
    if not result.ok:
        return ""
    return result.stdout


def listed_models(provider: str | None) -> frozenset[str]:
    """Ids Pi lists under *provider* right now. Empty when the catalogue is unread."""
    return parse_listed_ids(_catalogue_text(), effective_provider(provider))


def served_model(model: str | None, provider: str | None = None) -> str | None:
    """Rewrite *model* onto a listed id when a one-segment parent is served.

    ``None`` and ``""`` pass through so a caller that omitted ``--model`` still
    omits it. A concrete id with no trailing ``-N`` is returned as-is without
    consulting the catalogue, which keeps command-builder tests off ``pi``.
    """
    if not model:
        return model
    if parent_model_id(model) is None:
        return model
    return resolve_served_model(model, listed_models(provider))
