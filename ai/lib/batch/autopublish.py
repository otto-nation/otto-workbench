"""Run a publish child and, for `--auto-publish ff`, a fast-forward-only push.

`default_runner` writes a `$ <argv>` header before each child's output so a
failure's reason is read from that command alone (`last_command`).

`fast_forward` is the only remote write a schema-2 run makes. It reuses
`publish.plan` with closeout stripped to unlanded-only, then runs
`commands[0]` only when that argv is a plain `pr push --expect`. It never
force-pushes (`pr rebase --push-only`), never posts a closeout, and never
files an issue.

| Result | When |
|---|---|
| `skipped` | a drafted step is not `done`, the item has findings, local work no step vouched for, or the plan is not a clean fast-forward |
| `pushed` | `pr push --expect` exited 0; `remote_sha`/`published_sha` become the post-push local tip |
| `failed` | the push exited non-zero; `findings.record` of the classified failure (`push_refused` or `failed_unknown`) |
"""

# doc-group: batch

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path

import batch.findings
import batch.outcomes
import batch.publish
import batch.store
import core.timeouts
from batch.model import DecisionKind, Item, Run, StepStatus
from pr.comments_fix import CloseoutDebt

# What default_runner writes before each child command's output in a publish log.
COMMAND_HEADER = "$ "


def default_runner(argv: list[str], log_path: Path | None = None, *,
                   env: Mapping[str, str] | None = None) -> int:
    """Run one publish command as a child.

    With *log_path*, a `$ <argv>` header and everything it prints are appended
    there. *env* is the child's environment, this process's when None.
    """
    if log_path is None:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                              start_new_session=True, timeout=core.timeouts.UNBOUNDED,
                              env=env).returncode
    with log_path.open("a") as log:
        log.write(f"{COMMAND_HEADER}{shlex.join(argv)}\n")
        log.flush()
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              timeout=core.timeouts.UNBOUNDED, env=env).returncode


def last_command(lines: list[str]) -> list[str]:
    """The lines the failing command printed: those after the last `$ ` header.

    `default_runner` writes a `$ <argv>` header before each child command, so
    an earlier command that succeeded cannot lend the failure its reason. A
    log with no header is read whole.
    """
    starts = [i for i, line in enumerate(lines) if line.startswith(COMMAND_HEADER)]
    return lines[starts[-1] + 1:] if starts else lines


class FastForward(StrEnum):
    SKIPPED = "skipped"
    PUSHED = "pushed"
    FAILED = "failed"


def fast_forward(run: Run, item: Item, debt: CloseoutDebt, *, unpublished: bool,
                 pr_bin: str, tree: Callable[[Item], batch.publish.TreeState],
                 runner: Callable[..., int], emit: Callable[..., None]) -> FastForward:
    """Push a clean fast-forward at item close, or skip / record a finding."""
    drafted_done = all(r.status is StepStatus.DONE for r in item.steps if r.drafted)
    if not drafted_done or item.findings or unpublished:
        return FastForward.SKIPPED
    before = tree(item)
    plan = batch.publish.plan(
        item, pr_bin, before, closeout=CloseoutDebt(unlanded=debt.unlanded),
        no_verify=run.no_verify)
    if not (plan.ok and plan.pushes and plan.commands[0][1] == "push"):
        return FastForward.SKIPPED
    log = batch.store.attempt_log_path(run.id, item.repo, item.pr, "publish")
    if runner(plan.commands[0], log_path=log) != 0:
        lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
        found = batch.outcomes.classify_failure(last_command(lines))
        batch.findings.record(
            run, item, "publish", DecisionKind.FAILED,
            {"reason": found.reason.value, "detail": found.detail, "log": str(log)},
            emit=emit)
        return FastForward.FAILED
    item.remote_sha = item.published_sha = tree(item).local or before.local
    return FastForward.PUSHED
