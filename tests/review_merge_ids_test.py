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

from review_merge_support import _sections


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


class TestAnnotatePriorWithStableIds:
    def test_inserts_sid_comment(self):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        result = review.merge.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result
        assert "**[M1]**" in result
        assert "handler.go:42" in result

    def test_checkbox_format(self):
        text = '- [ ] **[S1]** `handler.go:42` — missing check\n'
        result = review.merge.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result

    def test_non_finding_lines_unchanged(self):
        text = "## Summary\nThis is a summary.\n"
        result = review.merge.annotate_prior_with_stable_ids(text)
        assert result == text

    def test_deterministic_ids(self):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        a = review.merge.annotate_prior_with_stable_ids(text)
        b = review.merge.annotate_prior_with_stable_ids(text)
        assert a == b


class TestPriorDisposition:
    def test_parses_the_word_the_prompt_asks_for(self):
        assert PriorDisposition.parse("Fixed") is PriorDisposition.FIXED
        assert (
            PriorDisposition.parse("Still open — see below")
            is PriorDisposition.STILL_OPEN
        )

    def test_unrecognised_wording_has_no_disposition(self):
        assert PriorDisposition.parse("moved to a follow-up") is None

    def test_a_qualified_verdict_is_not_read_as_its_optimistic_half(self):
        assert PriorDisposition.parse("Fixed, but only on the happy path") is None
        assert PriorDisposition.parse("Fixed in a follow-up branch") is None

    def test_a_verdict_ending_a_sentence_is_still_a_verdict(self):
        """The form a review writes when the explanation is prose, not a clause."""
        assert (
            PriorDisposition.parse("Fixed. `check_key` now calls it directly.")
            is PriorDisposition.FIXED
        )
        assert PriorDisposition.parse("Still open.") is PriorDisposition.STILL_OPEN

    def test_a_verdict_with_italicised_detail_is_still_a_verdict(self):
        """Emphasis carries no meaning here — the same line parses unitalicised."""
        assert (
            PriorDisposition.parse("Fixed *(removed entirely in 89f66d9)*")
            is PriorDisposition.FIXED
        )
        assert (
            PriorDisposition.parse("Still open *the guard is still there*")
            is PriorDisposition.STILL_OPEN
        )
        assert (
            PriorDisposition.parse("Declined *(documented `ceiling:` tradeoff)*")
            is PriorDisposition.DECLINED
        )

    def test_underscore_emphasis_reads_the_same_as_asterisk(self):
        assert (
            PriorDisposition.parse("Fixed _(removed entirely in 89f66d9)_")
            is PriorDisposition.FIXED
        )
        assert (
            PriorDisposition.parse("Still open _the guard is still there_")
            is PriorDisposition.STILL_OPEN
        )
        assert (
            PriorDisposition.parse("Declined _(documented `ceiling:` tradeoff)_")
            is PriorDisposition.DECLINED
        )

    def test_a_verdict_a_word_is_glued_to_is_not_a_verdict(self):
        """`-` and `_` join words, so a glued one is inside one, not a break."""
        assert PriorDisposition.parse("Fixed_up in a follow-up branch") is None
        assert PriorDisposition.parse("Fixed-up in a follow-up branch") is None
        assert PriorDisposition.parse("Still open_ended, see below") is None

    def test_a_dash_glued_to_the_verdict_still_breaks_the_line(self):
        """Only the two word-joining characters are read as part of a word."""
        assert (
            PriorDisposition.parse("Fixed\u2014the guard is gone")
            is PriorDisposition.FIXED
        )
        assert PriorDisposition.parse("Fixed: the guard is gone") is PriorDisposition.FIXED

    def test_parses_a_declined_verdict(self):
        assert PriorDisposition.parse("Declined") is PriorDisposition.DECLINED
        assert (
            PriorDisposition.parse("Declined — documented `ceiling:` tradeoff")
            is PriorDisposition.DECLINED
        )

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


