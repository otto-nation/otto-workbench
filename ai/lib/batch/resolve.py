"""Apply an operator's answer to one decision, then move the item on.

| Kind | Action | Effect |
|---|---|---|
| `step_review` | `accept` | the decision's own step → `done`; its commits stay drafted |
| | `retry` | that step → `pending`. A `stacked_on` review is retried by the scheduler once its base item is terminal |
| | `skip-step` | that step → `skipped`; commits it made stay drafted |
| | `undo` | rebase only: `git reset --hard <pre_rebase_head>`, rebase → `skipped` and undrafted; failure → `failed` |
| `comment_item` | `settle-fixed` / `settle-addressed` / `settle-dismissed` / `reply` / `track` | comments step `done` once no `comment_item` remains; `settle-dismissed` needs `--reason`; `reply` needs `--body-file` and a replyable item |
| `rebase_conflict` | `retry` / `abort` | rebase → `pending`, resuming the paused replay / `skipped`; a failed abort → `failed` |
| `rebase_refused` | `drop-pr` / `force` | item → `dropped` / forced draft rebase: rebase `done` and drafted (needs payload `override`) |
| `open_findings` | `accept` | from runs saved before `step_review`; review step `done` |
| `dirty_worktree` | `retry` / `drop-pr` | re-checked when the item is next admitted (the payload names a stash command) / item → `dropped` |
| `failed` / `interrupted` | `retry` / `skip-step` / `drop-pr` | step → `pending` / `skipped`; item → `dropped` |
| `failed` | `force-publish` | only `reason: not_incorporated_remote`; same as `publish` past exactly the commits listed in the refusal (a newly appeared remote commit refuses again) |
| `publish` | `publish` | see `batch.publish`; success → item `done`, or reopened once for `--watch-ci`; a refusal or failed command → `failed` on step `publish`, `reason` set; every command's output goes to `logs/<slug>-<pr>-publish-<n>.log`, which a failure names |
| | `discard` | item → `done`; local commits stay and nothing is pushed |

`ACTION_INPUTS` names the flags an action needs; `available_actions` and
`resolve_command` are what `pr batch status` offers.

`open-chat` is offered where listed and refused by the CLI, leaving the
decision open.
"""

# doc-group: batch

from __future__ import annotations

import secrets
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import batch.outcomes
import batch.plan
import batch.publish
import batch.store
import core.timeouts
from batch.model import Decision, DecisionKind, Item, ItemStatus, Run, Step, StepStatus
from batch.store import now_iso
from pr.comments_fix import CloseoutDebt

ACTIONS: dict[DecisionKind, frozenset[str]] = {
    DecisionKind.COMMENT_ITEM: frozenset({"settle-fixed", "settle-addressed",
                                          "settle-dismissed", "reply", "track", "open-chat"}),
    DecisionKind.REBASE_CONFLICT: frozenset({"retry", "abort", "open-chat"}),
    DecisionKind.REBASE_REFUSED: frozenset({"drop-pr", "force"}),
    DecisionKind.OPEN_FINDINGS: frozenset({"accept", "open-chat"}),
    DecisionKind.STEP_REVIEW: frozenset({"accept", "retry", "skip-step", "undo", "open-chat"}),
    DecisionKind.DIRTY_WORKTREE: frozenset({"retry", "drop-pr", "open-chat"}),
    DecisionKind.FAILED: frozenset({"retry", "skip-step", "drop-pr", "force-publish"}),
    DecisionKind.INTERRUPTED: frozenset({"retry", "skip-step", "drop-pr"}),
    DecisionKind.PUBLISH: frozenset({"publish", "discard"}),
}
_SETTLE_AS = {"settle-fixed": "fixed", "settle-addressed": "already_addressed",
              "settle-dismissed": "dismissed"}
# What default_runner writes before each child command's output in a publish log.
_COMMAND_HEADER = "$ "


@dataclass(frozen=True)
class ActionInput:
    """One flag an action reads off its request; `attr` is the `Request` field it fills."""

    flag: str
    attr: str
    metavar: str
    required: bool = True


# The single statement of what each action needs beyond `--action`: _validate
# enforces it and resolve_command spells it, so the two cannot disagree.
ACTION_INPUTS: dict[str, tuple[ActionInput, ...]] = {
    "settle-dismissed": (ActionInput("--reason", "reason", "<text>"),),
    "reply": (ActionInput("--body-file", "body_file", "<path>"),),
    "settle-fixed": (ActionInput("--commit", "commit", "<sha>", required=False),),
}


