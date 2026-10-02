"""Tests for review.state and review.completion — pipeline status and warnings,
failure detail, recoverability, and the prior review's resolve and cleanup."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from pr.domains import ReviewStatus
from review.state import read_pipeline_status, read_pipeline_warnings
import review.completion
import review.run

# cr is a pytest fixture: imported by name so tests here can request it.
from review_entry_support import cr


# ── read_pipeline_status ──────────────────────────────────────────────────────


def test_read_pipeline_status_no_dir(cr):
    assert read_pipeline_status(None) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_no_file(cr, tmp_path):
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_synthesis_ok(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis", "disprove"], "failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_stops_short_of_the_gate(cr, tmp_path):
    """A run killed in the disprove gate records no failure, only an absence.

    Reading it as completed is what sent `--recover` away from the one phase
    the run had left: the gate is the last one, so nothing else reports it.
    """
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_synthesis_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {"synthesis": "all groups failed"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value


def test_read_pipeline_status_mechanical_fallback(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {"synthesis": "mechanical fallback"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_budget_exceeded(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {"synthesis": "budget exceeded"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_groups_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {},
        "groups_failed": {"1": "no result record in session log"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_corrupt_json(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text("not valid json")
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


def test_read_pipeline_status_partial_groups_failed_synthesis_ok(cr, tmp_path):
    """Groups failed but synthesis succeeded → partial, not error."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1, 3], "groups_failed": {"2": "quota exhausted (429)"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_partial_mechanical_fallback(cr, tmp_path):
    """Synthesis fell back to mechanical merge → partial."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {"synthesis": "mechanical fallback"},
        "groups_done": [1], "groups_failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.PARTIAL.value


def test_read_pipeline_status_error_all_groups_failed(cr, tmp_path):
    """All groups failed → error (not partial)."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {"synthesis": "all groups failed"},
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)", "2": "quota exhausted (429)"},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value


def test_read_pipeline_status_complete_no_failures(cr, tmp_path):
    """Clean pipeline → completed."""
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis", "disprove"], "failed": {},
        "groups_done": [1, 2], "groups_failed": {},
    }))
    assert read_pipeline_status(tmp_path) == ReviewStatus.COMPLETED.value


# ── build_failure_detail ──────────────────────────────────────────────────────


def test_build_failure_detail_no_dir(cr):
    from review.state import build_failure_detail
    assert build_failure_detail(None) == ""


def test_build_failure_detail_no_failures(cr, tmp_path):
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1, 2], "groups_failed": {},
    }))
    assert build_failure_detail(tmp_path) == ""


def test_build_failure_detail_groups_failed(cr, tmp_path):
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1], "groups_failed": {"2": "quota exhausted (429)", "3": "agent hit max turns (5)"},
    }))
    result = build_failure_detail(tmp_path)
    assert "2/3 groups failed" in result
    assert "quota exhausted (429)" in result
    assert "agent hit max turns" in result


def test_build_failure_detail_reads_typed_diagnoses(cr, tmp_path):
    """The format `_write_pipeline_state` actually produces."""
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2", "g3"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1],
        "groups_failed": {
            "2": {"kind": "quota_exhausted", "no_write_tool": False,
                  "detail": "", "num_turns": None},
            "3": {"kind": "max_turns", "no_write_tool": False,
                  "detail": "", "num_turns": 5},
        },
    }))
    result = build_failure_detail(tmp_path)
    assert "2/3 groups failed" in result
    assert "quota exhausted (429)" in result
    assert "agent hit max turns (5)" in result


def test_build_failure_detail_synthesis_failed(cr, tmp_path):
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {"synthesis": "mechanical fallback"},
        "groups_done": [1], "groups_failed": {},
    }))
    result = build_failure_detail(tmp_path)
    assert "synthesis" in result.lower()


def test_build_recoverable_no_dir(cr):
    from review.state import build_recoverable
    assert build_recoverable(None) is False


def test_build_recoverable_no_failures(cr, tmp_path):
    """A clean run has nothing to retry, so there is nothing to recover."""
    from review.state import build_recoverable
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {},
        "groups_done": [1], "groups_failed": {},
    }))
    assert build_recoverable(tmp_path) is False


def test_build_recoverable_agrees_with_the_review_document(cr, tmp_path):
    """The summary and the Agent Failures hint answer the same question.

    A consumer reading the state file must reach the verdict the review
    document states, or the UI offers a Recover the CLI would refuse.
    """
    from review.state import build_failures_body, build_recoverable, PipelineState
    for kind, detail, expected in [
        ("agent_error", "Prompt is too long", False),
        ("agent_error", "permission denied", False),
        ("max_turns", "", True),
    ]:
        (tmp_path / "pipeline.json").write_text(json.dumps({
            "head_sha": "abc", "group_names": ["g1"],
            "done": ["synthesis"], "failed": {},
            "groups_done": [],
            "groups_failed": {"1": {
                "kind": kind, "no_write_tool": False,
                "detail": detail, "num_turns": 5,
            }},
        }))
        assert build_recoverable(tmp_path) is expected, detail
        state = PipelineState.load(tmp_path)
        hinted = "pr review --recover" in build_failures_body(state)
        assert hinted is expected, detail


