"""Agent artifacts: the examples a prompt shows are documents the reader accepts,
every field is explained, and each malformed shape is a named rejection."""

import collections
import dataclasses
import json
import sys
from pathlib import Path
from typing import get_type_hints

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Mode
from core.strict import SchemaRejected, example_json, load_strict
from review.artifacts import (
    EXAMPLE_LEAVES_EMPTY, FIELD_NOTES, AgentFinding, DisproveArtifact, GroupArtifact,
    HolisticArtifact, ReviewArtifact, ScoutArtifact, SeverityHint, _nested_dataclass,
    as_group_artifact, declinable_sids, output_format,
)
from review.budget import DERIVED_AREA_RESERVE_BYTES
from review.ingest import IngestErrorKind
from review.record import Disposition, LedgerLine, RecordFinding, ReviewRecord
from review.store import StoredReview
from review.types import SEVERITIES, PriorDisposition, ReviewMeta

ROOTS = [GroupArtifact, ReviewArtifact, ScoutArtifact, HolisticArtifact, DisproveArtifact]
FINDING = {"id": "M1", "path": "a.py", "line": 3, "body": "x", "evidence": "y = 1"}
NONE: frozenset[str] = frozenset()


def _shown_document(text: str) -> dict:
    """The JSON example an output format shows."""
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def _reachable(kind: type) -> set[type]:
    out = {kind}
    hints = get_type_hints(kind)
    for f in dataclasses.fields(kind):
        nested = _nested_dataclass(hints[f.name])
        if nested is not None:
            out |= _reachable(nested)
    return out


def _shown(instance, seen: dict) -> None:
    for f in dataclasses.fields(instance):
        value = getattr(instance, f.name)
        if f.default is dataclasses.MISSING or value != f.default:
            seen[type(instance)].add(f.name)
        nested = [item for item in (value if isinstance(value, tuple) else (value,))
                  if dataclasses.is_dataclass(item)]
        for item in nested:
            _shown(item, seen)


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("kind", ROOTS)
def test_every_example_is_a_document_the_reader_accepts(kind, mode):
    example = kind.example(mode)
    loaded = load_strict(kind, json.loads(example_json(example)))
    assert loaded == example
    assert loaded.problems() == ()


@pytest.mark.parametrize("kind", ROOTS)
def test_the_re_review_example_shows_every_field(kind):
    """`example()` is the re-review form; D20's first-round form hides the carry fields."""
    seen: dict = collections.defaultdict(set)
    _shown(kind.example(Mode.PR), seen)
    for cls in _reachable(kind):
        wanted = {f.name for f in dataclasses.fields(cls)} - EXAMPLE_LEAVES_EMPTY.get(cls, frozenset())
        assert seen[cls] >= wanted, (cls.__name__, sorted(wanted - seen[cls]))


def test_field_notes_name_exactly_the_fields():
    for cls in {c for root in ROOTS for c in _reachable(root)}:
        assert set(FIELD_NOTES[cls]) == {f.name for f in dataclasses.fields(cls)}, cls.__name__


def test_the_output_format_shows_the_example_and_explains_every_field():
    text = output_format(GroupArtifact, Mode.PR, rereview=True)
    assert example_json(GroupArtifact.example(Mode.PR)) in text
    assert "`findings[].evidence`" in text
    assert "`findings[].sid`" in text
    assert "`prior_findings[].disposition`" in text
    assert '"Fixed", "Still open", or "Declined"' in text
    assert 'Write "" or [] where a field\'s note says so' in text


@pytest.mark.parametrize("kind", [GroupArtifact, ReviewArtifact])
def test_a_first_round_format_leaves_out_the_carry_fields(kind):
    """D20: with no prior section, nothing on the page spells a carry."""
    text = output_format(kind, Mode.PR, rereview=False)
    doc = _shown_document(text)
    assert "prior_findings" not in doc
    assert doc["findings"] and all("sid" not in f and "declined" not in f for f in doc["findings"])
    for name in ("`findings[].sid`", "`findings[].declined`", "`prior_findings`"):
        assert name not in text
    assert "`findings[].evidence`" in text
    assert load_strict(kind, doc).problems() == ()