class ResolveError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    decision: str
    action: str
    reason: str = ""
    body_file: str = ""
    commit: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> Request:
        return cls(decision=d["decision"], action=d["action"], reason=d.get("reason", ""),
                   body_file=d.get("body_file", ""), commit=d.get("commit", ""))


def default_runner(argv: list[str], log_path: Path | None = None) -> int:
    """Run one resolve command as a child.

    With *log_path*, a `$ <argv>` header and everything it prints are appended
    there.
    """
    if log_path is None:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                              start_new_session=True, timeout=core.timeouts.UNBOUNDED).returncode
    with log_path.open("a") as log:
        log.write(f"{_COMMAND_HEADER}{shlex.join(argv)}\n")
        log.flush()
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              timeout=core.timeouts.UNBOUNDED).returncode


def _unavailable(decision: Decision, action: str, item: Item) -> str:
    """Why *action* cannot answer *decision* whatever inputs come with it, or ""."""
    kinds = ACTIONS[decision.kind]
    if action not in kinds:
        return (f"{action} is not an action for {decision.kind.value}; use one of: "
                + ", ".join(sorted(kinds)))
    if action == "open-chat":
        return "open-chat is handled by the UI; the CLI leaves the decision open"
    if action == "reply" and not decision.payload.get("replyable"):
        return "this item cannot take a reply; settle or track it instead"
    if action == "force" and not decision.payload.get("override"):
        return "this refusal names no override; force is not available"
    if action == "force-publish" and decision.payload.get("reason") != \
            batch.publish.Refusal.NOT_INCORPORATED_REMOTE.value:
        return "force-publish only answers a not_incorporated_remote refusal"
    if action == "undo" and (decision.step != Step.REBASE.value or not item.pre_rebase_head):
        return "undo needs a rebase step_review with a recorded pre-rebase head"
    return ""


def _validate(decision: Decision, request: Request, item: Item) -> None:
    if not decision.open:
        raise ResolveError(f"{decision.id} is already resolved ({decision.resolution})")
    if refusal := _unavailable(decision, request.action, item):
        raise ResolveError(refusal)
    for need in ACTION_INPUTS.get(request.action, ()):
        if need.required and not getattr(request, need.attr):
            raise ResolveError(f"{request.action} needs {need.flag}")


def available_actions(decision: Decision, item: Item) -> list[str]:
    """The actions `pr batch resolve` would accept for *decision*, given their inputs."""
    return sorted(a for a in ACTIONS[decision.kind] if not _unavailable(decision, a, item))


def resolve_command(run_id: str, decision_id: str, action: str) -> str:
    """`pr batch resolve` for one action, in the parser's order, its inputs as placeholders.

    A required input is `--flag <metavar>`; an optional one is bracketed.
    """
    parts = ["pr", "batch", "resolve", run_id, decision_id, "--action", action]
    for need in ACTION_INPUTS.get(action, ()):
        spelled = f"{need.flag} {need.metavar}"
        parts.append(spelled if need.required else f"[{spelled}]")
    return " ".join(parts)


def command_for(decision: Decision, item: Item, request: Request,
                pr_bin: str) -> list[list[str]]:
    wt = ["--repo-dir", item.worktree]
    a = request.action
    if a in _SETTLE_AS:
        argv = [pr_bin, "comments", "--settle", decision.payload["id"], "--as", _SETTLE_AS[a]]
        if a == "settle-dismissed":
            argv += ["--reason", request.reason]
        if a == "settle-fixed" and request.commit:
            argv += ["--commit", request.commit]
        return [argv + wt]
    if a == "reply":
        return [[pr_bin, "comments", "--reply", decision.payload["id"],
                 "--body-file", request.body_file, "--post"] + wt]
    if a == "undo":
        # A reset, not `pr rebase --abort`: the rebase has completed, so there
        # is no replay left to abort.
        return [["git", "-C", item.worktree, "reset", "--hard", item.pre_rebase_head]]
    if a == "abort":
        return [[pr_bin, "rebase", "--abort"] + wt]
    if a == "force":
        return [[pr_bin, "rebase", "--fix", "--force", "--no-push"] + wt]
    return []


