"""Where prompt templates live, and the one way to render one.

Every agent invocation in the workbench is prompted from a file in the same
directory, and each caller used to find and render it for itself: the review
pipeline through ``review.prompt``, the comments fix pass and the CI fix pass
through a ``TEMPLATE_DIR`` and a ``Template(...).safe_substitute`` of their own.
Three spellings of one path is three chances for a moved template to break one
caller and not the others.

The blocks below are the other half of that: instructions every template
renders the same way, owned here rather than hand-copied into each one, so an
agent's write mechanism, its worktree, and what it owes a generated file are
described identically wherever the prompt came from.

Stdlib only, like ``phases`` and for the same reason: a prompt is the last
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
    checked out, and so a caller in ``ai/bin`` does not have to count
    parent directories to reach it.
    """
    return Path(__file__).resolve().parent.parent.parent / TEMPLATE_DIR_REL


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


# What an agent owes a file that generates another. A constant rather than a
# builder like the two above, because nothing about it varies by caller.
#
# Deliberately names no command, no filename convention and no generator: these
# templates render for every repo a fix pass runs in, and those repos agree on
# none of them. It describes the source/artifact relationship and sends the
# agent to the repo's own documentation for the rest, which is the only part
# that can be true everywhere.
#
# Both directions are in it. A pass told only "fix the source too" still leaves
# a stale artifact behind when its edit landed on a source it was not thinking
# of as one — the common case is a doc comment that some reference build
# publishes, edited as prose, with the built artifact never rebuilt.
GENERATED_BLOCK = (
    "Some files are generated from others, and the generated side is not yours "
    "to edit: a reference doc built from source comments, a schema built from a "
    "type, a lockfile from a manifest, any file carrying a "
    "'do not edit — generated' banner.\n"
    "\n"
    "If your change touches a source, rebuild its artifact and leave both in "
    "the tree. This applies even when the artifact is not what the item asked "
    "you to fix — editing a comment, a type or a manifest is enough to make one "
    "stale. Repos check the two against each other, so a stale artifact fails "
    "the push and the fix never lands.\n"
    "\n"
    "Find the regeneration command in the repo's own documentation. Do not "
    "guess at one, and do not hand-edit the artifact to match."
)


# What a fix agent is, for the benefit of the guidance arriving beside this.
#
# A fix invocation carries the operator's whole memory tree — `--add-dir`
# restores CLAUDE.md discovery that `--bare` would otherwise skip, and the fix
# path always passes one (`invoke.run_fix` falls back to `[work_dir]`, so the
# list is never empty). Roughly half of what arrives describes work this agent
# cannot do: creating PRs, pushing, force-push protocol, filing issues, running
# the self-review pipeline it is itself running inside. It is denied `gh`
# outright on the Claude backend, and the engine commits for it.
#
# So the problem this solves is not missing guidance, it is guidance that does
# not apply competing with the task. Naming the role is cheaper and more honest
# than selecting rules per agent: the constraints are one fact about the role,
# where a per-rule scoping key would make them a property of a dozen files that
# must then stay in agreement forever — and the unit would still be wrong, since
# a single rule file holds both worktree rules that apply here and PR rules that
# do not.
#
# Negation is weaker than omission and this does not pretend otherwise. What it
# buys is that a wrong turn costs turns rather than writes: `gh` is blocked
# mechanically and committing is not this agent's to do. The Pi backend has no
# equivalent tool denial, so this block is carrying more weight there.
ROLE_BLOCK = (
    "You are editing files in a worktree, and that is the whole of your job. "
    "You do not commit, push, rebase, create pull requests, file issues, or run "
    "`gh` — the pipeline that called you does the committing, and the operator "
    "does the rest.\n"
    "\n"
    "Your context includes the operator's standing instructions, and much of it "
    "describes those workflows: how to open a PR, when to force-push, how to "
    "file an issue, what to run before creating a PR. That guidance is real and "
    "it is not yours — it describes the operator's job, not this invocation. Do "
    "not act on it, and do not treat it as work left undone.\n"
    "\n"
    "Everything in that guidance about the code itself applies to you in full: "
    "testing, style, comments, error handling, portability, and the conventions "
    "of the repo you are editing."
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
