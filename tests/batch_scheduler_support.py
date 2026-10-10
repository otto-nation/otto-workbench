"""The scheduler harness the batch_scheduler suites share.

A Scheduler whose spawn, replan, worktrees, head, publish runner and tree reads
are all fakes, so a run can be driven tick by tick with no git and no network.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.scheduler  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402

GiB = 1024 ** 3
NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
ALL = {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NEED}
HEALTHY = batch.admission.HostSample(8 * GiB, 1.0, 0.0)


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


@pytest.fixture(autouse=True)
def _quiet_outcomes(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "fix_checks", lambda wt, hb: [])
    monkeypatch.setattr(batch.outcomes, "ci_unfixed", lambda stdout: None)


class Harness:
    def __init__(self, rows, *, codes=None, auto_publish=(), pool=2, host=HEALTHY,
                 replan=None, worktrees=None, heads=None, selected=None, cfg=None,
                 runner=None, tree=None, closeout=None, moves=(), stdouts=None,
                 contains=None, dirty=None, rebasing=None, watch_ci=False,
                 verbose=False):
        self.codes = codes or {}
        self.moves, self.stdouts = set(moves), stdouts or {}
        self.spawned, self.events, self.live, self.max_live = [], [], 0, 0
        self.published, self.publish_code = [], 0
        self.run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER), selected=selected, pool=pool,
                               auto_publish=list(auto_publish), watch_ci=watch_ci)
        self.heads = heads or {}
        self._make = lambda: batch.scheduler.Scheduler(
            self.run, pr_bin="pr", cfg=cfg or BatchConfig(pool_max=4),
            host=lambda: host, spawn=self._spawn,
            replan=replan or (lambda r: r),
            worktrees=worktrees or (lambda d, b: WorktreeResult(f"/wt/{b}", False, "")),
            head=lambda wt: self.heads.get(wt, "h0"), rss=lambda pid: 0,
            emit=lambda kind, **f: self.events.append((kind, f)), sleep=lambda s: None,
            estimates=batch.admission.Estimates({}),
            runner=runner or self._publish, tree=tree or ff_tree,
            closeout=closeout or (lambda d, b: CloseoutDebt()),
            contains=contains or (lambda wt, sha: True),
            dirty=dirty or (lambda wt: False), rebasing=rebasing or (lambda wt: False),
            verbose=verbose)
        self.sched = self._make()

    def resume(self):
        """A fresh Scheduler over the same run, as `pr batch resume` builds after an interrupt."""
        self.sched = self._make()
        return self.sched

    def _publish(self, argv, log_path=None):
        self.published.append(argv)
        return self.publish_code

    def _spawn(self, argv, *, log_path, trail_root):
        h = self
        # One past the number of processes spawned so far, not a constant: a
        # test asserting on which pid got killed needs spawns to be
        # distinguishable from each other.
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