def _fail(run: Run, item: Item, step: str, *,
          reason: str = batch.outcomes.FailureReason.ERROR.value,
          detail: str = "", extra: dict | None = None) -> Decision:
    payload = {"reason": reason, "exit_code": 1, "log_tail": []}
    if detail:
        payload["detail"] = detail
    payload.update(extra or {})
    d = Decision(id=secrets.token_hex(4), item=item.key, step=step,
                 kind=DecisionKind.FAILED, payload=payload, created_at=now_iso())
    run.decisions.append(d)
    item.status = ItemStatus.AWAITING_DECISION
    return d


def _last_command(lines: list[str]) -> list[str]:
    """The lines the failing command printed: those after the last `$ ` header.

    `default_runner` writes a `$ <argv>` header before each child command, so
    an earlier command that succeeded cannot lend the failure its reason. A
    log with no header is read whole.
    """
    starts = [i for i, line in enumerate(lines) if line.startswith(_COMMAND_HEADER)]
    return lines[starts[-1] + 1:] if starts else lines


def _publish_failed(run: Run, item: Item, log: Path) -> Decision:
    """A failed publish command as a decision: its classified reason, headline and log."""
    lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
    found = batch.outcomes.classify_failure(_last_command(lines))
    return _fail(run, item, "publish", reason=found.reason.value, detail=found.detail,
                 extra={"log": str(log),
                        "log_tail": lines[-batch.outcomes.LOG_TAIL_LINES:]})


def step_of(decision: Decision) -> Step | None:
    """The Step a decision belongs to, or None for "worktree" / "publish"."""
    return Step(decision.step) if decision.step in {s.value for s in Step} else None


def _effect(run: Run, item: Item, decision: Decision, action: str, ok: bool) -> list[Decision]:
    created: list[Decision] = []
    step = step_of(decision)
    if action == "drop-pr":
        item.status = ItemStatus.DROPPED
    elif action == "discard":
        item.status = ItemStatus.DONE
    elif action == "retry" and step:
        item.step(step).status = StepStatus.PENDING
    elif action == "skip-step":
        if step:
            item.step(step).status = StepStatus.SKIPPED
        else:
            # decision.step is "worktree" or "publish" — there is no real Step to
            # mark skipped, and re-queuing would just redo the same operation, so
            # skipping it means giving up on the item like drop-pr does.
            item.status = ItemStatus.DROPPED
    elif action == "abort":
        if ok:
            item.step(Step.REBASE).status = StepStatus.SKIPPED
        else:
            created.append(_fail(run, item, "rebase"))
    elif action == "force":
        if ok:
            rec = item.step(Step.REBASE)
            rec.status, rec.drafted = StepStatus.DONE, True
            # Its stdout went to the operator's terminal, so the tip is read back from
            # the record the forced run saved.
            item.pre_rebase_head = batch.outcomes.recorded_pre_rebase_head(item)
        else:
            created.append(_fail(run, item, "rebase"))
    elif action == "undo":
        if ok:
            rec = item.step(Step.REBASE)
            rec.status, rec.drafted = StepStatus.SKIPPED, False
            item.pre_rebase_head = ""
        else:
            created.append(_fail(run, item, "rebase"))
    elif action == "accept":
        if step:
            item.step(step).status = StepStatus.DONE
    elif decision.kind is DecisionKind.COMMENT_ITEM:
        if action == "track":
            item.track.append(decision.payload["id"])
        if not [d for d in run.open_decisions(item.key) if d.kind is DecisionKind.COMMENT_ITEM]:
            item.step(Step.COMMENTS).status = StepStatus.DONE
    if item.status is ItemStatus.AWAITING_DECISION and not run.open_decisions(item.key):
        item.status = ItemStatus.QUEUED
    return created


def _reopen_for_ci(run: Run, item: Item) -> None:
    """With --watch-ci, send a just-published item back for one CI re-check.

    Once per item per run: `ci_watched` is spent here, so a red re-check is
    fixed and published once more and never watched again.
    """
    if not (run.watch_ci and item.has(Step.CI) and not item.ci_watched):
        return
    item.ci_watched = True
    for rec in item.steps:
        rec.drafted = False
    ci = item.step(Step.CI)
    ci.status, ci.watch = StepStatus.PENDING, True
    item.status = ItemStatus.QUEUED


