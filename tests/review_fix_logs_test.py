"""Fix-pass session logs: kept per pass, and counted in the run's cost."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.fix_logs  # noqa: E402
import review.summary  # noqa: E402
from review.paths import write_review_meta  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.store import write_review  # noqa: E402
from review.types import ReviewMeta  # noqa: E402

STARTED = "2026-10-21T09:15:02+00:00"


def _log(path: Path, cost: float) -> None:
    path.write_text(json.dumps({
        "type": "result", "total_cost_usd": cost, "duration_ms": 1000,
        "usage": {"input_tokens": 10, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }) + "\n")


def test_a_pass_is_kept_under_its_own_stamp(tmp_path):
    _log(tmp_path / "fix.jsonl", 0.5)
    _log(tmp_path / "fix_verify-1.jsonl", 0.25)
    kept = review.fix_logs.keep(tmp_path, STARTED)
    assert kept == tmp_path / "fix-logs" / "20261021T0915020000"
    assert sorted(p.name for p in kept.iterdir()) == ["fix.jsonl", "fix_verify-1.jsonl"]


def test_a_second_pass_does_not_overwrite_the_first(tmp_path):
    _log(tmp_path / "fix_verify-1.jsonl", 0.25)
    review.fix_logs.keep(tmp_path, STARTED)
    review.fix_logs.clear(tmp_path)
    _log(tmp_path / "fix_verify-1.jsonl", 0.75)
    review.fix_logs.keep(tmp_path, "2026-10-21T10:00:00+00:00")
    first = review.fix_logs.logs_for(tmp_path, STARTED)
    assert len(first) == 1 and '"total_cost_usd": 0.25' in Path(first[0]).read_text()


def test_only_the_newest_passes_are_kept(tmp_path):
    for hour in range(review.fix_logs.PASSES_KEPT + 2):
        _log(tmp_path / "fix.jsonl", 0.1)
        review.fix_logs.keep(tmp_path, f"2026-10-21T{hour:02d}:00:00+00:00")
    kept = sorted(p.name for p in (tmp_path / "fix-logs").iterdir())
    assert len(kept) == review.fix_logs.PASSES_KEPT
    assert kept[0].startswith("20261021T02")


def test_clear_leaves_the_review_alone(tmp_path):
    _log(tmp_path / "fix.jsonl", 0.1)
    (tmp_path / "review.md").write_text("x")
    review.fix_logs.clear(tmp_path)
    assert not (tmp_path / "fix.jsonl").exists() and (tmp_path / "review.md").exists()


def test_the_usage_line_counts_the_fix_pass(tmp_path, capsys):
    review_file = tmp_path / "review.md"
    write_review(review_file, ReviewRecord(summary="s"), ReviewMeta(started_at=STARTED))
    _log(tmp_path / "session.jsonl", 1.0)
    _log(tmp_path / "fix.jsonl", 0.5)
    _log(tmp_path / "fix_verify-1.jsonl", 0.25)
    review.fix_logs.keep(tmp_path, STARTED)
    report = review.summary.ReviewSummaryReport(review_file=str(review_file))
    review.summary.print_summary(report, str(tmp_path / "session.jsonl"))
    assert "$1.75" in capsys.readouterr().err


def test_the_json_summary_cost_counts_the_fix_pass(tmp_path):
    review_file = tmp_path / "review.md"
    write_review(review_file, ReviewRecord(summary="s"), ReviewMeta(started_at=STARTED))
    _log(tmp_path / "session.jsonl", 1.0)
    _log(tmp_path / "fix.jsonl", 0.5)
    review.fix_logs.keep(tmp_path, STARTED)
    report = review.summary.build_review_summary("o/r", "", str(review_file))
    assert abs(report.cost_usd - 1.5) < 1e-9


def test_another_runs_pass_is_not_counted(tmp_path):
    write_review_meta(tmp_path, ReviewMeta(started_at="2026-10-22T00:00:00+00:00"))
    _log(tmp_path / "fix.jsonl", 0.5)
    review.fix_logs.keep(tmp_path, STARTED)
    assert review.summary.usage_logs(tmp_path, "", "") == []