def test_the_id_note_names_every_severity():
    note = FIELD_NOTES[AgentFinding]["id"]
    assert all(f"{s.key} = {s.section}" in note for s in SEVERITIES)


def test_severity_hints_are_severity_labels():
    assert {h.value for h in SeverityHint} <= {s.label for s in SEVERITIES}


def _group(**finding) -> dict:
    return {"findings": [{**FINDING, **finding}]}


@pytest.mark.parametrize("doc, kind, at", [
    (_group(id="X1"), IngestErrorKind.UNKNOWN_SEVERITY, "$.findings[0].id"),
    (_group(id="M0"), IngestErrorKind.UNKNOWN_SEVERITY, "$.findings[0].id"),
    (_group(id="M1\n"), IngestErrorKind.UNKNOWN_SEVERITY, "$.findings[0].id"),
    (_group(body="  "), IngestErrorKind.EMPTY_FIELD, "$.findings[0].body"),
    (_group(path="a.py:3"), IngestErrorKind.LINE_IN_PATH, "$.findings[0].path"),
    (_group(path=""), IngestErrorKind.BAD_LOCATION, "$.findings[0].path"),
    (_group(path=None), IngestErrorKind.BAD_LOCATION, "$.findings[0].line"),
    (_group(line=0), IngestErrorKind.BAD_LOCATION, "$.findings[0].line"),
    (_group(end_line=2), IngestErrorKind.BAD_LOCATION, "$.findings[0].end_line"),
    ({"findings": [FINDING, FINDING]}, IngestErrorKind.DUPLICATE_ID, "$.findings"),
    ({"triage": [{"path": " ", "note": "x"}]}, IngestErrorKind.EMPTY_FIELD, "$.triage[0].path"),
    ({"prior_findings": [{"id": "Q1", "disposition": "Fixed"}]},
     IngestErrorKind.UNKNOWN_SEVERITY, "$.prior_findings[0].id"),
    ({"prior_findings": [{"id": "M1\n", "disposition": "Fixed"}]},
     IngestErrorKind.UNKNOWN_SEVERITY, "$.prior_findings[0].id"),
    (_group(declined="documented"), IngestErrorKind.BAD_CARRY, "$.findings[0].declined"),
])
def test_each_group_rejection_names_its_kind_and_place(doc, kind, at):
    problems = load_strict(GroupArtifact, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [(kind, at)]


FOCUS_OK = {"title": "Retry", "rationale": "why", "paths": ["a.py"]}


@pytest.mark.parametrize("kind, doc, at", [
    (ReviewArtifact, {"summary": " "}, "$.summary"),
    (HolisticArtifact, {"assessment": "", "focus": [FOCUS_OK]}, "$.assessment"),
    (ScoutArtifact, {"leads": [{"path": "a.py", "concern": ""}], "focus": [FOCUS_OK]},
     "$.leads[0].concern"),
    (ScoutArtifact, {"no_scrutiny": [{"path": "a.py", "reason": ""}], "focus": [FOCUS_OK]},
     "$.no_scrutiny[0].reason"),
    (DisproveArtifact, {"verdicts": [{"id": "M1", "outcome": "survives", "reason": ""}]},
     "$.verdicts[0].reason"),
])
def test_an_empty_required_text_is_rejected(kind, doc, at):
    problems = load_strict(kind, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [(IngestErrorKind.EMPTY_FIELD, at)]


@pytest.mark.parametrize("kind, doc", [
    (ScoutArtifact, {}),
    (HolisticArtifact, {"assessment": "ok"}),
])
def test_a_scan_without_focus_is_rejected(kind, doc):
    problems = load_strict(kind, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [(IngestErrorKind.FOCUS_COUNT, "$.focus")]


@pytest.mark.parametrize("kind, extra", [
    (ScoutArtifact, {}),
    (HolisticArtifact, {"assessment": "ok"}),
])
def test_a_scan_with_four_areas_is_rejected(kind, extra):
    problems = load_strict(kind, {**extra, "focus": [FOCUS_OK] * 4}).problems()
    assert [(p.kind, p.at) for p in problems] == [(IngestErrorKind.FOCUS_COUNT, "$.focus")]


def test_a_focus_path_with_a_line_is_rejected():
    doc = {"focus": [{"title": "t", "paths": ["a.py:3"]}]}
    problems = load_strict(ScoutArtifact, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [
        (IngestErrorKind.LINE_IN_PATH, "$.focus[0].paths[0]")]


def test_a_derived_area_over_the_reserve_is_rejected():
    doc = {"focus": [{"title": "t", "rationale": "x" * (DERIVED_AREA_RESERVE_BYTES + 1)}]}
    problems = load_strict(ScoutArtifact, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [
        (IngestErrorKind.FOCUS_SIZE, "$.focus[0]")]


def test_a_trailing_newline_on_a_disprove_id_is_rejected():
    doc = {"verdicts": [{"id": "M1\n", "outcome": "survives", "reason": "r"}]}
    problems = load_strict(DisproveArtifact, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [
        (IngestErrorKind.UNKNOWN_SEVERITY, "$.verdicts[0].id")]


def test_disprove_challenges_only_must_and_should_fix():
    doc = {"verdicts": [{"id": "N1", "outcome": "falsified", "reason": "r"},
                        {"id": "M1", "outcome": "survives", "reason": "r"},
                        {"id": "M1", "outcome": "survives", "reason": "r"}]}
    kinds = [(p.kind, p.at) for p in load_strict(DisproveArtifact, doc).problems()]
    assert kinds == [(IngestErrorKind.UNKNOWN_SEVERITY, "$.verdicts[0].id"),
                     (IngestErrorKind.DUPLICATE_ID, "$.verdicts")]


def test_a_ledger_disposition_outside_the_enum_is_rejected():
    """X5: an unreadable verdict is a schema violation the agent repairs."""
    with pytest.raises(SchemaRejected) as exc:
        load_strict(GroupArtifact, {"prior_findings": [{"id": "M1", "disposition": "Partially fixed"}]})
    (v,) = exc.value.violations
    assert v.at == "$.prior_findings[0].disposition" and '"Fixed"' in v.detail


@pytest.mark.parametrize("harness_field", ["disposition", "stable_id", "checkbox", "skipped"])
def test_a_harness_owned_field_is_not_writable(harness_field):
    with pytest.raises(SchemaRejected) as exc:
        load_strict(GroupArtifact, _group(**{harness_field: "x"}))
    assert exc.value.violations[0].at == f"$.findings[0].{harness_field}"


def test_a_finding_converts_with_its_severity_and_stable_id():
    f = AgentFinding(id="S2", path="ai/lib/x.py", line=12,
                     body="Missing check on the db.Query error\nmore", evidence="x()\n")
    r = f.to_record(checkbox=True, declinable=NONE)
    assert (r.severity, r.path, r.line, r.checkbox, r.disposition) == (
        "S", "ai/lib/x.py", 12, True, Disposition.OPEN)
    assert (r.stable_id, r.evidence) == ("99e705a8", "x()")


def test_a_declined_carry_of_a_prior_finding_converts_to_a_declined_finding():
    f = AgentFinding(id="S1", path="a.py", body="b", sid="3f9a2c1d", declined=" documented ")
    r = f.to_record(checkbox=False, declinable=frozenset({"3f9a2c1d"}))
    assert (r.disposition, r.reason, r.sid) == (Disposition.DECLINED, "documented", "3f9a2c1d")


def test_a_decline_naming_no_prior_finding_stays_open(capsys):
    """D19: an agent cannot decline a finding of its own on its say-so."""
    f = AgentFinding(id="S1", path="a.py", body="b", sid="3f9a2c1d", declined="documented")
    r = f.to_record(checkbox=False, declinable=frozenset({"00000000"}))
    assert (r.disposition, r.reason) == (Disposition.OPEN, "")
    assert "declined, but 3f9a2c1d names no prior finding — kept open" in capsys.readouterr().err


def test_a_sidless_decline_stays_open_even_when_the_empty_sid_is_declinable(capsys):
    f = AgentFinding(id="S1", path="a.py", body="b", declined="documented")
    r = f.to_record(checkbox=False, declinable=frozenset({""}))
    assert (r.disposition, r.reason) == (Disposition.OPEN, "")
    assert "declined without a sid — kept open" in capsys.readouterr().err


def test_the_declinable_sids_are_every_prior_finding_s_identities():
    prior = StoredReview(ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", stable_id="aaaa1111"),
        RecordFinding(id="S1", severity="S", stable_id="bbbb2222", sid="cccc3333"),
    )), ReviewMeta())
    assert declinable_sids(prior) == {"aaaa1111", "bbbb2222", "cccc3333"}
    assert declinable_sids(None) == frozenset()


def test_a_pathless_finding_converts_with_an_empty_path():
    assert AgentFinding(id="N1", body="b").to_record(checkbox=False, declinable=NONE).path == ""


def test_a_review_artifact_keeps_summary_ledger_and_verdict():
    ledger = (LedgerLine("M2", PriorDisposition.FIXED, "a.py"),)
    artifact = ReviewArtifact(summary=" s ", prior_findings=ledger, verdict="Approve")
    record = artifact.to_record(checkbox=False, declinable=NONE)
    assert (record.summary, record.ledger, record.verdict, record.triage) == ("s", ledger, "Approve", None)


def test_the_merged_record_is_shown_to_synthesis_as_a_group_artifact():
    """D9: every finding synthesis sees carries a sid, new ones their own stable ID."""
    record = GroupArtifact.example(Mode.PR).to_record(declinable=frozenset({"3f9a2c1d"}))
    shown = as_group_artifact(record)
    assert [f.id for f in shown.findings] == ["M1", "S1", "N1"]
    assert shown.findings[0].sid == record.findings[0].stable_id
    assert shown.findings[1].sid == "3f9a2c1d" and shown.findings[1].declined
    assert all(f.sid for f in shown.findings)
    assert shown.prior_findings == record.ledger
    assert load_strict(GroupArtifact, json.loads(example_json(shown))).problems() == ()


@pytest.mark.parametrize("doc, kind, at", [
    ({"summary": "s", "focus": [{"id": "F1", "coverage": ""}]},
     IngestErrorKind.EMPTY_FIELD, "$.focus[0].coverage"),
    ({"summary": "s", "focus": [{"id": "X1", "coverage": "c"}]},
     IngestErrorKind.UNKNOWN_FOCUS, "$.focus[0].id"),
    ({"summary": "s", "focus": [{"id": "", "coverage": "c"}]},
     IngestErrorKind.EMPTY_FIELD, "$.focus[0].title"),
    ({"summary": "s", "focus": [{"id": "F1", "coverage": "a"}, {"id": "F1", "coverage": "b"}]},
     IngestErrorKind.DUPLICATE_ID, "$.focus"),
    ({"summary": "s", "focus": [{"id": "", "title": "t", "coverage": "c"}] * 4},
     IngestErrorKind.FOCUS_COUNT, "$.focus"),
])
def test_each_focus_report_rejection_names_its_kind_and_place(doc, kind, at):
    problems = load_strict(ReviewArtifact, doc).problems()
    assert [(p.kind, p.at) for p in problems] == [(kind, at)]


def test_a_format_that_does_not_ask_for_areas_hides_the_derive_fields():
    text = output_format(ReviewArtifact, Mode.PR, rereview=False)
    doc = _shown_document(text)
    assert doc["focus"]
    assert all(set(e) == {"id", "coverage"} and e["id"] for e in doc["focus"])
    assert "`focus[].title`" not in text


def test_a_format_that_asks_for_areas_shows_one():
    text = output_format(ReviewArtifact, Mode.PR, rereview=False, derive_focus=True)
    doc = _shown_document(text)
    blank = [e for e in doc["focus"] if e["id"] == ""]
    assert blank and "title" in blank[0]


def test_a_format_with_no_areas_shows_no_covering_entry():
    text = output_format(
        ReviewArtifact, Mode.PR, rereview=False, derive_focus=True, has_areas=False,
    )
    doc = _shown_document(text)
    assert all(not e["id"] for e in doc["focus"])
