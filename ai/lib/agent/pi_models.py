"""Refuse a model id ``pi --list-models`` does not serve.

``AI_*_MODEL`` is the operator's id, often the Anthropic / Claude Code spelling
(``claude-sonnet-5-5``). Pi's ``google-vertex-claude`` provider lists Vertex
publisher ids (``claude-sonnet-5``). Those are different models. Passing the
env value through unchanged is how a review dies with ``Model "claude-sonnet-5-5"
not found`` and the RPC path used to report that as stdin already closed.
Rewriting the id onto a listed parent would spawn a different model than the
one configured, so this module does not.

A readable catalogue that lacks the id is a configuration error: the message
names the id, where it came from when that is knowable, the provider, the
closest listed ids, and how to override. An empty catalogue is not that error
— pi absent, hung, or unreadable must not block a spawn pi itself might still
accept; Pi's stderr is the remaining signal on that path.
"""

# doc-group: backend

from __future__ import annotations

import difflib
import functools
import json
import os
from collections.abc import Iterable
from pathlib import Path

import agent.pi_binary
import core.proc
import core.timeouts

# Matches ai/pi/settings.json and `_pi_default_provider` in ai/pi/steps.sh.
DEFAULT_PROVIDER = "google-vertex-claude"

def _pi_settings_path() -> Path:
    """``~/.pi/agent/settings.json``, or ``WORKBENCH_PI_SETTINGS`` when set.

    The override is the seam the other state/cache/data roots have
    (``WORKBENCH_STATE_DIR``, ``WORKBENCH_CACHE_DIR``, ...), each sandboxed per
    test in ``tests/conftest.py``. Without it, a caller passing
    ``provider=None`` falls through ``effective_provider`` to here and reads
    whatever ``~/.pi/agent/settings.json`` happens to exist on the machine
    running the test.
    """
    override = os.environ.get("WORKBENCH_PI_SETTINGS")
    if override:
        return Path(override)
    return Path.home() / ".pi" / "agent" / "settings.json"


class ModelNotServed(RuntimeError):
    """The configured model id is not in pi's provider catalogue."""

    def __init__(self, model: str, message: str):
        self.model = model
        super().__init__(message)


def listing_rows(text: str) -> list[tuple[str, str, list[str]]]:
    """``(provider, model, rest)`` for each data row of a `pi --list-models` listing.

    The header row is skipped. ``rest`` is the remaining columns (context,
    max-out, …) so a caller that needs windows and a caller that needs ids
    parse the same table.
    """
    rows: list[tuple[str, str, list[str]]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].lower() == "provider":
            continue
        rows.append((parts[0], parts[1], parts[2:]))
    return rows


def parse_listed_ids(text: str, provider: str | None = None) -> frozenset[str]:
    """Model ids from a `pi --list-models` listing, optionally one provider.

    A provider filter is exact on column 1, so a ``google-vertex`` row cannot
    satisfy a ``google-vertex-claude`` request.
    """
    ids: set[str] = set()
    for row_provider, model, _rest in listing_rows(text):
        if provider and row_provider != provider:
            continue
        ids.add(model)
    return frozenset(ids)


def closest_listed(model: str, listed: Iterable[str], n: int = 5) -> tuple[str, ...]:
    """The listed ids closest to *model*, or a stable slice when none are close."""
    ids = list(listed)
    if not ids:
        return ()
    matches = difflib.get_close_matches(model, ids, n=n, cutoff=0.4)
    if matches:
        return tuple(matches)
    return tuple(sorted(ids)[:n])


def unserved_message(
    model: str, listed: frozenset[str], provider: str | None = None,
) -> str:
    """The error for an id this provider does not serve."""
    named = model or "the requested model"
    provider_name = effective_provider(provider)
    parts = [f"pi's {provider_name} provider does not serve model {named!r}"]
    close = closest_listed(named, listed)
    if close:
        parts.append("Closest listed ids: " + ", ".join(close))
    parts.append(
        "Set --model, agent.model, WORKBENCH_AI_MODEL or WORKBENCH_AI_<PHASE>_MODEL "
        "to an id listed by `pi --list-models`"
    )
    return ". ".join(parts) + "."


