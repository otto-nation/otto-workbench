"""Drive a run: admit steps, reap them, and stop when only decisions remain."""

from __future__ import annotations

import collections
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import git.client
from batch import admission, events, outcomes, resolve, store
from batch.model import (STEP_ORDER, Decision, DecisionKind, Item, ItemStatus, Run, RunStatus,
                         Step, StepRecord, StepStatus)
from batch import plan as _plan
from batch.plan import PlanRow
from batch.steps import StepProcess, WorktreeResult, ensure_worktree, step_argv
from config.workbench_config import BatchConfig


def _local_head(worktree: str) -> str:
    return git.client.head_sha(cwd=worktree)


def new_run(rows: list[PlanRow], *, steps: list[Step], selected: dict[str, list[Step]] | None,
            pool: int, auto_publish: list[Step], now: datetime | None = None) -> Run:
    now = now or datetime.now(timezone.utc)
    items = []
    for r in rows:
        chosen = selected.get(r.key, []) if selected is not None else \
            [s for s in steps if r.needs.get(s) and r.needs[s].needed]
        if not chosen:
            continue
        recs = [StepRecord(s, StepStatus.PENDING if s in chosen else StepStatus.SKIPPED)
                for s in STEP_ORDER if s in steps]
        items.append(Item(key=r.key, repo=r.repo, repo_dir=r.repo_dir, pr=r.pr, branch=r.branch,
                          head_sha=r.head_sha, steps=recs))
    return Run(id=store.new_run_id(now), started_at=now.isoformat(timespec="seconds"),
               steps=list(steps), pool=pool, auto_publish=list(auto_publish), items=items)


def row_for(item: Item) -> PlanRow:
    return PlanRow(item.repo, item.repo_dir, item.pr, "", item.branch, item.head_sha, False, {})


def _decide(run: Run, item: Item, step: str, kind: DecisionKind, payload: dict) -> Decision:
    d = Decision(id=secrets.token_hex(4), item=item.key, step=step, kind=kind, payload=payload,
                 created_at=store.now_iso())
    run.decisions.append(d)
    item.status = ItemStatus.AWAITING_DECISION
    return d


def _interrupt_running(run: Run, item: Item) -> bool:
    found = False
    for rec in item.steps:
        if rec.status is not StepStatus.RUNNING:
            continue
        rec.status = StepStatus.INTERRUPTED
        _decide(run, item, rec.step.value, DecisionKind.INTERRUPTED,
                {"log_path": rec.log_path})
        found = True
    return found


def mark_interrupted(run: Run) -> bool:
    found = False
    for item in run.items:
        found = _interrupt_running(run, item) or found
    return found


@dataclass
class _Live:
    item: Item
    rec: StepRecord
    proc: object
    head_before: str
    peak: int = 0
    tail: collections.deque = field(default_factory=lambda: collections.deque(maxlen=40))


