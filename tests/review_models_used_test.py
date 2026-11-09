"""review.outcome.models_used: the `Models:` line read from the usage ledger, and where `_run_phases` reads it."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.usage
import core.log
import core.trail
import review.orchestrate
import review.outcome
from core.phases import Effort
from gh.types import PRContext, PRMetadata
from review.paths import read_review_meta
from review.types import ReviewJob, ReviewMeta
from review_record_support import record, store_record

ROOT = "abc123def456"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(core.trail.TRAIL_ROOT_ENV, ROOT)
    monkeypatch.setattr(agent.usage, "_warned", False)
    return tmp_path / "state" / agent.usage.LEDGER_DIRNAME


def _job(tmp_path, pr_number="42") -> ReviewJob:
    return ReviewJob(
        repo="org/repo", pr_number=pr_number,
        pr=PRMetadata(title="t", body="", head="feat", base="main", head_sha="abc",
                      additions=10, deletions=0, changed_files=1,
                      files=[{"path": "a.py", "additions": 10, "deletions": 0}]),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "rev" / "review.md"),
        session_log=str(tmp_path / "session.jsonl"), effort=Effort.MEDIUM,
    )


def _row(phase, *, served=None, model=None, thinking=None,
         invocation=ROOT, repo="org/repo", branch="feat", pr="42"):
    """One ledger row, written by the writer every agent call goes through."""
    agent.usage.record(
        script="review-orchestrate", entry_point="agent", backend="pi", model=model,
        usage=agent.usage.SessionUsage(cost_by_model=served or {}), exit_code=0,
        repo=repo, pr=pr, branch=branch, invocation=invocation, phase=phase,
        thinking=thinking,
    )


def test_identical_sessions_collapse_in_run_order(tmp_path, ledger):
    job = _job(tmp_path)
    _row("scout", served={"s": 1})
    _row("group", served={"g": 1}, thinking="low")
    _row("group", served={"g": 1}, thinking="low")
    _row("group", served={"g": 1}, thinking="low")
    _row("synthesis", served={"o": 1}, thinking="high")
    _row("disprove", served={"s": 1})
    assert review.outcome.models_used(job) == (
        "scout s · group (×3) g (low) · synthesis o (high) · disprove s"
    )


def test_only_this_runs_rows_are_read(tmp_path, ledger):
    job = _job(tmp_path)
    _row("single", served={"m": 1})
    _row("single", served={"m": 1}, invocation="fff000")
    _row("single", served={"m": 1}, branch="other")
    _row("single", served={"m": 1}, repo="org/other")
    _row("single", served={"m": 1}, pr="43")
    path = ledger / f"{datetime.now(timezone.utc):%Y-%m}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": "2000-01-01T00:00:00Z",
            "invocation": ROOT, "repo": "org/repo", "branch": "feat",
            "phase": "single", "model": "old",
        }) + "\n")
    assert review.outcome.models_used(job) == "single m"


# passes-at-base: self-review rows omit pr and already matched without comparing it
def test_a_self_review_with_no_pr_still_matches(tmp_path, ledger):
    job = _job(tmp_path, pr_number="")
    _row("single", served={"m": 1}, pr=None)
    assert review.outcome.models_used(job) == "single m"


def test_the_served_model_wins_over_the_requested_one(tmp_path, ledger):
    job = _job(tmp_path)
    _row("single", served={"claude-sonnet-5-5": 1}, model="sonnet")
    assert review.outcome.models_used(job) == "single claude-sonnet-5-5"


def test_a_row_whose_costs_name_two_models_shows_the_requested_one(tmp_path, ledger):
    job = _job(tmp_path)
    _row("single", served={"a": 1, "b": 1}, model="sonnet")
    assert review.outcome.models_used(job) == "single sonnet"


def test_a_row_naming_no_model_is_left_out(tmp_path, ledger):
    job = _job(tmp_path)
    _row("single")
    _row("disprove", served={"o": 1})
    assert review.outcome.models_used(job) == "disprove o"


def test_no_rows_is_no_line(tmp_path, ledger):
    assert review.outcome.models_used(_job(tmp_path)) == ""


def test_a_non_object_ledger_line_is_skipped(tmp_path, ledger):
    job = _job(tmp_path)
    _row("single", served={"m": 1})
    path = ledger / f"{datetime.now(timezone.utc):%Y-%m}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("null\n")
    assert review.outcome.models_used(job) == "single m"


def test_invalid_utf8_in_the_ledger_omits_the_models_line(tmp_path, ledger, monkeypatch):
    job = _job(tmp_path)
    warnings = []
    monkeypatch.setattr(core.log, "warn", warnings.append)
    ledger.mkdir(parents=True)
    (ledger / f"{datetime.now(timezone.utc):%Y-%m}.jsonl").write_bytes(b"\xff\xfe not utf-8\n")
    assert review.outcome.models_used(job) == ""
    assert len(warnings) == 1


def test_the_review_states_its_models_before_the_fix_pass(tmp_path, ledger, monkeypatch):
    job = _job(tmp_path)

    def single(j, **_):
        store_record(j.review_file, record(summary="s"), ReviewMeta(head_sha="abc"))
        _row("single", served={"m": 1}, thinking="low")

    def fix(j, trail):
        _row("fix", served={"f": 1})
        return 0

    monkeypatch.setattr(review.orchestrate, "run_single_agent", single)
    monkeypatch.setattr(review.orchestrate, "run_multi_phase", single)
    monkeypatch.setattr(review.orchestrate, "run_fix_pass", fix)
    monkeypatch.setattr(review.orchestrate, "_inject_static_analysis_section", lambda j: None)
    monkeypatch.setattr(review.orchestrate, "_file_open_findings", lambda *a: None)
    args = SimpleNamespace(max_parallel=2, max_cost=20.0, max_groups=None,
                           disprove=None, fix=True, effort=None)
    review.orchestrate._run_phases(MagicMock(), args, job)
    assert read_review_meta(Path(job.artifact_dir)).models == "single m (low)"
    assert "Models: single m (low)" in Path(job.review_file).read_text()
