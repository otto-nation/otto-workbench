"""Every path that writes a review now publishes a record: review.json beside a rendered review.md."""

import shutil
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import core.serde
import review.orchestrate
import review.outcome
import review.state
import review.steps
from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Phase
from gh.types import PRContext, PRMetadata
from pr.domains import ReviewStatus
from review.ingest import ingest_review
from review.merge import merge_records
from review.paths import FILENAME_RECORD
from review.record import Disposition
from review.render import BANNER
from review.state import PipelineState
from review.steps import DisproveRun, _phase_disprove, _run_disprove_gate
from review.store import StoredReview, read_review, write_review
from review.types import DeltaAttribution, PreflightData, ReviewJob
from review.artifacts import DisproveArtifact, DisproveOutcome, DisproveVerdict
from review.verify import apply_disprove

from review_record_support import FIXTURES, copy_tree, canonical, expected_text, fixture_meta, fixture_text

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
    from review.artifacts import GroupArtifact
    from review.ingest import load_artifact
    return merge_records([
        load_artifact(str(FIXTURES / "groups" / f"group-{i}.json"), GroupArtifact)
        .to_record(declinable=frozenset())
        for i in (1, 2)
    ])


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
    review.outcome._write_mechanical_fallback(job, 2, _merged())
    stored = read_review(Path(job.review_file))
    assert stored is not None and BANNER in Path(job.review_file).read_text()
    assert stored.record.summary.startswith("4 findings across 3 files in 2 groups.")
    assert [f.id for f in stored.record.findings] == ["M1", "S1", "N1", "N2"]
    assert "call()" in stored.record.finding("S1").evidence and "call()" not in Path(job.review_file).read_text()


def test_the_clean_review_is_published_as_a_record(tmp_path):
    job = _job(tmp_path)
    review.outcome._write_clean_review(job, 2, ingest_review("## File Triage\n- `src/app.py` — reviewed; nothing to report.\n"))
    assert (Path(job.review_file).parent / FILENAME_RECORD).is_file()
    assert _same_as_fixture(job, "clean")


def test_an_agent_review_is_post_processed_and_stamped(tmp_path):
    job = _job(tmp_path)
    record = ingest_review(fixture_text("evidence_dropped", "raw.md"))
    review.outcome._post_process_review(job, record)
    assert _same_as_fixture(job, "evidence_dropped")
    assert job.verification["dropped"] == ["M1"]


def test_the_unchanged_review_carries_the_prior_record(tmp_path):
    prior = StoredReview(ingest_review(fixture_text("synthesis_pr", "input.md")), fixture_meta("synthesis_pr"))
    job = _job(tmp_path)
    job.prior = prior
    job.preflight = PreflightData(diff="", commit_log="", file_contents={}, file_permissions={},
                                  instructions_md="", architecture_md="", delta_files=[],
                                  prior_head_sha=prior.meta.head_sha,
                                  delta_attribution=DeltaAttribution.ATTRIBUTED)
    review.outcome.write_unchanged_review(job)
    stored = read_review(Path(job.review_file))
    assert stored.meta.prior_date == "2026-10-15"
    assert [f.id for f in stored.record.findings] == ["S1", "S2", "N1", "N2", "I1"]


def test_completeness_is_read_off_the_record(tmp_path):
    job = _job(tmp_path)
    Path(job.review_file).write_text("## Summary\nraw agent text\n")
    assert not review.outcome.is_complete_review(job.review_file)


def test_the_sidecar_and_the_header_cannot_disagree(tmp_path):
    job = _job(tmp_path)
    review.outcome._write_clean_review(job, 1, ingest_review("## File Triage\n"))
    stored = read_review(Path(job.review_file))
    assert stored.meta.head_sha == HEAD
    assert f"<!-- head_sha: {HEAD} -->" in Path(job.review_file).read_text()