def _publish(run: Run, item: Item, pr_bin: str, run_cmd: Callable[..., int],
             read: Callable[[Item], batch.publish.TreeState], *,
             confirmed: Sequence[str] = (),
             closeout: Callable[[str, str], CloseoutDebt]) -> list[Decision]:
    """Push what the tree says to push; a refusal or a failed command is a decision.

    Every command of one publish attempt appends to one log,
    `logs/<slug>-<pr>-publish-<n>.log`, which a failure's decision names.
    The comments command closes out what the PR's saved `pr` state says is owed,
    when anything is. A head that `--finish` pushed becomes the lease.
    """
    tree = read(item)
    debt = closeout(item.repo_dir, item.branch)
    plan = batch.publish.plan(item, pr_bin, tree, confirmed=confirmed, closeout=debt)
    if not plan.ok:
        extra = {"commits": plan.commits} if plan.commits else None
        return [_fail(run, item, "publish", reason=plan.refusal.value, detail=plan.detail,
                      extra=extra)]
    log = batch.store.attempt_log_path(run.id, item.repo, item.pr, "publish")
    if plan.commands:
        log.touch()
    rest = plan.commands
    if plan.pushes:
        push, *rest = plan.commands
        if run_cmd(push, log_path=log) != 0:
            return [_publish_failed(run, item, log)]
        # The lease moves as soon as the push lands, before anything after it
        # can fail: a retry must see our own push as the planned head, not as
        # somebody else's. Leased on what the branch holds after the push, not
        # before, since a landing may commit (hook regeneration) first. The tree
        # seam re-reads it; an unreadable branch falls back to the pre-push tip.
        item.remote_sha = item.published_sha = read(item).local or tree.local
        # CI on the head just pushed has not been read, whatever an earlier
        # re-check of an earlier push found.
        item.ci_rechecked = False
    for argv in rest:
        if run_cmd(argv, log_path=log) != 0:
            # `--finish` may have pushed before the replies failed; lease that
            # head so a retry does not treat our own commit as someone else's.
            _adopt_own_push(item, read(item))
            return [_publish_failed(run, item, log)]
    # `pr comments --finish` pushes a commit the fix pass held back before it
    # replies, so the remote may have moved under our own hand.
    adopted = bool(rest) and _adopt_own_push(item, read(item))
    item.status = ItemStatus.DONE
    if plan.pushes or adopted:
        _reopen_for_ci(run, item)
    return []


def _adopt_own_push(item: Item, after: batch.publish.TreeState) -> bool:
    """Lease on the head `--finish` pushed; leave a head somebody else pushed alone.

    Ours is a remote that moved to exactly what the local branch holds. Any
    other move keeps the old lease, so the next publish refuses `remote_moved`
    instead of force-pushing over a colleague's commit.
    """
    if not after.fetched or not after.remote or after.remote == item.remote_sha:
        return False
    if after.remote != after.local:
        return False
    item.remote_sha = item.published_sha = after.remote
    item.ci_rechecked = False
    return True


def apply(run: Run, request: Request, *, pr_bin: str,
          runner: Callable[..., int] | None = None,
          tree: Callable[[Item], batch.publish.TreeState] | None = None,
          closeout: Callable[[str, str], CloseoutDebt] | None = None) -> list[Decision]:
    decision = run.decision(request.decision)
    item = run.item(decision.item)
    _validate(decision, request, item)
    run_cmd = default_runner if runner is None else runner
    if request.action in ("publish", "force-publish"):
        created = _publish(run, item, pr_bin, run_cmd, tree or batch.publish.read_tree,
                           confirmed=decision.payload.get("commits", [])
                           if request.action == "force-publish" else (),
                           closeout=closeout or batch.plan.closeout_debt)
        decision.resolution, decision.resolved_at = request.action, now_iso()
        return created
    ok = _run_commands(decision, item, request, pr_bin, run_cmd)
    decision.resolution, decision.resolved_at = request.action, now_iso()
    return _effect(run, item, decision, request.action, ok)


def _run_commands(decision: Decision, item: Item, request: Request, pr_bin: str,
                   run_cmd: Callable[..., int]) -> bool:
    for argv in command_for(decision, item, request, pr_bin):
        if run_cmd(argv) == 0:
            continue
        if decision.kind is DecisionKind.COMMENT_ITEM:
            raise ResolveError(f"{' '.join(argv)} failed; the decision is still open")
        return False
    return True
