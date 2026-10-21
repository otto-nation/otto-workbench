"""Apply an operator's answer to one decision, then move the item on.

| Kind | Action | Effect |
|---|---|---|
| `step_review` | `accept` | the decision's own step → `done`; its commits stay drafted |
| | `retry` | that step → `pending`. A `stacked_on` review is retried by the scheduler once its base item is terminal |
| | `skip-step` | that step → `skipped`; commits it made stay drafted |
| | `undo` | rebase only: `git reset --hard <pre_rebase_head>`, rebase → `skipped` and undrafted; failure → `failed` |
| `comment_item` | `settle-fixed` / `settle-addressed` / `settle-dismissed` / `reply` / `track` | comments step `done` once no `comment_item` remains; `settle-dismissed` needs `--reason`; `reply` needs `--body-file` and a replyable item |
| `rebase_conflict` | `retry` / `abort` | rebase → `pending`, resuming the paused replay / `skipped`; a failed abort → `failed` |
| `rebase_refused` | `drop-pr` / `force` / `rebase-from-fork-point` | item → `dropped` and its target lock released / forced draft rebase / draft rebase from the payload's fork point: rebase `done` and drafted (`force` needs payload `override`; `rebase-from-fork-point` needs payload `fork_point` or a remedy starting `--fork-point `); `pre_rebase_head` stays the tip the refused attempt started from, so a hand `--fork-point` rebase publishes through `force-publish` |
| `open_findings` | `accept` | from runs saved before `step_review`; review step `done` |
| `dirty_worktree` | `retry` / `drop-pr` | re-checked when the item is next admitted (the payload names a stash command) / item → `dropped`, and its target lock released (see below) |
| `failed` / `interrupted` | `retry` / `skip-step` / `drop-pr` | step → `pending` / `skipped` (item → `dropped` when the step is `worktree` or `publish`); item → `dropped` and its target lock released. A `retry` on step `publish` first re-reads the branch: a remote the operator moved to exactly the local branch becomes the lease. A `remote_moved` retry adopts a remote that fast-forwarded the lease to a commit the local branch holds, and otherwise `git reset --keep`s a patch-equivalent remote when tracked files are clean and the local branch has no unique commits (the remote may carry more) |
| `failed` | `force-publish` | only `reason: not_incorporated_remote` or `drafted_dropped`; same as `publish` past exactly the commits listed in the refusal, added to those the item was already forced past (a newly appeared commit refuses again) |
| `publish` | `publish` | see `batch.publish`; success → item `done`, or reopened once for `--watch-ci`; a refusal or failed command → `failed` on step `publish`, `reason` set; every command's output goes to `logs/<slug>-<pr>-publish-<n>.log`, which a failure names |
| | `discard` | item → `done` and its target lock released; local commits stay and nothing is pushed |

Every action that leaves the item terminal (`dropped` or `done`) ends the
batch's claim on that PR: the driver holding the run releases the item's target
lock at the tick that drains the request (`batch.targets.ItemHolds`), so a
manual `pr` command on the branch works while the run carries on. Only the
driver can release it, since the flock is its own; with no driver live,
`resolve` applies the request itself and no item lock is held to release.

`ACTION_INPUTS` names the flags an action needs; `available_actions` and
`resolve_command` are what `pr batch status` offers.

`open-chat` is offered where listed and refused by the CLI, leaving the
decision open.
"""

# doc-group: batch

from __future__ import annotations

import functools
import secrets
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import batch.outcomes
import batch.plan
import batch.publish
import batch.store
import batch.targets
import core.timeouts
from batch.model import Decision, DecisionKind, Item, ItemStatus, Run, Step, StepStatus
from batch.requests import Decide
from batch.store import now_iso
from pr.comments_fix import CloseoutDebt