def test_build_failure_detail_all_groups_failed(cr, tmp_path):
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {"synthesis": "all groups failed"},
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)", "2": "quota exhausted (429)"},
    }))
    result = build_failure_detail(tmp_path)
    assert "all groups failed" in result


def test_the_two_readers_agree_on_the_all_failed_sentinel(cr, tmp_path):
    """Status and detail answer the same question the same way.

    Synthesis records `all groups failed` when no group produced usable output,
    and the state can still carry fewer failure entries than there are groups —
    a group that crashed before it registered one. The two readers used to
    compute the all-failed rule separately, and only the status reader honoured
    the sentinel, so the review said `error` and `1/2 groups failed` at once.
    """
    from review.state import build_failure_detail
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["synthesis"], "failed": {"synthesis": "all groups failed"},
        "groups_done": [], "groups_failed": {"1": "quota exhausted (429)"},
    }))

    assert read_pipeline_status(tmp_path) == ReviewStatus.ERROR.value
    assert build_failure_detail(tmp_path).startswith("all groups failed:")


# ── read_pipeline_warnings ────────────────────────────────────────────────────


def test_read_pipeline_warnings_no_dir(cr):
    assert read_pipeline_warnings(None) == []


def test_read_pipeline_warnings_no_file(cr, tmp_path):
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_all_complete(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["holistic", "synthesis"], "failed": {},
        "groups_done": [1], "groups_failed": {},
    }))
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_holistic_incomplete(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
    }))
    assert read_pipeline_warnings(tmp_path) == ["holistic phase"]


def test_read_pipeline_warnings_groups_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1", "g2"],
        "done": ["holistic"], "groups_failed": {"1": "max turns", "2": "model error"},
    }))
    assert read_pipeline_warnings(tmp_path) == ["2 groups failed"]


def test_read_pipeline_warnings_single_group_failed(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["holistic"], "groups_failed": {"1": "max turns"},
    }))
    assert read_pipeline_warnings(tmp_path) == ["1 group failed"]


def test_read_pipeline_warnings_multiple(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "failed": {"synthesis": "all groups failed"},
    }))
    warnings = read_pipeline_warnings(tmp_path)
    assert "holistic phase" in warnings
    assert "synthesis" in warnings


def test_read_pipeline_warnings_skipped_phases_no_warning_when_synthesis_done(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({
        "head_sha": "abc", "group_names": ["g1"],
        "done": ["synthesis"], "failed": {},
    }))
    assert read_pipeline_warnings(tmp_path) == []


def test_read_pipeline_warnings_corrupt_json(cr, tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text("not valid json")
    assert read_pipeline_warnings(tmp_path) == []


# ── resolve_prior_review ─────────────────────────────────────────────────────


def test_resolve_prior_resume_true_returns_existing_prior(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior_file = review_dir / "prior.md"
    review_file.write_text("## Review")
    prior_file.write_text("## Prior")

    result = review.run.resolve_prior_review(review_file, "", True)
    assert result == str(prior_file)


def test_resolve_prior_resume_true_no_prior_returns_empty(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("## Review")

    result = review.run.resolve_prior_review(review_file, "", True)
    assert result == ""


def test_resolve_prior_resume_false_archives(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-43"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("## Review")
    session_log.write_text("{}")

    result = review.run.resolve_prior_review(review_file, str(session_log), False)

    assert not review_file.exists()
    assert result != ""


# ── cleanup_prior_review ─────────────────────────────────────────────────────


def test_cleanup_prior_removes_when_no_pipeline(tmp_path):
    review_dir = tmp_path / "reviews" / "test"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior = review_dir / "prior.md"
    prior.write_text("prior content")

    review.completion.cleanup_prior_review(review_file, str(prior))
    assert not prior.exists()


def test_cleanup_prior_keeps_when_pipeline_exists(tmp_path):
    review_dir = tmp_path / "reviews" / "test"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    prior = review_dir / "prior.md"
    pipeline = review_dir / "pipeline.json"
    prior.write_text("prior content")
    pipeline.write_text("{}")

    review.completion.cleanup_prior_review(review_file, str(prior))
    assert prior.exists()


def test_cleanup_prior_empty_path_is_noop(tmp_path):
    review_file = tmp_path / "review.md"
    review.completion.cleanup_prior_review(review_file, "")
