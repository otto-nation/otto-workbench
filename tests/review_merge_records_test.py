"""Group reviews merged as records: shifted, deduplicated, renumbered."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import ingest_review
from review.merge import merge_records, renumber_record
from review.record import Disposition, RecordFinding, ReviewRecord
from review.render import render_body

from review_record_support import canonical, fixture_text


def _groups():
    return [ingest_review(fixture_text("groups", f"group-{i}.md"), strict=False) for i in (1, 2)]


def test_merging_the_fixture_groups_matches_todays_merge():
    rendered = render_body(merge_records(_groups()), sids=True, evidence=True)
    assert canonical(rendered) == canonical(fixture_text("merged", "input.md"))


def test_a_repeat_across_groups_is_kept_once():
    merged = merge_records(_groups())
    assert [f.id for f in merged.findings if f.severity == "S"] == ["S1"]


def test_a_reference_follows_its_finding():
    merged = merge_records(_groups())
    n1 = next(f for f in merged.findings if f.path == "src/util.py")
    assert "[S1]" in n1.body


def test_renumbering_closes_gaps_and_marks_removed_references():
    record = ReviewRecord(summary="See [S3] and [S2].", findings=(
        RecordFinding(id="S1", severity="S", body="a"),
        RecordFinding(id="S3", severity="S", body="b"),
    ))
    out = renumber_record(record)
    assert [f.id for f in out.findings] == ["S1", "S2"]
    assert out.summary == "See [S2] and [removed]."


def test_a_falsified_finding_neither_takes_a_number_nor_moves():
    record = ReviewRecord(findings=(
        RecordFinding(id="S1", severity="S", body="a", disposition=Disposition.FALSIFIED),
        RecordFinding(id="S2", severity="S", body="b"),
    ))
    out = renumber_record(record)
    assert [(f.id, f.disposition) for f in out.findings] == [("S1", Disposition.FALSIFIED), ("S1", Disposition.OPEN)]


def test_a_group_ledger_keeps_the_prior_reviews_ids():
    record = ingest_review(
        "## Should fix\n- **[S1]** **`a.py:1`** — x\n"
        "## Prior findings\n- **[S3]** `a.py` — Fixed\n",
        strict=False,
    )
    merged = merge_records([record])
    assert "[S3]" in merged.ledger
