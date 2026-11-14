"""Admit AI `pr` subcommands through an otto-queue ticket before they run.

`gate` returns None to carry on in this process, or the ticket's exit status
when the command ran (or failed to get a slot) under the queue. Help, unqueued
commands, nested tickets, and a re-exec'd child skip it.

`otto_queue.ticket` and `cli.otto_queue` are imported inside `gate` so
`pr --help` does not pay for them.
"""

# doc-group: cli

from __future__ import annotations

import os
from collections.abc import Sequence

import cli.needs
import core.log
import git.client
import pr.target
from cli.registry import COMMANDS, queue_for
from otto_queue.model import EntryClass

NO_QUEUE_ENV = "WORKBENCH_PR_NO_QUEUE"
QUEUED_MARKER = "_WB_PR_QUEUED"

_LABEL_FLAGS = ("--self", "--fix", "--triage", "--recover")


def class_for(command: str, argv: Sequence[str]) -> EntryClass | None:
    """The queue class `command`'s *argv* declares, or None to run unqueued."""
    return queue_for(COMMANDS[command], argv)


def label_for(command: str, argv: Sequence[str], *, pr_ref: str | None,
              branch: str | None, repo_dir: str | None) -> str:
    """A display label: `pr <command>[ <flags>] · <target>`.

    `OTTO_QUEUE_LABEL` wins when set, so `pr batch` can name its steps.
    Repo and branch lookups are best-effort: an exception drops that part.
    """
    import otto_queue.ticket

    override = os.environ.get(otto_queue.ticket.LABEL_ENV)
    if override:
        return override
    flags = [flag for flag in _LABEL_FLAGS if cli.needs.has_flag(argv, flag)]
    head = " ".join(("pr", command, *flags))
    target = _target(pr_ref=pr_ref, branch=branch, repo_dir=repo_dir)
    return f"{head} · {target}" if target else head


def gate(command: str, argv: Sequence[str], *, reexec: Sequence[str],
         entry_class: EntryClass | None, label: str,
         no_queue: bool = False) -> int | None:
    """Wait for a slot and re-exec *reexec* under it, or return None to continue.

    None when there is no class, *no_queue* is set, `WORKBENCH_PR_NO_QUEUE` is
    1, this process already holds a ticket (`OTTO_QUEUE_ENTRY`), or this is the
    re-exec'd child (`_WB_PR_QUEUED`). An int is the ticket's exit status.
    """
    if entry_class is None or no_queue or os.environ.get(NO_QUEUE_ENV) == "1":
        return None
    if os.environ.get(QUEUED_MARKER):
        return None
    import otto_queue.ticket
    if os.environ.get(otto_queue.ticket.ENTRY_ENV):
        return None
    # This process only waits and then exits, so mutating its environment is
    # safe: the marker is how the re-exec'd child knows not to gate again.
    os.environ[QUEUED_MARKER] = "1"
    import cli.otto_queue
    request = otto_queue.ticket.TicketRequest(
        argv=tuple(reexec), cwd=os.getcwd(), entry_class=entry_class,
        label=label, wait_s=_wait_s())
    return otto_queue.ticket.run_ticket(
        request, cli.otto_queue.machine_policy()).exit_status


def _wait_s() -> float:
    import otto_queue.ticket

    raw = os.environ.get(otto_queue.ticket.WAIT_ENV)
    if raw is None:
        return otto_queue.ticket.DEFAULT_WAIT_S
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if value < 0 or value != value:
        core.log.warn(
            f"otto-queue: {otto_queue.ticket.WAIT_ENV}={raw!r} is not a "
            f"non-negative float; waiting {otto_queue.ticket.DEFAULT_WAIT_S:g}s")
        return otto_queue.ticket.DEFAULT_WAIT_S
    return value


def _target(*, pr_ref: str | None, branch: str | None,
            repo_dir: str | None) -> str | None:
    repo = _repo_label(repo_dir)
    number = _pr_number(pr_ref)
    if repo and number:
        return f"{repo}#{number}"
    name = _branch_name(branch, repo_dir)
    if repo and name:
        return f"{repo}@{name}"
    return None


def _repo_label(repo_dir: str | None) -> str | None:
    try:
        identity = pr.target.repo_identity_from_origin(repo_dir)
    except Exception:
        return None
    return identity.label if identity is not None else None


def _branch_name(branch: str | None, repo_dir: str | None) -> str | None:
    if branch:
        return branch
    try:
        name = git.client.current_branch(repo_dir or os.getcwd())
    except Exception:
        return None
    return name or None


def _pr_number(pr_ref: str | None) -> str | None:
    if not pr_ref:
        return None
    text = pr_ref.rstrip("/")
    if text.isdigit():
        return text
    i = len(text)
    while i > 0 and text[i - 1].isdigit():
        i -= 1
    digits = text[i:]
    return digits or None
