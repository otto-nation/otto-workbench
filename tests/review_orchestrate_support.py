"""Shared driver for orchestrate suites: a faked `run_orchestrate` over tmp_path."""

import contextlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import pr.state
import pr.target

ORCHESTRATE_REVIEW = synthetic_review(meta="status: completed", summary="All good.")


def orchestrate_args(ro, review_file, repo_dir, **overrides):
    """The argparse namespace `run_orchestrate` reads, with test defaults."""
    defaults = {
        "pr": "1", "review_file": str(review_file), "repo_dir": str(repo_dir),
        "mode": ro.Mode.PR, "effort": ro.Effort.MEDIUM,
        "prior_review": "", "issue": "", "issue_context": "",
        "generator_version": "", "model": "", "recover_sha": "",
        "target_dir": str(repo_dir), "max_parallel": 2,
        "max_cost": 20.0, "max_groups": None,
        "no_holistic": False, "no_scout": False, "no_group": False,
        "no_synthesis": False, "no_disprove": True, "disprove": None,
        "generated": False, "fix": False, "base": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def run_orchestrated(ro, monkeypatch, tmp_path, pipeline=None, out=None, prior_head_sha="",
                     **arg_overrides):
    """Drive `run_orchestrate` over *tmp_path* with every phase faked.

    Pass *out* to keep the result JSON the run prints; the default throws
    it away, since what most of these tests read is the directory.
    *prior_head_sha* makes the faked preflight an incremental one.
    """
    import fix.engine
    from review.reply_threads import ReplyThreads

    review_dir = tmp_path / "review-dir"
    review_dir.mkdir(exist_ok=True)
    review_file = review_dir / "review.md"

    pr_meta = ro.PRMetadata(
        title="t", body="", head="feat", base="main", head_sha="abc123",
        additions=10, deletions=0, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 0}],
    )

    def _pipeline(job, **_kwargs):
        Path(job.review_file).write_text(ORCHESTRATE_REVIEW)
        Path(job.session_log).write_text("{}\n")
        (review_dir / "meta.json").write_text("{}")
        (review_dir / "disprove.jsonl").write_text("{}\n")
        (review_dir / "prompt-single.md").write_text("PROMPT")

    def _fix(job, _trail=None, **_kwargs):
        artifacts = Path(job.artifact_dir)
        (artifacts / "fix.jsonl").write_text("{}\n")
        (artifacts / fix.engine.TRACKING_FILENAME).write_text("## <!-- fix:M1 -->\n")

    monkeypatch.setattr(agent.backend, "preflight", lambda *a, **k: True)
    monkeypatch.setattr(pr.state, "load_state", lambda *a, **k: None)
    monkeypatch.setattr(
        ro, "fetch_metadata",
        lambda *a, **k: ro.RunContext(pr_meta, ro.PRContext(), None),
    )
    # A real record rather than a mock: `_run_phases` sizes the run off
    # the preflight's delta fields, which a mock answers with objects that
    # do not compare against the thresholds.
    monkeypatch.setattr(
        ro, "collect_preflight_data",
        lambda job: ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="", prior_head_sha=prior_head_sha,
        ),
    )
    monkeypatch.setattr(ro, "run_single_agent", pipeline or _pipeline)
    monkeypatch.setattr(ro, "run_static_analysis", lambda *a, **k: [])
    monkeypatch.setattr(ro, "run_fix_pass", _fix)
    monkeypatch.setattr(
        ro, "fetch_reply_threads",
        lambda *a, **k: ReplyThreads([], {}),
    )

    args = orchestrate_args(ro, review_file, tmp_path, **arg_overrides)
    with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
        ro.run_orchestrate(MagicMock(), args, "org/repo", str(review_dir / "session.jsonl"))
    return review_dir


def focus_after_prior(ro, monkeypatch, tmp_path, **run_kwargs):
    """The job's focus on a re-review of a prior round that held a requested
    area (F1 Retry) and a covered derived one (F2 Parsing on a.py)."""
    from review.record import FocusSource
    from review.store import archive_prior, write_review
    from review.types import ReviewMeta
    from review_focus_support import area
    from review_record_support import record

    review_dir = tmp_path / "review-dir"
    review_dir.mkdir(exist_ok=True)
    prior_focus = (
        area("F1", "Retry", "why", source=FocusSource.USER, coverage="checked"),
        area("F2", "Parsing", paths=("a.py", "gone.py"), coverage="checked"),
    )
    write_review(review_dir / "review.md", record(summary="prior", focus=prior_focus),
                 ReviewMeta(head_sha="0ldc0de"))
    archive_prior(review_dir)
    (review_dir / "prior.md").write_text("## Summary\nprior\n")
    seen = {}

    def _capture(job, **_kwargs):
        seen["focus"] = job.focus
        Path(job.review_file).write_text(ORCHESTRATE_REVIEW)
        Path(job.session_log).write_text("{}")

    run_orchestrated(ro, monkeypatch, tmp_path, pipeline=_capture,
                     prior_review=str(review_dir / "prior.md"), **run_kwargs)
    return seen["focus"]