ACTIONS: dict[DecisionKind, frozenset[str]] = {
    DecisionKind.COMMENT_ITEM: frozenset({"settle-fixed", "settle-addressed",
                                          "settle-dismissed", "reply", "track", "open-chat"}),
    DecisionKind.REBASE_CONFLICT: frozenset({"retry", "abort", "open-chat"}),
    DecisionKind.REBASE_REFUSED: frozenset({"drop-pr", "force", "rebase-from-fork-point"}),
    DecisionKind.OPEN_FINDINGS: frozenset({"accept", "open-chat"}),
    DecisionKind.STEP_REVIEW: frozenset({"accept", "retry", "skip-step", "undo", "open-chat"}),
    DecisionKind.DIRTY_WORKTREE: frozenset({"retry", "drop-pr", "open-chat"}),
    DecisionKind.FAILED: frozenset({"retry", "skip-step", "drop-pr", "force-publish"}),
    DecisionKind.INTERRUPTED: frozenset({"retry", "skip-step", "drop-pr"}),
    DecisionKind.PUBLISH: frozenset({"publish", "discard"}),
}
# The publish refusals `force-publish` can push past: each lists the commits it
# would lose, and the operator confirms exactly that list.
_FORCEABLE = frozenset({batch.publish.Refusal.NOT_INCORPORATED_REMOTE.value,
                        batch.publish.Refusal.DRAFTED_DROPPED.value})
_SETTLE_AS = {"settle-fixed": "fixed", "settle-addressed": "already_addressed",
              "settle-dismissed": "dismissed"}
# What default_runner writes before each child command's output in a publish log.
_COMMAND_HEADER = "$ "


@dataclass(frozen=True)
class ActionInput:
    """One flag an action reads off its request; `attr` is the `Decide` field it fills."""

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


_FORK_POINT_PREFIX = "--fork-point "


def _named_fork_point(payload: Mapping) -> str:
    """The sha a refusal names for `--fork-point`, or empty."""
    sha = payload.get("fork_point", "")
    if isinstance(sha, str) and sha:
        return sha
    remedy = payload.get("remedy", "")
    if isinstance(remedy, str) and remedy.startswith(_FORK_POINT_PREFIX):
        named = remedy[len(_FORK_POINT_PREFIX):].split()
        return named[0] if named else ""
    return ""


def default_runner(argv: list[str], log_path: Path | None = None, *,
                   env: Mapping[str, str] | None = None) -> int:
    """Run one resolve command as a child.

    With *log_path*, a `$ <argv>` header and everything it prints are appended
    there. *env* is the child's environment, this process's when None.
    """
    if log_path is None:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                              start_new_session=True, timeout=core.timeouts.UNBOUNDED,
                              env=env).returncode
    with log_path.open("a") as log:
        log.write(f"{_COMMAND_HEADER}{shlex.join(argv)}\n")
        log.flush()
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              timeout=core.timeouts.UNBOUNDED, env=env).returncode


def command_runner(item: Item, runner: Callable[..., int] | None) -> Callable[..., int]:
    """What runs *item*'s resolve commands.

    *runner* when one is given (a test's, or the scheduler's seam); otherwise
    `default_runner` in `batch.targets.child_env(item)`, which names the
    item's target only when this process holds it.
    """
    if runner is not None:
        return runner
    return functools.partial(default_runner, env=batch.targets.child_env(item))


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
    if action == "rebase-from-fork-point" and not _named_fork_point(decision.payload):
        return ("this refusal names no fork point; "
                "rebase-from-fork-point is not available")
    if action == "force-publish" and decision.payload.get("reason") not in _FORCEABLE:
        return "force-publish only answers a not_incorporated_remote or drafted_dropped refusal"
    if action == "undo" and (decision.step != Step.REBASE.value or not item.pre_rebase_head):
        return "undo needs a rebase step_review with a recorded pre-rebase head"
    return ""


def _validate(decision: Decision, request: Decide, item: Item) -> None:
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


