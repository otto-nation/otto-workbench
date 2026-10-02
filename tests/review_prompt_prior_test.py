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
from review.document import SECTION_PRIOR_FINDINGS
from review.grammar import FindingIdentity, sid_marker
from review.reply_threads import ReplyThreads
from review.types import ReplyState
from review.prompt_prior import (
    _annotate_with_thread_state, _build_prior_section, _strip_internal_sections,
)


# ── _strip_internal_sections ─────────────────────────────────────────────────

PRIOR_WITH_INTERNAL = (
    "## File Triage\n"
    "- `a.py` — **Tier 2** (application logic)\n"
    "- `b.py` — **Tier 3** (generated)\n"
    "\n"
    "## Must fix\n"
    "- **[M1]** **`a.py:10`** — missing error check\n"
    "\n"
    "## Static Analysis\n"
    "\n"
    "<details>\n"
    "<summary>Static Analysis (1 violation)</summary>\n"
    "\n"
    "### Nesting depth\n"
    "\n"
    "- **`a.py:42`** — depth 3 exceeds limit 2 (in main())\n"
    "\n"
    "</details>\n"
    "\n"
    "## Verdict\n"
    "Request changes.\n"
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

        `_build_prior_section` runs `annotate_prior_with_stable_ids` over the
        review before annotating it, so the hash a posted comment carries and
        the hash that stamps have to be the same one — this is the test that
        they are.
        """
        line = "- **[M9]** `a.py:10` — Missing error check"
        prior = f"## Must fix\n{line}\n"
        identity = FindingIdentity.of(line)
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
        result = _build_prior_section("## Must-fix\n- **[M1]** `a.py:10` — Issue")
        assert "[CONTESTED]" not in result
        assert "Prior review" in result

    def test_with_threads_annotates(self):
        threads = ReplyThreads(threads=[
            {"finding_id": "M1", "state": ReplyState.CONTESTED},
        ], summary={})
        result = _build_prior_section(
            "## Must-fix\n- **[M1]** `a.py:10` — Issue",
            reply_threads=threads,
        )
        assert "[CONTESTED]" in result

    def test_empty_prior_returns_empty(self):
        assert _build_prior_section("", reply_threads=ReplyThreads(threads=[], summary={})) == ""


class TestBuildPriorSectionLedger:
    def test_asks_for_the_ledger_alongside_the_context(self):
        result = _build_prior_section(
            "## Must fix\n- **[M1]** `a.py:10` — Issue",
            "This is a re-review.",
        )
        assert "This is a re-review." in result
        assert f"## {SECTION_PRIOR_FINDINGS}" in result

    def test_ledger_asked_for_without_a_context(self):
        result = _build_prior_section("## Must fix\n- **[M1]** `a.py:10` — Issue")
        assert f"## {SECTION_PRIOR_FINDINGS}" in result

    def test_prior_ledger_not_shown_back_to_the_agent(self):
        # Reconciliation strips it before publishing, but a review from an
        # older generator can still carry one — it dispositions findings from
        # the review before last, which is noise here.
        result = _build_prior_section(
            "## Must fix\n- **[M1]** `a.py:10` — Issue\n"
            f"## {SECTION_PRIOR_FINDINGS}\n- **[M9]** `gone.py` — Fixed\n"
        )
        assert "gone.py" not in result


class TestStripInternalSections:
    def test_drops_triage_and_static_analysis(self):
        result = _strip_internal_sections(PRIOR_WITH_INTERNAL)
        assert "File Triage" not in result
        assert "Tier 2" not in result
        assert "Static Analysis" not in result
        assert "Nesting depth" not in result
        assert "<details>" not in result

    def test_keeps_findings_and_verdict(self):
        result = _strip_internal_sections(PRIOR_WITH_INTERNAL)
        assert "**[M1]**" in result
        assert "## Must fix" in result
        assert "Request changes." in result

    def test_section_after_excluded_one_resumes(self):
        # Verdict follows Static Analysis — exclusion must reset at its header
        assert _strip_internal_sections(PRIOR_WITH_INTERNAL).endswith("Request changes.")

    def test_unaffected_text_passes_through(self):
        text = "## Must fix\n- **[M1]** **`a.py:1`** — bug"
        assert _strip_internal_sections(text) == text

    def test_only_internal_sections_yields_empty(self):
        assert _strip_internal_sections("## File Triage\n- `a.py` — **Tier 1**\n") == ""

    def test_build_prior_section_omits_internal_sections(self):
        result = _build_prior_section(PRIOR_WITH_INTERNAL)
        assert "Prior review" in result
        assert "**[M1]**" in result
        assert "File Triage" not in result
        assert "Nesting depth" not in result

    def test_build_prior_section_empty_when_only_internal(self):
        assert _build_prior_section("## File Triage\n- `a.py` — **Tier 1**\n") == ""
