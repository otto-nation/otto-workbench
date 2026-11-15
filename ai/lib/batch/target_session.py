"""How to open one PR target's pi session, and the operator decisions recorded in it.

`session_id(target_dir)` is `target-` plus the first 16 hex characters of the
SHA-256 of the target directory's name (`<repo-key>-<branch-slug>`). It has a
fixed length, is the same in every process and on every machine, and is always
a valid pi session id. The directory name itself is not one: its slug can be
empty or end in `_`, and it can run past pi's id budget.

`recipe(ctx)` is the one owner of a target session's identity:

- that id;
- the name `target <repo> <branch>`;
- the branch's worktree as the working directory;
- argv `pi --session-id ID --name NAME`;
- env carrying the target directory (`WORKBENCH_TARGET_DIR`) and the expected
  session id (`WORKBENCH_SESSION_ID`), so nothing downstream re-derives either.

A branch with no worktree has no target session and raises
`TargetSessionError`.

`note(...)` appends one `decision` event (`item`, `finding` as
`<reason>:<step>`, `verdict`, `reason`, `commands`; `run` is the writing
command's trail invocation) to the target's `events.jsonl`. Before it does, it
checks that the PR is the target's, that the finding is one the target report
can show (`batch.target_report.FINDING_KEYS`), and that the reason is not
empty. It changes nothing else: a thread is settled with
`pr comments --settle`, and a PR stops waiting only when the report stops
deriving its finding. The latest note per (item, finding) wins
(`core.run_events.latest_decisions`).
"""

# doc-group: batch

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import batch.target_report
import core.run_events
import pr.context
import pr.state
import pr.target_events
from batch.model import Verdict
from batch.run_session import PI, SessionRecipe

TARGET_DIR_ENV = "WORKBENCH_TARGET_DIR"
SESSION_ID_ENV = "WORKBENCH_SESSION_ID"
SESSION_ID_PREFIX = "target-"
_ID_HEX = 16


class TargetSessionError(ValueError):
    """A target with no session, or a note naming what the target does not have."""


def session_id(target_dir: Path) -> str:
    digest = hashlib.sha256(Path(target_dir).name.encode("utf-8")).hexdigest()
    return SESSION_ID_PREFIX + digest[:_ID_HEX]


def recipe(ctx: pr.context.ResolvedContext) -> SessionRecipe:
    if ctx.worktree_root is None:
        raise TargetSessionError(f"no worktree holds {ctx.branch}: a target session runs in "
                                 f"the branch's checkout")
    sid = session_id(ctx.target_dir)
    name = f"target {ctx.repo} {ctx.branch}"
    return SessionRecipe(sid, name, str(ctx.worktree_root), (PI, "--session-id", sid, "--name", name),
                         {TARGET_DIR_ENV: str(ctx.target_dir), SESSION_ID_ENV: sid})


def note(state: pr.state.PRState | None, target_dir: Path, given_pr: str, finding: str,
         verdict: Verdict, reason: str, commands: Sequence[str] = (), *, run: str) -> dict:
    """Append one `decision` for *finding* to the target's history; return it."""
    text = reason.strip()
    if not text:
        raise TargetSessionError("the reason is empty: say why in a sentence")
    key = batch.target_report.pr_key_of(state)
    if key is None:
        raise TargetSessionError(f"no PR is recorded for target {Path(target_dir).name}; "
                                 f"run pr comments or pr ci on its branch first")
    if given_pr not in (key, str(state.identity.pr_number)):
        raise TargetSessionError(f"this target is {key}, not {given_pr}")
    if finding not in batch.target_report.FINDING_KEYS:
        known = ", ".join(sorted(batch.target_report.FINDING_KEYS))
        raise TargetSessionError(f"{key} has no finding {finding} (have {known})")
    rec = pr.target_events.record("decision", run=run, item=key, finding=finding,
                                  verdict=verdict.value, reason=text, commands=list(commands))
    try:
        core.run_events.append(Path(target_dir), rec)
    except FileNotFoundError as exc:
        raise TargetSessionError(f"target {Path(target_dir).name} has never been claimed "
                                 f"(no directory to record in): {exc}") from exc
    return rec
