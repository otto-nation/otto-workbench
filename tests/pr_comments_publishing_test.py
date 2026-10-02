"""The publishing gate: nothing pr.comments writes reaches GitHub until --post says so."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.comments
import core.publishing
import review.issue
import core.proc


REPO = "owner/repo"


# ── Publishing gate ──────────────────────────────────────────────────────────


@pytest.fixture
def no_subprocess(monkeypatch):
    """Any external call in draft mode is a bug, so make one impossible to miss."""
    def boom(*a, **kw):
        raise AssertionError(f"a subprocess ran in draft mode: {a}")
    monkeypatch.setattr("core.proc.subprocess.run", boom)
    monkeypatch.setattr(core.proc, "run", boom)


class TestPublishingGate:
    """Nothing reaches GitHub until --post says so."""

    def test_defaults_to_drafts(self):
        assert core.publishing.enabled() is False

    def test_thread_reply_is_not_posted(self, no_subprocess):
        assert pr.comments.post_thread_reply("o/r", 1, 99, "body") is False

    def test_issue_comment_is_not_posted(self, no_subprocess):
        assert pr.comments.post_issue_comment("o/r", 1, "body") is None

    def test_thread_is_not_resolved(self, no_subprocess):
        assert pr.comments.resolve_thread("PRRT_1") is False

    def test_pr_description_is_not_edited(self, no_subprocess):
        assert pr.comments.update_pr_body("o/r", 1, "new body") is False

    def test_pr_description_draft_goes_to_stderr(self, no_subprocess, capsys):
        pr.comments.update_pr_body("o/r", 1, "the rewritten description")
        assert "the rewritten description" in capsys.readouterr().err

    def test_draft_body_goes_to_stderr(self, no_subprocess, capsys):
        pr.comments.post_thread_reply("o/r", 1, 99, "the reply text")
        captured = capsys.readouterr()
        assert "the reply text" in captured.err
        assert captured.out == ""

    def test_enable_opens_the_gate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "core.proc.subprocess.run",
            lambda *a, **kw: calls.append(a) or SimpleNamespace(
                returncode=0, stdout='{"html_url": "u"}', stderr="",
            ),
        )
        core.publishing.enable()
        assert pr.comments.post_thread_reply("o/r", 1, 99, "body") is True
        assert len(calls) == 1


class TestTheGateIsScopedToOneRun:
    """What one entry point may publish is not an authorisation for the next.

    `pr fix` runs a review, a CI pass and a describe pass in one process.
    Until in-process dispatch, each was a subprocess and the gate died with
    it; now `call_entry_point` is the only thing between a `--post` on the
    first pass and an unasked-for PR body edit by the third.
    """

    def test_a_run_that_opens_the_gate_leaves_it_shut(self):
        core.publishing.call_entry_point(
            "fake_entry_points_support:opens_the_gate", [])
        assert core.publishing.enabled() is False

    def test_the_next_run_does_not_inherit_the_open_gate(self):
        """The failure this exists to catch, stated as two runs in a row."""
        core.publishing.call_entry_point(
            "fake_entry_points_support:opens_the_gate", [])
        seen = core.publishing.call_entry_point(
            "fake_entry_points_support:reports_the_gate", [])
        assert seen == 0, "the second run saw a gate the first one opened"

    def test_a_run_nested_in_an_open_one_restores_rather_than_closes(self):
        """Restores the previous value, so an outer --post survives its child."""
        core.publishing.enable()
        core.publishing.call_entry_point("fake_entry_points_support:reports_the_gate", [])
        assert core.publishing.enabled() is True

    def test_a_hold_is_not_restored_when_the_run_ends(self):
        """A hold outlives the run that reached it, unlike an enable.

        The asymmetry is the point: an enable is an instruction the caller
        gave, a hold is something the run *learned*, and an outer pass must
        not resume publishing because an inner one finished.
        """
        core.publishing.enable()
        core.publishing.call_entry_point("fake_entry_points_support:holds_the_gate", [])
        assert core.publishing.held() == "a question this run could not answer"
        assert core.publishing.enabled() is False

    def test_the_gate_is_restored_even_when_the_run_raises(self):
        with pytest.raises(RuntimeError):
            core.publishing.call_entry_point(
                "fake_entry_points_support:opens_the_gate_then_raises", [])
        assert core.publishing.enabled() is False


class TestHoweverARunEndsTheCallerGetsAnInt:
    """A spawn turned any ending into a returncode. This is what replaced it.

    The case that matters is `sys.exit(0)`: a review pass ending on a declined
    prompt would otherwise unwind through `pr fix` itself, skipping the CI and
    describe passes and exiting 0 — reporting success for work never done.
    """

    def test_a_returned_code_is_the_code(self):
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:returns_three", []) == 3

    def test_a_run_that_exits_zero_does_not_end_its_caller(self):
        after = []
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:exits_zero", []) == 0
        after.append("reached")
        assert after == ["reached"], "sys.exit(0) unwound past the seam"

    def test_a_run_that_exits_non_zero_reports_that_code(self):
        """`EXIT_SUPERSEDED` arrives this way: `review.preflight` exits 4."""
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:exits_four", []) == 4

    def test_a_bare_exit_is_success(self):
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:exits_bare", []) == 0

    def test_returning_nothing_is_success(self):
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:returns_none", []) == 0

    def test_exiting_with_a_message_prints_it_and_fails(self, capsys):
        rc = core.publishing.call_entry_point(
            "fake_entry_points_support:exits_with_a_message", [])
        assert rc == 1
        assert "could not read the review" in capsys.readouterr().err

    def test_a_keyboard_interrupt_is_left_to_the_entry_point(self):
        """Not caught here: the signal handler reports it once, for the whole
        invocation, and swallowing it would report an interrupt as an exit."""
        with pytest.raises(KeyboardInterrupt):
            core.publishing.call_entry_point(
                "fake_entry_points_support:interrupted", [])

    def test_the_argv_and_kwargs_reach_the_entry_point(self):
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:echoes_argv", ["--self", "--fix"]) == 2
        assert core.publishing.call_entry_point(
            "fake_entry_points_support:requires_a_kwarg", [],
            install_signal_handler=False) == 0


class TestPublishingHold:
    """A hold outranks --post, and nothing reopens it."""

    def test_hold_shuts_a_gate_post_had_opened(self, no_subprocess):
        core.publishing.enable()
        core.publishing.hold("discussion open")
        assert core.publishing.enabled() is False
        assert pr.comments.post_thread_reply("o/r", 1, 99, "body") is False

    def test_enable_after_a_hold_does_not_reopen(self, no_subprocess):
        core.publishing.hold("discussion open")
        core.publishing.enable()
        assert core.publishing.enabled() is False

    def test_the_first_reason_is_the_one_kept(self):
        core.publishing.hold("discussion open")
        core.publishing.hold("something else")
        assert core.publishing.held() == "discussion open"

    def test_no_hold_by_default(self):
        assert core.publishing.held() == ""

    def test_two_runs_in_one_process_do_not_leak_the_gate(self):
        """A hold taken inside one `run` must not outrank the next.

        Proves production `run()` resets both halves, not the `_drafts_only`
        fixture: both runs happen in this test.
        """
        with core.publishing.run(post=True):
            core.publishing.hold("discussion open")
            assert core.publishing.enabled() is False
        assert core.publishing.enabled() is False
        assert core.publishing.held() == ""
        with core.publishing.run(post=True):
            assert core.publishing.enabled() is True

    def test_a_rebase_opened_gate_does_not_authorise_a_describe_run(self):
        """#909 T7 4c: rebase then describe in one process.

        A bare `pr rebase` opens the gate because push is the default. A
        describe that was not given `--post` must not inherit that.
        """
        with core.publishing.run(post=True):
            assert core.publishing.enabled() is True
            with core.publishing.run(post=False):
                assert core.publishing.enabled() is False
            assert core.publishing.enabled() is True
        assert core.publishing.enabled() is False


class TestIssueTrackerGate:
    """A tracking issue is as public as a reply — same gate.

    Deferral issues were filed from an incorrect review claim once; drafting them
    keeps that mistake on this machine.
    """

    def test_issue_is_not_created(self, no_subprocess):
        created = review.issue.create_issue(
            "linear", "ENG", "title", "description",
        )
        assert created.filed is False
        assert created.issue == review.issue.CreatedIssue()

    def test_a_declined_write_is_not_a_failed_one(self, no_subprocess):
        """The gate declining a write owes nothing — a refused tracker does."""
        created = review.issue.create_issue(
            "linear", "ENG", "title", "description",
        )
        assert created.delivery is review.issue.IssueDelivery.SKIPPED
        assert created.owed is False

    def test_issue_is_not_updated(self, no_subprocess):
        assert review.issue.update_issue("linear", "ENG-1", "description") is False

    def test_draft_names_the_provider_and_title(self, no_subprocess, capsys):
        review.issue.create_issue("linear", "ENG", "the issue title", "body")
        assert "the issue title" in capsys.readouterr().err
