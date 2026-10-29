"""The review document `review.outcome` writes: clean, fallback, completeness and sidecar."""

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from review.record import FocusSource, ReviewRecord, TriageEntry, load_record
from review_focus_support import area, user_area
from review_record_support import finding, record


# ── 31. _document ───────────────────────────────────────────────────────────


class TestDocument:
    def _job(self, ro, tmp_path, **kwargs):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc123", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="1.0.0",
            **kwargs,
        )

    def test_a_full_review_is_framed_by_what_the_sidecar_records(self, ro, tmp_path):
        """Enough for the document to be read without its sidecar to hand.

        A review file is opened by a session that has no `meta.json` in front
        of it, so what the run covered — which branch, against which base, at
        which commit — is on the page rather than a join away.
        """
        from datetime import date
        from review.store import read_review
        from review_record_support import record
        job = self._job(ro, tmp_path)
        ro._publish(job, record(summary="body"))
        stored = read_review(Path(job.review_file))
        meta = stored.meta
        assert meta.date == date.today().isoformat()
        assert meta.head_sha == "abc123"
        assert meta.head_ref == "feat"
        assert meta.base_ref == "main"
        assert meta.generator_version == "1.0.0"
        rendered = Path(job.review_file).read_text()
        assert rendered.startswith("# Review: org/repo#42 — test\n")
        assert f"<!-- date: {date.today().isoformat()} -->" in rendered
        assert "<!-- generator: 1.0.0 -->" in rendered
        assert "## Summary" in rendered and "body" in rendered

    def test_a_full_review_reports_no_group_ratio(self, ro, tmp_path):
        """Skipped groups are a claim about an incremental run: the pipeline
        passes the count on every path, and a full review states none."""
        meta = ro._review_meta(self._job(ro, tmp_path), skipped_groups=1, total_groups=4)
        assert meta.skipped_groups == 0 and meta.total_groups == 0
        from review.document import ReviewHeader
        assert "skipped_groups" not in ReviewHeader.from_meta(meta).render()

    def _incremental(self, ro, tmp_path, *, prior_date=""):
        job = self._job(ro, tmp_path)
        if prior_date:
            from review.store import StoredReview
            from review.types import ReviewMeta
            from review_record_support import record
            job.prior = StoredReview(
                record(summary="prior"),
                ReviewMeta(date=prior_date, head_sha="def456"),
            )
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            delta_files=["a.py"], prior_head_sha="def456",
        )
        return job

    def test_an_incremental_review_dates_itself_against_the_prior_one(self, ro, tmp_path):
        meta = ro._review_meta(
            self._incremental(ro, tmp_path, prior_date="2026-08-20"),
            skipped_groups=1, total_groups=4,
        )
        assert meta.prior_date == "2026-08-20"
        assert meta.prior_sha == "def456"
        from review.document import ReviewHeader
        assert "<!-- skipped_groups: 1/4 -->" in ReviewHeader.from_meta(meta).render()

    def test_an_incremental_review_with_no_prior_document_says_so(self, ro, tmp_path):
        job = self._incremental(ro, tmp_path)
        assert ro._review_meta(job).prior_date == "unknown"


# ── 32. _build_mechanical_fallback ──────────────────────────────────────────


