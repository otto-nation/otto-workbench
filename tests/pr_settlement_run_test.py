"""pr.settlement: --settle, its targets, and resolving fixed threads."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _fix, _make_state, _no_published_summary  # noqa: E402
from conftest import git_in, git_out, make_ctx, run_checked
import pr.state
from pr.comments_state import ThreadState
import git.topology
import pr.comments_fix
import pr.attribution
import pr.settlement
from pr.summary_model import ActionCell
from pr.fix import FixOutcome, FixRecord, ItemOutcome, SETTLED_REASON
from pr.thread_models import CommentItem, ReportThread


# ── --settle ──────────────────────────────────────────────────────────────


def _hand_fixed(tmp_path, *, pushed=True):
    """A `feature` branch whose one commit changed line 1 of `a.py` by hand.

    Real git rather than a stub, because settle-time attribution is exactly the
    pair of questions no stub can stand in for: which commit changed this line,
    and does the remote have it. The second only has an honest answer when there
    is a remote to ask, which is why the origin is a real bare repo.
    """
    origin = tmp_path / "origin"
    run_checked(["git", "init", "--bare", "-q", "-b", "main", str(origin)])
    work = tmp_path / "work"
    run_checked(["git", "clone", "-q", str(origin), str(work)])
    git_in(work, "config", "user.email", "t@example.com")
    git_in(work, "config", "user.name", "Test")
    (work / "a.py").write_text("one\ntwo\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "base")
    git_in(work, "push", "-q", "-u", "origin", "main")
    git_in(work, "checkout", "-q", "-b", "feature")
    (work / "a.py").write_text("ONE\ntwo\n")
    git_in(work, "commit", "-q", "--no-verify", "-am", "fix line one by hand")
    if pushed:
        git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, sha=git_out(work, "rev-parse", "HEAD").strip())


class TestSettleFlagValidation:
    """A flag the recorded outcome will never read is refused, not ignored."""

    def test_a_dismissal_needs_its_reason(self):
        assert "--reason" in pr.settlement.settle_flag_error(FixOutcome.DISMISSED, "", "")

    def test_a_dismissal_that_gives_the_reviewer_something_to_answer_passes(self):
        assert pr.settlement.settle_flag_error(FixOutcome.DISMISSED, "not our layer", "") == ""

    @pytest.mark.parametrize("kind", [FixOutcome.FIXED, FixOutcome.ALREADY_ADDRESSED])
    def test_a_reason_no_reply_renders_is_refused(self, kind):
        assert "--reason is only read" in pr.settlement.settle_flag_error(kind, "because", "")

    @pytest.mark.parametrize("kind,reason", [
        (FixOutcome.DISMISSED, "not our layer"),
        (FixOutcome.ALREADY_ADDRESSED, ""),
    ])
    def test_a_commit_no_row_cites_is_refused(self, kind, reason):
        assert "--commit is only read" in pr.settlement.settle_flag_error(kind, reason, "abc1234")

    def test_a_fix_may_name_the_commit_that_carries_it(self):
        assert pr.settlement.settle_flag_error(FixOutcome.FIXED, "", "abc1234") == ""


class TestSettleTargets:
    """Every id is checked before any outcome is written."""

    def _record(self):
        return FixRecord(items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN),
            ItemOutcome(id="t2", outcome=FixOutcome.FIXED),
            ItemOutcome(id="t3", outcome=FixOutcome.DEFERRED),
        ])

    def test_resolves_the_named_outcomes(self):
        picked = pr.settlement.settle_targets(self._record(), ["t3", "t1"])
        assert [o.id for o in picked] == ["t3", "t1"]

    def test_one_unknown_id_settles_none_of_them(self, capsys):
        """"Settled nothing" and "settled the thread you meant" read alike."""
        assert pr.settlement.settle_targets(self._record(), ["t1", "typo"]) is None
        assert "typo" in capsys.readouterr().err

    def test_the_error_names_the_threads_still_waiting_on_a_person(self, capsys):
        pr.settlement.settle_targets(self._record(), ["typo"])
        err = capsys.readouterr().err
        assert "t1, t3" in err
        assert "t2" not in err

    def test_a_snapshot_with_nothing_left_to_settle_says_so(self, capsys):
        record = FixRecord(items=[ItemOutcome(id="t2", outcome=FixOutcome.FIXED)])
        assert pr.settlement.settle_targets(record, ["typo"]) is None
        assert "No thread in the fix snapshot is waiting" in capsys.readouterr().err


class TestRecordSettlement:

    def _outcome(self):
        return ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN,
                           reason="too complex to auto-fix")

    def test_a_dismissal_carries_the_operators_own_words(self):
        """Its reply is the one a reviewer may argue with, so it is theirs to write."""
        outcome = self._outcome()
        assert pr.settlement.record_settlement(outcome, FixOutcome.DISMISSED, "not our layer", "")
        assert outcome.outcome is FixOutcome.DISMISSED
        assert outcome.reason == "not our layer"

    def test_a_fix_records_where_the_settlement_came_from(self):
        outcome = self._outcome()
        assert pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert outcome.reason == SETTLED_REASON
        assert outcome.commit_sha == "abc1234"

    def test_saying_the_same_thing_twice_is_a_no_op(self):
        outcome = self._outcome()
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234") is False

    def test_a_commit_that_has_since_become_resolvable_is_a_change(self):
        """Reporting it as a no-op would leave a row uncited that can now cite."""
        outcome = self._outcome()
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "")
        assert pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")

    def test_an_earlier_attribution_survives_a_re_settle_that_found_none(self):
        outcome = self._outcome()
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "")
        assert outcome.commit_sha == "abc1234"

    @pytest.mark.parametrize("kind,reason", [
        (FixOutcome.DISMISSED, "not our layer"),
        (FixOutcome.ALREADY_ADDRESSED, ""),
    ])
    def test_an_ending_that_cites_no_commit_drops_the_one_it_replaced(
        self, kind, reason,
    ):
        """"Dismissed, fixed in abc1234" is not a state the operator can have meant."""
        outcome = self._outcome()
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert pr.settlement.record_settlement(outcome, kind, reason, "")
        assert outcome.commit_sha == ""


class TestResolveSettledCommit:
    """Which commit a hand-landed fix may cite, asked while the answer exists."""

    def _outcome(self, line=1):
        return ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN,
                           file="a.py", line=line)

    def test_infers_the_commit_that_changed_the_threads_own_line(self, tmp_path):
        repo = _hand_fixed(tmp_path)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(), "")
        assert resolved.ok
        assert resolved.sha == repo.sha[:7]

    def test_an_unpushed_fix_is_still_recorded_but_cites_nothing(self, tmp_path):
        """A link into a commit the remote never saw is a 404 for the reviewer."""
        repo = _hand_fixed(tmp_path, pushed=False)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(), "")
        assert resolved.ok
        assert resolved.sha == ""

    def test_a_thread_with_no_line_cites_nothing_and_is_no_error(self, tmp_path):
        repo = _hand_fixed(tmp_path)
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(line=0), "")
        assert resolved.ok
        assert resolved.sha == ""

    def test_a_commit_this_worktree_does_not_have_stops_the_run(self, tmp_path):
        repo = _hand_fixed(tmp_path)
        resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(), "nosuchref")
        assert not resolved.ok
        assert "nosuchref" in resolved.error

    def test_an_unpushed_commit_the_operator_named_stops_the_run(self, tmp_path):
        """They asked for this citation, so declining it quietly is the wrong answer."""
        repo = _hand_fixed(tmp_path, pushed=False)
        resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(), repo.sha)
        assert not resolved.ok
        assert "404" in resolved.error

    def test_the_named_commit_is_taken_over_the_inferred_one(self, tmp_path):
        """The point of --commit: a fix that landed away from the anchored line."""
        repo = _hand_fixed(tmp_path)
        with patch.object(pr.attribution, "find_addressing_commit") as infer:
            resolved = pr.settlement.resolve_settled_commit(repo.path, self._outcome(), "HEAD")
        infer.assert_not_called()
        assert resolved.sha == repo.sha[:7]


class TestRunSettle:
    """The ending the fix pass cannot see, told to the CLI rather than to state.json."""

    def _ctx(self, tmp_path):
        return make_ctx(branch="feature", worktree_root=tmp_path / "wt",
                        head_sha="abc1234", target_dir=tmp_path / "target")

    @staticmethod
    def _needs_human(tid="t1"):
        return ItemOutcome(id=tid, outcome=FixOutcome.NEEDS_HUMAN,
                           summary="contested", reason="too complex to auto-fix",
                           file="a.py", line=1)

    def _save(self, ctx, *items):
        pr.state.save_state(ctx.target_dir, _make_state(_fix(list(items))))

    def _reload(self, ctx):
        return pr.state.load_state(ctx.target_dir).fix

    def _resolves_to(self, sha):
        return patch.object(pr.settlement, "resolve_settled_commit",
                            return_value=pr.settlement.SettledCommit(sha=sha))

    def test_a_settled_thread_rejoins_the_ordinary_closeout(self, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        assert pr.settlement.run_settle(ctx, ["t1"], "dismissed", "not our layer", "") == 0
        fix = self._reload(ctx)
        assert fix.fix.items[0].outcome is FixOutcome.DISMISSED
        assert fix.fix.items[0].reason == "not our layer"
        # Both gates --finish reads before it does anything, and both of what
        # puts `⚠ closeout owed` back on `pr status`.
        assert fix.replies_pending
        assert fix.summary_deferred

    def test_a_settled_fix_is_attributed_to_the_commit_carrying_it(self, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with self._resolves_to("abc1234"):
            assert pr.settlement.run_settle(ctx, ["t1"], "fixed", "", "") == 0
        outcome = self._reload(ctx).fix.items[0]
        assert outcome.outcome is FixOutcome.FIXED
        assert outcome.commit_sha == "abc1234"
        assert outcome.reason == SETTLED_REASON

    def test_it_publishes_nothing_and_names_the_step_that_does(self, tmp_path, capsys):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with patch("core.proc.subprocess.run") as run:
            assert pr.settlement.run_settle(ctx, ["t1"], "already_addressed", "", "") == 0
        run.assert_not_called()
        assert pr.comments_fix.CLOSEOUT_COMMAND in capsys.readouterr().err

    def test_a_dismissal_with_no_reason_writes_nothing(self, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        assert pr.settlement.run_settle(ctx, ["t1"], "dismissed", "", "") == 1
        assert self._reload(ctx).fix.items[0].outcome is FixOutcome.NEEDS_HUMAN

    def test_no_fix_snapshot_names_the_pass_that_makes_one(self, tmp_path, capsys):
        assert pr.settlement.run_settle(self._ctx(tmp_path), ["t1"], "fixed", "", "") == 1
        assert "pr comments --fix" in capsys.readouterr().err

    def test_an_unknown_id_leaves_every_other_thread_alone(self, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t1"), self._needs_human("t2"))
        with self._resolves_to("abc1234"):
            assert pr.settlement.run_settle(ctx, ["t1", "typo"], "fixed", "", "") == 1
        assert [o.outcome for o in self._reload(ctx).fix.items] == \
            [FixOutcome.NEEDS_HUMAN] * 2

    def test_an_unresolvable_commit_discards_the_whole_run(self, tmp_path):
        """Half a run recorded is the state surgery this command exists to replace."""
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t1"), self._needs_human("t2"))
        answers = [pr.settlement.SettledCommit(sha="abc1234"),
                   pr.settlement.SettledCommit(error="--commit names no commit")]
        with patch.object(pr.settlement, "resolve_settled_commit", side_effect=answers):
            assert pr.settlement.run_settle(ctx, ["t1", "t2"], "fixed", "", "") == 1
        assert [o.outcome for o in self._reload(ctx).fix.items] == \
            [FixOutcome.NEEDS_HUMAN] * 2

    def test_recording_the_same_settlement_twice_rewrites_nothing(
        self, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        pr.settlement.run_settle(ctx, ["t1"], "dismissed", "not our layer", "")
        state_file = ctx.target_dir / pr.state.STATE_FILE
        before = state_file.read_text()
        capsys.readouterr()
        assert pr.settlement.run_settle(ctx, ["t1"], "dismissed", "not our layer", "") == 0
        assert "already recorded as dismissed" in capsys.readouterr().err
        assert state_file.read_text() == before

    def test_a_different_ending_replaces_the_first_and_says_which(
        self, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        pr.settlement.run_settle(ctx, ["t1"], "dismissed", "not our layer", "")
        capsys.readouterr()
        with self._resolves_to("abc1234"):
            assert pr.settlement.run_settle(ctx, ["t1"], "fixed", "", "") == 0
        assert "(was dismissed)" in capsys.readouterr().err
        assert self._reload(ctx).fix.items[0].outcome is FixOutcome.FIXED

    def test_a_fix_with_no_pushed_commit_says_how_its_row_will_read(
        self, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with self._resolves_to(""):
            assert pr.settlement.run_settle(ctx, ["t1"], "fixed", "", "") == 0
        err = capsys.readouterr().err
        assert ActionCell.RECONCILED in err
        assert "--commit" in err


# ── settlement.resolve_fixed_threads ──────────────────────────────────────


class TestResolveFixedThreads:

    def test_resolves_unresolved_threads(self):
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW, is_resolved=False),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED, is_resolved=False),
        }
        with patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
            resolved = pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == [ThreadState.NEW, ThreadState.ADDRESSED]
        assert mock_resolve.call_count == 2

    def test_skips_already_resolved(self):
        fixed = [CommentItem(id="t1")]
        threads_by_id = {"t1": ReportThread(id="t1", is_resolved=True)}
        with patch("pr.comments.resolve_thread") as mock_resolve:
            resolved = pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == []
        mock_resolve.assert_not_called()

    def test_skips_an_entry_absent_from_threads_by_id(self):
        """A synthetic comment id (ic-…/rb-…) is not a resolvable review thread.

        Regression: these used to fall through to an unconditional
        `resolve_thread`, spending a GraphQL mutation per comment item on an id
        the API cannot resolve. It failed silently, so nothing surfaced it.
        """
        fixed = [CommentItem(id="ic-123")]
        with patch("pr.comments.resolve_thread") as mock_resolve:
            resolved = pr.settlement.resolve_fixed_threads(fixed, {})
        assert resolved == []
        mock_resolve.assert_not_called()

    def test_reports_only_successful_resolves(self):
        """The buckets feed the tally, so a refused mutation must not appear.

        A drafted run refuses every one of them, which is how a run that
        published nothing is kept from moving the counts.
        """
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED),
        }
        with patch("pr.comments.resolve_thread", side_effect=[True, False]):
            resolved = pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == [ThreadState.NEW]

    def test_a_second_pass_over_the_same_threads_moves_nothing(self):
        """The buckets feed the tally, so resolving twice must not count twice.

        A combined --fix --finish run whose commit was held resolves the
        already-addressed bucket in the fix pass and then drains that same
        bucket in the closeout, both off the report's thread objects. Nothing
        re-fetches in between, so only the write-back marks them resolved.
        """
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED),
        }
        with patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
            first = pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
            second = pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
        assert first == [ThreadState.NEW, ThreadState.ADDRESSED]
        assert second == []
        assert mock_resolve.call_count == 2

    def test_a_refused_resolve_stays_open_for_the_next_pass(self):
        """Only a mutation that landed marks the thread resolved.

        A drafted run refuses every one of them, and the closeout that follows
        with --post has to find the bucket still owed.
        """
        fixed = [CommentItem(id="t1")]
        threads_by_id = {"t1": ReportThread(id="t1", state=ThreadState.NEW)}
        with patch("pr.comments.resolve_thread", return_value=False):
            pr.settlement.resolve_fixed_threads(fixed, threads_by_id)
        assert threads_by_id["t1"].is_resolved is False
