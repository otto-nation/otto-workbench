"""Tests for the comment pass's own fix record — its status lines and blockers.

The generic vocabulary every domain shares lives in ``pr_fix``, and is tested
in ``pr_fix_test.py``; this covers what ``FixSummary`` says on top of it.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_comments_fix
import pr_domains
from pr_comments_fix import CLOSEOUT_COMMAND
from pr_fix import CommitStatus, FixOutcome, FixRecord, ItemOutcome

# When the run being described happened. Any non-empty stamp means "written",
# which is the only thing render_status and readiness read it for.
_FIX_RUN = "2026-07-14T00:00:00+00:00"


def _summary(*outcomes: FixOutcome, **kwargs) -> pr_comments_fix.FixSummary:
    """A comment fix pass that recorded one outcome per argument, in order.

    Ids are positional, so a caller that only cares about the verdicts does not
    have to invent them — the counts every renderer here reads come off the
    outcomes, and the ids only have to be distinct enough not to fold together.
    That is why this takes verdicts where the `_fix` helpers in
    `pr_state_test` and `test_review_threads` take whole `ItemOutcome`s: those
    suites are about what the record holds, and this one is about what the
    domain says over it.
    """
    record = kwargs.pop("fix", FixRecord())
    record.items = [
        ItemOutcome(id=f"t{n}", outcome=outcome)
        for n, outcome in enumerate(outcomes, start=1)
    ]
    return pr_comments_fix.FixSummary(fix=record, updated_at=_FIX_RUN, **kwargs)


def test_fix_render_not_run():
    assert pr_comments_fix.FixSummary().render_status() == ["**Fix**: not run yet"]


def test_fix_render_with_data():
    lines = _summary(
        FixOutcome.FIXED, FixOutcome.FIXED, FixOutcome.DEFERRED, FixOutcome.DISMISSED,
        fix=FixRecord(commit_sha="abc1234", commit_status=CommitStatus.PUSHED),
    ).render_status()
    assert "**2 fixed**" in lines[0]
    assert "1 deferred" in lines[0]
    assert "1 dismissed" in lines[0]
    assert "abc1234" in lines[0]
    assert "pushed" in lines[0]


def test_fix_render_needs_human():
    lines = _summary(FixOutcome.NEEDS_HUMAN, FixOutcome.NEEDS_HUMAN).render_status()
    assert "2 need discussion" in lines[0]


def test_fix_render_already_addressed():
    lines = _summary(FixOutcome.ALREADY_ADDRESSED).render_status()
    assert "1 already addressed" in lines[0]


def test_fix_render_settled_elsewhere():
    """Reported under its own word, and never folded into the fixed count."""
    lines = _summary(FixOutcome.SETTLED_ELSEWHERE, FixOutcome.FIXED).render_status()
    assert "1 settled elsewhere" in lines[0]
    assert "**1 fixed**" in lines[0]


def test_every_verdict_has_a_word_on_the_status_line():
    """A member with no label is silently dropped, so the count under-reports.

    Swept over the enum rather than listed: the label table is in one module and
    the vocabulary in another, so a new verdict lands nowhere near this.
    """
    assert set(pr_comments_fix._STATUS_LABELS) == set(FixOutcome)


def test_the_status_line_counts_every_verdict_it_was_handed():
    """The other half of the sweep — a label that never renders is no label."""
    lines = _summary(*FixOutcome).render_status()
    assert len(lines[0].removeprefix("**Fix**: ").split(" · ")) == len(FixOutcome)


def test_a_thread_settled_on_the_forge_owes_no_reply():
    """Nothing was decided here, so there is nothing to tell the reviewer."""
    assert FixOutcome.SETTLED_ELSEWHERE not in pr_comments_fix._REPLY_OUTCOMES
    assert _summary(
        FixOutcome.SETTLED_ELSEWHERE,
        fix=FixRecord(commit_sha="abc1234", commit_status=CommitStatus.PUSHED),
        replies_pending=True,
    ).closeout_debt().reply_count == 0


def test_fix_render_deferred_issue():
    lines = _summary(
        FixOutcome.DEFERRED,
        fix=FixRecord(commit_sha="abc", commit_status=CommitStatus.PUSHED),
        deferred_issue_id="ENG-456",
    ).render_status()
    assert any("ENG-456" in line for line in lines)
    assert any("tracked in" in line for line in lines)


# ── Closeout debt ─────────────────────────────────────────────────────────


def _fix_with_closeout(**kwargs) -> pr_comments_fix.FixSummary:
    """A pushed fix pass with three reply-owing outcomes and one that owes none."""
    return _summary(
        FixOutcome.FIXED,
        FixOutcome.ALREADY_ADDRESSED,
        FixOutcome.DISMISSED,
        FixOutcome.NEEDS_HUMAN,
        fix=FixRecord(commit_sha="abc1234", commit_status=CommitStatus.PUSHED),
        **kwargs,
    )


def _closeout_line(lines: list[str]) -> str | None:
    return next((line for line in lines if "closeout owed" in line), None)


def test_closeout_debt_reads_both_flags():
    debt = _fix_with_closeout(summary_deferred=True, replies_pending=True).closeout_debt()
    assert debt.owed is True
    assert debt.summary is True
    assert debt.replies is True
    # NEEDS_HUMAN owes no reply — only the three buckets --finish drains count.
    assert debt.reply_count == 3


def test_closeout_debt_clean_state_owes_nothing():
    debt = _fix_with_closeout().closeout_debt()
    assert debt.owed is False
    assert debt.describe() == ""


def test_closeout_debt_counts_a_tracking_issue_that_was_never_filed():
    """Deferred threads with no issue behind them are undelivered closeout."""
    debt = _fix_with_closeout(deferred_issue_pending=True).closeout_debt()
    assert debt.owed is True
    assert debt.deferred_issue is True
    assert debt.describe() == "deferred tracking issue"


def test_closeout_debt_ignores_a_tracking_issue_that_was_filed():
    assert _fix_with_closeout(deferred_issue_id="ENG-456").closeout_debt().owed is False


def test_closeout_debt_reads_an_undelivered_pr_description():
    debt = _fix_with_closeout(pr_body_pending=True).closeout_debt()
    assert debt.owed is True
    assert debt.description is True


def test_closeout_command_files_the_tracking_issue_it_owes():
    """The bare command selects no threads to track, so it cannot drain this."""
    debt = _fix_with_closeout(deferred_issue_pending=True).closeout_debt()
    assert debt.command == f"{CLOSEOUT_COMMAND} --track-all"


def test_closeout_command_is_the_bare_one_for_everything_else():
    debt = _fix_with_closeout(summary_deferred=True).closeout_debt()
    assert debt.command == CLOSEOUT_COMMAND


def test_fix_render_warns_when_summary_and_replies_are_owed():
    lines = _fix_with_closeout(
        summary_deferred=True, replies_pending=True,
    ).render_status()
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: summary + 3 replies — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_warns_for_a_deferred_summary_alone():
    lines = _fix_with_closeout(summary_deferred=True).render_status()
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: summary — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_warns_for_a_pending_reply_queue_alone():
    lines = _fix_with_closeout(replies_pending=True).render_status()
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: 3 replies — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_warns_for_an_unfiled_tracking_issue():
    lines = _fix_with_closeout(deferred_issue_pending=True).render_status()
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: deferred tracking issue — run: {CLOSEOUT_COMMAND} --track-all"
    )


def test_fix_render_singularises_a_one_reply_queue():
    f = _summary(FixOutcome.FIXED, replies_pending=True)
    assert _closeout_line(f.render_status()) == (
        f"  ⚠ closeout owed: 1 reply — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_says_replies_when_no_outcome_carries_the_count():
    """A queue whose outcomes were pruned still says replies are owed, not zero."""
    f = _summary(replies_pending=True)
    assert _closeout_line(f.render_status()) == (
        f"  ⚠ closeout owed: replies — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_warns_for_a_pending_pr_description_alone():
    lines = _fix_with_closeout(pr_body_pending=True).render_status()
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: PR description — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_names_the_description_alongside_the_rest():
    lines = _fix_with_closeout(
        summary_deferred=True, replies_pending=True, pr_body_pending=True,
    ).render_status()
    assert _closeout_line(lines) == (
        "  ⚠ closeout owed: summary + 3 replies + PR description"
        f" — run: {CLOSEOUT_COMMAND}"
    )


def test_fix_render_silent_when_nothing_is_owed():
    assert _closeout_line(_fix_with_closeout().render_status()) is None


def test_fix_readiness_blocks_on_undelivered_closeout():
    answer = _fix_with_closeout(summary_deferred=True).readiness()
    assert answer.blockers == (
        f"closeout not delivered (run: {CLOSEOUT_COMMAND})",
    )


def test_fix_readiness_quotes_the_command_that_files_the_tracking_issue():
    """The blocker names a command that drains it, not one that cannot."""
    answer = _fix_with_closeout(deferred_issue_pending=True).readiness()
    assert answer.blockers == (
        f"closeout not delivered (run: {CLOSEOUT_COMMAND} --track-all)",
    )


def test_fix_readiness_clean_when_the_closeout_landed():
    assert _fix_with_closeout().readiness() == pr_domains.Readiness()