def _shift(offsets: dict[str, int], **by_key: str) -> review.merge._Merge:
    """A merge that has already used `offsets`, with one more group folded in."""
    merge = review.merge._Merge()
    merge.offsets.update(offsets)
    merge.sections = merge._shift(_sections(**by_key))
    return merge


class TestShiftGroup:
    def test_offset_zero(self):
        text = "- **[S1]** finding\n- **[S2]** another"
        merge = _shift({}, S=text)
        assert merge.sections["S"] == text
        assert merge.offsets["S"] == 2

    def test_positive_offset(self):
        text = "- **[S1]** finding\n- **[S2]** another"
        merge = _shift({"S": 3}, S=text)
        assert "[S4]" in merge.sections["S"]
        assert "[S5]" in merge.sections["S"]
        assert merge.offsets["S"] == 5

    def test_a_repeated_id_counts_once(self):
        merge = _shift({}, M="- **[M1]** finding\n  see [M1] above")
        assert merge.offsets["M"] == 1

    def test_offset_carries_references(self):
        # The offset is what keeps two groups' IDs apart. A reference left behind
        # would name whatever the earlier group happened to put at that number.
        text = "- **[S1]** first\n- **[S2]** second, see S1 above and [S1] again"
        assert "see S3 above and [S3] again" in _shift({"S": 2}, S=text).sections["S"]

    def test_offset_shifts_ids_it_did_not_expect(self):
        # IDs arrive however the agent wrote them; gaps are closed later, not here.
        merge = _shift({"S": 2}, S="- **[S1]** first\n- **[S7]** second")
        assert "[S3]" in merge.sections["S"]
        assert "[S9]" in merge.sections["S"]
        # What the next group has to clear, not how many findings this one had.
        assert merge.offsets["S"] == 9

    def test_a_reference_to_a_number_the_group_skipped_names_nothing(self):
        # The group declared S1 and nothing else, so S4 is not another group's
        # finding — no group can name one — it is a reference to nothing. This
        # is the last pass that knows the reference came from this group.
        merge = _shift({"S": 2}, S="- **[S1]** first, see S4 elsewhere")
        assert "see [removed] elsewhere" in merge.sections["S"]
        assert "S4" not in merge.sections["S"]

    def test_a_reference_to_a_severity_the_group_declares_none_of_names_nothing(self):
        # Every group numbers its Should-fix findings from [S1] independently,
        # so this one cannot be citing another group's. Left alone, the pooled
        # map would resolve it onto whichever group did declare an S1.
        merge = _shift({"M": 1}, M="- **[M1]** **`a.go:1`** — issue a, blocked on [S1]")
        assert "issue a, blocked on [removed]" in merge.sections["M"]

    def test_a_citation_across_severities_moves_with_what_it_names(self):
        # The Should-fix section is shifted past the earlier groups; the Must-fix
        # finding citing it has to make the same move, or it names the finding
        # the earlier groups left on that number.
        merge = _shift(
            {"M": 1, "S": 2},
            M="- **[M1]** **`a.go:1`** — issue a, blocked on [S1]",
            S="- **[S1]** **`a.go:9`** — issue b",
        )
        assert "blocked on [S3]" in merge.sections["M"]
        assert "- **[S3]** **`a.go:9`** — issue b" in merge.sections["S"]

    def test_a_declaration_filed_under_the_wrong_severity_still_moves(self):
        # A malformed review is still a declaration: reading only the Must-fix
        # section for M would leave this one behind and then blank it out as a
        # dangling reference when the gaps close.
        merge = _shift({"S": 2}, M="- **[S1]** **`a.go:1`** — misfiled")
        assert "- **[S3]** **`a.go:1`** — misfiled" in merge.sections["M"]

    def test_empty_group(self):
        merge = _shift({})
        assert merge.sections == _sections()
        assert merge.offsets == {s.key: 0 for s in SEVERITIES}


# ── 4. renumber_findings ────────────────────────────────────────────────────


def _decl(prefix: str, num: int, body: str = "finding") -> str:
    """A finding declaring its own ID — the only thing renumbering numbers."""
    return f"- **[{prefix}{num}]** **`file.go:{num}`** — {body}"


