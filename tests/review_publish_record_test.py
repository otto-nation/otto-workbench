"""Every path that writes a review now publishes a record: review.json beside a rendered review.md."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.outcome as outcome
from gh.types import PRContext, PRMetadata
from review.ingest import ingest_review
from review.merge import merge_records
from review.paths import FILENAME_RECORD
from review.render import BANNER
from review.store import StoredReview, read_review
from review.types import DeltaAttribution, PreflightData, ReviewJob

from review_record_support import copy_tree, canonical, expected_text, fixture_meta, fixture_text

HEAD = "9f1c2e3d4b5a69788796a5b4c3d2e1f00a1b2c3d"


def _job(tmp_path, **kw) -> ReviewJob:
    tree = copy_tree(tmp_path / "tree")
    (tmp_path / "rev").mkdir()
    return ReviewJob(
        repo="acme/widget", pr_number="38",
        pr=PRMetadata(title="feat: retry the flaky fetch", body="", head="feat/retry",
                      base="main", head_sha=HEAD, additions=40, deletions=4, changed_files=3,
                      files=[{"path": p} for p in ("src/app.py", "src/cli.py", "src/util.py")]),
        ctx=PRContext(), wt_path=str(tree), review_file=str(tmp_path / "rev" / "review.md"),
        session_log=str(tmp_path / "rev" / "session.jsonl"),
        generator_version="review 1.42.0 / otto-workbench 1.47.0 (6c990742)", **kw,
    )


def _merged():
    return merge_records([ingest_review(fixture_text("groups", f"group-{i}.md"), strict=False) for i in (1, 2)])


def _same_as_fixture(job, case):
    """Header dates are today's, so compare everything below the header."""
    stored = read_review(Path(job.review_file))
    assert stored is not None, "no review.json was written"
    from dataclasses import replace
    from review.render import render_review
    meta = replace(stored.meta, date=fixture_meta(case).date)
    return canonical(render_review(stored.record, meta)) == canonical(expected_text(case))


def test_the_synthesis_fallback_publishes_a_record(tmp_path):
    """The failures half of this path is Task 10's; here, the record and its summary."""
    job = _job(tmp_path)
    outcome._write_mechanical_fallback(job, 2, _merged())
    stored = read_review(Path(job.review_file))
    assert stored is not None and BANNER in Path(job.review_file).read_text()
    assert stored.record.summary.startswith("4 findings across 3 files in 2 groups.")
    assert [f.id for f in stored.record.findings] == ["M1", "S1", "N1", "N2"]
    assert "call()" in stored.record.finding("S1").evidence and "call()" not in Path(job.review_file).read_text()


def test_the_clean_review_is_published_as_a_record(tmp_path):
    job = _job(tmp_path)
    outcome._write_clean_review(job, 2, ingest_review("## File Triage\n- `src/app.py` — reviewed; nothing to report.\n"))
    assert (Path(job.review_file).parent / FILENAME_RECORD).is_file()
    assert _same_as_fixture(job, "clean")


def test_an_agent_review_is_post_processed_and_stamped(tmp_path):
    job = _job(tmp_path)
    record = ingest_review(fixture_text("evidence_dropped", "raw.md"))
    outcome._post_process_review(job, record)
    assert _same_as_fixture(job, "evidence_dropped")
    assert job.verification["dropped"] == ["M1"]


def test_the_unchanged_review_carries_the_prior_record(tmp_path):
    prior = StoredReview(ingest_review(fixture_text("synthesis_pr", "input.md")), fixture_meta("synthesis_pr"))
    job = _job(tmp_path, prior_review="unused")
    job.prior = prior
    job.preflight = PreflightData(diff="", commit_log="", file_contents={}, file_permissions={},
                                  instructions_md="", architecture_md="", delta_files=[],
                                  prior_head_sha=prior.meta.head_sha,
                                  delta_attribution=DeltaAttribution.ATTRIBUTED)
    outcome.write_unchanged_review(job)
    stored = read_review(Path(job.review_file))
    assert stored.meta.prior_date == "2026-10-15"
    assert [f.id for f in stored.record.findings] == ["S1", "S2", "N1", "N2", "I1"]


def test_completeness_is_read_off_the_record(tmp_path):
    job = _job(tmp_path)
    Path(job.review_file).write_text("## Summary\nraw agent text\n")
    assert outcome.agent_output_complete(job.review_file)
    assert not outcome.is_complete_review(job.review_file)


def test_the_sidecar_and_the_header_cannot_disagree(tmp_path):
    job = _job(tmp_path)
    outcome._write_clean_review(job, 1, ingest_review("## File Triage\n"))
    stored = read_review(Path(job.review_file))
    assert stored.meta.head_sha == HEAD
    assert f"<!-- head_sha: {HEAD} -->" in Path(job.review_file).read_text()