class TestBuildMechanicalFallback:
    def test_pr_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        from review_record_support import finding, record
        rec = ro._build_mechanical_fallback(
            job, 3, record(finding("M1", "issue", path="file.go")),
        )
        ro._publish(job, rec)
        result = Path(job.review_file).read_text()
        assert result.startswith("# Review: org/repo#42 — test PR\n")
        assert "Verdict" in result
        assert "1 finding" in result

    def test_self_review_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="",
            pr=ro.PRMetadata(title="test", body="", head="my-branch", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.SELF,
        )
        from review_record_support import finding, record
        rec = ro._build_mechanical_fallback(
            job, 2, record(finding("N1", "style", path="file.go")),
        )
        ro._publish(job, rec)
        result = Path(job.review_file).read_text()
        assert result.startswith("# Self-Review: org/repo — my-branch\n")
        assert "Verdict" not in result

    def test_counts_correct(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        from review_record_support import finding, record
        result = ro._build_mechanical_fallback(job, 3, record(
            finding("M1", "issue", path="a.go"),
            finding("N1", "style", path="b.go", line=2),
            finding("N2", "naming", path="c.go", line=3),
        ))
        assert "3 findings" in result.summary

    def _partial_job(self, ro, tmp_path, changed_files=16):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="t", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=changed_files, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )

    def test_a_partial_run_counts_the_groups_that_reported(self, ro, tmp_path):
        """"across 3 groups" over a group that wrote nothing reads as examined.

        The sentence sits directly above findings the missing group never
        contributed to, which is how a review of two thirds of a PR was acted
        on as a review of all of it.
        """
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState

        state = PipelineState(group_names=["a", "b", "c"])
        state.groups_failed[2] = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, record(), pipeline_state=state,
        )
        assert "in 2 of 3 groups" in result.summary
        assert "in 3 groups" not in result.summary

    def test_a_partial_run_withholds_the_clean_verdict(self, ro, tmp_path):
        """Approve over source no agent opened is the line a reader acts on."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState

        state = PipelineState(group_names=["a", "b", "c"])
        state.groups_failed[2] = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, record(), pipeline_state=state,
        )
        assert "No verdict — part of this review did not run." in result.verdict
        assert "Approve" not in result.verdict

    def test_a_run_where_every_group_reported_counts_plainly(self, ro, tmp_path):
        """The common case keeps its wording; only a partial run qualifies it."""
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, record(),
        )
        assert "in 3 groups" in result.summary
        assert " of 3 groups" not in result.summary

    def test_a_run_where_every_group_failed_claims_nothing(self, ro, tmp_path):
        """The worst case, and the one the partial guard did not cover.

        `partial` was `failed > 0 and failed < group_count`, false both when
        nothing failed and when everything did — so a run whose every group
        died took the full-coverage wording and shipped "No findings across 13
        files in 3 groups" over 13 files nothing read. Observed on this branch:
        three groups hit the turn cap, and the review reported a clean read of
        all of them above a failures table listing all three.
        """
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState

        state = PipelineState(group_names=["a", "b", "c"])
        for n in range(3):
            state.groups_failed[n] = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=15)
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, record(), pipeline_state=state,
        )

        assert "in 0 of 3 groups" in result.summary
        assert "No findings" not in result.summary
        assert "No group reported" in result.summary

    def test_a_run_where_every_group_failed_states_no_verdict(self, ro, tmp_path):
        """The summary said nothing reported; the verdict said Approve.

        Three agents dead before a prompt reached them, 0 tokens, and the
        review still closed on "Approve — no findings" — the one line a
        reader acts on without opening the failures table.
        """
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.document import ReviewDocument
        from review.state import PipelineState
        from review.verdict import NO_RUN_VERDICT

        state = PipelineState(group_names=["a", "b", "c"])
        for n in range(3):
            state.groups_failed[n] = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="boom")
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, record(), pipeline_state=state,
        )

        assert result.verdict.strip() == NO_RUN_VERDICT.strip()
        assert "Approve" not in result.verdict

    def test_a_run_synthesis_diagnosed_as_all_failed_states_no_verdict(self, ro, tmp_path):
        """`all_groups_failed` has a second source: synthesis's own diagnosis."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from core.phases import Phase
        from review.state import PipelineState
        from review.verdict import NO_RUN_VERDICT

        state = PipelineState(group_names=["a", "b"])
        state.failed[Phase.SYNTHESIS] = Diagnosis(DiagnosisKind.ALL_GROUPS_FAILED)
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 2, record(), pipeline_state=state,
        )

        assert NO_RUN_VERDICT.strip() in result.verdict
        assert "Approve" not in result.verdict
        assert "No findings" not in result.summary
        assert "No group reported" in result.summary


