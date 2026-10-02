"""The stub domain and snapshot stubs every fix.engine suite drives the pipeline with.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import git.land  # noqa: E402
from core.phases import Phase  # noqa: E402
from fix.types import FixItem  # noqa: E402
from git.land import CommitStatus  # noqa: E402
import fix.scope
import git.client


# ── the stub domain ─────────────────────────────────────────────────────────


class StubAdapter(fix.engine.FixAdapter):
    """A domain that hands over `count` items and records what came back.

    `ci_fix` is borrowed as the phase because its template asks for nothing
    beyond the substitutions the engine supplies — `template_vars` returning
    nothing is then a real statement rather than a stub's convenience.
    """

    phase = Phase.CI_FIX
    title = "Stub Fix Tracking"
    action = "fixing things"
    item_noun = "item"

    def __init__(self, wt_path, count=1, *, spec=None):
        self.workdir = Path(wt_path)
        self.artifacts = self.workdir / "artifacts"
        self.branch = "isaac/feat/x"
        self.repo = "owner/repo"
        self._count = count
        self._spec = spec or fix.engine.LandSpec(message="fix: stub")
        self.recorded = None

    def items(self):
        return [
            FixItem(id=f"i{n}", file="a.py", line=n + 1, label=f"item {n}",
                    body=f"body {n}")
            for n in range(self._count)
        ]

    def template_vars(self):
        return {}

    def landing(self, outcomes, changed):
        self.landing_saw = list(outcomes)
        self.landing_scope = changed
        return self._spec

    def record(self, run):
        self.recorded = run


def _answer(adapter, *, tick="fixed", ids=None, reason=None):
    """A `run_fix` stub that answers the checklist it finds on disk.

    The engine rewrites the file immediately before each invocation, so an
    answer written any earlier is thrown away before an agent would see it.
    `ids` limits the answer to those items; the rest are left as work owed.

    `reason` writes the agent's words after the tick, replacing the `<why>` the
    render leaves there. Left unset, the placeholder stands and the parse reads
    an empty reason — the evidence-less path, which is what an agent that ticks
    the box and says nothing produces.
    """
    def run_fix(_phase, _prompt, **_kwargs):
        text = adapter.tracking_path.read_text()
        out = []
        keep = True
        for line in text.splitlines(keepends=True):
            if line.startswith("## <!-- fix:"):
                keep = ids is None or line.split("fix:")[1].split(" ")[0] in ids
            if keep and line.startswith(f"- [ ] {tick}"):
                line = (f"- [x] {tick} — {reason}\n" if reason is not None
                        else line.replace("- [ ]", "- [x]", 1))
            out.append(line)
        adapter.tracking_path.write_text("".join(out))
        return agent.invoke.FixResult(0, None)
    return run_fix


@pytest.fixture
def landed():
    """Stub the landing owner out; `land_test.py` holds what it really does."""
    with patch.object(git.land, "land",
                      return_value=git.land.LandResult(CommitStatus.PUSHED, "abc1234")) as m:
        yield m


@pytest.fixture
def head():
    with patch.object(git.client, "head_sha", return_value="9999999"):
        yield


@pytest.fixture(autouse=True)
def snapshots():
    """An empty worktree before the agent and after it, unless a test says otherwise.

    Autouse because every run now reads the dirty set on both sides of the
    agent, and `tmp_path` is not a repo — an unstubbed read fails, which the
    engine correctly treats as a reason not to run the pass at all.
    """
    with patch.object(fix.scope, "changed_files",
                      return_value=set()) as m:
        yield m


# How many times a one-batch pass reads the worktree: the shared baseline, the
# reading that closes the batch's own observation, and the final one the commit
# scope is taken from. Named because the tests below drive `changed_files` by a
# list of answers, and a list the wrong length fails as a StopIteration inside
# mock rather than as anything about fix passes.
PASS_READS = 3


def _reads(baseline, *rest):
    """Snapshot answers for a one-batch pass, padded to the reads it makes.

    A test cares about the baseline and the final reading — the difference
    between them is the commit scope. The batch-level reading in between is the
    engine's own bookkeeping, and every test here would otherwise have to
    restate it to keep the list long enough.

    The last answer given is repeated to fill, so a test naming two readings
    gets its second one at the position the commit scope is taken from. Each
    repetition is its own set object — callers only ever combine a reading
    with `-`/`|`, but a copy per slot keeps a future mutating caller from
    corrupting every other stubbed reading in the list.

    Sized to `PASS_READS`, which is a one-batch pass's own read count — a
    multi-batch or retried pass reads the tree more times than this pads for,
    and needs `_settles_at` instead.
    """
    answers = [baseline, *rest]
    padding = PASS_READS - len(answers) + 1
    return [*answers[:-1], *(_copy(answers[-1]) for _ in range(padding))]


def _copy(reading):
    """A fresh copy of a stubbed `changed_files` reading, or `None` unchanged."""
    return set(reading) if reading is not None else None


def _settles_at(baseline, final):
    """A snapshot stub that answers `baseline` once and `final` ever after.

    For a pass whose read count is not fixed in advance. A deferral triggers the
    retry, which is a second invocation with two readings of its own, so a test
    about deferrals cannot state a list of the right length without encoding how
    many times the engine retries — which is not what those tests are about, and
    would fail them for a change to the retry rather than to reconciliation.

    The worktree it describes is one an agent edited once and then left alone:
    every reading after the first shows the same difference from the baseline.
    Each reading returned is its own set object, so a caller that mutated one
    in place could not corrupt a later stubbed reading.
    """
    answers = iter([baseline])
    return lambda *_a, **_k: _copy(next(answers, final))


def _run(adapter, **kwargs):
    with patch.object(agent.invoke, "run_fix",
                      side_effect=kwargs.pop("run_fix", _answer(adapter))) as inv:
        run = fix.engine.run(adapter, **kwargs)
    return run, inv


# ── the verify gate ─────────────────────────────────────────────────────────
#
# A fix the agent ticked is a claim it edited something, not a claim the edit
# works. The gate is what turns the first into evidence for the second before a
# commit is made or a reviewer is told anything.


def _verdicts(*pairs):
    """A `run_verify` stub answering the ids it was given, in one dict."""
    def run_verify(_phase, _prompt, **_kwargs):
        return dict(pairs)
    return run_verify
