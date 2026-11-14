"""How to open a batch run's pi session, and the operator decisions recorded in it.

`recipe(run)` is the one owner of a run session's identity: session id
`batch-<run-id>`, name `batch <owner/repo> <run-id>`, working directory the
run's one checkout, argv `pi --session-id ID --name NAME`, and
`WORKBENCH_RUN_DIR` set to the run directory. `pr batch session` prints or
executes it; every UI asks that command rather than building the argv. The
checkout is `Run.checkouts`, or for a run saved before that field its items'
`repo_dir`. A run over more than one checkout or repo has no run session and
raises `RunSessionError`: herdr starts one run per machine and repo
(`ai/herdr/app/mods/_lib/batch_launch.js` `groupByRepo`), so that is the run a
UI ever asks about. A run with no items is named after its checkout's directory.

`note` appends one `decision` event (`item`, `finding` as `<reason>:<step>`,
`verdict`, `reason`, `commands`) to the run's `events.jsonl`, after checking
the finding is one a session for the item can show
(`batch.session_report.finding_keys`) and the reason is not empty. It takes no
lock and changes no run state: an item stops needing a person only when the
report stops deriving its finding. The latest note per (item, finding) wins
(`batch.events.latest_decisions`).
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import batch.events
import batch.session_report
import batch.store
import core.run_events
from batch.model import Item, Run, Verdict

SCHEMA_VERSION = 1
RUN_DIR_ENV = "WORKBENCH_RUN_DIR"
PI = "pi"


class RunSessionError(ValueError):
    """A run with no run session, or a note naming what the run does not have."""


@dataclass(frozen=True)
class SessionRecipe:
    """How to open one run's session: run *argv* in *cwd* with *env* added."""

    session_id: str
    name: str
    cwd: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "session_id": self.session_id,
                "name": self.name, "cwd": self.cwd, "argv": list(self.argv),
                "env": dict(self.env)}


def _checkout(run: Run) -> str:
    dirs = sorted(set(run.checkouts)) or sorted({i.repo_dir for i in run.items if i.repo_dir})
    if len(dirs) != 1:
        listed = ", ".join(dirs) or "none recorded"
        raise RunSessionError(f"run {run.id} covers {len(dirs)} checkouts ({listed}); "
                              f"a run session needs a run over one")
    return dirs[0]


def _repo(run: Run, checkout: str) -> str:
    repos = sorted({i.repo for i in run.items})
    if len(repos) > 1:
        raise RunSessionError(f"run {run.id} covers {len(repos)} repos ({', '.join(repos)}); "
                              f"a run session needs a run over one")
    return repos[0] if repos else Path(checkout).name


def recipe(run: Run) -> SessionRecipe:
    cwd = _checkout(run)
    session_id = f"batch-{run.id}"
    name = f"batch {_repo(run, cwd)} {run.id}"
    return SessionRecipe(session_id, name, cwd, (PI, "--session-id", session_id, "--name", name),
                         {RUN_DIR_ENV: str(batch.store.run_dir(run.id))})


def note(run: Run, item: Item, finding: str, verdict: Verdict, reason: str,
         commands: Sequence[str] = ()) -> dict:
    """Append one `decision` for *finding* on *item* to *run*'s history; return it."""
    text = reason.strip()
    if not text:
        raise RunSessionError("the reason is empty: say why in a sentence")
    known = batch.session_report.finding_keys(run, item)
    if finding not in known:
        raise RunSessionError(f"{item.key} has no finding {finding} "
                              f"(have {', '.join(sorted(known))})")
    rec = batch.events.record("decision", run=run.id, item=item.key, finding=finding,
                              verdict=verdict.value, reason=text, commands=list(commands))
    core.run_events.append(batch.store.run_dir(run.id), rec)
    return rec
