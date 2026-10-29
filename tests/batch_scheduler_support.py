"""The scheduler harness the batch_scheduler suites share.

A Scheduler whose spawn, replan, worktrees, head, publish runner and tree reads
are all fakes, so a run can be driven tick by tick with no git and no network.
Each Harness seeds the run, then holds a DRIVER session open for its lifetime;
`resume()` builds a new Scheduler on that same session.
"""

import contextlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.events  # noqa: E402
import batch.inbox  # noqa: E402
import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import open_session, seed  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402

GiB = 1024 ** 3
NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
ALL = {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NEED}
HEALTHY = batch.admission.HostSample(8 * GiB, 1.0, 0.0)

_OPEN = []


def row(n, needs=ALL, *, repo="o/r", repo_dir="/r", **extra):
    return PlanRow(repo, repo_dir, n, f"t{n}", f"b{n}", "h", False, dict(needs), **extra)


def step_name(argv):
    """The step a spawned argv runs: `pr <step> ...`, or `ci-check ...` for CI."""
    return "ci" if Path(argv[0]).name == "ci-check" else argv[1]


def ff_tree(item):
    """The remote is still the planned head and the local branch is one commit ahead."""
    return TreeState(local=f"{item.remote_sha}+1", remote=item.remote_sha,
                     divergence=RefDivergence(ahead=1, behind=0, comparable=True))


def _even(item):
    """Nothing to push: the local branch is the planned remote head."""
    return TreeState(local=item.remote_sha, remote=item.remote_sha,
                     divergence=RefDivergence(ahead=0, behind=0, comparable=True))


def _inbox(pr_bin, *, runner, tree, closeout, emit):
    """The session Inbox a scheduler test drives with: the fakes as apply seams, events to *emit*.

    It is the only place a drained or synthesized request is applied and
    reported, so event assertions see every outcome through `emit_outcome`.
    """
    return batch.inbox.Inbox(batch.inbox.ApplyContext(
        pr_bin, runner=runner, tree=tree, closeout=closeout,
        report=lambda run, outcome: batch.scheduler.emit_outcome(run, outcome, emit=emit)))


def attach_scheduler(run, *, runner=None, tree=None, closeout=None, **kw):
    """Open a DRIVER session for *run* (creating it when needed) and return a Scheduler on it.

    *runner*, *tree* and *closeout* are apply seams, so they go to the session
    inbox's ApplyContext; the Scheduler also gets *closeout*, which `_close`
    reads for auto-publish. Outcomes are emitted to `kw["emit"]` when given.
    The session stays open until `_close_open_sessions` tears the stack down.
    `create` + first `commit` is used when the run is not on disk yet, so
    `finish` has not settled an empty run to DONE before the scheduler starts.
    """
    stack = contextlib.ExitStack()
    inbox = _inbox(kw.get("pr_bin", "pr"), runner=runner, tree=tree, closeout=closeout,
                   emit=kw.get("emit", batch.events.emit))
    state = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    if state.is_file():
        session = stack.enter_context(
            batch.store.session(run.id, Role.DRIVER, inbox=inbox, command="test scheduler"))
    else:
        session = stack.enter_context(
            batch.store.create(run, inbox=inbox, command="test scheduler"))
        session.commit()
    _OPEN.append(stack)
    return batch.scheduler.Scheduler(session, closeout=closeout, **kw)


@pytest.fixture(autouse=True)
def _quiet_outcomes(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "fix_checks", lambda wt, hb: [])
    monkeypatch.setattr(batch.outcomes, "ci_unfixed", lambda stdout: None)


@pytest.fixture(autouse=True)
def _close_open_sessions():
    """Close every Harness / attach_scheduler session this test opened."""
    yield
    while _OPEN:
        _OPEN.pop().close()