class TestRenumberFindings:
    def test_sequential_already(self):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 2, 'second')}"
        assert review.merge.renumber_findings(text) == text

    def test_with_gaps(self):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 3, 'third')}"
        result = review.merge.renumber_findings(text)
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S3]" not in result

    def test_repeated_ids(self):
        text = "\n".join([
            _decl("S", 3, "first"), _decl("S", 1, "second"), _decl("S", 3, "repeat"),
        ])
        result = review.merge.renumber_findings(text)
        assert result.count("[S1]") == 2  # S3 appears first -> becomes S1
        assert "[S2]" in result  # S1 appears second -> becomes S2

    def test_unbracketed_cross_refs(self):
        text = f"{_decl('S', 3)}\nsee S3 above"
        result = review.merge.renumber_findings(text)
        assert "see S1 above" in result
        assert "S3" not in result

    def test_reference_to_a_dropped_finding_points_nowhere(self):
        # S1 was dropped by verification, so only its reference is left. Closing
        # the gap on S2 frees up the number 1, and the reference must not take it.
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        result = review.merge.renumber_findings(text)
        assert "- **[S1]** **`file.go:2`** — real problem" in result
        assert "blocked on [removed]" in result

    def test_bare_reference_to_a_dropped_finding_points_nowhere(self):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on S1"
        assert "blocked on [removed]" in review.merge.renumber_findings(text)

    def test_prose_that_merely_looks_like_an_id_is_left_alone(self):
        # S3 the object store, M1 the laptop. Nothing cites them, so nothing
        # may rewrite them — and a review of storage code says "S3" constantly.
        text = "\n".join([
            _decl("S", 3, "uploads to an S3 bucket on every M1 build"),
            _decl("S", 5, "second"),
        ])
        result = review.merge.renumber_findings(text)
        assert "uploads to an S3 bucket on every M1 build" in result
        assert "- **[S1]**" in result
        assert "- **[S2]**" in result

    def test_a_cited_bare_reference_is_still_rewritten(self):
        text = f"{_decl('S', 3, 'first')}\n{_decl('S', 5, 'second, duplicate of S3')}"
        assert "duplicate of S1" in review.merge.renumber_findings(text)

    def test_references_survive_a_second_pass(self):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        once = review.merge.renumber_findings(text)
        assert review.merge.renumber_findings(once) == once

    def test_text_that_declares_nothing_is_left_alone(self):
        # A section can mention IDs it does not own — the triage list, a prior
        # review's ledger. With no declaration there is no map to rewrite through.
        text = "carried over from [S4] and [S7]"
        assert review.merge.renumber_findings(text) == text

    def test_checklist_findings_declare_their_ids(self):
        # Self-review writes findings as checkboxes; they are declarations too.
        text = "- [ ] **[S3]** `file.go:1` — finding\nsee [S3]"
        result = review.merge.renumber_findings(text)
        assert "- [ ] **[S1]** `file.go:1` — finding" in result
        assert "see [S1]" in result

    def test_renumbers_gaps(self):
        text = "\n".join([
            _decl("M", 1, "first"), _decl("M", 3, "third"),
            _decl("S", 1, "s1"), _decl("S", 5, "s5"), "",
        ])
        result = review.merge.renumber_findings(text)
        assert "[M1]" in result
        assert "[M2]" in result
        assert "[M3]" not in result
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S5]" not in result

    def test_each_severity_is_renumbered_independently(self):
        # A Nit citing a dropped Must-fix loses the citation; its own ID does not
        # move, because each severity's gaps close over that severity alone.
        text = "\n".join([
            _decl("M", 2, "kept"),
            "- **[N1]** **`file.go:9`** — revisit once [M1] lands",
        ])
        result = review.merge.renumber_findings(text)
        assert "- **[M1]** **`file.go:2`** — kept" in result
        assert "revisit once [removed] lands" in result

    def test_empty_text(self):
        assert review.merge.renumber_findings("") == ""

    def test_no_findings_unchanged(self):
        content = "No findings here.\n"
        assert review.merge.renumber_findings(content) == content