# ── 33. _write_clean_review ─────────────────────────────────────────────────


class TestWriteCleanReview:
    TRIAGE = ReviewRecord(triage=(
        TriageEntry("a.py", "tier 2, reviewed"),
        TriageEntry("b.py", "tier 3, skimmed"),
    ))

    @staticmethod
    def _job(ro, tmp_path, mode):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42" if mode == ro.Mode.PR else "",
            pr=ro.PRMetadata(title="clean PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=3, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=mode,
        )

    def test_pr_mode(self, ro, tmp_path):
        job = self._job(ro, tmp_path, ro.Mode.PR)
        ro._write_clean_review(job, 2, self.TRIAGE)
        content = Path(job.review_file).read_text()
        assert "# Review:" in content
        assert "No findings across 3 files in 2 groups" in content
        assert "Approve — clean review." in content

    def test_self_review_mode(self, ro, tmp_path):
        job = self._job(ro, tmp_path, ro.Mode.SELF)
        ro._write_clean_review(job, 2, self.TRIAGE)
        content = Path(job.review_file).read_text()
        assert "# Self-Review:" in content
        assert "No findings across 3 files in 2 groups" in content
        assert "Verdict" not in content

    def test_carries_the_triage_its_groups_merged(self, ro, tmp_path):
        """The triage is the only evidence a findings-free run leaves that the
        groups examined anything, and it was the one path that dropped it."""
        job = self._job(ro, tmp_path, ro.Mode.PR)
        ro._write_clean_review(job, 2, self.TRIAGE)
        content = Path(job.review_file).read_text()
        assert "## File Triage" in content
        assert "- `a.py` — tier 2, reviewed" in content
        assert "- `b.py` — tier 3, skimmed" in content

    def test_a_clean_review_stores_no_ledger(self, ro, tmp_path):
        """X11: publishing clears the ledger; the clean path was the one writer that skipped it."""
        from review.record import LedgerLine, ReviewRecord, load_record
        from review.types import PriorDisposition
        job = self._job(ro, tmp_path, ro.Mode.PR)
        merged = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.FIXED, "a.py"),))
        ro._write_clean_review(job, 2, merged)
        assert load_record(tmp_path).ledger == ()
        assert "## Prior findings" not in Path(job.review_file).read_text()


# ── 35. is_complete_review ──────────────────────────────────────────────────


