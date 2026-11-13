"""Prompt-stats rows name the run and head they belong to."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.phases import Phase  # noqa: E402
from review.prompt_stats import log_prompt_size  # noqa: E402
from review.registry import build_prompt  # noqa: E402
from review.store import StoredReview  # noqa: E402
from review.types import ReviewMeta  # noqa: E402
from conftest import TEST_MODEL  # noqa: E402
from review_prompt_support import _make_job, _make_preflight  # noqa: E402
from review_record_support import finding, record  # noqa: E402


def _job(tmp_path, *, started_at=None):
    job = _make_job(_make_preflight())
    job.review_file = str(tmp_path / "review.md")
    if started_at is not None:
        job.started_at = started_at
    return job


def _stats(tmp_path):
    return json.loads((tmp_path / "prompt-stats.json").read_text())


class TestPromptStatsStampTheRun:
    def test_two_runs_stamp_distinct_started_at(self, tmp_path):
        first = _job(tmp_path, started_at="2026-01-01T00:00:00+00:00")
        second = _job(tmp_path, started_at="2026-01-02T00:00:00+00:00")
        log_prompt_size("t", "p", {}, first, budget_bytes=10_000, model=TEST_MODEL)
        log_prompt_size("t", "p", {}, second, budget_bytes=10_000, model=TEST_MODEL)
        rows = _stats(tmp_path)
        assert rows[0]["started_at"] != rows[1]["started_at"]
        assert rows[0]["started_at"] == first.started_at
        assert rows[1]["started_at"] == second.started_at
        assert rows[0]["head_sha"] == first.pr.head_sha
        assert rows[1]["head_sha"] == second.pr.head_sha

    def test_a_single_prompt_with_a_prior_counts_the_prior_section(self, tmp_path):
        job = _job(tmp_path)
        job.prior = StoredReview(record(finding("M1", "still broken")), ReviewMeta())
        build_prompt(Phase.SINGLE, job, max_turns=10)
        row = _stats(tmp_path)[-1]
        assert row["sections"]["prior_section"] > 0
