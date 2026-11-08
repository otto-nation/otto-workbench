"""The compact document `pr batch status` and a run's last line print.

One builder: `build` is the whole report.

| Section | Content |
|---|---|
| `run` | `id`, `status`, `active` (a scheduler is driving it), `exit_hint`, `hint`, `pending_requests` (requests waiting in its inbox), `failed_requests` (`name` and `error` of each request a drain rejected) |
| `counts` | items by status |
| `items` | non-terminal items only: worktree, branch, `remote_sha`, `pre_rebase_head`, `stacked_on`, `base_ref`, step statuses |
| `github` | `pr batch status` only: what GitHub says about the run's repos now (see `batch.live`) |
| `sessions` | `pr batch report` only: per-PR findings and session commands (`batch.session_report`) |

`exit_hint` is what `run`/`resume` exit with for the run as it stands: 0 once
it is `done` or `cancelled`, 1 when it stopped without settling (interrupted —
`pr batch resume` it) or when a stored waiting v1 run has not been resumed, and
null while a scheduler holds the run.
"""

# doc-group: batch

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import batch.live
from batch.model import Item, Run, RunStatus
from batch.store import FailedRequest

SCHEMA_VERSION = 2
EXIT_OK = 0
EXIT_FAILED = 1
_STATUS_EXIT = {RunStatus.DONE: EXIT_OK, RunStatus.CANCELLED: EXIT_OK}


def exit_code(status: RunStatus) -> int:
    """What `run`/`resume` exit with for a run that stopped at *status*."""
    return _STATUS_EXIT.get(status, EXIT_FAILED)


def _github_hint(github: dict) -> str:
    counts = [(len(github["owed"]), "with work owed"),
              (len(github["untracked"]), "this run never saw"),
              (len(github["moved"]), "pushed since it ran")]
    said = "; ".join(f"{n} open PR{'' if n == 1 else 's'} {what}" for n, what in counts if n)
    return f"GitHub shows {said} — see `github`, and start a new `pr batch run`"


def _hint(run: Run, active: bool, github: dict | None = None) -> str:
    if active:
        return "a scheduler is driving this run; wait for its run_summary line"
    if run.status in (RunStatus.DONE, RunStatus.CANCELLED):
        if batch.live.owes(github):
            return f"the run is {run.status.value}; {_github_hint(github)}"
        return f"the run is {run.status.value}; nothing is left to answer"
    return (f"continue with `pr batch resume {run.id}`; its decisions become "
            "findings (`pr batch report`)")


def _item_view(item: Item) -> dict:
    return {"pr": item.key, "status": item.status.value, "worktree": item.worktree,
            "branch": item.branch, "remote_sha": item.remote_sha,
            "pre_rebase_head": item.pre_rebase_head, "stacked_on": item.stacked_on,
            "base_ref": item.base_ref,
            "steps": {r.step.value: r.status.value for r in item.steps}}


def build(run: Run, *, active: bool,
          pending_requests: int = 0,
          failed_requests: Sequence[FailedRequest] = (),
          github: dict | None = None,
          sessions: list | None = None) -> dict:
    """The whole report. *active* is whether a scheduler is driving the run.

    *github* is the `batch.live` section; None leaves the key out, which is
    what a `run_summary` event carries.
    *sessions* is the per-PR session list; None leaves the key out.

    *pending_requests* is how many requests wait in the run's inbox, and
    *failed_requests* lists the ones a drain rejected. Both are reported under
    `run`.
    """
    report = {
        "schema_version": SCHEMA_VERSION,
        "run": {"id": run.id, "status": run.status.value, "active": active,
                "exit_hint": None if active else exit_code(run.status),
                "hint": _hint(run, active, github),
                "pending_requests": pending_requests,
                "failed_requests": [{"name": f.name, "error": f.error}
                                    for f in failed_requests]},
        "counts": {"items": dict(Counter(i.status.value for i in run.items))},
        "items": [_item_view(i) for i in run.items if not i.terminal],
    }
    if github is not None:
        report["github"] = github
    if sessions is not None:
        report["sessions"] = sessions
    return report
