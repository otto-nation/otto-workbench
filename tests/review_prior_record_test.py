"""A re-review reads its predecessor as data: prior.json and prior-meta.json, never prior.md."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import ingest_review
from review.prompt_prior import _build_prior_section, _scoped
from review.reconcile import _prior_findings, passed_over, reconcile
from review.render import BANNER
from review.store import StoredReview

from review_record_support import fixture_meta, fixture_text


def _prior() -> StoredReview:
    return StoredReview(ingest_review(fixture_text("synthesis_pr", "input.md")), fixture_meta("synthesis_pr"))


def test_prior_findings_come_from_the_record():
    refs = [p.ref.finding_id for p in _prior_findings(_prior().record)]
    assert refs == ["S1", "S2", "N1", "N2", "I1"]


def test_a_fixed_prior_finding_is_not_reconciled():
    prior = StoredReview(ingest_review(fixture_text("self_incremental_fixed", "input.md")),
                         fixture_meta("self_incremental_fixed"))
    assert "N1" not in [p.ref.finding_id for p in _prior_findings(prior.record)]


def test_reconcile_dates_the_prior_from_its_sidecar():
    review = ingest_review("## Prior findings\n- **[S1]** **`apps/server/src/voice/transcriber.ts:343`** — Fixed\n")
    result = reconcile(_prior(), review)
    assert result.prior_date == "2026-10-15"


def test_the_prompt_quotes_the_prior_record_without_bookkeeping():
    section = _build_prior_section(_prior(), "ctx")
    assert "## File Triage" not in section and "**[S1]** <!-- sid:" in section
    assert "## Summary" in section
    assert BANNER not in section


def test_scoping_keeps_only_located_findings_in_the_group():
    scoped = _scoped(_prior().record, ["apps/web/src/stores/voice.ts"])
    assert [f.id for f in scoped.findings] == ["S2"]


def test_a_stale_prior_means_no_prior_section():
    assert _build_prior_section(None, "ctx") == ""


def test_the_delta_is_measured_from_the_prior_sidecar(monkeypatch):
    import review.collect as collect
    from types import SimpleNamespace
    job = SimpleNamespace(prior=_prior(), prior_review="ignored",
                          pr=SimpleNamespace(head_sha="ffff"), wt_path="/nowhere")
    monkeypatch.setattr(collect.git.client, "out", lambda *a, **k: "commit")
    assert collect._prior_sha_for_delta(job) == "2cb613ec084559a9590f074eabde546c44b284a3"
