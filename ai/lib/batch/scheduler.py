"""Drive a run: admit steps, reap them, and stop when only decisions remain."""

# doc-group: batch

from __future__ import annotations

import collections
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import batch.admission
import batch.events
import batch.outcomes
import batch.plan
import batch.resolve
import batch.store
import git.client
from batch.model import (STEP_ORDER, Decision, DecisionKind, Item, ItemStatus, Run, RunStatus,
                         Step, StepRecord, StepStatus)
from batch.plan import PlanRow
from batch.steps import StepProcess, WorktreeResult, ensure_worktree, step_argv
from config.workbench_config import BatchConfig


def _local_head(worktree: str) -> str:
    return git.client.head_sha(cwd=worktree)


def new_run(rows: list[PlanRow], *, steps: list[Step], selected: dict[str, list[Step]] | None,
            pool: int, auto_publish: list[Step], now: datetime | None = None,
            ref_namespace: str = "", ref_dirs: list[str] | None = None) -> Run:
    now = now or datetime.now(timezone.utc)
    items = []
    explicit = selected is not None
    for r in rows:
        chosen = selected.get(r.key, []) if selected is not None else \
            [s for s in steps if r.needs.get(s) and r.needs[s].needed]
        if not chosen:
            continue
        recs = [StepRecord(s, StepStatus.PENDING if s in chosen else StepStatus.SKIPPED,
                           explicit=explicit and s in chosen)
                for s in STEP_ORDER if s in steps]
        items.append(Item(key=r.key, repo=r.repo, repo_dir=r.repo_dir, pr=r.pr, branch=r.branch,
                          head_sha=r.head_sha, steps=recs))
    # A fork's head branch names a branch in another repo — often `main` —
    # so keying it under the base repo would stack every PR based on `main`.
    by_branch = {(r.repo, r.branch): r.key for r in rows if not r.is_fork}
    queued = {it.key for it in items}
    bases = {r.key: by_branch.get((r.repo, r.base_ref), "") for r in rows if r.base_ref}
    for it in items:
        base = bases.get(it.key, "")
        if base in queued and base != it.key:
            it.stacked_on = base
    return Run(id=batch.store.new_run_id(now), started_at=now.isoformat(timespec="seconds"),
               steps=list(steps), pool=pool, auto_publish=list(auto_publish), items=items,
               ref_namespace=ref_namespace, ref_dirs=list(ref_dirs or []))


def row_for(item: Item, local_head: str = "", ref_namespace: str = "") -> PlanRow:
    # The remote sha, not head_sha: a replan that echoes its input must never
    # read as GitHub reporting the plan-time head again.
    return PlanRow(item.repo, item.repo_dir, item.pr, "", item.branch, item.remote_sha, False,
                   {}, local_head=local_head, ref_namespace=ref_namespace)


def _decide(run: Run, item: Item, step: str, kind: DecisionKind, payload: dict, *,
            emit: Callable[..., None]) -> Decision:
    d = Decision(id=secrets.token_hex(4), item=item.key, step=step, kind=kind, payload=payload,
                 created_at=batch.store.now_iso())
    run.decisions.append(d)
    item.status = ItemStatus.AWAITING_DECISION
    emit("decision_created", run=run.id, item=item.key, decision=d.id,
         step=d.step, payload=d.payload, decision_kind=d.kind.value)
    return d


def _interrupt_running(run: Run, item: Item, *, emit: Callable[..., None]) -> bool:
    found = False
    for rec in item.steps:
        if rec.status is not StepStatus.RUNNING:
            continue
        rec.status = StepStatus.INTERRUPTED
        _decide(run, item, rec.step.value, DecisionKind.INTERRUPTED,
                {"log_path": rec.log_path}, emit=emit)
        found = True
    return found


