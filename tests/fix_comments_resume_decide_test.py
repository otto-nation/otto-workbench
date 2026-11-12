"""fix.comments_resume.decide against a real git worktree."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import commit_all, git_out, make_ctx
from review_convergence_support import committed_repo, head
import fix.comments_resume as resume
import fix.pass_resume as fix_resume
import pr.fix_checkpoint as ckpt_mod
from pr.comments_state import ThreadState
from pr.fix_checkpoint import CheckpointPhase, EngineProgress
from pr.thread_models import CommentItem, PRReport, ReportThread, TriageResult


def _ctx(repo: Path, target: Path, **kw):
    return make_ctx(
        repo="owner/repo", pr_number=7, branch="feat/x",
        worktree_root=repo, target_dir=target,
        head_sha=kw.pop("head_sha", head(repo)),
        **kw,
    )


def _write(ctx, **kw):
    kw.setdefault("version", ckpt_mod.CHECKPOINT_VERSION)
    kw.setdefault("phase", CheckpointPhase.TRIAGED)
    kw.setdefault("repo", ctx.repo)
    kw.setdefault("pr", ctx.pr_number or 0)
    kw.setdefault("branch", ctx.branch)
    kw.setdefault("head_sha", ctx.head_sha)
    if "engine" not in kw and CheckpointPhase(kw["phase"]).reached(
        CheckpointPhase.AGENT_RUNNING
    ):
        kw["engine"] = EngineProgress(head_before=kw["head_sha"])
    ckpt = ckpt_mod.CommentsFixCheckpoint(**kw)
    assert ckpt_mod.save(ctx.target_dir, ckpt)
    return ckpt


def _report(*tids: str) -> PRReport:
    return PRReport(
        threads=[
            ReportThread(id=tid, state=ThreadState.NEW) for tid in tids
        ],
    )


def _decide(ctx, repo, mode, report=None):
    return resume.decide(ctx, repo, mode, report or PRReport())


def test_mode_for_maps_the_flags():
    assert resume.mode_for(False, False) is resume.ResumeMode.AUTO
    assert resume.mode_for(True, False) is resume.ResumeMode.REQUIRE
    assert resume.mode_for(False, True) is resume.ResumeMode.FRESH


def test_no_file_auto_is_empty(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    decision = _decide(ctx, repo, resume.ResumeMode.AUTO)
    assert not decision.resumes
    assert not decision.refused


def test_no_file_require_refuses(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    decision = _decide(ctx, repo, resume.ResumeMode.REQUIRE)
    assert decision.refused
    assert "no interrupted `pr comments --fix` pass to resume for PR #7" in (
        decision.refusal
    )


def test_fresh_deletes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(ctx)
    assert ckpt_mod.checkpoint_path(ctx.target_dir).is_file()
    decision = _decide(ctx, repo, resume.ResumeMode.FRESH)
    assert not decision.resumes
    assert not ckpt_mod.checkpoint_path(ctx.target_dir).is_file()


def test_head_moved_auto_none_require_refusal(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(ctx, engine=EngineProgress(head_before=ctx.head_sha, agent_files=["a.py"]))
    (repo / "a.py").write_text("y\n")
    commit_all(repo, "moved")
    moved = _ctx(repo, ctx.target_dir)
    auto = _decide(moved, repo, resume.ResumeMode.AUTO)
    assert not auto.resumes
    assert not auto.refused
    req = _decide(moved, repo, resume.ResumeMode.REQUIRE)
    assert req.refused
    assert "HEAD has moved" in req.refusal


def test_checked_with_one_commit_on_top_resumes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    first = head(repo)
    ctx = _ctx(repo, tmp_path / "target", head_sha=first)
    _write(ctx, phase=CheckpointPhase.CHECKED, head_sha=first)
    (repo / "a.py").write_text("y\n")
    commit_all(repo, "landed")
    live = _ctx(repo, ctx.target_dir)
    decision = _decide(live, repo, resume.ResumeMode.AUTO)
    assert decision.resumes
    assert decision.checkpoint.phase is CheckpointPhase.CHECKED


def test_landed_at_landed_sha_resumes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    first = head(repo)
    ctx = _ctx(repo, tmp_path / "target", head_sha=first)
    (repo / "a.py").write_text("y\n")
    commit_all(repo, "landed")
    landed = head(repo)
    _write(
        ctx, phase=CheckpointPhase.LANDED, head_sha=first,
        engine=EngineProgress(head_before=first, commit_sha=landed),
    )
    live = _ctx(repo, ctx.target_dir, head_sha=landed)
    decision = _decide(live, repo, resume.ResumeMode.AUTO)
    assert decision.resumes


def test_agent_file_edited_refuses_naming_it(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    blob = git_out(repo, "rev-parse", "HEAD:a.py").strip()
    _write(
        ctx, phase=CheckpointPhase.AGENT_DONE,
        engine=EngineProgress(head_before=ctx.head_sha, file_shas={"a.py": blob}),
    )
    (repo / "a.py").write_text("edited\n")
    decision = _decide(ctx, repo, resume.ResumeMode.AUTO)
    assert decision.refused
    assert "a.py" in decision.refusal
    assert "--no-resume" in decision.refusal


def test_terminal_phases_ignored(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    for phase in (CheckpointPhase.COMPLETE, CheckpointPhase.ADOPTED):
        _write(ctx, phase=phase)
        auto = _decide(ctx, repo, resume.ResumeMode.AUTO)
        assert not auto.resumes
        req = _decide(ctx, repo, resume.ResumeMode.REQUIRE)
        assert req.refused
        assert "no interrupted" in req.refusal


def test_new_open_thread_at_triaged_auto_fresh(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(
        ctx, phase=CheckpointPhase.TRIAGED,
        triage=TriageResult(threads=[CommentItem(id="t1")]),
    )
    decision = _decide(
        ctx, repo, resume.ResumeMode.AUTO, _report("t1", "t2"),
    )
    assert not decision.resumes
    assert not decision.refused


def test_new_open_thread_at_agent_done_resumes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(
        ctx, phase=CheckpointPhase.AGENT_DONE,
        triage=TriageResult(threads=[CommentItem(id="t1")]),
    )
    decision = _decide(
        ctx, repo, resume.ResumeMode.AUTO, _report("t1", "t2"),
    )
    assert decision.resumes


def test_matching_head_auto_resumes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    written = _write(ctx)
    decision = _decide(ctx, repo, resume.ResumeMode.AUTO)
    assert decision.resumes
    assert decision.checkpoint.phase is written.phase


def test_new_open_thread_at_triaged_require_resumes(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(
        ctx, phase=CheckpointPhase.TRIAGED,
        triage=TriageResult(threads=[CommentItem(id="t1")]),
    )
    decision = _decide(
        ctx, repo, resume.ResumeMode.REQUIRE, _report("t1", "t2"),
    )
    assert decision.resumes


def test_wrong_pr_is_unusable(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(ctx, pr=99)
    auto = _decide(ctx, repo, resume.ResumeMode.AUTO)
    assert not auto.resumes
    assert not auto.refused
    req = _decide(ctx, repo, resume.ResumeMode.REQUIRE)
    assert req.refused
    assert "no interrupted" in req.refusal


def test_unreadable_worktree_refuses(tmp_path):
    repo = committed_repo(tmp_path / "wt", {"a.py": "x\n"})
    ctx = _ctx(repo, tmp_path / "target")
    _write(ctx, phase=CheckpointPhase.AGENT_DONE)
    with patch.object(fix_resume, "edited_since", return_value=None):
        decision = _decide(ctx, repo, resume.ResumeMode.AUTO)
    assert decision.refused
    assert "cannot read the worktree" in decision.refusal