class Scheduler:
    def __init__(self, run: Run, *, pr_bin: str, cfg: BatchConfig,
                 host: Callable[[], admission.HostSample] = admission.read_host,
                 spawn: Callable[..., object] = StepProcess.start,
                 replan: Callable[[PlanRow], PlanRow | None] | None = None,
                 worktrees: Callable[[str, str], WorktreeResult] = ensure_worktree,
                 head: Callable[[str], str] = _local_head,
                 rss: Callable[[int], int] = admission.tree_rss,
                 estimates: admission.Estimates | None = None,
                 emit: Callable[..., None] = events.emit,
                 sleep: Callable[[float], None] = time.sleep, tick: float = 0.5):
        self.run, self.pr_bin, self.cfg = run, pr_bin, cfg
        # Looked up at construction, not bound as a default, so a patched
        # batch.plan.replan_row is the one used.
        self._host, self._spawn = host, spawn
        self._replan = replan or _plan.replan_row
        self._worktrees, self._head, self._rss = worktrees, head, rss
        self._estimates = estimates or admission.Estimates.load()
        self._emit, self._sleep, self._tick = emit, sleep, tick
        self._live: dict[str, _Live] = {}

    # ── requests and cancel ──────────────────────────────────────────────

    def _apply_requests(self) -> None:
        for raw in store.take_requests(self.run.id):
            req = resolve.Request.from_dict(raw)
            try:
                resolve.apply(self.run, req, pr_bin=self.pr_bin)
                self._emit("decision_resolved", run=self.run.id, decision=req.decision,
                           action=req.action)
            except (resolve.ResolveError, KeyError) as exc:
                self._emit("decision_resolved", run=self.run.id, decision=req.decision,
                           action=req.action, error=str(exc))

    # ── reaping ──────────────────────────────────────────────────────────

    def _reap(self) -> None:
        for key, live in list(self._live.items()):
            for line in live.proc.drain_lines():
                live.tail.append(line)
                self._emit("step_log", run=self.run.id, item=key, step=live.rec.step.value,
                           line=line)
            live.peak = max(live.peak, self._rss(live.proc.pid))
            code = live.proc.poll()
            if code is not None:
                self._finish(live, code)
                del self._live[key]

    def _finish(self, live: _Live, code: int) -> None:
        item, rec = live.item, live.rec
        result = outcomes.classify(rec.step, code, live.proc.stdout(), item=item,
                                   log_tail=list(live.tail))
        rec.exit_code, rec.ended_at, rec.status = code, store.now_iso(), result.status
        rec.drafted = code == 0 and (rec.step not in self.run.auto_publish
                                     or result.status is StepStatus.NEEDS_DECISION)
        if rec.step is not Step.REVIEW and self._head(item.worktree) != live.head_before:
            item.head_moved = True
            if item.has(Step.REVIEW) and item.step(Step.REVIEW).status in (
                    StepStatus.SKIPPED, StepStatus.DONE):
                item.step(Step.REVIEW).status = StepStatus.PENDING
        self._estimates.observe(item.repo, rec.step, live.peak)
        item.status = ItemStatus.QUEUED
        for draft in result.decisions:
            d = _decide(self.run, item, rec.step.value, draft.kind, draft.payload)
            # `kind` is emit's first argument, so the decision kind cannot be a field.
            self._emit("decision_created", run=self.run.id, item=item.key, decision=d.id,
                       step=d.step, payload=d.payload, decision_kind=d.kind.value)
        self._emit("step_finished", run=self.run.id, item=item.key, step=rec.step.value,
                   status=rec.status.value, exit_code=code)

    # ── admitting ────────────────────────────────────────────────────────

    def _ready(self, item: Item) -> bool:
        return not (item.terminal or item.key in self._live or self.run.open_decisions(item.key))

    def _ensure_worktree(self, item: Item) -> bool:
        if item.worktree:
            return True
        res = self._worktrees(item.repo_dir, item.branch)
        if not res.ok:
            _decide(self.run, item, "worktree", DecisionKind.FAILED,
                    {"reason": "error", "detail": res.error})
            return False
        if res.dirty:
            _decide(self.run, item, "worktree", DecisionKind.DIRTY_WORKTREE, {"path": res.path})
            return False
        item.worktree = res.path
        return True

    def _close(self, item: Item) -> None:
        if any(rec.drafted for rec in item.steps):
            _decide(self.run, item, "publish", DecisionKind.PUBLISH,
                    {"drafted": [r.step.value for r in item.steps if r.drafted],
                     "track": list(item.track)})
            item.status = ItemStatus.READY_TO_PUBLISH
        else:
            item.status = ItemStatus.DONE
        self._emit("item_finished", run=self.run.id, item=item.key, status=item.status.value)

    def _next_step(self, item: Item) -> StepRecord | None:
        while True:
            rec = next((r for r in item.steps if r.status is StepStatus.PENDING), None)
            if rec is None:
                return None
            fresh = self._replan(row_for(item))
            if fresh is None:
                item.status = ItemStatus.SKIPPED_CLOSED
                self._emit("item_finished", run=self.run.id, item=item.key,
                           status=item.status.value)
                return None
            need = fresh.needs.get(rec.step)
            forced = rec.step is Step.REVIEW and item.head_moved
            if need is not None and not need.needed and not forced:
                rec.status = StepStatus.SKIPPED
                continue
            return rec

    def _admit(self) -> None:
        for item in self.run.items:
            if not self._ready(item) or not self._ensure_worktree(item):
                continue
            rec = self._next_step(item)
            if item.terminal:
                continue
            if rec is None:
                self._close(item)
                continue
            verdict = admission.decide(self._host(), running=len(self._live),
                                       limit=self.run.pool,
                                       estimate=self._estimates.get(item.repo, rec.step),
                                       cfg=self.cfg)
            if verdict.admit:
                self._start(item, rec)
                continue
            if item.wait_reason != verdict.reason:
                self._emit("admission_wait", run=self.run.id, item=item.key,
                           step=rec.step.value, reason=verdict.reason)
            item.status, item.wait_reason = ItemStatus.WAITING_ADMISSION, verdict.reason

    def _start(self, item: Item, rec: StepRecord) -> None:
        attempt = sum(1 for _ in store.logs_dir(self.run.id).glob(f"{item.pr}-{rec.step}-*"))
        log = store.logs_dir(self.run.id) / f"{item.pr}-{rec.step.value}-{attempt}.log"
        argv = step_argv(rec.step, self.pr_bin, item.worktree,
                         publish=rec.step in self.run.auto_publish)
        # Sample HEAD before spawn: the harness mutates it inside `_spawn`.
        head_before = self._head(item.worktree)
        proc = self._spawn(argv, log_path=log, trail_root=self.run.trail_root)
        rec.status, rec.started_at, rec.log_path = StepStatus.RUNNING, store.now_iso(), str(log)
        item.status, item.wait_reason = ItemStatus.RUNNING, ""
        self._live[item.key] = _Live(item, rec, proc, head_before)
        self._emit("step_started", run=self.run.id, item=item.key, step=rec.step.value,
                   argv=argv, log_path=str(log))

    # ── loop ─────────────────────────────────────────────────────────────

    def _settle(self, status: RunStatus) -> RunStatus:
        self.run.status = status
        store.save(self.run)
        self._emit("run_waiting" if status is RunStatus.WAITING else "run_finished",
                   run=self.run.id, status=status.value,
                   open_decisions=len(self.run.open_decisions()))
        return status

    def _kill_live(self) -> None:
        for live in self._live.values():
            live.proc.kill()

    def _blocked_status(self, cancel: store.CancelRequest) -> RunStatus | None:
        if self._live:
            return None
        if cancel.requested:
            return RunStatus.CANCELLED
        if all(i.terminal for i in self.run.items):
            return RunStatus.DONE
        if all(i.terminal or self.run.open_decisions(i.key) for i in self.run.items):
            return RunStatus.WAITING
        return None

    def run_until_blocked(self) -> RunStatus:
        self.run.status = RunStatus.RUNNING
        while True:
            self._apply_requests()
            cancel = store.cancel_requested(self.run.id)
            if cancel.kill:
                self._kill_live()
            self._reap()
            if not cancel.requested:
                self._admit()
            store.save(self.run)
            blocked = self._blocked_status(cancel)
            if blocked is not None:
                return self._settle(blocked)
            self._sleep(self._tick)
