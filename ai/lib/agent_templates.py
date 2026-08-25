"""Where prompt templates live, and the one way to render one.

Every agent invocation in the workbench is prompted from a file in the same
directory, and each caller used to find and render it for itself: the review
pipeline through ``review_prompt``, the comments fix pass and the CI fix pass
through a ``TEMPLATE_DIR`` and a ``Template(...).safe_substitute`` of their own.
Three spellings of one path is three chances for a moved template to break one
caller and not the others.

Stdlib only, like ``agent_types`` and for the same reason: a prompt is the last
thing that should need the PR state machine to render.
"""

# doc-group: pipeline

from __future__ import annotations

from pathlib import Path
from string import Template

# The directory every template is read from, relative to the repo's `ai/`. Named
# for the review pipeline that first owned it; the fix passes of the comments
# and CI entry points render out of it too.
TEMPLATE_DIR_REL = Path("lib") / "review-templates"


def template_dir() -> Path:
    """The absolute path to the template directory.

    Derived from this module's own location so it holds wherever the repo is
    checked out, and so a caller in ``ai/claude/bin`` does not have to count
    parent directories to reach it.
    """
    return Path(__file__).resolve().parent.parent / TEMPLATE_DIR_REL


def render(name: str, **kwargs) -> str:
    """Render the named template with ``kwargs`` substituted into it.

    ``safe_substitute``, so a placeholder no caller filled survives into the
    prompt rather than raising. Templates carry shell and JSON snippets a
    reviewer is meant to read literally, and failing the whole run over one is
    worse than an agent seeing a ``$name`` it can ignore.
    """
    return Template((template_dir() / name).read_text()).safe_substitute(**kwargs)