@pytest.mark.parametrize(
    ("skip_synthesis", "cost_so_far"),
    [(True, 0.0), (False, 25.0)],
    ids=["no-synthesis", "budget"],
)
def test_no_synthesis_paths_reconcile_and_verify(tmp_path, skip_synthesis, cost_so_far):
    """Skipping `_reconcile_and_verify` on either path must fail this."""
    from core.phases import Phase
    from review.state import PipelineState
    from review.steps import Merged, _run_synthesis_or_fallback

    job = _job(
        tmp_path,
        skip_phases=frozenset({Phase.SYNTHESIS}) if skip_synthesis else frozenset(),
    )
    state = PipelineState(
        head_sha=HEAD, group_names=["g1"], done={Phase.HOLISTIC}, groups_done=[1],
    )
    merged = Merged(
        ingest_review(
            "## Should fix\n- **[S1]** **`ghost.go:1`** — gone\n"
            "  > ```go\n  > not_in_the_tree()\n  > ```\n",
            strict=False,
        ),
    )
    _run_synthesis_or_fallback(job, state, "", 1, merged, [], 0, cost_so_far, 20.0)
    assert job.verification is not None and job.verification["dropped"] == ["S1"]
    assert "verification-drops" in Path(job.review_file).read_text()
    _assert_meta_status_matches_header(job)


def test_a_new_round_keeps_the_static_declines_an_earlier_round_recorded(tmp_path):
    from review.paths import read_review_meta, write_review_meta
    from review.types import ReviewMeta
    job = _job(tmp_path)
    review_dir = Path(job.review_file).parent
    write_review_meta(review_dir, ReviewMeta(
        head_sha="0" * 40, static_declined={"a1b2c3d4": "generated file"},
        fix_commit_sha="f" * 40,
    ))
    review.outcome._publish(job, _merged())
    meta = read_review_meta(review_dir)
    assert meta.static_declined == {"a1b2c3d4": "generated file"}
    assert meta.head_sha == HEAD
    # The previous round's fix commit belongs to that round, not this one.
    assert meta.fix_commit_sha == ""


def _assert_meta_status_matches_header(job):
    stored = read_review(Path(job.review_file))
    assert stored is not None and stored.meta.status is not None
    assert f"<!-- status: {stored.meta.status.value} -->" in Path(job.review_file).read_text()


def test_failures_and_status_land_in_record_and_meta(tmp_path):
    job = _job(tmp_path)
    review.outcome._write_clean_review(job, 2, _merged())
    state = PipelineState(head_sha=HEAD, group_names=["app", "cli"],
                          groups_failed={2: Diagnosis(DiagnosisKind.OUTPUT_MISSING)})
    core.serde.write_json(Path(job.review_file).parent / "pipeline.json", core.serde.to_dict(state))
    review.state._inject_failures_and_status(job.review_file, state)
    stored = read_review(Path(job.review_file))
    assert stored.record.failures[0].agent == "group-2: cli"
    assert stored.meta.status is ReviewStatus.PARTIAL
    assert "<!-- status: partial -->" in Path(job.review_file).read_text()


def test_a_disprove_after_failures_keeps_both(tmp_path, monkeypatch):
    job = _job(tmp_path)
    write_review(
        Path(job.review_file),
        ingest_review(fixture_text("synthesis_pr", "input.md")),
        fixture_meta("synthesis_pr"),
    )
    state = PipelineState(head_sha=HEAD, group_names=["a"],
                          failed={Phase.DISPROVE: Diagnosis(DiagnosisKind.OUTPUT_MISSING)})
    review.state._inject_failures_and_status(job.review_file, state)
    from review.phases import PhaseResult
    monkeypatch.setattr(review.steps, "run_phase", lambda *a, **k: PhaseResult(
        artifact=DisproveArtifact(verdicts=(
            DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "no"),)),
    ))
    stored = read_review(Path(job.review_file))
    run = _phase_disprove(job, stored.record)
    assert isinstance(run, DisproveRun)
    assert [f.id for f in run.record.findings if f.disposition is Disposition.FALSIFIED] == ["S1"]
    assert run.record.finding("S1") is None
    assert run.record.failures, "the failures table must survive the disprove write"


