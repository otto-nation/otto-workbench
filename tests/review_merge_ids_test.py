"""Tests for `review.merge` — finding identity across groups and reviews.

The stable IDs that give a finding an identity later reviews can recognise, the
prior-findings ledger unioned across groups, and shifting and renumbering IDs.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.merge
from review.grammar import FindingIdentity
from review.types import SEVERITIES, PriorDisposition

from review_merge_support import _group_of
from review_record_support import finding


def _stable_id(path: str, desc: str) -> str:
    return FindingIdentity(path, None, desc).stable_id


class TestComputeStableId:
    def test_deterministic(self):
        a = _stable_id("pkg/handler.go", "missing error check on db.Query()")
        b = _stable_id("pkg/handler.go", "missing error check on db.Query()")
        assert a == b

    def test_eight_hex_chars(self):
        sid = _stable_id("file.go", "desc")
        assert len(sid) == 8
        assert all(c in "0123456789abcdef" for c in sid)

    def test_case_insensitive_path(self):
        a = _stable_id("Pkg/Handler.go", "desc")
        b = _stable_id("pkg/handler.go", "desc")
        assert a == b

    def test_different_descriptions_differ(self):
        a = _stable_id("file.go", "missing error check")
        b = _stable_id("file.go", "unused import")
        assert a != b

    def test_truncates_description_at_80(self):
        desc_80 = "x" * 80
        desc_100 = desc_80 + "y" * 20
        assert _stable_id("f.go", desc_80) == _stable_id("f.go", desc_100)


class TestPriorDisposition:
    def test_the_older_two_verdicts_keep_their_spelling(self):
        """A review file written before Declined existed still has to parse."""
        assert PriorDisposition.FIXED.value == "Fixed"
        assert PriorDisposition.STILL_OPEN.value == "Still open"

    def test_declined_outranks_the_verdicts_it_must_survive(self):
        assert (
            PriorDisposition.DECLINED.precedence
            > PriorDisposition.STILL_OPEN.precedence
            > PriorDisposition.FIXED.precedence
        )


# ── 3. shifting one group's IDs ─────────────────────────────────────────────


def _shift(offsets: dict[str, int], *findings):
    """A group shifted past `offsets`; returns the record and the updated offsets."""
    full = {s.key: 0 for s in SEVERITIES}
    full.update(offsets)
    out = review.merge._shift_record(_group_of(*findings), full)
    return out, full


class TestShiftGroup:
    def test_offset_zero(self):
        out, offsets = _shift(
            {},
            finding("S1", "finding", path="a.go"),
            finding("S2", "another", path="a.go", line=2),
        )
        assert [f.id for f in out.findings] == ["S1", "S2"]
        assert offsets["S"] == 2

    def test_positive_offset(self):
        out, offsets = _shift(
            {"S": 3},
            finding("S1", "finding", path="a.go"),
            finding("S2", "another", path="a.go", line=2),
        )
        assert [f.id for f in out.findings] == ["S4", "S5"]
        assert offsets["S"] == 5

    def test_a_repeated_id_counts_once(self):
        _out, offsets = _shift(
            {}, finding("M1", "finding\n  see [M1] above", path="a.go"),
        )
        assert offsets["M"] == 1

    def test_offset_carries_references(self):
        # The offset is what keeps two groups' IDs apart. A reference left behind
        # would name whatever the earlier group happened to put at that number.
        out, _offsets = _shift(
            {"S": 2},
            finding("S1", "first", path="a.go"),
            finding("S2", "second, see S1 above and [S1] again", path="a.go", line=2),
        )
        assert "see S3 above and [S3] again" in out.findings[1].body

    def test_offset_shifts_ids_it_did_not_expect(self):
        # IDs arrive however the agent wrote them; gaps are closed later, not here.
        out, offsets = _shift(
            {"S": 2},
            finding("S1", "first", path="a.go"),
            finding("S7", "second", path="a.go", line=2),
        )
        assert [f.id for f in out.findings] == ["S3", "S9"]
        # What the next group has to clear, not how many findings this one had.
        assert offsets["S"] == 9

    def test_a_reference_to_a_number_the_group_skipped_names_nothing(self):
        # The group declared S1 and nothing else, so S4 is not another group's
        # finding — no group can name one — it is a reference to nothing. This
        # is the last pass that knows the reference came from this group.
        out, _offsets = _shift(
            {"S": 2},
            finding("S1", "first, see S4 elsewhere", path="a.go"),
        )
        assert "see [removed] elsewhere" in out.findings[0].body
        assert "S4" not in out.findings[0].body

    def test_a_reference_to_a_severity_the_group_declares_none_of_names_nothing(self):
        # Every group numbers its Should-fix findings from [S1] independently,
        # so this one cannot be citing another group's. Left alone, the pooled
        # map would resolve it onto whichever group did declare an S1.
        out, _offsets = _shift(
            {"M": 1},
            finding("M1", "issue a, blocked on [S1]", path="a.go"),
        )
        assert "issue a, blocked on [removed]" in out.findings[0].body

    def test_a_citation_across_severities_moves_with_what_it_names(self):
        # The Should-fix section is shifted past the earlier groups; the Must-fix
        # finding citing it has to make the same move, or it names the finding
        # the earlier groups left on that number.
        out, _offsets = _shift(
            {"M": 1, "S": 2},
            finding("M1", "issue a, blocked on [S1]", path="a.go"),
            finding("S1", "issue b", path="a.go", line=9),
        )
        must = next(f for f in out.findings if f.severity == "M")
        should = next(f for f in out.findings if f.severity == "S")
        assert "blocked on [S3]" in must.body
        assert should.id == "S3"
        assert should.path == "a.go"

    def test_a_declaration_filed_under_the_wrong_severity_still_moves(self):
        # A malformed review is still a declaration: reading only the Must-fix
        # section for M would leave this one behind and then blank it out as a
        # dangling reference when the gaps close.
        out, _offsets = _shift({"S": 2}, finding("S1", "misfiled", path="a.go"))
        assert out.findings[0].id == "S3"
        assert "misfiled" in out.findings[0].body

    def test_empty_group(self):
        out, offsets = _shift({})
        assert out.findings == ()
        assert offsets == {s.key: 0 for s in SEVERITIES}
