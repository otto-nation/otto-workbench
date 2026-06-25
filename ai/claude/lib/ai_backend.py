"""AI backend abstraction layer.

Dispatches prompt(), invoke_agent(), and invoke_fix() to the correct
backend (Claude Code CLI or Pi CLI) based on AI_BACKEND env var.
"""

from __future__ import annotations

import os
import shutil

ENV_AI_BACKEND = "AI_BACKEND"
BACKEND_CLAUDE = "claude"
BACKEND_PI = "pi"


def _backend() -> str:
    return os.environ.get(ENV_AI_BACKEND, BACKEND_CLAUDE)


def _get_module():
    backend = _backend()
    if backend == BACKEND_PI:
        import ai_backend_pi as mod
    else:
        import ai_backend_claude as mod
    return mod


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
    label: str = "",
) -> int:
    """Full agent with tool use and JSONL streaming. Returns exit code."""
    return _get_module().invoke_agent(
        prompt, session_log,
        add_dirs=add_dirs, agent=agent,
        max_turns=max_turns, max_budget=max_budget,
        model=model, label=label,
    )


def invoke_fix(
    prompt: str, *,
    add_dirs: list[str],
    max_turns: int | None = None,
    model: str | None = None,
) -> int:
    """Agent with workspace write access, raw output echoed. Returns exit code."""
    return _get_module().invoke_fix(
        prompt, add_dirs=add_dirs, max_turns=max_turns, model=model,
    )


def is_available() -> bool:
    """Check if the selected backend binary exists on PATH."""
    backend = _backend()
    if backend == BACKEND_PI:
        return shutil.which("pi") is not None
    return shutil.which("claude") is not None
