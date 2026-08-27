"""Shared constants and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants. The
vocabulary they name those constants alongside — severities, findings, the job
a run threads through — is `review_types`', and where a review's files sit is
`review_paths`', so a consumer that only needs a noun takes neither the
artifact layout nor the vocabulary with it.
"""

# doc-group: findings

from __future__ import annotations

import argparse
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from agent_registry import PHASES, REVIEW_PHASES
from agent_types import Phase


# ── Sections ─────────────────────────────────────────────────────────────────

SECTION_FILE_TRIAGE = "File Triage"
SECTION_STATIC_ANALYSIS = "Static Analysis"

# A re-review's ledger: one line per prior finding, saying whether the change
# resolved it. Reconciliation reads it to tell a finding the re-review dropped
# on purpose from one it lost track of; it is stripped before the review is
# posted, since its finding IDs number the prior review, not this one.
SECTION_PRIOR_FINDINGS = "Prior findings"

PIPELINE_MULTI = "multi"
PIPELINE_SINGLE = "single"


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"


_EnumT = TypeVar("_EnumT", bound=StrEnum)


def enum_arg(enum_cls: type[_EnumT]) -> Callable[[str], _EnumT]:
    """An argparse ``type`` that converts to ``enum_cls`` by value.

    Passing the enum class directly as ``type=`` drops the valid-value list from
    the error message, because a failed conversion never reaches argparse's own
    ``choices`` check — so the message is reproduced here.
    """
    def parse(value: str) -> _EnumT:
        try:
            return enum_cls(value)
        except ValueError:
            choices = ", ".join(repr(str(m)) for m in enum_cls)
            raise argparse.ArgumentTypeError(
                f"invalid choice: {value!r} (choose from {choices})"
            ) from None

    return parse


# ── Switching a phase off from the command line ──────────────────────────────
#
# `claude-review` and `review-orchestrate` both offer the flags and one forwards
# them to the other, so all three sides are generated from the same registry
# read: a phase declared `optional` gets its flag, its parse and its argv entry
# at once, and nothing can offer a flag the pipeline has no path around.

def _switchable() -> tuple[Phase, ...]:
    return tuple(p for p in REVIEW_PHASES if PHASES[p].optional)


def add_phase_skip_flags(parser: argparse.ArgumentParser) -> None:
    """Add a ``--no-<phase>`` for every review phase that may be switched off."""
    for phase in _switchable():
        parser.add_argument(
            f"--no-{phase}", action="store_true",
            help=f"Skip the {PHASES[phase].label.lower()} phase",
        )


def phase_skips(args: argparse.Namespace) -> frozenset[Phase]:
    """The phases ``--no-<phase>`` switched off on this command line."""
    return frozenset(p for p in _switchable() if getattr(args, f"no_{p}", False))


def phase_skip_argv(skips: frozenset[Phase]) -> list[str]:
    """``skips`` as the flags that reproduce it on a child process's argv."""
    return [f"--no-{p}" for p in _switchable() if p in skips]


# ── Shared prompt blocks ─────────────────────────────────────────────────────
#
# Owned here rather than hand-copied into each template: every template that
# writes an output file or works in a worktree renders the same block.

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


# ── Log preservation for retries ─────────────────────────────────────────────


def preserve_log(path: str) -> str:
    """Read session log content before a retry that will overwrite it."""
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def restore_preserved(path: str, prior: str) -> None:
    """Prepend prior log content so both attempts' result records are preserved."""
    if not prior:
        return
    try:
        current = Path(path).read_text()
    except OSError:
        current = ""
    Path(path).write_text(prior + current)
