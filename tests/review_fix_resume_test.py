"""The run after a stopped fix pass: leftovers set aside, then resume or review the rest."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git.client  # noqa: E402
import review.collect  # noqa: E402
import review.convergence  # noqa: E402
import review.invoke  # noqa: E402
import review.orchestrate  # noqa: E402
import review.run  # noqa: E402
from conftest import git_out  # noqa: E402
from core.proc import CmdResult  # noqa: E402
from review.convergence import blob_of  # noqa: E402
from review.paths import read_review_meta, write_review_meta  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.store import StoredReview  # noqa: E402
from review.types import FixInterruption, ReviewMeta  # noqa: E402
from review_convergence_support import committed_repo, head, review_job  # noqa: E402


def _repo(tmp_path):
    return committed_repo(tmp_path / "wt", {"a.py": "x = 1\n", "b.py": "y = 1\n"})


def _stopped(tmp_path, wt, leftovers):
    """A review directory recording an interruption on HEAD with *leftovers* as left."""
    review_dir = tmp_path / "rev"
    review_dir.mkdir()
    stopped = FixInterruption(head(wt), {p: blob_of(wt, p) for p in leftovers})
    write_review_meta(review_dir, ReviewMeta(head_sha=head(wt), fix_interrupted=stopped))
    return review_dir, stopped


def test_an_interruption_counts_only_on_the_head_it_happened_on(tmp_path):
    wt = _repo(tmp_path)
    review_dir, stopped = _stopped(tmp_path, wt, [])
    assert review.convergence.interrupted_fix(review_dir, str(wt)) == stopped
    write_review_meta(review_dir, ReviewMeta(head_sha="e" * 40, fix_interrupted=stopped))
    assert review.convergence.interrupted_fix(review_dir, str(wt)) == stopped
    drifted = FixInterruption("e" * 40, {})
    write_review_meta(review_dir, ReviewMeta(head_sha=head(wt), fix_interrupted=drifted))
    assert review.convergence.interrupted_fix(review_dir, str(wt)) is None
    empty = FixInterruption("", {})
    write_review_meta(review_dir, ReviewMeta(head_sha=head(wt), fix_interrupted=empty))
    assert review.convergence.interrupted_fix(review_dir, str(wt)) == empty
    write_review_meta(review_dir, ReviewMeta(head_sha="e" * 40, fix_interrupted=empty))
    assert review.convergence.interrupted_fix(review_dir, str(wt)) is None


def test_untouched_leftovers_are_stashed_and_the_record_cleared(tmp_path, capsys):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    (wt / "b.py").write_text("y = 2\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["a.py", "b.py"])
    (wt / "b.py").write_text("y = 3 — the operator's edit\n")
    message = review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    assert message and message in git_out(wt, "stash", "list")
    assert (wt / "a.py").read_text() == "x = 1\n"
    assert (wt / "b.py").read_text().startswith("y = 3")
    meta = read_review_meta(review_dir)
    assert meta.fix_interrupted is None and meta.dirty_files == ("a.py",)
    err = capsys.readouterr().err
    assert "a.py" in err and "b.py" in err


def test_a_non_leftover_dirty_file_is_never_stashed(tmp_path):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    (wt / "b.py").write_text("y = 2\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["a.py"])
    review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    assert (wt / "a.py").read_text() == "x = 1\n"
    assert (wt / "b.py").read_text() == "y = 2\n"


def test_new_and_deleted_leftovers_are_stashed_too(tmp_path):
    wt = _repo(tmp_path)
    (wt / "c.py").write_text("new\n")
    (wt / "b.py").unlink()
    review_dir, stopped = _stopped(tmp_path, wt, ["c.py", "b.py"])
    assert stopped.leftovers["b.py"] == ""
    assert review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    assert not (wt / "c.py").exists() and (wt / "b.py").read_text() == "y = 1\n"
    assert git_out(wt, "status", "--short").strip() == ""


def test_after_the_stash_a_review_without_fix_is_incremental(tmp_path):
    """The tree is clean and on the reviewed HEAD; the stashed files are still the delta."""
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["a.py"])
    review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    job = review_job(wt, tmp_path / "next", head_sha=head(wt), files=["a.py", "b.py"],
                     prior=StoredReview(ReviewRecord(), read_review_meta(review_dir)))
    assert review.collect._prior_sha_for_delta(job) == head(wt)
    assert review.collect._collect_delta(job).files == ["a.py"]


@pytest.mark.parametrize("fix, recover, resumes, stashes", [
    (True, False, True, True),
    (False, False, False, True),
    (True, True, False, False),
])
def test_the_run_resumes_only_with_fix_and_never_under_recover(
        tmp_path, fix, recover, resumes, stashes):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    review_dir, _ = _stopped(tmp_path, wt, ["a.py"])
    flags = SimpleNamespace(fix=fix, recover=recover)
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is resumes
    assert ((wt / "a.py").read_text() == "x = 1\n") is stashes


def test_the_argv_carries_resume_fix(tmp_path):
    request = review.invoke.OrchestrateRequest(
        repo="o/r", pr_number="", review_file=tmp_path / "review.md", wt_path="/wt",
        target_dir=tmp_path, session_log="/log", bin_dir=Path("/bin"),
        generator_version="v", mode="self", fix_pass=True, resume_fix=True)
    argv = review.invoke.build_argv(request)
    assert argv[argv.index("--fix") + 1] == "--resume-fix"


def test_resume_runs_the_fix_pass_alone(tmp_path, monkeypatch):
    job = review_job(tmp_path, tmp_path / "rev")
    calls = []
    monkeypatch.setattr(review.orchestrate, "run_fix_pass", lambda j, t: calls.append(j) or 0)
    monkeypatch.setattr(review.orchestrate, "_inject_static_analysis_section", lambda j: None)
    monkeypatch.setattr(review.orchestrate, "run_multi_phase",
                        lambda *a, **k: pytest.fail("reviewed again"))
    monkeypatch.setattr(review.orchestrate, "run_single_agent",
                        lambda *a, **k: pytest.fail("reviewed again"))
    args = SimpleNamespace(resume_fix=True, fix=True, track=[], track_all=False)
    run = review.orchestrate._run_phases(MagicMock(), args, job)
    assert calls == [job] and run.fix_rc == 0


def test_resume_stamps_the_run_start_so_its_logs_are_counted(tmp_path, monkeypatch):
    job = review_job(tmp_path, tmp_path / "rev")
    write_review_meta(Path(job.artifact_dir), ReviewMeta(started_at="2026-01-01T00:00:00+00:00"))
    monkeypatch.setattr(review.orchestrate, "run_fix_pass", lambda j, t: 0)
    monkeypatch.setattr(review.orchestrate, "_inject_static_analysis_section", lambda j: None)
    review.orchestrate._resume_fix_pass(MagicMock(), SimpleNamespace(track=[], track_all=False), job)
    assert read_review_meta(Path(job.artifact_dir)).started_at == job.started_at


def test_glob_special_leftover_is_stashed_as_a_literal_pathspec(tmp_path):
    wt = _repo(tmp_path)
    (wt / "[ab].py").write_text("new\n")
    (wt / "a.py").write_text("operator\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["[ab].py"])
    message = review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    assert message
    assert not (wt / "[ab].py").exists()
    assert (wt / "a.py").read_text() == "operator\n"
    stashed = git_out(wt, "stash", "show", "--name-only", "--include-untracked")
    names = {line.strip() for line in stashed.splitlines() if line.strip()}
    assert "[ab].py" in names
    assert "a.py" not in names


def test_a_partial_stash_clears_the_record_and_does_not_repeat(
        tmp_path, monkeypatch, capsys):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    review_dir, _ = _stopped(tmp_path, wt, ["a.py"])
    real_run = git.client.run

    def fail_after_stash(*args, **kwargs):
        if args[:2] == ("stash", "push"):
            real_run(*args, **kwargs)
            return CmdResult(returncode=1, stderr="partial write")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", fail_after_stash)
    flags = SimpleNamespace(fix=True, recover=False)
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is False
    assert read_review_meta(review_dir).fix_interrupted is None
    sha = git_out(wt, "rev-parse", "refs/stash").strip()
    err = capsys.readouterr().err
    assert sha in err
    stash_list = git_out(wt, "stash", "list")
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is False
    assert git_out(wt, "stash", "list") == stash_list


def test_a_failed_set_aside_does_not_resume(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["a.py"])
    real_run = git.client.run

    def fail_stash(*args, **kwargs):
        if args and args[0] == "stash":
            return CmdResult(returncode=1, stderr="index.lock")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", fail_stash)
    flags = SimpleNamespace(fix=True, recover=False)
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is False
    assert read_review_meta(review_dir).fix_interrupted == stopped
    assert (wt / "a.py").read_text() == "x = 2\n"


def test_staged_non_leftover_skips_set_aside(tmp_path, capsys):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    (wt / "e.py").write_text("staged\n")
    git_out(wt, "add", "e.py")
    review_dir, stopped = _stopped(tmp_path, wt, ["a.py"])
    flags = SimpleNamespace(fix=True, recover=False)
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is False
    assert (wt / "a.py").read_text() == "x = 2\n"
    assert read_review_meta(review_dir).fix_interrupted == stopped
    assert "e.py" in capsys.readouterr().err
    assert git_out(wt, "stash", "list").strip() == ""


def test_ignored_leftovers_are_dropped_not_stashed(tmp_path, capsys):
    wt = _repo(tmp_path)
    (wt / ".gitignore").write_text("tmp.py\n")
    (wt / "tmp.py").write_text("scratch\n")
    review_dir, stopped = _stopped(tmp_path, wt, ["tmp.py"])
    message = review.convergence.set_aside_leftovers(review_dir, str(wt), stopped, None)
    assert message == ""
    assert (wt / "tmp.py").read_text() == "scratch\n"
    assert git_out(wt, "stash", "list").strip() == ""
    assert "tmp.py" in capsys.readouterr().err
    assert read_review_meta(review_dir).fix_interrupted is None


def test_force_with_fix_stashes_but_does_not_resume(tmp_path):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    review_dir, _ = _stopped(tmp_path, wt, ["a.py"])
    flags = SimpleNamespace(fix=True, recover=False, force=True)
    assert review.run._interrupted_pass(review_dir, str(wt), flags, None) is False
    assert (wt / "a.py").read_text() == "x = 1\n"
    assert read_review_meta(review_dir).fix_interrupted is None
