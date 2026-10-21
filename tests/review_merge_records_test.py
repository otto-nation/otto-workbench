"""Group reviews merged as records: shifted, deduplicated, renumbered."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.artifacts import GroupArtifact
from review.ingest import load_artifact
from review.merge import merge_records, renumber_record
from review.record import Disposition, LedgerLine, RecordFinding, ReviewRecord
from review.render import render_body
from review.types import PriorDisposition

from review_record_support import FIXTURES, canonical, finding, fixture_text, record


def _groups():
    return [
        load_artifact(str(FIXTURES / "groups" / f"group-{i}.json"), GroupArtifact)
        .to_record(declinable=frozenset())
        for i in (1, 2)
    ]


def test_merging_the_fixture_groups_matches_todays_merge():
    rendered = render_body(merge_records(_groups()), sids=True, evidence=True)
    assert canonical(rendered) == canonical(fixture_text("merged", "expected.md"))


def test_an_empty_severity_section_renders_no_placeholder():
    """X13: a group with no Must-fix finding cannot leave a placeholder above another group's M1.

    GroupArtifact has no field for prose under a severity heading, so the empty
    group is just a record with no must-fix finding.
    """
    empty = record(finding("S1", "should only"))
    has_m = record(finding("M1", "a real must-fix"))
    rendered = render_body(merge_records([empty, has_m]))
    must = rendered.split("## Must fix", 1)[1].split("## ", 1)[0]
    lines = [line for line in must.splitlines() if line.strip()]
    assert lines and all(line.startswith("- ") for line in lines)
    assert "(none)" not in rendered
    assert "**[M1]**" in must


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
    rec = record(
        finding("S1", "x"),
        ledger=(LedgerLine("S3", PriorDisposition.FIXED, "a.py"),),
    )
    merged = merge_records([rec])
    assert [(e.id, e.disposition, e.path) for e in merged.ledger] == [
        ("S3", PriorDisposition.FIXED, "a.py"),
    ]


def test_dedup_reads_the_fields_not_a_rendered_line():
    a = RecordFinding(id="S1", severity="S", path="a.py", line=3, body="Same words")
    merged = merge_records([ReviewRecord(findings=(a,)), ReviewRecord(findings=(a,))])
    assert [f.id for f in merged.visible] == ["S1"]


# passes-at-base: two PR-wide findings have no location to agree on, so they stay two
def test_pathless_findings_are_never_merged():
    a = RecordFinding(id="N1", severity="N", body="Same words")
    merged = merge_records([ReviewRecord(findings=(a,)), ReviewRecord(findings=(a,))])
    assert [f.id for f in merged.visible] == ["N1", "N2"]


def test_two_groups_dispositioning_one_prior_finding_keep_the_stronger_verdict():
    fixed = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.FIXED, "a.py"),))
    still = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.STILL_OPEN, "a.py", "still there"),))
    other = ReviewRecord(ledger=(LedgerLine("S1", PriorDisposition.FIXED, "b.py"),))
    merged = merge_records([fixed, still, other])
    assert [(e.id, e.disposition) for e in merged.ledger] == [
        ("M1", PriorDisposition.STILL_OPEN), ("S1", PriorDisposition.FIXED),
    ]


def test_the_stronger_verdict_wins_whichever_path_each_group_copied():
    """A prior review numbers each finding once, so its ID alone names it.

    Keyed on (ID, path), a group that copied no path and one that copied it
    would leave two lines for one finding, and reconciliation would settle it
    on whichever came first.
    """
    bare = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.FIXED),))
    pathed = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.DECLINED, "a.py"),))
    merged = merge_records([bare, pathed])
    assert [(e.id, e.disposition) for e in merged.ledger] == [("M1", PriorDisposition.DECLINED)]
