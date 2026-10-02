"""Apply an operator's answer to one decision, then move the item on."""

# doc-group: batch

from __future__ import annotations

import secrets
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

import core.timeouts
import git.push
from batch.model import Decision, DecisionKind, Item, ItemStatus, Run, Step, StepStatus
from batch.store import now_iso

ACTIONS: dict[DecisionKind, frozenset[str]] = {
    DecisionKind.COMMENT_ITEM: frozenset({"settle-fixed", "settle-addressed",
                                          "settle-dismissed", "reply", "track", "open-chat"}),
    DecisionKind.REBASE_CONFLICT: frozenset({"retry", "abort", "open-chat"}),
    DecisionKind.REBASE_REFUSED: frozenset({"skip-pr", "force"}),
    DecisionKind.OPEN_FINDINGS: frozenset({"accept", "open-chat"}),
    DecisionKind.DIRTY_WORKTREE: frozenset({"retry", "drop-pr", "open-chat"}),
    DecisionKind.FAILED: frozenset({"retry", "skip-step", "drop-pr"}),
    DecisionKind.INTERRUPTED: frozenset({"retry", "skip-step", "drop-pr"}),
    DecisionKind.PUBLISH: frozenset({"publish", "discard"}),
}
_SETTLE_AS = {"settle-fixed": "fixed", "settle-addressed": "already_addressed",
              "settle-dismissed": "dismissed"}
GIT_PUSH = "git-push"


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


def default_runner(argv: list[str]) -> int:
    if argv[0] == GIT_PUSH:
        return 0 if git.push.push(argv[1], gated=False).ok else 1
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                          start_new_session=True, timeout=core.timeouts.UNBOUNDED).returncode


def _validate(decision: Decision, request: Request) -> None:
    a = request.action
    if not decision.open:
        raise ResolveError(f"{decision.id} is already resolved ({decision.resolution})")
    if a not in ACTIONS[decision.kind]:
        raise ResolveError(f"{a} is not an action for {decision.kind.value}; use one of: "
                           + ", ".join(sorted(ACTIONS[decision.kind])))
    if a == "open-chat":
        raise ResolveError("open-chat is handled by the UI; the CLI leaves the decision open")
    if a == "settle-dismissed" and not request.reason:
        raise ResolveError("settle-dismissed needs --reason")
    if a == "reply" and not decision.payload.get("replyable"):
        raise ResolveError("this item cannot take a reply; settle or track it instead")
    if a == "reply" and not request.body_file:
        raise ResolveError("reply needs --body-file")
    if a == "force" and not decision.payload.get("override"):
        raise ResolveError("this refusal names no override; force is not available")


def publish_commands(item: Item, pr_bin: str) -> list[list[str]]:
    wt = ["--repo-dir", item.worktree]
    drafted = {rec.step for rec in item.steps if rec.drafted}
    cmds = []
    if Step.REBASE in drafted:
        cmds.append([pr_bin, "rebase", "--push-only"] + wt)
    elif Step.REVIEW in drafted or Step.COMMENTS in drafted:
        cmds.append([GIT_PUSH, item.worktree])
    if Step.COMMENTS in drafted or item.track:
        track = [arg for t in item.track for arg in ("--track", t)]
        cmds.append([pr_bin, "comments", "--finish", "--post", *track] + wt)
    return cmds


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
    if a == "abort":
        return [[pr_bin, "rebase", "--abort"] + wt]
    if a == "force":
        return [[pr_bin, "rebase", "--fix", "--force", "--no-push"] + wt]
    if a == "publish":
        return publish_commands(item, pr_bin)
    return []


def _fail(run: Run, item: Item, step: str) -> Decision:
    d = Decision(
        id=secrets.token_hex(4), item=item.key, step=step, kind=DecisionKind.FAILED,
        payload={"reason": "error", "exit_code": 1, "log_tail": []}, created_at=now_iso())
    run.decisions.append(d)
    item.status = ItemStatus.AWAITING_DECISION
    return d


def _step_of(decision: Decision) -> Step | None:
    return Step(decision.step) if decision.step in {s.value for s in Step} else None


def _effect(run: Run, item: Item, decision: Decision, action: str, ok: bool) -> list[Decision]:
    created: list[Decision] = []
    step = _step_of(decision)
    if action in ("drop-pr", "skip-pr"):
        item.status = ItemStatus.DROPPED
    elif action == "discard":
        item.status = ItemStatus.DONE
    elif action == "publish":
        if ok:
            item.status = ItemStatus.DONE
        else:
            created.append(_fail(run, item, "publish"))
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
        else:
            created.append(_fail(run, item, "rebase"))
    elif action == "accept":
        item.step(Step.REVIEW).status = StepStatus.DONE
    elif decision.kind is DecisionKind.COMMENT_ITEM:
        if action == "track":
            item.track.append(decision.payload["id"])
        if not [d for d in run.open_decisions(item.key) if d.kind is DecisionKind.COMMENT_ITEM]:
            item.step(Step.COMMENTS).status = StepStatus.DONE
    if item.status is ItemStatus.AWAITING_DECISION and not run.open_decisions(item.key):
        item.status = ItemStatus.QUEUED
    return created


def apply(run: Run, request: Request, *, pr_bin: str,
          runner: Callable[[list[str]], int] | None = None) -> list[Decision]:
    decision = run.decision(request.decision)
    item = run.item(decision.item)
    _validate(decision, request)
    run_cmd = default_runner if runner is None else runner
    ok = _run_commands(decision, item, request, pr_bin, run_cmd)
    decision.resolution, decision.resolved_at = request.action, now_iso()
    return _effect(run, item, decision, request.action, ok)


def _run_commands(decision: Decision, item: Item, request: Request, pr_bin: str,
                   run_cmd: Callable[[list[str]], int]) -> bool:
    for argv in command_for(decision, item, request, pr_bin):
        if run_cmd(argv) == 0:
            continue
        if decision.kind is DecisionKind.COMMENT_ITEM:
            raise ResolveError(f"{' '.join(argv)} failed; the decision is still open")
        return False
    return True
