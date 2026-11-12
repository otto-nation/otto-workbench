"""Resume an interrupted comments-fix pass from a checkpointed phase."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _answering_the_owner, _fake_ctx, _git_ran, _no_published_summary,
    _tick_every_fix,
)
import agent.backend
import fix.comment_checklist
import fix.comments
import fix.verify
import git.client
import git.topology
import pr.fix_checkpoint
import pr.fix_state
import pr.thread_context
from pr.comments_state import ThreadState
from pr.fix_checkpoint import CheckpointPhase
from pr.thread_models import CommentItem, PRReport, ReportThread, TriageResult

# `_no_published_summary` is autouse.
assert _no_published_summary


class _Killed(BaseException):
    """A stop that is not KeyboardInterrupt, so the engine does not fold it."""


def _item(tid, verification, **kw):
    return CommentItem(
        id=tid, file="f.go", line=10, reviewer="kgn", summary=f"{tid} summary",
        classification="actionable_suggestion", verification=verification,
        complexity="low", state=ThreadState.NEW, **kw,
    )


def _save_then_kill(phase):
    real = pr.fix_checkpoint.save

    def wrapped(target, ckpt):
        ok = real(target, ckpt)
        if ckpt.phase is phase:
            raise _Killed(phase)
        return ok
    return wrapped


def _run(tmp_path, *, kill_at=None, resume=None, invoke=None, posts=None,
         invocations=None):
    threads = [
        _item("t1", "valid"),
        _item("t2", "invalid", reasoning="not our layer"),
    ]
    report = PRReport(
        repo="owner/repo", pr_number=1,
        threads=[
            ReportThread(
                id=t.id, file=t.file, line=t.line,
                comments=[{"databaseId": 100 + n}],
            )
            for n, t in enumerate(threads)
        ],
    )
    ctx = _fake_ctx(tmp_path)
    if posts is None:
        posts = []
    if invocations is None:
        invocations = []

    def counting_invoke(invocation):
        invocations.append(invocation)
        if invoke is not None:
            return invoke(invocation)
        return _tick_every_fix(tmp_path)(invocation)

    def mock_run(*cmd, **kwargs):
        return _git_ran(0, stdout="abc1234\n")

    def post_reply(*args, **kwargs):
        posts.append(args)
        return True

    save = _save_then_kill(kill_at) if kill_at is not None else pr.fix_checkpoint.save
    with patch.object(agent.backend, "invoke_fix", side_effect=counting_invoke), \
         patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
         patch.object(fix.comment_checklist, "find_and_update_main_worktree",
                      return_value=None), \
         patch.object(git.topology, "default_branch_cached", return_value="main"), \
         patch.object(pr.fix_state, "persist"), \
         patch.object(git.client, "run", side_effect=_answering_the_owner(mock_run)), \
         patch("pr.comments.post_thread_reply", side_effect=post_reply), \
         patch("pr.comments.post_issue_comment", return_value="u"), \
         patch("pr.comments.resolve_thread", return_value=True), \
         patch.object(fix.verify, "run", return_value={}), \
         patch.object(pr.fix_checkpoint, "save", side_effect=save):
        return fix.comments.run_pass(
            TriageResult(threads=threads), report, tmp_path, ctx, resume=resume,
        )


class TestRunPassResumesFromAKilledSave:
    """Kill at a phase via save, then resume from the file that write left."""

    @pytest.mark.parametrize("phase", [
        CheckpointPhase.TRIAGED,
        CheckpointPhase.ADDRESSED,
        CheckpointPhase.REPLIED,
        CheckpointPhase.AGENT_DONE,
    ])
    def test_a_killed_pass_resumes_without_repeating_posts_or_the_agent(
        self, tmp_path, phase, publishing_on,
    ):
        posts = []
        invocations = []
        with pytest.raises(_Killed):
            _run(tmp_path, kill_at=phase, posts=posts, invocations=invocations)
        loaded = pr.fix_checkpoint.load(tmp_path)
        assert loaded is not None
        assert loaded.phase is phase

        _run(tmp_path, resume=loaded, posts=posts, invocations=invocations)
        posted = [(p[2], p[3]) for p in posts]
        assert posted, "a completed resume posted nothing"
        assert len(posted) == len(set(posted)), "a reply was posted twice"
        assert len(invocations) == 1, "agent invoked once across both runs"
        final = pr.fix_checkpoint.load(tmp_path)
        assert final is not None
        assert final.phase is CheckpointPhase.COMPLETE

    def test_interrupted_folds_ticked_answers(self, tmp_path, publishing_on):
        def tick_then_stop(invocation):
            _tick_every_fix(tmp_path)(invocation)
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            _run(tmp_path, invoke=tick_then_stop)
        loaded = pr.fix_checkpoint.load(tmp_path)
        assert loaded is not None
        assert loaded.phase is CheckpointPhase.AGENT_RUNNING
        assert any(
            o.id == "t1" and o.outcome.value != "deferred"
            for o in loaded.engine.answers
        )