def mark_interrupted(run: Run, *, emit: Callable[..., None]) -> bool:
    found = False
    for item in run.items:
        found = _interrupt_running(run, item, emit=emit) or found
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
                 host: Callable[[], batch.admission.HostSample] = batch.admission.read_host,
                 spawn: Callable[..., object] = StepProcess.start,
                 replan: Callable[[PlanRow], PlanRow | None] | None = None,
                 worktrees: Callable[[str, str], WorktreeResult] = ensure_worktree,
                 head: Callable[[str], str] = _local_head,
                 rss: Callable[[int], int] = batch.admission.tree_rss,
                 estimates: batch.admission.Estimates | None = None,
                 emit: Callable[..., None] = batch.events.emit,
                 sleep: Callable[[float], None] = time.sleep, tick: float = 0.5):
        self.run, self.pr_bin, self.cfg = run, pr_bin, cfg
        # Looked up at construction, not bound as a default, so a patched
        # batch.plan.replan_row is the one used.
        self._host, self._spawn = host, spawn
        self._replan = replan or batch.plan.replan_row
        self._worktrees, self._head, self._rss = worktrees, head, rss
        self._estimates = estimates or batch.admission.Estimates.load()
        self._emit, self._sleep, self._tick = emit, sleep, tick
        self._live: dict[str, _Live] = {}

    # ── requests and cancel ──────────────────────────────────────────────

    def _apply_one(self, raw) -> None:
        if isinstance(raw, dict) and raw.get("error") and "decision" not in raw:
            self._emit("decision_resolved", run=self.run.id, decision="", action="",
                       error=raw["error"])
            return
        try:
            req = batch.resolve.Request.from_dict(raw)
            item = self.run.item(self.run.decision(req.decision).item)
            was_terminal = item.terminal
            created = batch.resolve.apply(self.run, req, pr_bin=self.pr_bin)
            self._emit("decision_resolved", run=self.run.id, decision=req.decision,
                       action=req.action)
            for d in created:
                self._emit("decision_created", run=self.run.id, item=d.item,
                           decision=d.id, step=d.step, payload=d.payload,
                           decision_kind=d.kind.value)
            if item.terminal and not was_terminal:
                self._emit("item_finished", run=self.run.id, item=item.key,
                           status=item.status.value)
        except (batch.resolve.ResolveError, KeyError, TypeError) as exc:
            decision = raw.get("decision", "") if isinstance(raw, dict) else ""
            action = raw.get("action", "") if isinstance(raw, dict) else ""
            self._emit("decision_resolved", run=self.run.id,
                       decision=decision, action=action, error=str(exc))

    def _apply_requests(self) -> None:
        # ceiling: force and publish run inline, so a tick blocks reaping and events
        # while they run; upgrade to scheduled step attempts if a live UI needs step
        # logs during publish or inline runs exceed a minute.
        for raw in batch.store.take_requests(self.run.id):
            self._apply_one(raw)

    # ── reaping ──────────────────────────────────────────────────────────

    def _reap(self, kill: bool = False) -> None:
        for key, live in list(self._live.items()):
            for line in live.proc.drain_lines():
                live.tail.append(line)
                self._emit("step_log", run=self.run.id, item=key, step=live.rec.step.value,
                           line=line)
            live.peak = max(live.peak, self._rss(live.proc.pid))
            code = live.proc.poll()
            if code is None:
                continue
            if kill:
                # The tracked step is gone, so this is the last call that will
                # ever reach its process group: anything it left running there
                # gets a decisive SIGKILL now rather than whatever signal the
                # grace-window escalation happened to be up to.
                live.proc.kill(force=True)
            self._finish(live, code)
            del self._live[key]

    def _finish(self, live: _Live, code: int) -> None:
        item, rec = live.item, live.rec
        result = batch.outcomes.classify(rec.step, code, live.proc.stdout(), item=item,
                                   log_tail=list(live.tail))
        rec.exit_code, rec.ended_at, rec.status = code, batch.store.now_iso(), result.status
        rec.drafted = code == 0 and (not self._should_publish(item, rec.step)
                                     or result.status is StepStatus.NEEDS_DECISION)
        if rec.step is not Step.REVIEW and self._head(item.worktree) != live.head_before:
            item.head_moved = True
            if item.has(Step.REVIEW) and item.step(Step.REVIEW).status in (
                    StepStatus.SKIPPED, StepStatus.DONE):
                item.step(Step.REVIEW).status = StepStatus.PENDING
        self._estimates.observe(item.repo, rec.step, live.peak)
        item.status = ItemStatus.QUEUED
        for draft in result.decisions:
            _decide(self.run, item, rec.step.value, draft.kind, draft.payload, emit=self._emit)
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
                    {"reason": "error", "detail": res.error}, emit=self._emit)
            return False
        if res.dirty:
            _decide(self.run, item, "worktree", DecisionKind.DIRTY_WORKTREE, {"path": res.path},
                    emit=self._emit)
            return False
        item.worktree = res.path
        return True

    def _close(self, item: Item) -> None:
        if any(rec.drafted for rec in item.steps):
            _decide(self.run, item, "publish", DecisionKind.PUBLISH,
                    {"drafted": [r.step.value for r in item.steps if r.drafted],
                     "track": list(item.track)}, emit=self._emit)
            item.status = ItemStatus.READY_TO_PUBLISH
        else:
            item.status = ItemStatus.DONE
        self._emit("item_finished", run=self.run.id, item=item.key, status=item.status.value)

    def _pending(self, item: Item) -> StepRecord | None:
        return next((r for r in item.steps if r.status is StepStatus.PENDING), None)

    def _confirm(self, item: Item, rec: StepRecord) -> StepRecord | None:
        try:
            fresh = self._replan(row_for(item, self._head(item.worktree), self.run.ref_namespace))
        except batch.plan.PlanError as exc:
            _decide(self.run, item, rec.step.value, DecisionKind.FAILED,
                    {"reason": "github", "detail": str(exc)}, emit=self._emit)
            return None
        if fresh is None:
            item.status = ItemStatus.SKIPPED_CLOSED
            self._emit("item_finished", run=self.run.id, item=item.key,
                       status=item.status.value)
            return None
        need = fresh.needs.get(rec.step)
        forced = rec.explicit or (rec.step is Step.REVIEW and item.head_moved)
        if need is not None and not need.needed and not forced:
            rec.status = StepStatus.SKIPPED
            return None
        return rec

    def _refuse(self, item: Item, rec: StepRecord, reason: str) -> None:
        if item.wait_reason != reason:
            self._emit("admission_wait", run=self.run.id, item=item.key,
                       step=rec.step.value, reason=reason)
        item.status, item.wait_reason = ItemStatus.WAITING_ADMISSION, reason

    def _host_for_admit(self) -> batch.admission.HostSample:
        sample = self._host()
        pending = sum(max(0, self._estimates.get(live.item.repo, live.rec.step) - live.peak)
                      for live in self._live.values())
        if sample.mem_available is None or not pending:
            return sample
        return batch.admission.HostSample(max(0, sample.mem_available - pending),
                                          sample.cpu_some_avg10, sample.mem_some_avg10)

    def _admit(self) -> None:
        for item in self.run.items:
            if not self._ready(item) or not self._ensure_worktree(item):
                continue
            rec = self._pending(item)
            if rec is None:
                self._close(item)
                continue
            verdict = batch.admission.decide(self._host_for_admit(), running=len(self._live),
                                       limit=max(1, self.run.pool),
                                       estimate=self._estimates.get(item.repo, rec.step),
                                       cfg=self.cfg)
            if not verdict.admit:
                self._refuse(item, rec, verdict.reason)
                continue
            rec = self._confirm(item, rec)
            if rec is None:
                continue
            self._start(item, rec)

    def _should_publish(self, item: Item, step: Step) -> bool:
        if step is Step.CI or step not in self.run.auto_publish:
            return False
        earlier = STEP_ORDER[:STEP_ORDER.index(step)]
        return not any(item.has(s) and item.step(s).drafted for s in earlier)

    def _start(self, item: Item, rec: StepRecord) -> None:
        slug = item.repo.replace("/", "__")
        attempt = sum(1 for _ in batch.store.logs_dir(self.run.id).glob(
            f"{slug}-{item.pr}-{rec.step.value}-*"))
        log = batch.store.logs_dir(self.run.id) / f"{slug}-{item.pr}-{rec.step.value}-{attempt}.log"
        argv = step_argv(rec.step, self.pr_bin, item.worktree,
                         publish=self._should_publish(item, rec.step),
                         remote_sha=item.remote_sha)
        # Sample HEAD before spawn: the harness mutates it inside `_spawn`.
        head_before = self._head(item.worktree)
        proc = self._spawn(argv, log_path=log, trail_root=self.run.trail_root)
        rec.status, rec.started_at, rec.log_path = StepStatus.RUNNING, batch.store.now_iso(), str(log)
        item.status, item.wait_reason = ItemStatus.RUNNING, ""
        self._live[item.key] = _Live(item, rec, proc, head_before)
        self._emit("step_started", run=self.run.id, item=item.key, step=rec.step.value,
                   argv=argv, log_path=str(log))

    # ── loop ─────────────────────────────────────────────────────────────

    def _settle(self, status: RunStatus) -> RunStatus:
        self.run.status = status
        # Save before dropping: cleanup that fails must never leave the stored
        # run claiming it is still RUNNING.
        batch.store.save(self.run)
        if status in (RunStatus.DONE, RunStatus.CANCELLED):
            batch.plan.drop_refs(self.run.ref_dirs, self.run.ref_namespace)
        self._emit("run_waiting" if status is RunStatus.WAITING else "run_finished",
                   run=self.run.id, status=status.value,
                   open_decisions=len(self.run.open_decisions()))
        return status

    def _kill_live(self) -> None:
        for live in self._live.values():
            live.proc.kill()

    def _blocked_status(self, cancel: batch.store.CancelRequest) -> RunStatus | None:
        if self._live:
            return None
        if batch.store.has_requests(self.run.id):
            return None
        if cancel.requested:
            return RunStatus.CANCELLED
        if all(i.terminal for i in self.run.items):
            return RunStatus.DONE
        if all(i.terminal or self.run.open_decisions(i.key) for i in self.run.items):
            return RunStatus.WAITING
        return None

    def _once(self) -> RunStatus | None:
        self._apply_requests()
        cancel = batch.store.cancel_requested(self.run.id)
        if cancel.kill:
            self._kill_live()
        self._reap(cancel.kill)
        if not cancel.requested:
            self._admit()
        batch.store.save(self.run)
        return self._blocked_status(cancel)

    def _loop(self) -> RunStatus:
        while True:
            blocked = self._once()
            if blocked is not None:
                return self._settle(blocked)
            self._sleep(self._tick)

    def _flush(self, pending: list) -> None:
        try:
            for kind, fields in pending:
                self._emit(kind, **fields)
        except Exception:
            pass

    def _on_interrupt(self) -> None:
        self._kill_live()
        pending = []
        if self.run.status in (RunStatus.DONE, RunStatus.WAITING, RunStatus.CANCELLED):
            self._flush(pending)
            return
        mark_interrupted(self.run, emit=lambda kind, **fields: pending.append((kind, fields)))
        self.run.status = RunStatus.INTERRUPTED
        batch.store.save(self.run)
        pending.append(("run_finished", {
            "run": self.run.id, "status": RunStatus.INTERRUPTED.value,
            "open_decisions": len(self.run.open_decisions()),
        }))
        self._flush(pending)

    def run_until_blocked(self) -> RunStatus:
        self.run.status = RunStatus.RUNNING
        try:
            return self._loop()
        except BaseException:
            self._on_interrupt()
            raise
