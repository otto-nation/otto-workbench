"""A re-review reads its predecessor as data: prior.json and prior-meta.json, never prior.md."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Phase
from review.phases import PhaseResult
from review.prompt_prior import _build_prior_section, _scoped
from review.reconcile import _prior_findings, passed_over, reconcile
from review.record import LedgerLine, SeveritySection
from review.render import BANNER
from review.steps import _carry_forward_prior_findings
from review.store import StoredReview
from review.types import Group, PreflightData, PriorDisposition, ReviewMeta

from review_record_support import finding, fixture_meta, fixture_record, record


def _prior() -> StoredReview:
    return StoredReview(fixture_record("synthesis_pr"), fixture_meta("synthesis_pr"))


def test_prior_findings_come_from_the_record():
    refs = [p.ref.finding_id for p in _prior_findings(_prior().record)]
    assert refs == ["S1", "S2", "N1", "N2", "I1"]


def test_a_fixed_prior_finding_is_not_reconciled():
    prior = StoredReview(fixture_record("self_incremental_fixed"),
                         fixture_meta("self_incremental_fixed"))
    assert "N1" not in [p.ref.finding_id for p in _prior_findings(prior.record)]


def test_reconcile_dates_the_prior_from_its_sidecar():
    review = record(ledger=(LedgerLine(
        "S1", PriorDisposition.FIXED, "apps/server/src/voice/transcriber.ts",
    ),))
    result = reconcile(_prior(), review)
    assert result.prior_date == "2026-10-15"


def test_the_prompt_quotes_the_prior_record_without_bookkeeping():
    section = _build_prior_section(_prior(), "ctx")
    assert "## File Triage" not in section and "**[S1]** <!-- sid:" in section
    assert "## Summary" in section
    assert BANNER not in section


def test_scoping_keeps_line_less_findings_by_path():
    """`[I1]` names `voice.ts` with no line; the group holding the file carries it."""
    scoped = _scoped(_prior().record, ["apps/web/src/stores/voice.ts"])
    assert [f.id for f in scoped.findings] == ["S2", "I1"]


def test_a_stale_prior_means_no_prior_section():
    assert _build_prior_section(None, "ctx") == ""


def test_the_delta_is_measured_from_the_prior_sidecar(monkeypatch):
    import review.collect
    from types import SimpleNamespace
    job = SimpleNamespace(prior=_prior(), focus=(),
                          pr=SimpleNamespace(head_sha="ffff"), wt_path="/nowhere")
    monkeypatch.setattr(review.collect.git.client, "out", lambda *a, **k: "commit")
    assert review.collect._prior_sha_for_delta(job) == "2cb613ec084559a9590f074eabde546c44b284a3"


def test_the_pipeline_carries_scoped_prior_findings_into_the_merged_record(
    monkeypatch, tmp_path,
):
    import review.pipeline
    from gh.types import PRContext, PRMetadata
    from review.types import ReviewJob

    captured = {}

    def _capture(job, state, holistic, group_count, merged, *a, **k):
        captured["merged"] = merged
        return PhaseResult()

    monkeypatch.setattr(review.pipeline, "_run_synthesis_or_fallback", _capture)
    monkeypatch.setattr(review.pipeline, "_run_disprove_gate", lambda *a, **k: PhaseResult())
    monkeypatch.setattr(review.pipeline, "_group_merge_cap", lambda job: 10**9)
    monkeypatch.setattr(
        review.pipeline, "group_files",
        lambda pr: [
            Group(name="auth", files=["src/auth.go"], lines=250),
            Group(name="docker", files=["docker/build.sh"], lines=250),
        ],
    )
    monkeypatch.setattr(
        review.pipeline, "merge_smallest_groups", lambda groups, *a, **k: groups,
    )

    prior = StoredReview(
        record(
            finding("M1", "auth bug", path="src/auth.go", line=10, checkbox=True),
            finding("M2", "no USER", path="docker/build.sh", line=50, checkbox=True),
            severity_sections=(SeveritySection("M"),),
        ),
        ReviewMeta(),
    )
    files = [
        {"path": "src/auth.go", "additions": 250, "deletions": 0},
        {"path": "docker/build.sh", "additions": 250, "deletions": 0},
    ]
    job = ReviewJob(
        repo="org/repo", pr_number="1",
        pr=PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=500, deletions=0, changed_files=2, files=files,
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        skip_phases=frozenset({Phase.GROUP}),
        prior=prior,
        preflight=PreflightData(
            diff="", commit_log="", file_contents={},
            file_permissions={}, instructions_md="", architecture_md="",
            delta_files=["src/auth.go"], prior_head_sha="0ldc0de",
        ),
    )
    from review.artifacts import declinable_sids
    from review.state import PIPELINE_SCHEMA_VERSION
    written = []
    real_write = review.pipeline._write_pipeline_state
    monkeypatch.setattr(review.pipeline, "_write_pipeline_state",
                        lambda job, state: written.append(state) or real_write(job, state))
    real_merge = review.pipeline._merge_round
    monkeypatch.setattr(review.pipeline, "_merge_round",
                        lambda *a, **k: captured.update(declinable=k["declinable"]) or real_merge(*a, **k))
    review.pipeline.run_multi_phase(job, disprove=False)
    merged = captured["merged"]
    assert [(f.path, f.body) for f in merged.record.findings] == [
        ("docker/build.sh", "no USER"),
    ]
    assert [f.id for f in merged.record.findings] == ["M1"]
    assert all(f.sid for f in merged.record.findings)
    assert any(
        s.note == "### Carried forward: docker" for s in merged.record.severity_sections
    )
    # A fresh run stamps the version where it constructs the state; a resume
    # refuses any state file without it (X8).
    assert written and written[0].schema_version == PIPELINE_SCHEMA_VERSION
    # The groups may decline only what the prior round held.
    assert captured["declinable"] == declinable_sids(prior) != frozenset()