class Harness:
    def __init__(self, rows, *, codes=None, auto_publish=(), pool=2, host=HEALTHY,
                 replan=None, worktrees=None, heads=None, selected=None, cfg=None,
                 runner=None, tree=None, closeout=None, moves=(), stdouts=None,
                 contains=None, dirty=None, rebasing=None, unpublished=None,
                 descends=None, commits=None, verbose=False,
                 machine=None, hold_tree=None, branch=None, focus=()):
        self.codes = codes or {}
        self.moves, self.stdouts = set(moves), stdouts or {}
        self.spawned, self.events, self.live, self.max_live = [], [], 0, 0
        self.published, self.publish_code = [], 0
        run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER),
                                      selected=selected, pool=pool,
                                      auto_publish=list(auto_publish),
                                      focus=focus)
        self.heads = heads or {}
        publish = runner or self._publish
        tree_fn = tree or ff_tree
        close = closeout or (lambda d, b: CloseoutDebt())
        seed(run)
        self._stack = contextlib.ExitStack()
        _OPEN.append(self._stack)
        # The publish runner and tree read are apply seams: they reach the
        # session inbox, which applies every request. closeout also goes to
        # the Scheduler, whose _close reads it for auto-publish.
        self.session = self._stack.enter_context(open_session(
            run.id, inbox=_inbox("pr", runner=publish, tree=tree_fn, closeout=close,
                                 emit=self._record)))
        self._make = lambda: batch.scheduler.Scheduler(
            self.session, pr_bin="pr", cfg=cfg or BatchConfig(pool_max=4),
            host=lambda: host, spawn=self._spawn,
            replan=replan or (lambda r: r),
            worktrees=worktrees or (lambda d, b: WorktreeResult(f"/wt/{b}", False, "")),
            head=lambda wt: self.heads.get(wt, "h0"), rss=lambda pid: 0,
            emit=self._record, sleep=lambda s: None,
            estimates=batch.admission.Estimates({}), closeout=close,
            contains=contains or (lambda wt, sha: True),
            dirty=dirty or (lambda wt: False), rebasing=rebasing or (lambda wt: False),
            unpublished=unpublished or (lambda wt, sha: False),
            descends=descends or (lambda wt, old, new: True),
            # A moved fake head is one commit: the head itself.
            commits=commits or (lambda wt, base: [self.heads.get(wt, "h0")]),
            verbose=verbose, machine=machine, hold_tree=hold_tree,
            # The fake worktree for branch b is /wt/b, so its name is its branch.
            branch=branch or (lambda wt: Path(wt).name))
        self.sched = self._make()

    @property
    def run(self):
        """The session's run: the object the Scheduler mutates."""
        return self.session.run

    def close(self):
        """Release the DRIVER session. The autouse fixture also calls this."""
        self._stack.close()

    def _record(self, kind, **fields):
        """The one event sink: the Scheduler's own events and every reported outcome."""
        self.events.append((kind, fields))

    def resume(self):
        """A fresh Scheduler over the same session, as `pr batch resume` after an interrupt."""
        self.sched = self._make()
        return self.sched

    def _publish(self, argv, log_path=None):
        self.published.append(argv)
        return self.publish_code

    def _spawn(self, argv, *, log_path, trail_root, env=None):
        h = self
        # One past the number spawned so far, not a constant: each fake step gets
        # its own pid, so a kill or an RSS read aimed at one step cannot land on another.
        next_pid = len(self.spawned) + 1
        key = (step_name(argv), argv[-1])
        if key in self.moves:
            self.heads[argv[-1]] = f"{key[0]}-{next_pid}"
        out = self.stdouts.get(key, "{}")

        class Proc:
            pid = next_pid
            polls = 0

            def poll(self):
                self.polls += 1
                if self.polls < 2:
                    return None
                if not getattr(self, "done", False):
                    self.done = True
                    h.live -= 1
                return h.codes.get(key, 0)

            def drain_lines(self):
                return []

            def stdout(self):
                return out

            def kill(self):
                pass

        self.live += 1
        self.max_live = max(self.max_live, self.live)
        self.spawned.append(argv)
        return Proc()

    def kinds(self):
        return [k for k, _ in self.events]
