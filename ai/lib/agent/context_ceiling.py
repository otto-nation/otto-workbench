"""``context.compact_at`` as each harness's own compaction settings read it.

The config names one ceiling in tokens; each harness spells the point it
compacts at differently, and this module is the one translation, so the two
syncs and the pipeline cannot read the ceiling two ways.

- Claude Code compacts when the context reaches its auto-compact window
  (``autoCompactWindow`` in settings, ``--autocompact`` on the CLI), which it
  takes from 100k to 1M tokens and caps at the model's window itself.
- Pi compacts when ``contextTokens > contextWindow - reserveTokens``, set per
  model under ``compaction.modelOverrides["provider/id"]``. A reserve of
  ``window - compact_at`` makes that ``contextTokens > compact_at``. The reserve
  decides only when compaction starts: Pi sizes the summary request from the
  model's output limit, not from it, so a reserve of 800k on a 1M model asks
  for no 800k-token reply.
- The review budget plans a prompt against the ceiling the selected backend
  actually compacts at, when that is under the model's window.

The ceiling is machine-wide, and ``machine_ceiling`` is the one place it is
read. Both harnesses keep it in machine-wide settings, and Pi takes no
per-run override, so a repo-level value would be one the review budget
planned against and Pi never applied.

Not: writing the settings files (``ai/claude/steps.sh``, ``ai/pi/steps.sh``),
reading the catalogue (``agent.pi_models``).
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

from agent.pi_models import listed_windows
from config.workbench_config import load_config_or_default
from core.phases import Backend

# The auto-compact windows `claude --autocompact` and `autoCompactWindow` take.
CLAUDE_WINDOW_MIN = 100_000
CLAUDE_WINDOW_MAX = 1_000_000


@dataclass(frozen=True)
class ClaudeWindow:
    """The auto-compact window Claude Code runs at, and why it differs from the ceiling."""

    window: int | None = None
    note: str | None = None


def claude_window(compact_at: int | None) -> ClaudeWindow:
    """The window *compact_at* runs at on Claude Code; None when it is unset.

    A ceiling outside the CLI's range runs at the nearer end, with a note
    saying so: the CLI would otherwise reject the flag, or ignore the setting.
    """
    if compact_at is None:
        return ClaudeWindow()
    window = min(max(compact_at, CLAUDE_WINDOW_MIN), CLAUDE_WINDOW_MAX)
    if window == compact_at:
        return ClaudeWindow(window=window)
    return ClaudeWindow(window=window, note=(
        f"context.compact_at: {compact_at:,} is outside the {CLAUDE_WINDOW_MIN:,}–"
        f"{CLAUDE_WINDOW_MAX:,} tokens Claude Code takes, so it compacts at {window:,}"
    ))


def pi_reserve_tokens(compact_at: int, listing: str) -> dict[str, int | None]:
    """``provider/id`` -> the ``reserveTokens`` that compacts it at *compact_at*.

    Every model the *listing* gives a window, so the caller can remove a value
    as well as write one: None for a model whose window is at or below the
    ceiling, which compacts at Pi's own reserve, near its window.
    """
    return {
        f"{provider}/{model}": window - compact_at if window > compact_at else None
        for (provider, model), window in listed_windows(listing).items()
    }


def machine_ceiling() -> int | None:
    """``context.compact_at`` from the machine-wide config, None when unset.

    Through ``load_config_or_default``, the way a phase's model is read: an
    unreadable file is reported by the commands that act on the config, and
    every reader of the ceiling then agrees on there being none.
    """
    return load_config_or_default(None).context.compact_at


def backend_ceiling(compact_at: int | None, backend: Backend | None) -> int | None:
    """The point *backend* compacts at under *compact_at*: Claude Code's clamped window, else the ceiling."""
    if backend is Backend.PI:
        return compact_at
    return claude_window(compact_at).window


def capped_window(window: int, compact_at: int | None) -> int:
    """The context a conversation on a *window*-token model is allowed to reach."""
    return window if compact_at is None else min(window, compact_at)
