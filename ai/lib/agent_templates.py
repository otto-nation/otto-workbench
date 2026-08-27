"""Where prompt templates live, and the one way to render one.

Every agent invocation in the workbench is prompted from a file in the same
directory, and each caller used to find and render it for itself: the review
pipeline through ``review_prompt``, the comments fix pass and the CI fix pass
through a ``TEMPLATE_DIR`` and a ``Template(...).safe_substitute`` of their own.
Three spellings of one path is three chances for a moved template to break one
caller and not the others.

The two blocks below are the other half of that: instructions every template
renders the same way, owned here rather than hand-copied into each one, so an
agent's write mechanism and its worktree are described identically wherever the
prompt came from.

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


def build_output_block(output_path: str, *, stdout_warning: bool = False) -> str:
    """How an agent saves its output file.

    Agents run under `claude --bare`, which exposes only Bash, Edit and Read —
    there is no Write tool. The pipeline pre-creates the output file empty, so
    an Edit with an empty old_string inserts the whole document in one call.
    """
    stdout_line = (
        "\nDo NOT print the output to stdout — it only counts if it lands in the file."
        if stdout_warning else ""
    )
    return (
        f"Write your output to: {output_path}\n"
        "The file already exists and is empty — Read it, then use the Edit tool "
        "with an empty `old_string` to insert the complete contents. That Read "
        "plus one Edit is the entire write; do not build the file up in pieces.\n"
        "The Write tool is NOT available in this environment — do not attempt it, "
        "and do not fall back to Bash (`cat`, heredoc, python). Do NOT create "
        f"directories or empty files.{stdout_line}"
    )


def build_worktree_block(wt_path: str) -> str:
    """Where the branch is checked out and how to address it.

    Like `build_output_block`, this is the body only — the template owns the
    `## Worktree` heading above the slot.
    """
    return (
        f"Branch checked out at: {wt_path}\n"
        "\n"
        "All file reads and git commands MUST use this path directly "
        f'(e.g. `git -C "{wt_path}" diff`).\n'
        "Never use command substitution `$(...)` to discover the worktree path — "
        "it triggers permission prompts."
    )
