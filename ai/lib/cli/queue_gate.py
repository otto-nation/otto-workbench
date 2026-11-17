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
from otto_queue.model import ACTION_ENV, ADOPT_ENV, LEASE_FD_ENV, SUBJECT_ENV, EntryClass

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
         no_queue: bool = False, pr_ref: str | None = None,
         repo_dir: str | None = None) -> int | None:
    """Wait for a slot and re-exec *reexec* under it, or return None to continue.

    None when there is no class, *no_queue* is set, `WORKBENCH_PR_NO_QUEUE` is
    1, this process already holds a ticket (`OTTO_QUEUE_ENTRY`), or this is the
    re-exec'd child (`_WB_PR_QUEUED`). An int is the ticket's exit status.
    Subject is `owner/repo#N` only when a number is known (or `OTTO_QUEUE_SUBJECT`
    is set); action is the `pr` subcommand then, or `OTTO_QUEUE_ACTION`.
    When `OTTO_QUEUE_ADOPT` and `OTTO_QUEUE_LEASE_FD` are set, this process runs
    under that entry instead of recording its own; a refused adoption falls
    through to a new ticket. Both variables are removed from the environment
    before any re-exec.
    """
    # Pop before any return or re-exec so a grandchild never inherits the fd.
    adopt_raw = os.environ.pop(ADOPT_ENV, None)
    fd_raw = os.environ.pop(LEASE_FD_ENV, None)
    import otto_queue.ticket
    if (entry_class is None or no_queue or os.environ.get(NO_QUEUE_ENV) == "1"
            or os.environ.get(QUEUED_MARKER)
            or os.environ.get(otto_queue.ticket.ENTRY_ENV)):
        _close_fd(fd_raw)
        return None
    # This process only waits and then exits, so mutating its environment is
    # safe: the marker is how the re-exec'd child knows not to gate again.
    os.environ[QUEUED_MARKER] = "1"
    import cli.otto_queue
    subject = _subject(pr_ref=pr_ref, repo_dir=repo_dir)
    request = otto_queue.ticket.TicketRequest(
        argv=tuple(reexec), cwd=os.getcwd(), entry_class=entry_class,
        label=label, wait_s=_wait_s(), subject=subject,
        action=_action(command, subject))
    policy = cli.otto_queue.machine_policy()
    if adopt_raw is not None and fd_raw is not None:
        try:
            return otto_queue.ticket.adopt(
                int(adopt_raw), int(fd_raw), request, policy).exit_status
        except (ValueError, otto_queue.ticket.AdoptRefused):
            _close_fd(fd_raw)
    else:
        _close_fd(fd_raw)
    return otto_queue.ticket.run_ticket(request, policy).exit_status


def _close_fd(fd_raw: str | None) -> None:
    """Close an inherited lease fd that was not adopted, so its flock is released."""
    if fd_raw is None:
        return
    try:
        os.close(int(fd_raw))
    except (ValueError, OSError):
        pass


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


def _subject(*, pr_ref: str | None, repo_dir: str | None) -> str | None:
    """`owner/repo#N` when a number is known, or `OTTO_QUEUE_SUBJECT`."""
    override = os.environ.get(SUBJECT_ENV) or None
    if override:
        return override
    repo = _repo_label(repo_dir)
    number = _pr_number(pr_ref)
    if repo and number:
        return f"{repo}#{number}"
    return None


def _action(command: str, subject: str | None) -> str | None:
    """The `pr` subcommand when *subject* is set, or `OTTO_QUEUE_ACTION`."""
    override = os.environ.get(ACTION_ENV) or None
    if override:
        return override
    return command if subject is not None else None


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
