"""Interrupted comments-pass leftovers are resumed when HEAD and blobs still match."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conftest import git_out, make_ctx

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke  # noqa: E402
import fix.comments_resume  # noqa: E402
import fix.engine  # noqa: E402
import fix.resume  # noqa: E402
import git.client  # noqa: E402
import pr.comments  # noqa: E402
import pr.fix_checkpoint  # noqa: E402
from fix_containment_support import _repo  # noqa: E402
from fix_engine_support import StubAdapter, _answer  # noqa: E402
from pr.fix_checkpoint import CheckpointPhase, EngineProgress  # noqa: E402
from pr.thread_models import PRReport  # noqa: E402
from review_fix_pass_support import _PUSHED  # noqa: E402


def test_a_matching_blob_at_the_same_head_is_dropped_from_dirty_before(tmp_path, capsys):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    dirty = {"a.py", "other.py"}
    trail = Mock()

    fix.resume.record(artifacts, wt, head, ["a.py"])
    out = fix.resume.adopt(artifacts, wt, dirty, head, trail)

    assert "a.py" not in out
    assert "other.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()
    assert "Resuming 1 edit" in capsys.readouterr().err
    trail.info.assert_called()
    assert trail.info.call_args.args[0] == "fix_leftovers_resumed"


def test_a_changed_blob_stays_in_dirty_before(tmp_path):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(artifacts, wt, head, ["a.py"])
    (wt / "a.py").write_text("changed again\n")

    out = fix.resume.adopt(artifacts, wt, {"a.py"}, head, None)

    assert "a.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()


def test_a_moved_head_keeps_the_dirt_and_clears_the_file(tmp_path):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(artifacts, wt, head, ["a.py"])
    (wt / "b.py").write_text("other\n")
    git_out(wt, "add", "b.py")
    git_out(wt, "commit", "-qm", "move HEAD")

    out = fix.resume.adopt(
        artifacts, wt, {"a.py"}, git.client.head_sha(cwd=wt), None)

    assert "a.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()


class _Resuming(StubAdapter):
    resumes_interrupted = True

    def landing(self, outcomes, changed):
        self.landing_scope = changed
        return fix.engine.LandSpec(message="fix: stub", paths=changed)


@patch("git.push.push", return_value=_PUSHED)
def test_resumed_edits_appear_in_the_landing_paths(_push, tmp_path):
    """The leftover is attributed to the pass only because adopt dropped it."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    adapter = _Resuming(wt)
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(adapter.artifacts, wt, head, ["a.py"])

    with patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)):
        fix.engine.run(adapter)

    assert adapter.landing_scope is not None
    assert "a.py" in adapter.landing_scope


@patch("git.push.push", return_value=_PUSHED)
def test_checkpoint_resume_does_not_adopt_and_clears_leftover_record(
        _push, tmp_path):
    """Checkpoint resume owns dirty_before; leftover-adoption must not run."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    adapter = _Resuming(wt)
    adapter.keeps_checkpoints = True
    sha = git.client.head_sha(cwd=wt)
    adapter.artifacts.mkdir(parents=True, exist_ok=True)
    fix.resume.record(adapter.artifacts, wt, sha, ["a.py"])
    leftover = adapter.artifacts / "interrupted-pass.json"
    assert leftover.is_file()
    adapter.resume = EngineProgress(head_before=sha, branch="HEAD")
    adapter.resume_phase = CheckpointPhase.LANDED
    with (
        patch.object(fix.resume, "adopt", wraps=fix.resume.adopt) as adopt,
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
    ):
        fix.engine.run(adapter)
    adopt.assert_not_called()
    assert not leftover.is_file()


@patch("git.push.push", return_value=_PUSHED)
def test_fresh_pass_still_adopts_pending_leftovers(_push, tmp_path):
    """No checkpoint: leftover-adoption still rewrites dirty_before."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    adapter = _Resuming(wt)
    sha = git.client.head_sha(cwd=wt)
    adapter.artifacts.mkdir(parents=True, exist_ok=True)
    fix.resume.record(adapter.artifacts, wt, sha, ["a.py"])
    with (
        patch.object(fix.resume, "adopt", wraps=fix.resume.adopt) as adopt,
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
    ):
        fix.engine.run(adapter)
    adopt.assert_called_once()


@patch("git.push.push", return_value=_PUSHED)
def test_agent_running_checkpoint_keeps_dirty_before_out_of_landing(
        _push, tmp_path):
    """AGENT_RUNNING resume must not leftover-adopt paths already in dirty_before."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    adapter = _Resuming(wt)
    adapter.keeps_checkpoints = True
    sha = git.client.head_sha(cwd=wt)
    adapter.artifacts.mkdir(parents=True, exist_ok=True)
    fix.resume.record(adapter.artifacts, wt, sha, ["a.py"])
    leftover = adapter.artifacts / "interrupted-pass.json"
    adapter.resume = EngineProgress(
        head_before=sha, branch="HEAD", dirty_before=["a.py"],
    )
    adapter.resume_phase = CheckpointPhase.AGENT_RUNNING
    with (
        patch.object(fix.resume, "adopt", wraps=fix.resume.adopt) as adopt,
        patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)),
    ):
        fix.engine.run(adapter)
    adopt.assert_not_called()
    assert adapter.landing_scope is not None
    assert "a.py" not in adapter.landing_scope
    assert not leftover.is_file()


def test_moved_head_fresh_decide_keeps_leftovers_dirty(tmp_path):
    """HEAD moved: decide() starts fresh; adopt keeps dirt and drops the record."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    target = tmp_path / "target"
    sha = git.client.head_sha(cwd=wt)
    ctx = make_ctx(
        repo="owner/repo", pr_number=7, branch="feat/x",
        worktree_root=wt, target_dir=target, head_sha=sha,
    )
    ckpt = pr.fix_checkpoint.CommentsFixCheckpoint(
        version=pr.fix_checkpoint.CHECKPOINT_VERSION,
        phase=CheckpointPhase.AGENT_RUNNING,
        repo=ctx.repo, pr=ctx.pr_number or 0, branch=ctx.branch,
        head_sha=sha,
        engine=EngineProgress(head_before=sha, dirty_before=["a.py"]),
    )
    assert pr.fix_checkpoint.save(ctx.target_dir, ckpt)
    artifacts = pr.comments.artifacts_dir(ctx.target_dir)
    fix.resume.record(artifacts, wt, sha, ["a.py"])
    leftover = artifacts / "interrupted-pass.json"
    (wt / "b.py").write_text("other\n")
    git_out(wt, "add", "b.py")
    git_out(wt, "commit", "-qm", "move HEAD")
    moved = make_ctx(
        repo="owner/repo", pr_number=7, branch="feat/x",
        worktree_root=wt, target_dir=target,
        head_sha=git.client.head_sha(cwd=wt),
    )
    decision = fix.comments_resume.decide(
        moved, wt, fix.comments_resume.ResumeMode.AUTO, PRReport(),
    )
    assert not decision.resumes
    out = fix.resume.adopt(
        artifacts, wt, {"a.py"}, git.client.head_sha(cwd=wt), None)
    assert "a.py" in out
    assert not leftover.is_file()


class _Boom(_Resuming):
    def interrupted(self, leftovers, head_before):
        raise RuntimeError("domain failed")


def test_interrupted_hook_failure_still_records_leftovers(tmp_path):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    adapter = _Boom(wt)
    leftover = adapter.artifacts / "interrupted-pass.json"
    with patch.object(agent.invoke, "run_fix", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            fix.engine.run(adapter)
    assert leftover.is_file()
