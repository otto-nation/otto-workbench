"""review.prompt_prior: the prior-review section and thread-state annotation."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
from dataclasses import replace

from review.document import SECTION_PRIOR_FINDINGS
from review.grammar import FindingIdentity, sid_marker
from review.record import LedgerLine, RecordFinding, TriageEntry
from review.reply_threads import ReplyThreads
from review.store import StoredReview
from review.types import PriorDisposition, ReplyState, ReviewMeta
from review.prompt_prior import (
    _annotate_with_thread_state, _build_prior_section, _in_group, _prior_text, _scoped,
)
from review_record_support import finding, record


def _stored(*findings, rec=None, **fields) -> StoredReview:
    body = rec if rec is not None else record(*findings, **fields)
    return StoredReview(body, ReviewMeta())


# ── omitted bookkeeping sections ─────────────────────────────────────────────

PRIOR_WITH_INTERNAL = record(
    finding("M1", "missing error check", path="a.py", line=10),
    triage=(
        TriageEntry("a.py", "**Tier 2** (application logic)"),
        TriageEntry("b.py", "**Tier 3** (generated)"),
    ),
    static_analysis=(
        "<details>\n"
        "<summary>Static Analysis (1 violation)</summary>\n"
        "\n"
        "### Nesting depth\n"
        "\n"
        "- **`a.py:42`** — depth 3 exceeds limit 2 (in main())\n"
        "\n"
        "</details>"
    ),
    verdict="Request changes.",
)


# ── _annotate_with_thread_state ──────────────────────────────────────────────

class TestThreadStateFollowsTheIdentityNotTheNumber:
    """The annotation lands on the finding the thread hangs off, after a renumber.

    `renumber_for_posting` numbers inline findings by `(path, line)` on every
    round, so the `[M1]` a thread's root carries is the number *its* round
    assigned. Matched on that number, a thread rooted a round earlier annotates
    whatever finding now holds it — silently, and on the wrong line.
    """

    REVIEW = (
        f"- **[M2]**{sid_marker('aaaa1111')} `a.py:10` — Missing error check\n"
        f"- **[M1]**{sid_marker('bbbb2222')} `b.py:5` — SQL injection\n"
    )

    def _threads(self, **thread) -> ReplyThreads:
        return ReplyThreads(threads=[{"state": ReplyState.CONTESTED, **thread}], summary={})

    def test_the_state_lands_on_the_finding_the_thread_hangs_off(self):
        threads = self._threads(finding_id="M1", stable_id="aaaa1111")
        annotated = _annotate_with_thread_state(self.REVIEW, threads).split("\n")
        assert annotated[0].endswith("[CONTESTED]")
        assert not annotated[1].endswith("[CONTESTED]")

    def test_a_thread_with_no_identity_still_matches_on_its_number(self):
        threads = self._threads(finding_id="M1", stable_id="")
        annotated = _annotate_with_thread_state(self.REVIEW, threads).split("\n")
        assert annotated[1].endswith("[CONTESTED]")
        assert not annotated[0].endswith("[CONTESTED]")

    def test_an_identity_matching_nothing_annotates_nothing(self):
        threads = self._threads(finding_id="M1", stable_id="cccc3333")
        assert _annotate_with_thread_state(self.REVIEW, threads) == self.REVIEW

    def test_the_prior_review_is_stamped_with_the_ids_the_comments_carry(self):
        """End to end, on a prior review that carries no markers of its own.

        `_prior_text` stamps `sid=f.sid or f.stable_id` before annotating, so
        the hash a posted comment carries and the hash that stamps have to be
        the same one — this is the test that they are.
        """
        prior = _stored(finding("M9", "Missing error check", path="a.py", line=10))
        identity = FindingIdentity.of_fields("a.py", 10, "Missing error check")
        # Both sides carrying "" would match each other and prove nothing.
        assert identity.stable_id
        threads = self._threads(finding_id="M1", stable_id=identity.stable_id)

        section = _build_prior_section(prior, reply_threads=threads)

        assert "Missing error check" in section
        assert "[CONTESTED]" in section


# ── _annotate_with_thread_state ──────────────────────────────────────────────

class TestAnnotateWithThreadState:
    def test_adds_labels_to_matching_findings(self):
        review = (
            "## Must-fix\n"
            "- **[M1]** `a.py:10` — Missing error check\n"
            "- **[M2]** `b.py:5` — SQL injection\n"
        )
        threads = ReplyThreads(threads=[
            {"finding_id": "M1", "state": ReplyState.CONTESTED},
            {"finding_id": "M2", "state": ReplyState.ACKNOWLEDGED},
        ], summary={})
        result = _annotate_with_thread_state(review, threads)
        assert "[CONTESTED]" in result
        assert "[ACKNOWLEDGED]" in result

    def test_no_label_for_unreplied(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        threads = ReplyThreads(threads=[
            {"finding_id": "M1", "state": ReplyState.UNREPLIED},
        ], summary={})
        result = _annotate_with_thread_state(review, threads)
        assert "[UNREPLIED]" not in result
        assert result.strip() == review.strip()

    def test_empty_threads(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        result = _annotate_with_thread_state(review, ReplyThreads(threads=[], summary={}))
        assert result == review

    def test_no_reply_threads_at_all(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        result = _annotate_with_thread_state(review, None)
        assert result == review


# ── _build_prior_section with reply_threads ──────────────────────────────────

class TestBuildPriorSectionWithThreads:
    def test_without_threads_unchanged(self):
        result = _build_prior_section(_stored(finding("M1", "Issue", path="a.py", line=10)))
        assert "[CONTESTED]" not in result
        assert "Prior review" in result

    def test_with_threads_annotates(self):
        threads = ReplyThreads(threads=[
            {"finding_id": "M1", "state": ReplyState.CONTESTED},
        ], summary={})
        result = _build_prior_section(
            _stored(finding("M1", "Issue", path="a.py", line=10)),
            reply_threads=threads,
        )
        assert "[CONTESTED]" in result

    def test_empty_prior_returns_empty(self):
        assert _build_prior_section(None, reply_threads=ReplyThreads(threads=[], summary={})) == ""


class TestBuildPriorSectionLedger:
    def test_asks_for_the_ledger_alongside_the_context(self):
        result = _build_prior_section(
            _stored(finding("M1", "Issue", path="a.py", line=10)),
            "This is a re-review.",
        )
        assert "This is a re-review." in result
        assert "`prior_findings`" in result

    def test_ledger_asked_for_without_a_context(self):
        result = _build_prior_section(_stored(finding("M1", "Issue", path="a.py", line=10)))
        assert "`prior_findings`" in result

    def test_prior_ledger_not_shown_back_to_the_agent(self):
        # Reconciliation strips it before publishing, but a review from an
        # older generator can still carry one — it dispositions findings from
        # the review before last, which is noise here.
        result = _build_prior_section(
            _stored(
                finding("M1", "Issue", path="a.py", line=10),
                ledger=(LedgerLine("M9", PriorDisposition.FIXED, "gone.py"),),
            )
        )
        assert "gone.py" not in result


class TestPriorTextOmitsBookkeeping:
    def test_drops_triage_and_static_analysis(self):
        result = _prior_text(_stored(rec=PRIOR_WITH_INTERNAL), None)
        assert "File Triage" not in result
        assert "Tier 2" not in result
        assert "Static Analysis" not in result
        assert "Nesting depth" not in result
        assert "<details>" not in result

    def test_keeps_findings_and_verdict(self):
        result = _prior_text(_stored(rec=PRIOR_WITH_INTERNAL), None)
        assert "**[M1]**" in result
        assert "## Must fix" in result
        assert "Request changes." in result

    def test_section_after_excluded_one_resumes(self):
        assert "Request changes." in _prior_text(_stored(rec=PRIOR_WITH_INTERNAL), None)

    def test_findings_survive_render(self):
        assert "**[M1]**" in _prior_text(_stored(finding("M1", "bug")), None)

    def test_only_internal_sections_yields_empty(self):
        assert _prior_text(_stored(triage=(TriageEntry("a.py", "**Tier 1**"),)), None) == ""

    def test_build_prior_section_omits_internal_sections(self):
        result = _build_prior_section(_stored(rec=PRIOR_WITH_INTERNAL))
        assert "Prior review" in result
        assert "**[M1]**" in result
        assert "File Triage" not in result
        assert "Nesting depth" not in result

    def test_build_prior_section_empty_when_only_internal(self):
        assert _build_prior_section(_stored(triage=(TriageEntry("a.py", "**Tier 1**"),))) == ""

    def test_a_record_rendered_prompt_has_no_banner(self):
        from review.render import BANNER
        result = _build_prior_section(_stored(finding("M1", "bug")))
        assert BANNER not in result
        assert "**[M1]**" in result

    def test_carried_findings_are_stamped_with_their_stable_id(self):
        result = _prior_text(_stored(finding("M1", "missing error check", path="handler.go", line=42)), None)
        assert "<!-- sid:" in result
        assert "**[M1]**" in result
        assert "handler.go:42" in result
        identity = FindingIdentity.of_fields("handler.go", 42, "missing error check")
        assert identity.stable_id in result
        assert _prior_text(_stored(finding("M1", "missing error check", path="handler.go", line=42)), None) == result


def test_scoping_reads_the_finding_s_own_path_and_line():
    f = RecordFinding(id="M1", severity="M", path="src/a.go", line=10, body="x")
    assert _in_group(f, {"src/a.go"})
    assert not _in_group(replace(f, line=None), {"src/a.go"})
    assert not _in_group(f, {"src/b.go"})


def test_a_finding_without_a_path_is_scoped_to_no_group():
    """X12: a location ingest never placed is a whole-PR finding, in no group's slice."""
    f = RecordFinding(id="M1", severity="M", body="src/auth.go:10 — auth bug")
    assert not _in_group(f, {"src/auth.go"})
