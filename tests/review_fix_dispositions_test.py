"""The fix pass's answers become dispositions on the record; nothing ticks markdown."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.finding_issue
from gh.types import PRContext, PRMetadata
from pr.fix import FixOutcome, ItemOutcome
from review.fix import apply_outcomes, apply_static_outcomes
from review.ingest import ingest_review
from review.outcome import write_unchanged_review
from review.record import Disposition, RecordFinding, ReviewRecord
from review.render import render_review
from review.store import StoredReview, read_review, write_review
from review.types import DeltaAttribution, PreflightData, ReviewJob

from review_record_support import canonical, expected_text, fixture_meta, fixture_text

OUTCOMES = [
    ItemOutcome(id="S1", outcome=FixOutcome.FIXED, verified=False, verify_detail="the suite did not run"),
    ItemOutcome(id="S2", outcome=FixOutcome.DECLINED, reason="argparse already validates it with type=int"),
    ItemOutcome(id="N1", outcome=FixOutcome.NEEDS_HUMAN, reason="needs a product call"),
    ItemOutcome(id="N2", outcome=FixOutcome.DEFERRED),
    ItemOutcome(id="SA1", outcome=FixOutcome.FIXED, verified=True),
]


def _answered():
    from dataclasses import replace
    record = apply_outcomes(ingest_review(fixture_text("fix_annotated", "before.md")), OUTCOMES)
    return replace(record, static_analysis=apply_static_outcomes(record.static_analysis, OUTCOMES))


def test_the_answered_review_renders_as_todays_fix_pass_wrote_it():
    rendered = render_review(_answered(), fixture_meta("fix_annotated"))
    assert canonical(rendered) == canonical(expected_text("fix_annotated"))


def test_each_outcome_is_a_disposition():
    f = {x.id: x for x in _answered().findings}
    assert (f["S1"].disposition, f["S1"].unverified) == (Disposition.FIXED, True)
    assert f["S2"].disposition is Disposition.DECLINED
    assert f["N1"].disposition is Disposition.SKIPPED
    assert f["N2"].disposition is Disposition.UNREACHED


def test_a_pr_mode_finding_without_a_box_stays_open_when_fixed():
    record = ingest_review("## Should fix\n- **[S1]** `a` — x\n")
    out = apply_outcomes(record, [ItemOutcome(id="S1", outcome=FixOutcome.FIXED, verified=True)])
    assert out.finding("S1").disposition is Disposition.OPEN


def test_an_earlier_verdict_outranks_this_pass():
    record = ingest_review("## Nit\n- [ ] **[N1]** `a` — x *(declined — no)*\n")
    out = apply_outcomes(record, [ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=True)])
    assert out.finding("N1").disposition is Disposition.DECLINED


def test_a_deferred_finding_can_be_fixed_by_a_later_pass():
    """Held by the UNREACHED→OPEN reset *and* UNREACHED staying off `_STICKY`.

    Putting UNREACHED on `_STICKY` alone cannot fail this: the reset runs first.
    Removing the reset and adding UNREACHED to `_STICKY` together is the revert.
    """
    record = apply_outcomes(ingest_review("## Nit\n- [ ] **[N1]** `a` — x\n"),
                            [ItemOutcome(id="N1", outcome=FixOutcome.DEFERRED)])
    again = apply_outcomes(record, [ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=True)])
    assert again.finding("N1").disposition is Disposition.FIXED


def test_filable_findings_are_the_ones_left_open_and_not_yet_filed(tmp_path):
    write_review(tmp_path / "review.md", _answered(), fixture_meta("fix_annotated"))
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["N1", "N2"]
    review.finding_issue.clear_filed(tmp_path, {"N1"}, "ENG-9")
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["N2"]
    assert read_review(tmp_path / "review.md").record.finding("N1").filed == "ENG-9"


def test_the_issue_body_names_why_each_is_open(tmp_path):
    from types import SimpleNamespace
    write_review(tmp_path / "review.md", _answered(), fixture_meta("fix_annotated"))
    job = SimpleNamespace(pr_number="", host="", repo="acme/widget", pr=SimpleNamespace(head="feat/retry"))
    body = review.finding_issue.build_body(review.finding_issue.filable(tmp_path), job)
    assert "needs a product call" in body and "not reached by the fix pass" in body
    assert "`src/util.py:1`" in body


def test_a_skipped_finding_stays_filable_across_passes(tmp_path):
    """D11: skipped is sticky, so a later pass that rebuilds nothing still files it."""
    record = apply_outcomes(
        ingest_review("## Nit\n- [ ] **[N1]** `a` — x\n"),
        [ItemOutcome(id="N1", outcome=FixOutcome.NEEDS_HUMAN, reason="needs a product call")],
    )
    later = apply_outcomes(record, [])
    write_review(tmp_path / "review.md", later, fixture_meta("fix_annotated"))
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["N1"]
    assert later.finding("N1").disposition is Disposition.SKIPPED


def test_a_deferral_not_repeated_is_no_longer_filable(tmp_path):
    record = apply_outcomes(
        ingest_review("## Nit\n- [ ] **[N1]** `a` — x\n"),
        [ItemOutcome(id="N1", outcome=FixOutcome.DEFERRED)],
    )
    later = apply_outcomes(record, [])
    write_review(tmp_path / "review.md", later, fixture_meta("fix_annotated"))
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == []
    assert later.finding("N1").disposition is Disposition.OPEN


def test_an_outcome_lands_on_the_visible_finding_not_a_falsified_twin():
    record = ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", body="gone", disposition=Disposition.FALSIFIED),
        RecordFinding(id="M1", severity="M", body="live", disposition=Disposition.OPEN, checkbox=True),
    ))
    out = apply_outcomes(
        record, [ItemOutcome(id="M1", outcome=FixOutcome.FIXED, verified=True)],
    )
    assert [f.disposition for f in out.findings] == [Disposition.FALSIFIED, Disposition.FIXED]
    assert out.finding("M1").body == "live"
    rendered = render_review(out, fixture_meta("fix_annotated"))
    assert "gone" not in rendered
    assert "- [x] **[M1]**" in rendered


def test_a_fixed_finding_stays_fixed_after_an_unchanged_rereview(tmp_path):
    answered = apply_outcomes(
        ingest_review("## Should fix\n- [ ] **[S1]** `a.py:1` — x\n"),
        [ItemOutcome(id="S1", outcome=FixOutcome.FIXED, verified=True)],
    )
    prior = StoredReview(answered, fixture_meta("fix_annotated"))
    (tmp_path / "rev").mkdir()
    job = ReviewJob(
        repo="acme/widget", pr_number="",
        pr=PRMetadata(
            title="t", body="", head="feat/x", base="main",
            head_sha="abc", additions=0, deletions=0, changed_files=0, files=[],
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "rev" / "review.md"),
        session_log=str(tmp_path / "rev" / "session.jsonl"),
        prior=prior,
        preflight=PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            prior_head_sha=prior.meta.head_sha,
            delta_attribution=DeltaAttribution.ATTRIBUTED,
        ),
    )
    write_unchanged_review(job)
    stored = read_review(Path(job.review_file))
    assert stored.record.finding("S1").disposition is Disposition.FIXED
    assert stored.record.finding("S1").view().checked


def test_with_finding_raises_for_a_falsified_or_unknown_id():
    record = ReviewRecord(findings=(
        RecordFinding(id="N2", severity="N", body="gone", disposition=Disposition.FALSIFIED),
        RecordFinding(id="S1", severity="S", body="live", checkbox=True),
    ))
    with pytest.raises(KeyError, match="N2"):
        record.with_finding("N2", filed="ENG-1")
    with pytest.raises(KeyError, match="Z9"):
        record.with_finding("Z9", filed="ENG-1")
    assert record.with_finding("S1", filed="ENG-1").finding("S1").filed == "ENG-1"
