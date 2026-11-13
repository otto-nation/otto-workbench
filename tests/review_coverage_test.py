"""Coverage gaps: required triage, publish persistence, and unchanged-review carry."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Mode
from gh.types import PRContext, PRMetadata
import review.coverage
import review.verdict
import review.outcome
from review.artifacts import ReviewArtifact
from review.ingest import IngestErrorKind
from review.paths import read_review_meta
from review.record import FocusSource, TriageEntry
from review.store import StoredReview
from review.types import (
    CoverageGap, CoverageGapKind, PreflightData, ReviewJob, ReviewMeta,
    review_meta_from_dict,
)
from review_focus_support import area, user_area
from review_record_support import record, store_record


def _file(path: str) -> dict:
    return {"path": path, "additions": 1, "deletions": 0}


def _job(tmp_path, files, *, mode=Mode.PR, omitted=(), incremental=False, **kwargs):
    paths = [f["path"] if isinstance(f, dict) else f for f in files]
    file_dicts = [_file(p) if isinstance(p, str) else p for p in files]
    job = ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=len(paths), deletions=0, changed_files=len(paths),
            files=file_dicts,
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        mode=mode,
        **kwargs,
    )
    if omitted or incremental:
        job.preflight = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            omitted_files=list(omitted),
            delta_files=list(paths) if incremental else [],
            prior_head_sha="0ldc0de" if incremental else "",
        )
    return job


class TestRequiredTriage:
    def test_excludes_generated_files(self, tmp_path):
        job = _job(tmp_path, ["a.py", "go.sum", "pkg/gen/stub.py"])
        assert review.coverage.required_triage(job) == ("a.py",)

    def test_uses_delta_files_when_incremental(self, tmp_path):
        job = _job(tmp_path, ["old.py", "delta.py", "go.sum"], incremental=True)
        job.preflight.delta_files = ["delta.py", "go.sum"]
        assert review.coverage.required_triage(job) == ("delta.py",)

    def test_self_mode_requires_nothing(self, tmp_path):
        job = _job(tmp_path, ["a.py", "b.py"], mode=Mode.SELF)
        assert review.coverage.required_triage(job) == ()


class TestTriageProblems:
    def test_lists_exactly_the_missing_required_paths(self):
        art = ReviewArtifact(summary="s", triage=(TriageEntry("a.py", "ok"),))
        errors = review.coverage.triage_problems(
            art, ("a.py", "b.py", "omitted.go"),
        )
        assert len(errors) == 1
        err = errors[0]
        assert err.kind is IngestErrorKind.MISSING_TRIAGE
        assert err.at == "$.triage"
        assert err.detail == "no triage entry for b.py, omitted.go"

    def test_empty_when_complete(self):
        art = ReviewArtifact(summary="s", triage=(
            TriageEntry("a.py", "ok"),
            TriageEntry("./b.py", "ok"),
            TriageEntry("`c.py`", "ok"),
            TriageEntry("extra.py", "ok"),
        ))
        assert review.coverage.triage_problems(art, ("a.py", "b.py", "c.py")) == ()

    def test_empty_for_self_mode(self, tmp_path):
        job = _job(tmp_path, ["a.py"], mode=Mode.SELF)
        art = ReviewArtifact(summary="s", triage=())
        assert review.coverage.triage_problems(
            art, review.coverage.required_triage(job),
        ) == ()


class TestCoverageGaps:
    def test_missing_triage_gaps_every_required_file(self, tmp_path):
        job = _job(tmp_path, ["a.py", "b.py"])
        gaps = review.coverage.coverage_gaps(job, record(triage=None))
        assert gaps == (
            CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject="a.py"),
            CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject="b.py"),
        )

    def test_a_complete_triage_has_no_gaps(self, tmp_path):
        job = _job(tmp_path, ["a.py", "b.py"])
        rec = record(triage=(
            TriageEntry(path="a.py", note="ok"),
            TriageEntry(path="b.py", note="ok"),
        ))
        assert review.coverage.coverage_gaps(job, rec) == ()

    def test_dotted_and_backticked_paths_match(self, tmp_path):
        job = _job(tmp_path, ["a.py"])
        dotted = record(triage=(TriageEntry(path="./a.py", note="ok"),))
        tick = record(triage=(TriageEntry(path="`a.py`", note="ok"),))
        assert review.coverage.coverage_gaps(job, dotted) == ()
        assert review.coverage.coverage_gaps(job, tick) == ()

    def test_a_user_area_with_empty_coverage_is_a_gap(self, tmp_path):
        job = _job(tmp_path, ["a.py"])
        rec = record(
            triage=(TriageEntry(path="a.py", note="ok"),),
            focus=(area(id="F1", title="Locking", source=FocusSource.USER),),
        )
        gaps = review.coverage.coverage_gaps(job, rec)
        assert gaps == (
            CoverageGap(kind=CoverageGapKind.FOCUS_UNASSESSED, subject="F1 (Locking)"),
        )

    def test_a_derived_area_with_empty_coverage_is_not_a_gap(self, tmp_path):
        job = _job(tmp_path, ["a.py"])
        rec = record(
            triage=(TriageEntry(path="a.py", note="ok"),),
            focus=(area(id="F1", title="Retry", source=FocusSource.DERIVED),),
        )
        assert review.coverage.coverage_gaps(job, rec) == ()

    def test_self_mode_yields_no_gaps(self, tmp_path):
        job = _job(tmp_path, ["a.py"], mode=Mode.SELF)
        assert review.coverage.coverage_gaps(job, record(triage=None)) == ()


class TestGapsProse:
    def test_names_at_most_five_then_and_more(self):
        gaps = tuple(
            CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject=f"f{i}.py")
            for i in range(7)
        )
        prose = review.verdict.gaps_prose(gaps)
        assert "no triage entry for 7 changed files" in prose
        assert "f0.py, f1.py, f2.py, f3.py, f4.py and 2 more" in prose
        assert "f5.py" not in prose


class TestSidecarLoadsGaps:
    def test_a_sidecar_without_coverage_gaps_reads_as_empty(self):
        assert review_meta_from_dict({}).coverage_gaps == ()
        assert review_meta_from_dict({"repo": "org/repo"}).coverage_gaps == ()

    def test_gaps_round_trip(self, tmp_path):
        gaps = (
            CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject="a.py"),
            CoverageGap(kind=CoverageGapKind.FOCUS_UNASSESSED, subject="F1 (Locking)"),
        )
        store_record(tmp_path / "review.md", record(summary="s"), ReviewMeta(coverage_gaps=gaps))
        assert read_review_meta(tmp_path).coverage_gaps == gaps

    def test_an_unknown_kind_still_loads(self):
        meta = review_meta_from_dict({
            "coverage_gaps": [{"kind": "future_kind", "subject": "x.py"}],
        })
        assert meta.coverage_gaps == (CoverageGap(kind="future_kind", subject="x.py"),)


class TestPublishGaps:
    def test_a_pr_with_no_triage_records_a_gap_per_required_file(self, tmp_path):
        """The 5472 shape: PR mode, 20 files, triage null, one file omitted from pre-collection."""
        paths = [f"docs/f{i:02d}.md" for i in range(19)] + ["ui-console/CLAUDE.md"]
        job = _job(tmp_path, paths, omitted=["ui-console/CLAUDE.md"])
        review.outcome._publish(job, record(summary="ok", triage=None))
        gaps = read_review_meta(tmp_path).coverage_gaps
        assert {g.kind for g in gaps} == {CoverageGapKind.UNTRIAGED}
        assert [g.subject for g in gaps] == sorted(paths)
        assert "ui-console/CLAUDE.md" in [g.subject for g in gaps]

    def test_self_mode_publish_records_no_gaps(self, tmp_path):
        job = _job(tmp_path, ["a.py"], mode=Mode.SELF)
        review.outcome._publish(job, record(summary="ok", triage=None))
        assert read_review_meta(tmp_path).coverage_gaps == ()

    def test_publish_records_unassessed_requested_focus(self, tmp_path):
        job = _job(tmp_path, ["a.py"])
        job.focus = (user_area("Locking"),)
        review.outcome._publish(
            job, record(summary="ok", triage=(TriageEntry(path="a.py", note="ok"),)),
        )
        gaps = read_review_meta(tmp_path).coverage_gaps
        assert gaps == (
            CoverageGap(kind=CoverageGapKind.FOCUS_UNASSESSED, subject="F1 (Locking)"),
        )


class TestUnchangedReviewCarriesGaps:
    def test_carries_the_prior_gaps_even_when_the_carried_record_has_no_triage(self, tmp_path):
        prior_gaps = (CoverageGap(kind=CoverageGapKind.UNTRIAGED, subject="old.py"),)
        job = _job(tmp_path, ["a.py"])
        job.prior = StoredReview(
            record(summary="prior", triage=(TriageEntry(path="a.py", note="ok"),)),
            ReviewMeta(coverage_gaps=prior_gaps),
        )
        review.outcome.write_unchanged_review(job)
        assert read_review_meta(tmp_path).coverage_gaps == prior_gaps
        assert read_review_meta(tmp_path).coverage_gaps[0].subject != "a.py"
