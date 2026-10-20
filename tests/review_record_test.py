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
from pr.domains import ReviewStatus
from review.document import ReviewHeader
from review.paths import FILENAME_PRIOR_RECORD, FILENAME_RECORD
from review.store import read_prior
from review.record import (
    SCHEMA_VERSION, AgentFailure, Disposition, ProseSection, RecordFinding,
    ReviewRecord, SeveritySection, TriageEntry, defuse_first_line, load_record,
    save_record, unquote_evidence,
)
from review.types import ReviewMeta, ReviewType, review_meta_from_dict
from review_record_support import copy_tree


def _record() -> ReviewRecord:
    return ReviewRecord(
        summary="Sound.",
        findings=(
            RecordFinding(id="S1", severity="S", path="a.py", line=3,
                          body="Missing guard.", evidence="x",
                          stable_id="abcd1234", checkbox=True),
            RecordFinding(id="N1", severity="N", body="nit", disposition=Disposition.DECLINED,
                          reason="by design"),
            RecordFinding(id="N2", severity="N", body="gone", disposition=Disposition.FALSIFIED),
        ),
        triage=(TriageEntry(path="a.py", note="reviewed"),),
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

    def test_it_is_stamped_with_schema_version_2(self, tmp_path):
        save_record(tmp_path, _record())
        payload = json.loads((tmp_path / FILENAME_RECORD).read_text())
        assert payload["schema_version"] == SCHEMA_VERSION == 2
        assert payload["findings"][1]["disposition"] == "declined"

    def test_an_absent_record_is_none_not_empty(self, tmp_path):
        assert load_record(tmp_path) is None

    def test_a_record_from_another_schema_version_is_stale(self, tmp_path):
        (tmp_path / FILENAME_RECORD).write_text(json.dumps({"schema_version": 3}))
        assert load_record(tmp_path) is None

    def test_a_json_list_is_stale_without_a_schema_warning(self, tmp_path):
        (tmp_path / FILENAME_RECORD).write_text("[]")
        warned = []
        with patch.object(core.log, "warn", side_effect=warned.append):
            assert load_record(tmp_path) is None
        assert any("could not be read" in msg for msg in warned)
        assert not any("schema" in msg for msg in warned)

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


class TestCopyTree:
    def test_copy_tree_restores_py_names(self, tmp_path):
        dest = copy_tree(tmp_path / "tree")
        assert (dest / "src" / "cli.py").is_file()
        assert not list(dest.rglob("*.txt"))


class TestMetaHeaderFields:
    def test_status_and_date_round_trip_through_meta_json(self):
        meta = review_meta_from_dict({"date": "2026-10-15", "status": "partial"})
        assert meta.date == "2026-10-15" and meta.status is ReviewStatus.PARTIAL

    def test_an_unknown_status_costs_only_itself(self):
        meta = review_meta_from_dict({"status": "exploded", "repo": "a/b"})
        assert meta.status is None and meta.repo == "a/b"

    def test_the_header_states_what_meta_records(self):
        meta = ReviewMeta(date="2026-10-19", status=ReviewStatus.COMPLETED,
                          review_type=ReviewType.INCREMENTAL, prior_sha="d54f",
                          prior_date="2026-10-17", skipped_groups=0, total_groups=6,
                          delta_files=("a", "b", "c"))
        rendered = ReviewHeader.from_meta(meta).render()
        assert "<!-- date: 2026-10-19 -->" in rendered
        assert "<!-- prior_date: 2026-10-17 -->" in rendered
        assert "<!-- skipped_groups: 0/6 -->" in rendered
        assert "<!-- status: completed -->" in rendered

    def test_a_full_review_states_no_ratio(self):
        meta = ReviewMeta(review_type=ReviewType.FULL, skipped_groups=1, total_groups=4)
        assert "skipped_groups" not in ReviewHeader.from_meta(meta).render()


# A record as `save_record` wrote it at schema 1: a finding's location as the
# agent typed it (`lead`), evidence still in its blockquote and fence, triage as
# whole list items, and the non-empty ledger `_write_clean_review` stored, the
# one writer that never cleared it (X11). The stable ID
# is the one `of` gave a `--`-separated line, which `of_fields` does not
# reproduce (D12), so only keeping the stored value keeps it.
V1 = {
    "schema_version": 1, "summary": "s",
    "findings": [{
        "id": "M1", "severity": "M", "path": "a.py", "line": 3, "lead": "`a.py:3` -- ",
        "body": "body here", "stable_id": "5e406135",
        "evidence": "  > ```python\n  > y = compute(x)\n  >\n  >     return y\n  > ```",
    }],
    "triage": [
        {"path": "a.go", "text": "- `a.go` — **Tier 2** (logic)"},
        {"path": "", "text": "- `a.py`, `b.py` — **Tier 1**"},
    ],
    "ledger": "- **[M1]** `a.py` — Fixed",
}


def test_a_schema_1_record_is_upgraded(tmp_path):
    """X7: a record written before agents wrote JSON loads, in today's shapes."""
    (tmp_path / FILENAME_RECORD).write_text(json.dumps(V1))
    record = load_record(tmp_path)
    (f,) = record.findings
    assert record.schema_version == SCHEMA_VERSION
    assert (f.path, f.line, f.body) == ("a.py", 3, "body here")
    assert f.evidence == "y = compute(x)\n    return y"
    assert [(e.path, e.note) for e in record.triage] == [
        ("a.go", "**Tier 2** (logic)"), ("", "`a.py`, `b.py` — **Tier 1**"),
    ]
    # The ledger is still a string field until Task 3 types it; Task 3 tightens
    # this to `record.ledger == ()`.
    assert not record.ledger


def test_a_schema_1_approach_is_upgraded_like_a_finding(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text(json.dumps({**V1, "approach": V1["findings"][0]}))
    record = load_record(tmp_path)
    assert record is not None
    assert record.approach.evidence == "y = compute(x)\n    return y"
    assert record.approach.stable_id == "5e406135"


def test_a_boolean_schema_version_is_not_schema_1(tmp_path):
    """`True == 1` in Python; the upgrade must not read a boolean as a version."""
    (tmp_path / FILENAME_RECORD).write_text(json.dumps({**V1, "schema_version": True}))
    assert load_record(tmp_path) is None


# passes-at-base: base reads schema 1 natively and keeps the stored ID; this pins that the upgrade does too
def test_a_stored_stable_id_survives_the_upgrade(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text(json.dumps(V1))
    assert load_record(tmp_path).findings[0].stable_id == "5e406135"


def test_a_schema_1_prior_is_upgraded_too(tmp_path):
    (tmp_path / FILENAME_PRIOR_RECORD).write_text(json.dumps(V1))
    prior = read_prior(tmp_path)
    assert prior is not None
    assert prior.record.findings[0].evidence == "y = compute(x)\n    return y"


# passes-at-base: none of these versions was ever readable; the upgrade must not widen that
@pytest.mark.parametrize("version", [None, 0, 3, "1"])
def test_an_unknown_schema_is_stale(tmp_path, version):
    (tmp_path / FILENAME_RECORD).write_text(json.dumps({"schema_version": version, "summary": "s"}))
    assert load_record(tmp_path) is None


INLINE_EVIDENCE = "  > `raise DiagramError(...) from None`"
TWO_FENCE_EVIDENCE = "  > ```\n  > first\n  > ```\n  > ```\n  > second\n  > ```"


@pytest.mark.parametrize("quoted, raw", [
    (INLINE_EVIDENCE, ""),
    (TWO_FENCE_EVIDENCE, "first"),
])
def test_unquote_evidence_reads_the_first_fenced_block(quoted, raw):
    assert unquote_evidence(quoted) == raw


@pytest.mark.parametrize("quoted, raw", [
    (INLINE_EVIDENCE, ""),
    (TWO_FENCE_EVIDENCE, "first"),
])
def test_upgrade_v1_unwraps_each_evidence_shape(tmp_path, quoted, raw):
    payload = {**V1, "findings": [{**V1["findings"][0], "evidence": quoted}]}
    (tmp_path / FILENAME_RECORD).write_text(json.dumps(payload))
    assert load_record(tmp_path).findings[0].evidence == raw
