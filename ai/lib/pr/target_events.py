"""The history of one PR target: `<target-dir>/events.jsonl`.

`pr comments` and `pr ci` (`cli.review_threads.main`, `cli.ci_check.main`,
which the `pr batch` CI and comments steps also run) file a `step_started` and
a `step_finished` (`step` `comments` or `ci`, `status` `done` or `failed`,
`exit_code`, None when the command raised) for every run that reaches its
trail, so hand runs, herdr runs and batch steps land in one history. The target
note command files a `decision`. The envelope is `core.run_events.record`'s;
`run` is the writing command's trail invocation and `item` the PR key
(`pr.needs_person.pr_key`). A branch with no PR files nothing, and neither does
a target directory that does not exist: the target lock creates it, and a
history never resurrects a pruned target. An append that fails warns once per
target on stderr and never fails the command.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import core.run_events
import pr.context
import pr.needs_person

KINDS = frozenset({"step_started", "step_finished", "decision"})

# Targets whose history this process already failed to append to, so the
# warning is printed once per target rather than once per event.
_warned: set[str] = set()


def record(kind: str, *, run: str, item: str, **fields) -> dict:
    """A target history line: the core envelope, then `run`, `item` and *fields*."""
    if kind not in KINDS:
        raise ValueError(f"unknown target event: {kind}")
    return core.run_events.record(kind, core.run_events.now_iso(), run=run, item=item, **fields)


@dataclass(frozen=True)
class StepHistory:
    """Where one command run files its step; inert when `target_dir` is None."""

    target_dir: Path | None
    run: str = ""
    item: str = ""
    step: str = ""

    def started(self) -> None:
        self._file("step_started")

    def finished(self, exit_code: int | None) -> None:
        self._file("step_finished", status="done" if exit_code == 0 else "failed",
                   exit_code=exit_code)

    def _file(self, kind: str, **fields) -> None:
        # The target lock creates the directory before any command writes;
        # one that is missing was never claimed or was pruned mid-run.
        if self.target_dir is None or not self.target_dir.is_dir():
            return
        core.run_events.append_or_warn(
            self.target_dir,
            record(kind, run=self.run, item=self.item, step=self.step, **fields),
            warned=_warned, key=str(self.target_dir),
            subject=f"pr: could not record events for {self.item}",
            consequence="the command continues")


NO_HISTORY = StepHistory(None)


def step_history(ctx: pr.context.ResolvedContext, run: str, step: str) -> StepHistory:
    """The history *step* of this run files to, or `NO_HISTORY` for a branch with no PR."""
    if ctx.pr_number is None:
        return NO_HISTORY
    return StepHistory(Path(ctx.target_dir), run, pr.needs_person.pr_key(ctx.repo, ctx.pr_number),
                       step)