class TestIsCompleteReview:
    @staticmethod
    def _store(tmp_path, **kw):
        from review.record import ReviewRecord, save_record
        save_record(tmp_path, ReviewRecord(**kw))
        return str(tmp_path / "review.md")

    def test_has_summary(self, ro, tmp_path):
        assert ro.is_complete_review(self._store(tmp_path, summary="Looks good.")) is True

    def test_has_verdict(self, ro, tmp_path):
        assert ro.is_complete_review(self._store(tmp_path, verdict="Approve.")) is True

    def test_missing_summary_and_verdict(self, ro, tmp_path):
        assert ro.is_complete_review(self._store(tmp_path)) is False

    def test_markdown_without_record_is_incomplete(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Summary\nLooks good.\n")
        assert ro.is_complete_review(str(f)) is False

    def test_file_not_exists(self, ro, tmp_path):
        assert ro.is_complete_review(str(tmp_path / "nonexistent.md")) is False


# ── _write_review_sidecar enriched meta.json ────────────────────────────────


class TestWriteReviewSidecar:
    @staticmethod
    def _blank():
        from review.record import ReviewRecord
        return ReviewRecord(summary="x")

    @staticmethod
    def _make_job(ro, tmp_path):
        pr = ro.PRMetadata(
            title="Test PR", body="", head="feat/test", base="main",
            head_sha="abc123", additions=10, deletions=5,
            changed_files=3, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        return ro.ReviewJob(
            repo="org/repo", pr_number="42", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="test-v1",
        )

    def test_meta_includes_title_and_changed_files(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["title"] == "Test PR"
        assert meta["changed_files"] == 3

    def test_meta_includes_mode(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["mode"] == "pr"

    def test_meta_includes_generator_version(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["generator_version"] == "test-v1"

    def test_meta_states_no_generator_when_the_run_had_none(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.generator_version = ""
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["generator_version"]

    def test_meta_records_the_runs_start(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.started_at = "2026-08-18T13:47:03+00:00"
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["started_at"] == "2026-08-18T13:47:03+00:00"

    def test_meta_claims_nothing_about_being_reviewed(self, ro, tmp_path):
        """The sidecar is written from every branch that reaches a review file,
        so it cannot be the thing that says a review was produced."""
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["reviewed_at"]

    def test_a_second_write_keeps_the_same_start(self, ro, tmp_path):
        """One run, one start — the sidecar carries the job's stamp, not the
        clock at each of the branches that write it."""
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        first = json.loads((tmp_path / "meta.json").read_text())["started_at"]
        ro._publish(job, self._blank())
        assert json.loads((tmp_path / "meta.json").read_text())["started_at"] == first

    def test_every_key_on_disk_is_a_field_of_the_type(self, ro, tmp_path):
        """What the sidecar having an owner buys: a writer cannot record a key
        no reader can name, and cannot drop one a reader looks for."""
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert set(meta) == {f.name for f in dataclasses.fields(ro.ReviewMeta)}

    def test_the_sidecar_reads_back_as_what_was_written(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        assert ro.read_review_meta(tmp_path) == ro._review_meta(job)

    def test_the_job_host_reaches_the_sidecar(self, ro, tmp_path):
        """The stamp `review-post` depends on.

        It is spawned with a review file and never reads a remote, so a host the
        job knew and the sidecar did not is a host that reaches no permalink.
        """
        job = self._make_job(ro, tmp_path)
        job.host = "ghe.acme.com"
        ro._publish(job, self._blank())
        assert json.loads((tmp_path / "meta.json").read_text())["host"] == "ghe.acme.com"
        assert ro.read_review_meta(tmp_path).host == "ghe.acme.com"

    def test_an_incremental_run_records_what_it_is_a_delta_against(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            delta_files=["a.py", "b.py"], prior_head_sha="dead00",
        )
        ro._publish(job, self._blank())
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.INCREMENTAL
        assert meta.prior_sha == "dead00"
        assert meta.delta_files == ("a.py", "b.py")

    def test_a_full_run_records_no_delta(self, ro, tmp_path):
        """A full review is a delta against nothing, which is not the same
        claim as a delta against a prior review that moved no files."""
        job = self._make_job(ro, tmp_path)
        ro._publish(job, self._blank())
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.FULL
        assert (meta.prior_sha, meta.delta_files) == ("", ())


def test_a_mechanical_review_carries_the_run_s_areas_unassessed(ro, tmp_path):
    job = TestWriteReviewSidecar._make_job(ro, tmp_path)
    job.focus = (user_area("A"),)
    ro._write_mechanical_fallback(job, 1, record())
    rec = load_record(tmp_path)
    assert [(a.title, a.coverage) for a in rec.focus] == [("A", "")]


def test_an_unchanged_review_does_not_carry_last_round_s_coverage(ro, tmp_path):
    from review.store import StoredReview
    from review.types import ReviewMeta
    job = TestWriteReviewSidecar._make_job(ro, tmp_path)
    job.prior = StoredReview(
        record(focus=(area(
            id="F1", title="A", source=FocusSource.USER, coverage="old",
        ),)),
        ReviewMeta(),
    )
    job.focus = (user_area("A"),)
    ro.write_unchanged_review(job)
    rec = load_record(tmp_path)
    assert [(a.title, a.coverage) for a in rec.focus] == [("A", "")]
