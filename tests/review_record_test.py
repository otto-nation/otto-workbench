"""The review record: its shape on disk, and the view the read API hands out."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import core.log
from review.paths import FILENAME_RECORD
from review.record import (
    SCHEMA_VERSION, AgentFailure, Disposition, ProseSection, RecordFinding,
    ReviewRecord, SeveritySection, TriageEntry, defuse_first_line, load_record,
    save_record,
)


def _record() -> ReviewRecord:
    return ReviewRecord(
        summary="Sound.",
        findings=(
            RecordFinding(id="S1", severity="S", path="a.py", line=3, lead="**`a.py:3`** — ",
                          body="Missing guard.", evidence="  > ```\n  > x\n  > ```",
                          stable_id="abcd1234", checkbox=True),
            RecordFinding(id="N1", severity="N", body="nit", disposition=Disposition.DECLINED,
                          reason="by design"),
            RecordFinding(id="N2", severity="N", body="gone", disposition=Disposition.FALSIFIED),
        ),
        triage=(TriageEntry(path="a.py", text="- `a.py` — reviewed"),),
        failures=(AgentFailure("disprove", "no output", "failed", recoverable=True),),
        verdict="**Approve.**",
        severity_sections=(SeveritySection("M", "(none)"),),
        extra=(ProseSection("Review gaps", "- group 2"),),
    )


class TestOnDisk:
    def test_a_record_round_trips_through_review_json(self, tmp_path):
        save_record(tmp_path, _record())
        loaded = load_record(tmp_path)
        assert loaded == _record()
        assert [f.id for f in loaded.visible] == ["S1", "N1"]
        assert loaded.findings[1].disposition is Disposition.DECLINED

    def test_it_is_stamped_with_schema_version_1(self, tmp_path):
        save_record(tmp_path, _record())
        payload = json.loads((tmp_path / FILENAME_RECORD).read_text())
        assert payload["schema_version"] == SCHEMA_VERSION == 1
        assert payload["findings"][1]["disposition"] == "declined"

    def test_an_absent_record_is_none_not_empty(self, tmp_path):
        assert load_record(tmp_path) is None

    def test_a_record_from_another_schema_version_is_stale(self, tmp_path):
        (tmp_path / FILENAME_RECORD).write_text(json.dumps({"schema_version": 2}))
        assert load_record(tmp_path) is None

    def test_an_absent_triage_is_not_an_empty_one(self, tmp_path):
        save_record(tmp_path, ReviewRecord(triage=()))
        assert load_record(tmp_path).triage == ()
        save_record(tmp_path, ReviewRecord())
        assert load_record(tmp_path).triage is None

    def test_a_finding_list_that_cannot_coerce_is_stale(self, tmp_path):
        (tmp_path / FILENAME_RECORD).write_text(
            json.dumps({"schema_version": 1, "findings": [42]}),
        )
        assert load_record(tmp_path) is None

    @pytest.mark.parametrize("findings", [42, None, {}])
    def test_a_non_list_findings_field_is_stale(self, tmp_path, findings):
        (tmp_path / FILENAME_RECORD).write_text(
            json.dumps({"schema_version": 1, "findings": findings}),
        )
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            assert load_record(tmp_path) is None
        assert any("dropped findings" in msg for msg in warned)

    def test_a_null_triage_round_trips(self, tmp_path):
        save_record(tmp_path, ReviewRecord(triage=None))
        loaded = load_record(tmp_path)
        assert loaded is not None
        assert loaded.triage is None

    def test_a_non_object_approach_is_stale(self, tmp_path):
        (tmp_path / FILENAME_RECORD).write_text(
            json.dumps({"schema_version": 1, "approach": "nope"}),
        )
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            assert load_record(tmp_path) is None
        assert any("dropped approach" in msg for msg in warned)


class TestWhatTheReadApiSees:
    def test_a_string_disposition_is_the_enum(self):
        f = RecordFinding(id="N2", severity="N", body="gone", disposition="falsified")
        assert f.disposition is Disposition.FALSIFIED
        assert not f.visible

    def test_a_falsified_finding_is_not_visible(self):
        assert [f.id for f in _record().visible] == ["S1", "N1"]

    def test_open_counts_tally_visible_unfixed_findings(self):
        record = _record().with_finding("S1", disposition=Disposition.FIXED)
        assert record.open_counts == {"M": 0, "S": 0, "N": 1, "I": 0}

    def test_the_view_carries_the_annotation_in_its_body_as_the_parser_did(self):
        view = _record().finding("N1").view()
        assert view.body == "nit *(declined — by design)*"
        assert view.declined and view.decline_reason == "by design"
        assert not view.checked

    def test_the_view_of_a_fixed_finding_is_checked(self):
        view = _record().with_finding("S1", disposition=Disposition.FIXED).finding("S1").view()
        assert view.checked and view.seq == 1 and view.path == "a.py"

    def test_an_unverified_fix_says_so(self):
        f = RecordFinding(id="S1", severity="S", body="x", disposition=Disposition.FIXED,
                          unverified=True, reason="suite red", checkbox=True)
        assert f.annotation == "*(unverified — suite red)*"

    def test_an_unreached_finding_carries_no_annotation(self):
        f = RecordFinding(id="N2", severity="N", body="x", disposition=Disposition.UNREACHED,
                          reason="not reached")
        assert f.annotation == ""

    def test_defusing_touches_only_the_first_line(self):
        assert defuse_first_line("a *(b)*\nc *(d)*") == "a * (b)*\nc *(d)*"
