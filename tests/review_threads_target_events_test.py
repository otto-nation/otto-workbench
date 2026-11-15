"""`pr comments` files step_started/step_finished in its target's history.

Revert: delete `history.started()` in cli.review_threads.main; the started test fails.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx  # noqa: E402
import cli.locks  # noqa: E402
import cli.pr  # noqa: E402
import cli.review_threads  # noqa: E402
import core.run_events  # noqa: E402
import pr.context  # noqa: E402
import pr.target_events  # noqa: E402
import review.comment_threads  # noqa: E402
import review.review_body_reply  # noqa: E402


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


def _run(target, argv, *, threads=0, ctx=None):
    starts = []
    real = cli.review_threads.Trail.start

    def spy(*a, **k):
        trail = real(*a, **k)
        starts.append(trail)
        return trail

    with patch.object(pr.context, "resolve", return_value=ctx or make_ctx(
            target_dir=target, worktree_root=target)), \
         patch.object(cli.locks, "claim_declared"), \
         patch.object(cli.review_threads.Trail, "start", side_effect=spy), \
         patch.object(review.comment_threads, "run_threads",
                      side_effect=threads if callable(threads) else (lambda *a: threads)):
        code = cli.review_threads.main(argv)
    return code, starts


def _kinds(target):
    return [(e["kind"], e.get("status"), e.get("exit_code"))
            for e in core.run_events.read(target).events]


def test_a_fix_run_files_started_then_finished_under_its_trail(target):
    code, starts = _run(target, ["--fix"], threads=0)
    assert code == 0
    events = core.run_events.read(target).events
    assert [(e["kind"], e["step"], e["item"]) for e in events] == [
        ("step_started", "comments", "owner/repo#42"),
        ("step_finished", "comments", "owner/repo#42")]
    assert {e["run"] for e in events} == {starts[0].invocation}
    assert events[1]["status"] == "done" and events[1]["exit_code"] == 0


def test_the_exit_code_is_unchanged_and_filed(target):
    code, _ = _run(target, ["--triage"], threads=3)
    assert code == 3
    assert _kinds(target) == [("step_started", None, None), ("step_finished", "failed", 3)]


def test_a_raise_is_filed_as_failed_with_no_exit_code(target):
    def boom(*a):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        _run(target, ["--fix"], threads=boom)
    assert _kinds(target) == [("step_started", None, None), ("step_finished", "failed", None)]


def test_settle_files_nothing(target):
    with patch.object(cli.review_threads, "_github_only", return_value=0):
        code, starts = _run(target, ["--settle", "PRRT_1", "--as", "fixed"])
    assert code == 0 and starts == []
    assert not core.run_events.events_path(target).exists()


def test_a_review_body_reply_files_nothing(target):
    route = SimpleNamespace(source="review-body", pr_data=None, review_body_ids=())
    with patch.object(cli.review_threads.pr.thread_replies, "load_reply_body", return_value="hi"), \
         patch.object(review.review_body_reply, "route_reply", return_value=route), \
         patch.object(review.review_body_reply, "run_review_body_reply", return_value=0):
        code, starts = _run(target, ["--reply", "123"])
    assert code == 0 and len(starts) == 1
    assert not core.run_events.events_path(target).exists()


def test_a_queued_parent_files_nothing(target, monkeypatch):
    calls = []
    monkeypatch.setattr(pr.target_events.StepHistory, "_file",
                        lambda self, kind, **f: calls.append(kind))
    with patch("cli.queue_gate.gate", return_value=0), \
         patch.object(cli.review_threads, "main", side_effect=AssertionError("dispatched")):
        try:
            code = cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR)
        except SystemExit as exc:
            code = exc.code
    assert code == 0 and calls == []
