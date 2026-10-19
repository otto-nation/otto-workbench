"""Ingest: the one place an agent's markdown review becomes a ReviewRecord."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import IngestErrorKind, IngestRejected, ingest_review
from review.record import Disposition
from review.render import BANNER

from review_record_support import copy_tree, fixture_text


def _kinds(text: str) -> list[IngestErrorKind]:
    with pytest.raises(IngestRejected) as exc:
        ingest_review(text)
    return [e.kind for e in exc.value.errors]


class TestRejections:
    @pytest.mark.parametrize("text, kind", [
        ("## Should fix\n- **[Q1]** `a.py:1` — what is this for?\n", IngestErrorKind.UNKNOWN_SEVERITY),
        ("## Nit\n- **[N1]** `a.py:1` — one\n- **[N1]** `b.py:2` — two\n", IngestErrorKind.DUPLICATE_ID),
        ("## Must fix\n- **[M1]**\n", IngestErrorKind.MISSING_ANCHOR),
        (f"# Review\n{BANNER}\n\n## Summary\nx\n", IngestErrorKind.RENDERED_INPUT),
    ])
    def test_a_malformed_entry_is_rejected_with_a_named_error(self, text, kind):
        assert kind in _kinds(text)

    def test_every_error_is_reported_not_just_the_first(self):
        text = "## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n- **[X1]** `c.py:3` — z\n"
        assert set(_kinds(text)) >= {IngestErrorKind.DUPLICATE_ID, IngestErrorKind.UNKNOWN_SEVERITY}

    def test_errors_name_the_line(self):
        with pytest.raises(IngestRejected) as exc:
            ingest_review("## Summary\nok\n\n## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n")
        assert exc.value.errors[0].line == 6
        assert "N1" in exc.value.errors[0].render()

    def test_errors_come_back_sorted_by_line(self):
        text = (
            "## Nit\n"
            "- **[X1]** `a.py:1` — z\n"
            "- **[N1]** `b.py:2` — x\n"
            "- **[N1]** `c.py:3` — y\n"
        )
        with pytest.raises(IngestRejected) as exc:
            ingest_review(text)
        assert [e.line for e in exc.value.errors] == [2, 4]

    def test_missing_anchor_quotes_the_unparsed_line(self):
        line = "- **[M1]**: `a.py:1` — body"
        with pytest.raises(IngestRejected) as exc:
            ingest_review(f"## Must fix\n{line}\n")
        err = exc.value.errors[0]
        assert err.kind is IngestErrorKind.MISSING_ANCHOR
        assert line in err.detail
        assert "says nothing" not in err.detail

    def test_a_capital_x_checkbox_stays_prose(self):
        record = ingest_review("## Should fix\n- [X] **[S1]** `a.py:1` — body\n")
        assert record.findings == ()

    def test_a_rendered_file_is_refused_even_when_not_strict(self):
        with pytest.raises(IngestRejected):
            ingest_review(f"{BANNER}\n## Summary\nx\n", strict=False)

    def test_not_strict_keeps_a_duplicate_rather_than_refusing(self):
        record = ingest_review(
            "## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n",
            strict=False,
        )
        assert [f.id for f in record.findings] == ["N1", "N1"]


class TestWhatIsRead:
    def test_a_finding_keeps_its_location_text_and_body_apart(self):
        record = ingest_review("## Should fix\n- **[S1]** **`a.py:3-5`** — Missing guard.\n")
        (f,) = record.findings
        assert (f.path, f.line, f.end_line) == ("a.py", 3, 5)
        assert f.lead == "**`a.py:3-5`** — " and f.body == "Missing guard."

    def test_a_named_location_without_a_dash_still_splits(self):
        (f,) = ingest_review("## Should fix\n- **[S1]** `a.py:1` text without a dash\n").findings
        assert f.lead == "`a.py:1` " and f.body == "text without a dash"
        assert (f.path, f.line) == ("a.py", 1)

    def test_an_unlocated_finding_keeps_its_whole_text_as_body(self):
        (f,) = ingest_review("## Should fix\n- [ ] **[S3]** PR scope — two changes.\n").findings
        assert f.lead == "" and f.body == "PR scope — two changes." and f.checkbox

    def test_evidence_is_split_from_the_body(self):
        text = (
            "## Should fix\n- **[S1]** **`a.py:2`** — Loop.\n"
            "  more prose\n  > ```python\n  > call()\n  > ```\n"
        )
        (f,) = ingest_review(text).findings
        assert f.body == "Loop.\n  more prose"
        assert f.evidence == "  > ```python\n  > call()\n  > ```"

    def test_annotations_become_dispositions(self):
        text = (
            "## Nit\n"
            "- [x] **[N1]** `a.py:1` — done\n"
            "- [ ] **[N2]** `b.py:2` — no. *(declined — by design)*\n"
            "- [ ] **[N3]** `c.py:3` — hm. *(skipped — needs a person)*\n"
            "- [x] **[N4]** `d.py:4` — ok. *(unverified — suite red)*\n"
        )
        f = {x.id: x for x in ingest_review(text).findings}
        assert f["N1"].disposition is Disposition.FIXED
        assert (f["N2"].disposition, f["N2"].reason, f["N2"].body) == (Disposition.DECLINED, "by design", "no.")
        assert (f["N3"].disposition, f["N3"].reason) == (Disposition.SKIPPED, "needs a person")
        assert (f["N4"].unverified, f["N4"].reason) == (True, "suite red")

    def test_a_skip_on_the_declaration_does_not_count_once_a_continuation_follows(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — body *(skipped — r)*\n  more prose\n"
        ).findings
        assert f.disposition is Disposition.OPEN
        assert "*(skipped — r)*" in f.body

    def test_a_skip_at_the_end_of_the_last_continuation_counts(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — body\n  more prose *(skipped — r)*\n"
        ).findings
        assert (f.disposition, f.reason, f.body) == (Disposition.SKIPPED, "r", "body\n  more prose")

    def test_a_skip_head_on_a_continuation_counts_when_the_declaration_body_is_empty(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` —\n  *(skipped — r)* more text\n"
        ).findings
        assert (f.disposition, f.reason) == (Disposition.SKIPPED, "r")
        assert f.body == "—\n  more text"

    def test_a_tail_annotation_with_an_em_dash_is_not_the_body_separator(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` text *(declined — x)*\n"
        ).findings
        assert (f.disposition, f.reason, f.body) == (Disposition.DECLINED, "x", "text")

    def test_a_sid_marker_is_kept_off_the_lead(self):
        (f,) = ingest_review("## Nit\n- **[N1]** <!-- sid:ab12cd34 --> `a.py:1` — x\n").findings
        assert f.sid == "ab12cd34" and f.lead == "`a.py:1` — "

    def test_the_stable_id_is_the_grammar_s(self):
        from review.grammar import FindingIdentity
        line = "- **[N1]** `a.py:1` — something"
        (f,) = ingest_review(f"## Nit\n{line}\n").findings
        assert f.stable_id == FindingIdentity.of(line).stable_id

    def test_sections_land_in_their_fields(self):
        record = ingest_review(fixture_text("synthesis_pr", "input.md"))
        assert record.summary.startswith("Adds speech-to-text")
        assert record.verdict.startswith("**Approve.**")
        assert [e.path for e in record.triage] == ["apps/desktop/package.json", "packages/shared/src/index.ts"]
        assert record.failures[0].agent == "disprove" and record.failures[0].recoverable
        assert record.severity_sections[0].note == "(none)"
        assert [f.id for f in record.findings] == ["S1", "S2", "N1", "N2", "I1"]

    def test_the_ledger_and_unknown_sections_are_kept(self):
        text = "## Prior findings\n- **[S1]** `a` — Fixed\n\n## Review gaps\n- group 2 failed\n"
        record = ingest_review(text)
        assert record.ledger == "- **[S1]** `a` — Fixed" and record.findings == ()
        assert record.extra[0].heading == "Review gaps"

    def test_the_header_is_not_read(self):
        record = ingest_review(
            "# T\n<!-- head_sha: abc -->\n<!-- summary: HEADER_ONLY_KEY -->\n"
            "This would be the summary if the header were read.\n\n## Summary\nx\n"
        )
        assert record.summary == "x"
        assert record.verdict == ""
        assert "HEADER_ONLY_KEY" not in repr(record)
        assert "would be the summary" not in repr(record)

    def test_copy_tree_restores_py_names(self, tmp_path):
        dest = copy_tree(tmp_path / "tree")
        assert (dest / "src" / "cli.py").is_file()
        assert not list(dest.rglob("*.txt"))