def test_the_disprove_gate_hides_a_falsified_finding(tmp_path, monkeypatch):
    job = _job(tmp_path)
    write_review(
        Path(job.review_file),
        ingest_review(fixture_text("synthesis_pr", "input.md")),
        fixture_meta("synthesis_pr"),
    )
    from review.phases import PhaseResult
    monkeypatch.setattr(review.steps, "run_phase", lambda *a, **k: PhaseResult(
        artifact=DisproveArtifact(verdicts=(
            DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "no"),)),
    ))
    state = PipelineState(head_sha=HEAD, group_names=["a"], done={Phase.SYNTHESIS})
    _run_disprove_gate(job, state, disprove=True, cost_so_far=0.0, max_cost=20.0)
    assert read_review(Path(job.review_file)).record.finding("S1") is None


def test_an_unchanged_review_does_not_resurrect_a_falsified_finding(tmp_path):
    applied = apply_disprove(
        ingest_review(fixture_text("synthesis_pr", "input.md")),
        DisproveArtifact(verdicts=(
            DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "not reachable"),)),
    )
    prior = StoredReview(applied.record, fixture_meta("synthesis_pr"))
    job = _job(tmp_path)
    job.prior = prior
    job.preflight = PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        instructions_md="", architecture_md="", delta_files=[],
        prior_head_sha=prior.meta.head_sha,
        delta_attribution=DeltaAttribution.ATTRIBUTED,
    )
    review.outcome.write_unchanged_review(job)
    stored = read_review(Path(job.review_file))
    assert stored.record.finding("S1") is None
    assert next(f for f in stored.record.findings if f.id == "S1").disposition is Disposition.FALSIFIED
    assert "**[S1]**" not in Path(job.review_file).read_text()


def test_the_synthesis_fallback_stamps_the_same_status_on_meta_and_header(tmp_path):
    job = _job(tmp_path)
    review.outcome._write_mechanical_fallback(job, 2, _merged())
    _assert_meta_status_matches_header(job)


def test_static_analysis_is_a_record_field(tmp_path, monkeypatch):
    job = _job(tmp_path)
    review.outcome._write_clean_review(job, 1, _merged())
    monkeypatch.setattr(review.orchestrate, "run_static_analysis", lambda *a, **k: [])
    monkeypatch.setattr(review.orchestrate, "format_static_analysis", lambda results, declined: "All checks passed.")
    monkeypatch.setattr(review.orchestrate, "added_lines", lambda *a, **k: None)
    review.orchestrate._inject_static_analysis_section(job)
    assert read_review(Path(job.review_file)).record.static_analysis == "All checks passed."
    text = Path(job.review_file).read_text()
    assert text.index("## Static Analysis") < text.index("## Verdict")


def test_rebuild_publishes_a_record_matching_todays_rebuild(tmp_path):
    import review.rebuild
    from core.phases import Mode
    from review.paths import write_review_meta
    from review.types import ReviewMeta, ReviewType
    rd = tmp_path / "rev"
    rd.mkdir()
    for i in (1, 2):
        shutil.copy(FIXTURES / "groups" / f"group-{i}.json", rd / f"group-{i}.json")
    write_review_meta(rd, ReviewMeta(repo="acme/widget", pr_number=38, head_sha=HEAD, head_ref="feat/retry",
                                     base_ref="main", title="feat: retry the flaky fetch", changed_files=3,
                                     generator_version="review 1.42.0 / otto-workbench 1.47.0 (6c990742)",
                                     review_type=ReviewType.FULL, mode=Mode.PR))
    assert review.rebuild.rebuild(rd, "38", script="test") == 0
    stored = read_review(rd / "review.md")
    from dataclasses import replace
    from review.render import render_review
    meta = replace(stored.meta, date=fixture_meta("rebuild").date)
    assert canonical(render_review(stored.record, meta)) == canonical(expected_text("rebuild"))