def not_served_message(
    model: str, stderr: str, provider: str | None = None, *, pi: tuple[str, ...],
) -> str | None:
    """An override hint when Pi's stderr says the model is unknown, else None.

    ``pi`` is the argv that started the binary that wrote *stderr*, so the closest ids
    it suggests come from the same catalogue that refused the model.
    """
    if not stderr:
        return None
    lowered = stderr.lower()
    if 'model "' not in lowered and "no models match pattern" not in lowered:
        return None
    return unserved_message(model, listed_models(provider, pi=pi), provider)


def default_provider() -> str:
    """Pi's defaultProvider, or the workbench template's fallback."""
    try:
        data = json.loads(_pi_settings_path().read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PROVIDER
    value = data.get("defaultProvider")
    return value if isinstance(value, str) and value else DEFAULT_PROVIDER


def effective_provider(explicit: str | None) -> str:
    """The provider a spawn will query: the flag, else Pi's default."""
    return explicit or default_provider()


@functools.cache
def catalogue_text(pi: tuple[str, ...]) -> str:
    """Stdout of ``<pi> --list-models``, or empty when the catalogue cannot be read.

    ``pi`` is the argv `PiBinary.spawn` starts the chosen binary with, so the
    catalogue a model is checked against is the one the spawn will run with —
    two pi versions serve different model sets. This is the one place the workbench
    lists models; the review budget reads its context windows from here too.

    The empty string is a skip, not an empty provider: a missing or unreadable
    ``pi`` must not refuse a model this process cannot check. The result is
    cached per argv for the life of the process, so a transient miss skips
    validation for every later call with that binary too.

    Only the argv is taken, not the spawn's environment: the listing runs in
    this process's environment, and the one variable a direct release spawn
    adds (``PI_MANAGED_INSTALL_ROOT``) governs pi's self-update, not its models.
    """
    try:
        result = core.proc.run(
            [*pi, "--list-models"], timeout=core.timeouts.LOCAL,
        )
    except OSError:
        # Not just FileNotFoundError: a `pi` present on disk but not executable
        # raises PermissionError, and that must skip the same way.
        return ""
    if not result.ok:
        return ""
    return result.stdout


def catalogue_for(cwd: str | Path) -> str:
    """The catalogue of the pi that would run in *cwd*, or empty when none can be chosen.

    For lookups that are optional, such as the review budget. A pi that cannot
    be chosen is not reported here: the spawn that needs it raises
    `PiUnresolvable` with the reason, where a run can record it.
    """
    try:
        binary = agent.pi_binary.resolve(cwd)
    except agent.pi_binary.PiUnresolvable:
        return ""
    return catalogue_text(binary.spawn(os.environ).argv)


def listed_models(provider: str | None, *, pi: tuple[str, ...]) -> frozenset[str]:
    """Ids *pi* lists under *provider* right now. Empty when the catalogue is unread."""
    return parse_listed_ids(catalogue_text(pi), effective_provider(provider))


class ModelNotConfigured(RuntimeError):
    """A provider is set and no layer names a model for it.

    Pi refuses ``--provider`` without ``--model`` and has no per-provider
    default for extension providers, so this is a configuration error, raised
    before a spawn with the keys that fix it.
    """

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"Pi needs a model when a provider is set (provider {provider!r}). "
            "Set agent.model, WORKBENCH_AI_MODEL or WORKBENCH_AI_<PHASE>_MODEL, "
            "or pass --model."
        )


def require_model_with_provider(provider: str | None, model: str | None) -> None:
    """Raise ``ModelNotConfigured`` for a provider with no model."""
    if provider and not model:
        raise ModelNotConfigured(provider)


def require_listed_model(
    model: str | None, provider: str | None = None, *, pi: tuple[str, ...],
) -> None:
    """Raise ``ModelNotServed`` when *model* is absent from a readable catalogue.

    An empty catalogue is skipped rather than treated as "nothing is served":
    pi absent, hung, or unreadable must not block a spawn pi itself might
    still accept. Pi's stderr is the remaining signal on that path.
    """
    if not model:
        return
    listed = listed_models(provider, pi=pi)
    if not listed:
        # The catalogue could not be read, not that this provider serves
        # nothing. Refusing here would turn an optional lookup into a hard
        # dependency on `pi` and would hide ids Pi would have accepted.
        return
    if model in listed:
        return
    raise ModelNotServed(model, unserved_message(model, listed, provider))
