"""AI backend abstraction layer.

Dispatches preflight(), prompt(), invoke_agent(), and invoke_fix() to the
correct backend (Claude Code CLI or Pi CLI) based on AI_BACKEND env var.
"""

from __future__ import annotations

import os
import shutil
import types
from enum import StrEnum

ENV_AI_BACKEND = "AI_BACKEND"


class Backend(StrEnum):
    """Which CLI serves AI calls, selected by the AI_BACKEND env var."""

    CLAUDE = "claude"
    PI = "pi"


def _backend() -> Backend:
    """The selected backend; an unrecognised AI_BACKEND falls back to Claude."""
    try:
        return Backend(os.environ.get(ENV_AI_BACKEND, Backend.CLAUDE))
    except ValueError:
        return Backend.CLAUDE


def _get_module() -> types.ModuleType:
    if _backend() is Backend.PI:
        import ai_backend_pi as mod
    else:
        import ai_backend_claude as mod
    return mod


def preflight(models: dict[str, list[str]], trail) -> bool:
    """Verify the backend can serve the requested models before any run.

    ``models`` maps each resolved model id to the phases requesting it.
    Returns False to abort — backends fail open when they cannot tell.
    """
    return _get_module().preflight(models, trail)


def prompt(text: str, *, model: str | None = None) -> tuple[str, int]:
    """Stateless text-in/text-out. Returns (response_text, exit_code)."""
    return _get_module().prompt(text, model=model)


def invoke_agent(
    prompt: str, session_log: str, *,
    add_dirs: list[str],
    agent: str | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    provider: str | None = None,
    label: str = "",
) -> int:
    """Full agent with tool use and JSONL streaming. Returns exit code."""
    return _get_module().invoke_agent(
        prompt, session_log,
        add_dirs=add_dirs, agent=agent,
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
        provider=provider, label=label,
    )


def invoke_fix(
    prompt: str, *,
    session_log: str = "",
    add_dirs: list[str],
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    provider: str | None = None,
) -> int:
    """Agent with workspace write access, raw output echoed. Returns exit code."""
    return _get_module().invoke_fix(
        prompt, session_log=session_log, add_dirs=add_dirs,
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
        provider=provider,
    )


def is_available() -> bool:
    """Check if the selected backend binary exists on PATH."""
    return shutil.which(_backend()) is not None
