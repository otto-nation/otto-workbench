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
closest listed ids, and how to override. A catalogue that cannot be read is not
that error — pi absent, hung, or unreadable must not block a spawn pi itself
might still accept; Pi's stderr is the remaining signal on that path.
`catalogue_text` raises `CatalogueUnreadable` with the reason rather than
returning an empty listing, so a caller that fails open (`require_listed_model`)
still does, a caller that must explain itself (the review budget) can, and no
failure is ever cached: the next caller lists again.

The same check runs once, up front, for every model a run would use
(`check_targets`): each reference is judged by the provider it names, so a run
mixing providers fails before it pays for anything, not at the first phase that
reaches the wrong catalogue.
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
import core.errors
import core.proc
import core.timeouts
from agent.backend import abort_preflight
from agent.models import PhaseModels

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


class ModelNotServed(core.errors.OperatorError):
    """The configured model id is not in pi's provider catalogue."""

    def __init__(self, model: str, message: str):
        self.model = model
        super().__init__(message)


class CatalogueUnreadable(RuntimeError):
    """``pi --list-models`` produced no listing; the message says why."""


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


def parse_context_tokens(raw: str) -> int | None:
    """A `pi --list-models` context cell as an integer token count, or None.

    Suffixes: K=1_000, M=1_000_000. Decimals are allowed. The result is
    floored — overestimating a window is the expensive direction.
    """
    text = raw.strip()
    if not text:
        return None
    multiplier = 1
    suffix = text[-1]
    if suffix in ("K", "k"):
        multiplier = 1_000
        text = text[:-1]
    elif suffix in ("M", "m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount < 0:
        return None
    tokens = int(amount * multiplier)
    if tokens <= 0:
        return None
    return tokens


def listed_windows(text: str) -> dict[tuple[str, str], int]:
    """``(provider, model)`` -> context window, for each row of a listing that has one.

    Keyed by both columns because one id served by two providers can have two
    windows. A row whose context cell does not parse is left out rather than
    guessed at.
    """
    windows: dict[tuple[str, str], int] = {}
    for provider, model, rest in listing_rows(text):
        tokens = parse_context_tokens(rest[0]) if rest else None
        if tokens is not None:
            windows[(provider, model)] = tokens
    return windows


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
    if provider_name:
        parts = [f"pi's {provider_name} provider does not serve model {named!r}"]
    else:
        parts = [f"no pi provider serves model {named!r}"]
    close = closest_listed(named, listed)
    if close:
        parts.append("Closest listed ids: " + ", ".join(close))
    parts.append(
        "Set --model, the phase's models.<tier>, WORKBENCH_AI_MODEL or "
        "WORKBENCH_AI_<PHASE>_MODEL to an id listed by `pi --list-models`"
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


def default_provider() -> str | None:
    """Pi's defaultProvider, or None when its settings name none or cannot be read.

    None is not a guess at a provider: with none set, pi resolves a model id
    across every provider it loaded, and a catalogue check does the same.
    """
    try:
        data = json.loads(_pi_settings_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("defaultProvider")
    return value if isinstance(value, str) and value else None


def effective_provider(explicit: str | None) -> str | None:
    """The provider a spawn will query: the flag, else Pi's default, else None (any)."""
    return explicit or default_provider()


@functools.cache
def catalogue_text(pi: tuple[str, ...]) -> str:
    """Stdout of ``<pi> --list-models``. Raises `CatalogueUnreadable` with the reason when there is none.

    ``pi`` is the argv `PiBinary.spawn` starts the chosen binary with, so the
    catalogue a model is checked against is the one the spawn will run with —
    two pi versions serve different model sets. This is the one place the workbench
    lists models; the review budget reads its context windows from here too.

    Only a listing is cached, per argv, for the life of the process. A failure
    raises, and `functools.cache` stores nothing for a call that raised, so the
    next caller lists again — a listing that timed out under load must not turn
    off every later model check, nor fail the budget of a model the catalogue
    would have named.

    Only the argv is taken, not the spawn's environment: the listing runs in
    this process's environment, and the one variable a direct release spawn
    adds (``PI_MANAGED_INSTALL_ROOT``) governs pi's self-update, not its models.
    """
    argv = [*pi, "--list-models"]
    try:
        result = core.proc.run(argv, timeout=core.timeouts.LAUNCH)
    except OSError as exc:
        # Not just FileNotFoundError: a `pi` present on disk but not executable
        # raises PermissionError, and that is unreadable in the same way.
        raise CatalogueUnreadable(f"cannot run {' '.join(argv)}: {exc}") from exc
    if not result.ok:
        raise CatalogueUnreadable(
            core.proc.failure_message(f"{' '.join(argv)} failed", result)
        )
    if not listing_rows(result.stdout):
        raise CatalogueUnreadable(f"{' '.join(argv)} printed no model table")
    return result.stdout


def catalogue_for(cwd: str | Path) -> str:
    """The catalogue of the pi that would run in *cwd*. Raises `CatalogueUnreadable` when there is none.

    For optional lookups, such as the review budget, which fall back and quote
    the reason. A pi that cannot be chosen is the reason here; the spawn that
    needs one raises `PiUnresolvable` itself.
    """
    try:
        binary = agent.pi_binary.resolve(cwd)
    except agent.pi_binary.PiUnresolvable as exc:
        raise CatalogueUnreadable(f"no pi could be chosen: {exc}") from exc
    return catalogue_text(binary.spawn(os.environ).argv)


def listed_models(provider: str | None, *, pi: tuple[str, ...]) -> frozenset[str]:
    """Ids *pi* lists under *provider* right now. Empty when the catalogue cannot be read."""
    try:
        text = catalogue_text(pi)
    except CatalogueUnreadable:
        # Fail open: an unreadable catalogue is not a provider that serves
        # nothing. Refusing here would make an optional lookup a hard dependency
        # on `pi`; the spawn's own stderr is the remaining signal.
        return frozenset()
    return parse_listed_ids(text, effective_provider(provider))


class ModelNotConfigured(core.errors.OperatorError):
    """A provider is set and no layer names a model for it.

    Pi refuses ``--provider`` without ``--model`` and has no per-provider
    default for extension providers, so this is a configuration error, raised
    before a spawn with the keys that fix it.
    """

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"Pi needs a model when a provider is set (provider {provider!r}). "
            "Set the phase's models.<tier>, WORKBENCH_AI_MODEL or "
            "WORKBENCH_AI_<PHASE>_MODEL, or pass --model."
        )


def require_model_with_provider(provider: str | None, model: str | None) -> None:
    """Raise ``ModelNotConfigured`` for a provider with no model."""
    if provider and not model:
        raise ModelNotConfigured(provider)


def check_targets(models: PhaseModels, trail, cwd: str) -> bool:
    """False when a phase's model is not listed under the provider it names.

    ``models`` maps each model reference to the phases requesting it. Each is
    checked against the catalogue of the provider it names, or of Pi's default
    provider when it names none, so a phase on ``xai/grok-4.6`` and one on a
    Claude id are each judged by their own provider. Every failure is reported
    before the run stops, so one pass names every model to fix.

    Fails open — True — when no pi can be chosen or its catalogue cannot be read:
    the spawn that needs it reports the reason, and refusing here would turn an
    optional lookup into a hard dependency. An unreadable catalogue is recorded on
    *trail*, so the skipped check and its reason are not silent.
    """
    try:
        pi = agent.pi_binary.resolve(cwd).spawn(os.environ).argv
    except agent.pi_binary.PiUnresolvable:
        return True
    try:
        catalogue_text(pi)
    except CatalogueUnreadable as exc:
        trail.warn(
            "model_preflight",
            f"models not checked against pi's catalogue: {exc}",
        )
        return True
    ok = True
    for ref, phases in sorted(models.items(), key=lambda item: item[0].spec()):
        try:
            require_listed_model(ref.id, ref.provider, pi=pi)
        except ModelNotServed as exc:
            ok = False
            named = ", ".join(str(phase) for phase in phases)
            abort_preflight(trail, f"{exc} Phases: {named}.")
    return ok


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
