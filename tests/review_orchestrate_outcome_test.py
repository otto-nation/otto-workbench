"""The review document `review.outcome` writes: clean, fallback, completeness and sidecar."""

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401


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
        rendered = ro._document(self._job(ro, tmp_path), "## Summary\nbody\n").render()
        assert rendered == (
            "# Review: org/repo#42 — test\n"
            f"<!-- date: {date.today().isoformat()} -->\n"
            "<!-- mode: pr -->\n"
            "<!-- pr: 42 -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- head_ref: feat -->\n"
            "<!-- base_ref: main -->\n"
            "<!-- review_type: full -->\n"
            "<!-- generator: 1.0.0 -->\n"
            "\n"
            "## Summary\nbody\n"
        )

    def test_a_full_review_reports_no_group_ratio(self, ro, tmp_path):
        """Skipped groups are a claim about an incremental run: the pipeline
        passes the count on every path, and a full review states none."""
        document = ro._document(
            self._job(ro, tmp_path), "## Summary\n",
            skipped_groups=1, total_groups=4,
        )
        assert "skipped_groups" not in document.render()

    def _incremental(self, ro, tmp_path, prior_review=""):
        job = self._job(ro, tmp_path, prior_review=prior_review)
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            delta_files=["a.py"], prior_head_sha="def456",
        )
        return job

    def test_an_incremental_review_dates_itself_against_the_prior_one(self, ro, tmp_path):
        prior = (
            "# Review: org/repo#42\n"
            "<!-- date: 2026-08-20 -->\n"
            "<!-- head_sha: old -->\n"
        )
        document = ro._document(
            self._incremental(ro, tmp_path, prior), "## Summary\n",
            skipped_groups=1, total_groups=4,
        )
        assert document.header.prior_date == "2026-08-20"
        assert document.header.prior_sha == "def456"
        assert "<!-- skipped_groups: 1/4 -->" in document.render()

    def test_an_incremental_review_with_no_prior_document_says_so(self, ro, tmp_path):
        job = self._incremental(ro, tmp_path)
        assert ro._document(job, "## Summary\n").header.prior_date == "unknown"


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
        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._build_mechanical_fallback(job, 3, merged).render()
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
        merged = "## Nit\n- **[N1]** **`file.go:1`** — style\n"
        result = ro._build_mechanical_fallback(job, 2, merged).render()
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
        merged = (
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Nit\n- **[N1]** **`b.go:2`** — style\n- **[N2]** **`c.go:3`** — naming\n"
        )
        result = ro._build_mechanical_fallback(job, 3, merged)
        assert "3 findings" in result.body

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
            self._partial_job(ro, tmp_path), 3, "", pipeline_state=state,
        )
        assert "in 2 of 3 groups" in result.body
        assert "in 3 groups" not in result.body

    def test_a_partial_run_withholds_the_clean_verdict(self, ro, tmp_path):
        """Approve over source no agent opened is the line a reader acts on."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState

        state = PipelineState(group_names=["a", "b", "c"])
        state.groups_failed[2] = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, "", pipeline_state=state,
        )
        assert "No verdict — part of this review did not run." in result.body
        assert "Approve" not in result.body

    def test_a_run_where_every_group_reported_counts_plainly(self, ro, tmp_path):
        """The common case keeps its wording; only a partial run qualifies it."""
        result = ro._build_mechanical_fallback(
            self._partial_job(ro, tmp_path), 3, "",
        )
        assert "in 3 groups" in result.body
        assert " of 3 groups" not in result.body

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
            self._partial_job(ro, tmp_path), 3, "", pipeline_state=state,
        )

        assert "in 0 of 3 groups" in result.body
        assert "No findings" not in result.body
        assert "No group reported" in result.body


# ── 33. _write_clean_review ─────────────────────────────────────────────────


class TestWriteCleanReview:
    TRIAGE = "## File Triage\n- `a.py` — tier 2, reviewed\n- `b.py` — tier 3, skimmed\n"

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


# ── 35. is_complete_review ──────────────────────────────────────────────────


class TestIsCompleteReview:
    def test_has_summary(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Summary\nLooks good.\n")
        assert ro.is_complete_review(str(f)) is True

    def test_has_verdict(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Verdict\nApprove.\n")
        assert ro.is_complete_review(str(f)) is True

    def test_missing_headers(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\nSome content without required headers.\n")
        assert ro.is_complete_review(str(f)) is False

    def test_file_not_exists(self, ro, tmp_path):
        assert ro.is_complete_review(str(tmp_path / "nonexistent.md")) is False

    def test_empty_file(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("")
        assert ro.is_complete_review(str(f)) is False


# ── _write_review_sidecar enriched meta.json ────────────────────────────────


class TestWriteReviewSidecar:
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
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["title"] == "Test PR"
        assert meta["changed_files"] == 3

    def test_meta_includes_mode(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["mode"] == "pr"

    def test_meta_includes_generator_version(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["generator_version"] == "test-v1"

    def test_meta_states_no_generator_when_the_run_had_none(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.generator_version = ""
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["generator_version"]

    def test_meta_records_the_runs_start(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.started_at = "2026-08-18T13:47:03+00:00"
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["started_at"] == "2026-08-18T13:47:03+00:00"

    def test_meta_claims_nothing_about_being_reviewed(self, ro, tmp_path):
        """The sidecar is written from every branch that reaches a review file,
        so it cannot be the thing that says a review was produced."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["reviewed_at"]

    def test_a_second_write_keeps_the_same_start(self, ro, tmp_path):
        """One run, one start — the sidecar carries the job's stamp, not the
        clock at each of the branches that write it."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        first = json.loads((tmp_path / "meta.json").read_text())["started_at"]
        ro._write_review_sidecar(job)
        assert json.loads((tmp_path / "meta.json").read_text())["started_at"] == first

    def test_every_key_on_disk_is_a_field_of_the_type(self, ro, tmp_path):
        """What the sidecar having an owner buys: a writer cannot record a key
        no reader can name, and cannot drop one a reader looks for."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert set(meta) == {f.name for f in dataclasses.fields(ro.ReviewMeta)}

    def test_the_sidecar_reads_back_as_what_was_written(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        assert ro.read_review_meta(tmp_path) == ro._job_meta(job)

    def test_the_job_host_reaches_the_sidecar(self, ro, tmp_path):
        """The stamp `review-post` depends on.

        It is spawned with a review file and never reads a remote, so a host the
        job knew and the sidecar did not is a host that reaches no permalink.
        """
        job = self._make_job(ro, tmp_path)
        job.host = "ghe.acme.com"
        ro._write_review_sidecar(job)
        assert json.loads((tmp_path / "meta.json").read_text())["host"] == "ghe.acme.com"
        assert ro.read_review_meta(tmp_path).host == "ghe.acme.com"

    def test_an_incremental_run_records_what_it_is_a_delta_against(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            delta_files=["a.py", "b.py"], prior_head_sha="dead00",
        )
        ro._write_review_sidecar(job)
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.INCREMENTAL
        assert meta.prior_sha == "dead00"
        assert meta.delta_files == ("a.py", "b.py")

    def test_a_full_run_records_no_delta(self, ro, tmp_path):
        """A full review is a delta against nothing, which is not the same
        claim as a delta against a prior review that moved no files."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.FULL
        assert (meta.prior_sha, meta.delta_files) == ("", ())
