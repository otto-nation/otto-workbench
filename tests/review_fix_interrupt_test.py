"""A fix pass stopped mid-flight: the stop propagates, and what it left is recorded."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import review.fix  # noqa: E402
from fix_engine_support import StubAdapter, _settles_at, head  # noqa: F401,E402
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401,E402
from review.convergence import blob_of  # noqa: E402
from review.paths import read_review_meta  # noqa: E402
from review.types import FixInterruption  # noqa: E402
from review_convergence_support import committed_repo, review_job  # noqa: E402


class _Trail:
    def __init__(self):
        self.events = []

    def info(self, action, detail, data=None):
        self.events.append(("info", action))

    def warn(self, action, detail, data=None):
        self.events.append(("warn", action))


class _Recording(StubAdapter):
    stopped = None

    def interrupted(self, leftovers, head_before):
        self.stopped = SimpleNamespace(leftovers=leftovers, head_before=head_before)


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_records_what_the_agent_left_and_still_propagates(
        tmp_path, head, snapshots, stop):
    adapter, trail = _Recording(tmp_path), _Trail()
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", side_effect=stop):
        with pytest.raises(stop):
            fix.engine.run(adapter, trail=trail)
    assert adapter.stopped.leftovers == frozenset({"a.py"})
    assert adapter.stopped.head_before == "9999999"
    assert ("warn", "fix_interrupted") in trail.events


def test_a_failing_record_does_not_replace_the_stop(tmp_path, head, snapshots):
    class _Broken(StubAdapter):
        def interrupted(self, leftovers, head_before):
            raise OSError("disk full")
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    with patch.object(agent.invoke, "run_fix", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            fix.engine.run(_Broken(tmp_path))


def test_the_review_adapter_records_the_leftovers_by_blob(tmp_path):
    wt = committed_repo(tmp_path / "wt", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    job = review_job(wt, tmp_path / "rev")
    (wt / "a.py").write_text("x = 2\n")
    (wt / "b.py").unlink()
    review.fix.ReviewFixAdapter(job, []).interrupted(frozenset({"a.py", "b.py"}), "h" * 40)
    stopped = read_review_meta(Path(job.artifact_dir)).fix_interrupted
    assert stopped == FixInterruption("h" * 40, {"a.py": blob_of(wt, "a.py"), "b.py": ""})