def command_for(decision: Decision, item: Item, request: Decide,
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
    if a == "rebase-from-fork-point":
        sha = _named_fork_point(decision.payload)
        if not sha:
            return []
        return [[pr_bin, "rebase", "--fix", "--fork-point", sha, "--no-push"] + wt]
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
    elif action in ("force", "rebase-from-fork-point"):
        if ok:
            rec = item.step(Step.REBASE)
            # The tip the refused attempt started from, not this run's: an
            # operator who rebased by hand with --fork-point, or this action's
            # own `--fork-point` replay, left the run starting from that rebase,
            # which publish would then check the remote against. The run's stdout
            # went to the operator's terminal, so its own tip is read back from
            # the record it saved, for state written before start_head existed.
            # start_head is the first attempt's HEAD, so it is post-hand-rebase
            # only when the hand rebase preceded that first attempt.
            item.pre_rebase_head = rec.start_head or batch.outcomes.recorded_pre_rebase_head(item)
            rec.status, rec.drafted = StepStatus.DONE, True
        else:
            created.append(_fail(run, item, "rebase"))
    elif action == "undo":
        if ok:
            rec = item.step(Step.REBASE)
            rec.status, rec.drafted = StepStatus.SKIPPED, False
            rec.commits = []
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
        rec.drafted, rec.commits = False, []
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
    plan = batch.publish.plan(item, pr_bin, tree, confirmed=confirmed, closeout=debt,
                              no_verify=run.no_verify)
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
        # re-check of an earlier push found. What the operator forced past is
        # behind this push, and confirms nothing about the next one.
        item.ci_rechecked = False
        item.forced = []
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
    # A confirmation covers one publish; a completed one spends it, push or not.
    item.forced = []
    if plan.pushes or adopted:
        _reopen_for_ci(run, item)
    return []


def _moved_to_local(item: Item, after: batch.publish.TreeState) -> bool:
    """Whether the remote left the lease for exactly what the local branch holds.

    Any other move is somebody else's, so the lease stays and the next publish
    refuses `remote_moved` instead of force-pushing over a colleague's commit.
    """
    return (after.fetched and bool(after.remote) and after.remote != item.remote_sha
            and after.remote == after.local)


def _adopt_own_push(item: Item, after: batch.publish.TreeState) -> bool:
    """Lease on the head `--finish` pushed; leave a head somebody else pushed alone."""
    if not _moved_to_local(item, after):
        return False
    item.remote_sha = item.published_sha = after.remote
    item.ci_rechecked = False
    return True


def apply(run: Run, request: Decide, *, pr_bin: str,
          runner: Callable[..., int] | None = None,
          tree: Callable[[Item], batch.publish.TreeState] | None = None,
          closeout: Callable[[str, str], CloseoutDebt] | None = None) -> list[Decision]:
    decision = run.decision(request.decision)
    item = run.item(decision.item)
    _validate(decision, request, item)
    if request.action == "retry" and decision.step == "publish":
        # A publish refusal has no step to re-run, so a retry re-plans against
        # the same lease. Re-read the branch first: once the operator has pushed
        # the local branch by hand, that head is the lease, not a remote_moved
        # refusal on every retry. A remote_moved retry also adopts a
        # patch-equivalent remote (reset --keep) when the tree is clean of
        # tracked changes and every local commit is already on it. It is not
        # published_sha: the batch pushed nothing.
        # A fast-forward is checked before patch equivalence: it needs no reset
        # and leaves the local branch exactly as it is.
        after = (tree or batch.publish.read_tree)(item)
        moved = decision.payload.get("reason") == batch.publish.Refusal.REMOTE_MOVED.value
        if _moved_to_local(item, after):
            item.remote_sha = after.remote
        elif moved and batch.publish.remote_fast_forwarded(item, after):
            item.remote_sha = after.remote
        elif moved and batch.publish.try_adopt_equivalent_remote(item, after):
            item.remote_sha = after.remote
    run_cmd = command_runner(item, runner)
    if request.action in ("publish", "force-publish"):
        if request.action == "force-publish":
            item.forced.extend(c for c in decision.payload.get("commits", [])
                               if c not in item.forced)
        created = _publish(run, item, pr_bin, run_cmd, tree or batch.publish.read_tree,
                           confirmed=item.forced,
                           closeout=closeout or batch.plan.closeout_debt)
        decision.resolution, decision.resolved_at = request.action, now_iso()
        return created
    ok = _run_commands(decision, item, request, pr_bin, run_cmd)
    decision.resolution, decision.resolved_at = request.action, now_iso()
    return _effect(run, item, decision, request.action, ok)


def _run_commands(decision: Decision, item: Item, request: Decide, pr_bin: str,
                   run_cmd: Callable[..., int]) -> bool:
    for argv in command_for(decision, item, request, pr_bin):
        if run_cmd(argv) == 0:
            continue
        if decision.kind is DecisionKind.COMMENT_ITEM:
            raise ResolveError(f"{' '.join(argv)} failed; the decision is still open")
        return False
    return True
