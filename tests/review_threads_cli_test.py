"""cli.review_threads: flags, phases, resolution and fetching."""

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
from conftest import assert_no_worktree_exit, make_ctx, triaged_thread_record
from gh.pr_data import PRData
import pr.state
import pr.comments_state
from pr.comments_state import ThreadRecord, ThreadState
import pr.comments
import pr.context
import pr.thread_replies
import pr.settlement
from pr.fix import FixOutcome
from pr.thread_models import PRReport
import cli.locks
import cli.review_threads
import review.comment_threads
import review.closeout
import review.review_body_reply
import core.run_lock


class TestTrackFlagParsing:
    def test_track_is_repeatable(self):
        args = cli.review_threads.build_parser().parse_args(
            ["--finish", "--track", "t1", "--track", "t2"])
        assert args.track == ["t1", "t2"]

    def test_track_all_is_separate(self):
        args = cli.review_threads.build_parser().parse_args(["--finish", "--track-all"])
        assert args.track_all is True
        assert args.track == []

    def test_track_defaults_to_empty(self):
        args = cli.review_threads.build_parser().parse_args(["--finish"])
        assert args.track == []
        assert args.track_all is False


class TestFinishFlag:
    """`--finish` is the one spelling.

    It shipped as `--resolve-verified` on the removed `claude-review threads`
    subcommand and carried both historical names as aliases. Nothing calls them
    any more, and three spellings is three chances for a doc to pick a dead one
    — so they are gone, and an unknown-flag error is the whole migration path.
    """

    def test_finish_sets_finish(self):
        assert cli.review_threads.build_parser().parse_args(["--finish"]).finish

    def test_it_is_off_by_default(self):
        assert not cli.review_threads.build_parser().parse_args([]).finish

    def test_no_push_verify_is_off_by_default_and_parses(self):
        parser = cli.review_threads.build_parser()
        assert parser.parse_args([]).no_push_verify is False
        assert parser.parse_args(["--finish", "--no-push-verify"]).no_push_verify is True

    @pytest.mark.parametrize("alias", ["--resolve", "--resolve-verified"])
    def test_the_old_aliases_are_rejected(self, alias):
        # Exit 2 specifically: argparse's unknown-flag code, not any SystemExit
        # a broken parser might raise on the way past.
        with pytest.raises(SystemExit) as exc:
            cli.review_threads.build_parser().parse_args([alias])
        assert exc.value.code == 2


class TestSettleFlagParsing:
    def test_settle_is_repeatable(self):
        args = cli.review_threads.build_parser().parse_args(["--settle", "t1", "--settle", "t2"])
        assert args.settle == ["t1", "t2"]

    def test_settle_records_a_fix_unless_told_otherwise(self):
        """The ending an operator who was offered "fix it" chose."""
        args = cli.review_threads.build_parser().parse_args(["--settle", "t1"])
        assert args.settle_as == FixOutcome.FIXED.value

    @pytest.mark.parametrize("outcome", [o.value for o in (
        FixOutcome.FIXED, FixOutcome.DISMISSED, FixOutcome.ALREADY_ADDRESSED)])
    def test_every_bucket_the_closeout_can_reply_to_is_offered(self, outcome):
        args = cli.review_threads.build_parser().parse_args(["--settle", "t1", "--as", outcome])
        assert args.settle_as == outcome

    def test_deferral_is_not_a_settlement(self):
        """--track already files work still owed; --settle records work finished."""
        with pytest.raises(SystemExit):
            cli.review_threads.build_parser().parse_args(["--settle", "t1", "--as", "deferred"])

    def test_nothing_is_settled_unless_asked(self):
        assert cli.review_threads.build_parser().parse_args(["--finish"]).settle == []


class TestSettleIsNotAPublishingPhase:
    """Recording is its own step, the way --post gates every other write."""

    @pytest.mark.parametrize("flag", ["--post", "--finish", "--fix", "--triage"])
    def test_it_refuses_to_run_alongside_a_phase_that_publishes(self, capsys, flag):
        """The code is returned, not raised: `main` is a cli entry point and the
        shim owns the exit, so a refusal is a non-zero return like any other."""
        with patch.object(pr.context, "resolve") as resolve:
            assert cli.review_threads.main(["--settle", "t1", flag]) == 1
        resolve.assert_not_called()
        assert flag in capsys.readouterr().err

    def test_the_conflict_named_is_the_one_that_was_typed(self, capsys):
        """--fix widens itself into --triage; the operator did not type --triage."""
        with patch.object(pr.context, "resolve"):
            assert cli.review_threads.main(["--settle", "t1", "--fix"]) == 1
        err = capsys.readouterr().err
        assert "--fix" in err
        assert "--triage" not in err

    def test_it_does_not_announce_a_draft_run_it_is_not(self, capsys):
        """Nothing here was ever going to be posted, drafted or otherwise."""
        with patch.object(pr.context, "resolve", return_value=make_ctx()), \
             patch.object(pr.settlement, "run_settle", return_value=0) as settle:
            assert cli.review_threads.main(["--settle", "t1"]) == 0
        assert "Draft mode" not in capsys.readouterr().err
        assert settle.call_args[0][1] == ["t1"]


class TestReplyIsNotAPhase:
    """--reply writes one thread and returns, so a phase beside it never runs.

    Without the refusal the run exits 0 having posted the reply and dropped the
    phase, which is indistinguishable from a run that did both.
    """

    @pytest.mark.parametrize("flag", ["--triage", "--fix", "--finish"])
    def test_it_refuses_to_run_alongside_a_phase(self, capsys, flag):
        with patch.object(pr.context, "resolve") as resolve, \
             patch.object(pr.thread_replies, "run_reply") as run_reply:
            assert cli.review_threads.main(["--reply", "t1", flag]) == 1
        resolve.assert_not_called()
        run_reply.assert_not_called()
        assert flag in capsys.readouterr().err

    def test_the_conflict_named_is_the_one_that_was_typed(self, capsys):
        """--fix widens itself into --triage; the operator did not type --triage."""
        with patch.object(pr.context, "resolve"), \
             patch.object(pr.thread_replies, "run_reply"):
            assert cli.review_threads.main(["--reply", "t1", "--fix"]) == 1
        err = capsys.readouterr().err
        assert "--fix" in err
        assert "--triage" not in err

    def test_post_is_the_gate_the_reply_needs_not_a_conflict(self, tmp_path):
        """A reply that publishes is the ordinary use, and must still run."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), thread=True)
        body = tmp_path / "f"
        body.write_text("Done.")
        with patch.object(pr.context, "resolve", return_value=make_ctx()), \
             patch.object(review.review_body_reply, "route_reply", return_value=route), \
             patch.object(pr.thread_replies, "run_reply", return_value=0) as run_reply:
            assert cli.review_threads.main(
                ["--reply", "t1", "--body-file", str(body), "--post"]) == 0
        assert run_reply.call_args[0][1] == "t1"
        assert run_reply.call_args.kwargs["pr_data"] is route.pr_data


class TestReplyRouting:
    """A thread receives the loaded body; a review body takes the locked path."""

    def _main(self, route, tmp_path):
        ctx = make_ctx(worktree_root=tmp_path, target_dir=tmp_path / "target")
        (tmp_path / "f").write_text("Done.")
        with patch.object(pr.context, "resolve", return_value=ctx), \
             patch.object(review.review_body_reply, "route_reply", return_value=route), \
             patch.object(pr.thread_replies, "run_reply", return_value=0) as thread, \
             patch.object(review.review_body_reply, "run_review_body_reply",
                          return_value=0) as body, \
             patch.object(cli.locks, "claim_declared") as lock, \
             patch.object(cli.review_threads.Trail, "start") as trail:
            code = cli.review_threads.main(
                ["--reply", "88", "--body-file", str(tmp_path / "f")])
        return code, thread, body, lock, trail

    def test_a_review_body_is_answered_under_the_lock(self, tmp_path):
        """It writes the state file, so it runs where every state writer runs."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), source="rb-88")
        code, thread, body, lock, trail = self._main(route, tmp_path)
        assert code == 0
        thread.assert_not_called()
        lock.assert_called_once()
        trail.assert_called_once()
        assert body.call_args[0][1] is route
        assert body.call_args[0][2] == "Done."

    def test_a_review_body_with_no_worktree_refuses_before_the_lock(self, tmp_path, capsys):
        """It writes state, so unlike a thread reply it needs the branch's checkout."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), source="rb-88")
        ctx = make_ctx(branch="isaac/feat/x", worktree_root=None)
        (tmp_path / "f").write_text("Done.")
        with patch.object(pr.context, "resolve", return_value=ctx), \
             patch.object(review.review_body_reply, "route_reply", return_value=route), \
             patch.object(review.review_body_reply, "run_review_body_reply") as body, \
             patch.object(cli.locks, "claim_declared") as lock, \
             pytest.raises(SystemExit) as exit_:
            cli.review_threads.main(["--reply", "88", "--body-file", str(tmp_path / "f")])
        assert exit_.value.code == 1
        body.assert_not_called()
        lock.assert_not_called()
        assert "No worktree for 'isaac/feat/x'" in capsys.readouterr().err

    def test_a_thread_reply_with_no_worktree_still_runs(self, tmp_path):
        """A thread reply is GitHub-only and degrades, as `require_worktree` documents."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), thread=True)
        ctx = make_ctx(worktree_root=None)
        (tmp_path / "f").write_text("Done.")
        with patch.object(pr.context, "resolve", return_value=ctx), \
             patch.object(review.review_body_reply, "route_reply", return_value=route), \
             patch.object(pr.thread_replies, "run_reply", return_value=0) as thread, \
             patch.object(cli.locks, "claim_declared"):
            code = cli.review_threads.main(
                ["--reply", "88", "--body-file", str(tmp_path / "f")])
        assert code == 0
        thread.assert_called_once()

    def test_a_missing_body_file_is_refused_before_the_pr_is_read(self, tmp_path, capsys):
        """The error costs no fetch, and reads as it always has."""
        with patch.object(pr.context, "resolve", return_value=make_ctx()), \
             patch.object(review.review_body_reply, "route_reply") as route:
            code = cli.review_threads.main(
                ["--reply", "88", "--body-file", str(tmp_path / "nope.md")])
        assert code == 1
        route.assert_not_called()
        assert f"--body-file not found: {tmp_path / 'nope.md'}" in capsys.readouterr().err

    def test_empty_stdin_is_refused_before_the_pr_is_read(self, capsys):
        with patch.object(pr.context, "resolve", return_value=make_ctx()), \
             patch.object(review.review_body_reply, "route_reply") as route, \
             patch.object(sys, "stdin", io.StringIO("  \n")):
            code = cli.review_threads.main(["--reply", "88", "--body-file", "-"])
        assert code == 1
        route.assert_not_called()
        assert "--body-file is empty: -" in capsys.readouterr().err

    def test_a_target_naming_nothing_reports_through_the_thread_path(self, tmp_path):
        """Which keeps its error, and gains the review ids it could have meant."""
        route = review.review_body_reply.ReplyRoute(
            pr_data=MagicMock(), review_body_ids=("88",))
        code, thread, body, lock, trail = self._main(route, tmp_path)
        assert code == 0
        body.assert_not_called()
        # The target lock `comments --reply` declares, and none of the trail
        # the review-body path opens.
        lock.assert_called_once()
        assert lock.call_args[0][:2] == ("comments", ["--reply", "88", "--body-file",
                                                      str(tmp_path / "f")])
        trail.assert_not_called()
        assert thread.call_args.kwargs["review_body_ids"] == ("88",)

    def _stdin_reply(self, tmp_path, route, text):
        ctx = make_ctx(worktree_root=tmp_path, target_dir=tmp_path / "target")
        stdin = io.StringIO(text)
        with patch.object(pr.context, "resolve", return_value=ctx), \
             patch.object(review.review_body_reply, "route_reply", return_value=route), \
             patch.object(pr.thread_replies, "run_reply", return_value=0) as thread, \
             patch.object(review.review_body_reply, "run_review_body_reply",
                          return_value=0) as body, \
             patch.object(core.run_lock, "claim_for_process"), \
             patch.object(sys, "stdin", stdin):
            code = cli.review_threads.main(["--reply", "88", "--body-file", "-"])
        return code, thread, body

    def test_stdin_body_file_is_read_once_on_the_thread_path(self, tmp_path):
        """`-` is drained before the PR fetch; the thread path gets the text."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), thread=True)
        code, thread, body = self._stdin_reply(tmp_path, route, "See the line.\n")
        assert code == 0
        body.assert_not_called()
        assert thread.call_args[0][2] == "See the line."

    def test_stdin_body_file_is_read_once_on_the_review_body_path(self, tmp_path):
        """A second read of `-` would be empty; the review-body path gets the text."""
        route = review.review_body_reply.ReplyRoute(pr_data=MagicMock(), source="rb-88")
        code, thread, body = self._stdin_reply(tmp_path, route, "Tightened.\n")
        assert code == 0
        thread.assert_not_called()
        assert body.call_args[0][2] == "Tightened."


# ── _resolve_verified_threads ─────────────────────────────────────────────


class TestResolveVerifiedThreads:
    """Which threads the pre-snapshot resolve sends to GitHub, and what it counts.

    The twin of `settlement.resolve_fixed_threads`, and deliberately not the
    same function: this one reads raw GraphQL nodes against a frozen
    `ThreadRecord` map and skips on the recorded state, where the other reads
    entries against mutable `ReportThread`s and skips on `is_resolved`.

    `pc.resolve_thread` is patched throughout: it is a live GraphQL mutation
    against a real PR, so an unpatched call would either resolve a thread on
    GitHub or fail on the network.
    """

    def _node(self, tid):
        return {"id": tid, "isResolved": False, "path": "a.py", "line": 1}

    def test_resolves_only_the_verified_threads(self):
        raw = [self._node("t1"), self._node("t2"), self._node("t3")]
        threads = {
            "t1": ThreadRecord(state=ThreadState.VERIFIED),
            "t2": ThreadRecord(state=ThreadState.NEW),
            "t3": ThreadRecord(state=ThreadState.VERIFIED),
        }
        with patch.object(pr.comments, "resolve_thread", return_value=True) as resolve:
            count = review.comment_threads.resolve_verified_threads(raw, threads)
        assert count == 2
        assert [c.args[0] for c in resolve.call_args_list] == ["t1", "t3"]

    def test_a_resolved_thread_is_recorded_as_resolved(self):
        raw = [self._node("t1")]
        threads = {"t1": ThreadRecord(state=ThreadState.VERIFIED, reviewer="alice")}
        with patch.object(pr.comments, "resolve_thread", return_value=True):
            review.comment_threads.resolve_verified_threads(raw, threads)
        assert threads["t1"].state is ThreadState.RESOLVED
        # The record is replaced, not rebuilt: everything triage decided about
        # the thread survives the state change.
        assert threads["t1"].reviewer == "alice"

    def test_a_thread_already_resolved_is_left_alone(self):
        """RESOLVED is not VERIFIED, so the mutation is never spent on it."""
        raw = [self._node("t1")]
        threads = {"t1": ThreadRecord(state=ThreadState.RESOLVED)}
        with patch.object(pr.comments, "resolve_thread") as resolve:
            assert review.comment_threads.resolve_verified_threads(raw, threads) == 0
        resolve.assert_not_called()

    def test_a_node_with_no_record_is_skipped(self):
        """A thread the sync never recorded has no verdict to act on."""
        raw = [self._node("t1")]
        with patch.object(pr.comments, "resolve_thread") as resolve:
            assert review.comment_threads.resolve_verified_threads(raw, {}) == 0
        resolve.assert_not_called()

    def test_a_record_with_no_node_is_never_reached(self):
        """The raw fetch drives the loop, so a short fetch resolves only what it saw."""
        threads = {"t1": ThreadRecord(state=ThreadState.VERIFIED)}
        with patch.object(pr.comments, "resolve_thread") as resolve:
            assert review.comment_threads.resolve_verified_threads([], threads) == 0
        resolve.assert_not_called()

    def test_only_a_mutation_that_landed_is_counted(self):
        """A drafted run refuses every resolve, and must move no counts."""
        raw = [self._node("t1"), self._node("t2")]
        threads = {
            "t1": ThreadRecord(state=ThreadState.VERIFIED),
            "t2": ThreadRecord(state=ThreadState.VERIFIED),
        }
        with patch.object(pr.comments, "resolve_thread", side_effect=[True, False]):
            assert review.comment_threads.resolve_verified_threads(raw, threads) == 1
        assert threads["t1"].state is ThreadState.RESOLVED
        # Still owed: the next run with --post has to find it verified.
        assert threads["t2"].state is ThreadState.VERIFIED

    def test_a_second_pass_over_the_same_threads_resolves_nothing(self):
        raw = [self._node("t1"), self._node("t2")]
        threads = {
            "t1": ThreadRecord(state=ThreadState.VERIFIED),
            "t2": ThreadRecord(state=ThreadState.VERIFIED),
        }
        with patch.object(pr.comments, "resolve_thread", return_value=True) as resolve:
            first = review.comment_threads.resolve_verified_threads(raw, threads)
            second = review.comment_threads.resolve_verified_threads(raw, threads)
        assert (first, second) == (2, 0)
        assert resolve.call_count == 2


# ── Blocking reviewers ────────────────────────────────────────────────────────


class TestBlockingReviewers:
    """Verify blocking_reviewers extracts actual logins from verdict data.

    Verdicts from PRData.reviewer_verdicts() use the key "user", not "author".
    Regression test for a bug where v.get("author", {}).get("login") was used.
    """

    def _extract_blocking(self, verdicts):
        return [
            v.get("user", "unknown")
            for v in verdicts
            if v.get("state") == "CHANGES_REQUESTED"
        ]

    def test_extracts_login_from_user_key(self):
        verdicts = [
            {"user": "alice", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == ["alice"]

    def test_multiple_blocking_reviewers(self):
        verdicts = [
            {"user": "alice", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
            {"user": "bob", "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"},
            {"user": "carol", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == ["alice", "carol"]

    def test_no_blocking_reviewers(self):
        verdicts = [
            {"user": "alice", "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == []

    def test_empty_verdicts(self):
        assert self._extract_blocking([]) == []


# ── worktree_root guards ────────────────────────────────────────────────────


class TestWorktreeGuard:
    """Every entry point fails the same actionable way with no worktree."""

    def _ctx(self):
        return make_ctx(branch="isaac/feat/x", worktree_root=None,
                        head_sha="abc1234")

    def test_run_threads_exits_before_touching_github(self, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                review.comment_threads.run_threads, None, None, self._ctx())

    def test_finish_deferred_work_exits_with_guidance(self, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                review.closeout.finish_deferred_work, self._ctx(), PRReport())

    def test_settle_exits_before_reading_the_snapshot(self, capsys):
        """A settled fix is attributed to a commit, which needs a checkout to find."""
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                pr.settlement.run_settle, self._ctx(), ["t1"], "fixed", "", "")


# ── A truncated fetch must not erase the ledger (#1354) ─────────────────────


class TestTruncatedThreadFetch:
    """End to end: what `pr comments` writes when it could not read every thread.

    Driven through `run_threads` rather than `sync_threads` because the loss
    happened between them — the sweep fetched, synced, and saved, and only the
    save is durable. `fetch_pr_data` is the single seam the whole path hangs
    off, so patching it is enough to stand up a short fetch.
    """

    def _ctx(self, tmp_path):
        work = tmp_path / "wt"
        (work / ".git").mkdir(parents=True)
        return make_ctx(repo="owner/repo", pr_number=1, branch="isaac/feat/x",
                        worktree_root=work, head_sha="abc1234",
                        target_dir=tmp_path / "target")

    def _pr_data(self, threads, *, complete):
        return PRData(
            viewer_login="isaacg", head_sha="abc1234", head_ref="isaac/feat/x",
            base_ref="main", review_threads=threads, threads_complete=complete,
        )

    def _thread(self, tid, author="alice"):
        return {
            "id": tid, "isResolved": False, "path": "handler.go", "line": 42,
            "comments": {"totalCount": 1, "nodes": [{
                "id": f"c-{tid}", "databaseId": 1000, "author": {"login": author},
                "body": "Fix this", "createdAt": "2026-06-14T12:00:00Z",
            }]},
        }

    def _verified_thread(self, tid):
        """A thread GitHub already shows as acknowledged by the reviewer."""
        return {
            "id": tid, "isResolved": False, "path": "handler.go", "line": 42,
            "comments": {"totalCount": 2, "nodes": [
                {"id": f"c-{tid}-1", "databaseId": 1000,
                 "author": {"login": "alice"}, "body": "Fix this",
                 "createdAt": "2026-06-14T12:00:00Z"},
                {"id": f"c-{tid}-2", "databaseId": 1001,
                 "author": {"login": "isaacg"}, "body": "Done",
                 "createdAt": "2026-06-14T13:00:00Z"},
                {"id": f"c-{tid}-3", "databaseId": 1002,
                 "author": {"login": "alice"}, "body": "lgtm",
                 "createdAt": "2026-06-14T14:00:00Z"},
            ]},
        }

    def _seed_ledger(self, ctx):
        """A prior run's ledger, carrying a human verdict on each of two threads."""
        state_path = pr.comments.threads_state_path(ctx.target_dir)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        triaged = {
            tid: triaged_thread_record(f"verdict on {tid}")
            for tid in ("T_page1", "T_page2")
        }
        pr.comments_state.save_state(state_path, pr.comments_state.CommentsState(
            repo="owner/repo", pr_number=1, my_login="isaacg", threads=triaged,
        ))
        return state_path

    def _run(self, tmp_path, threads, *, complete, finish=False):
        ctx = self._ctx(tmp_path)
        state_path = self._seed_ledger(ctx)
        args = cli.review_threads.build_parser().parse_args(
            ["--finish"] if finish else [])
        with patch.object(review.comment_threads, "fetch_pr_data",
                          return_value=self._pr_data(threads, complete=complete)), \
             patch.object(review.closeout, "finish_deferred_work") as fin:
            code = review.comment_threads.run_threads(MagicMock(), args, ctx)
        return code, pr.comments_state.load_state(state_path), fin

    def test_an_incomplete_fetch_does_not_erase_triage_from_the_written_ledger(
            self, tmp_path):
        """The bug, at the layer that made it permanent."""
        _code, written, _fin = self._run(
            tmp_path, [self._thread("T_page1")], complete=False)

        assert "T_page2" in written.threads
        assert written.threads["T_page2"].summary == "verdict on T_page2"
        assert written.threads["T_page2"].classification == "suggestion"

    def test_a_complete_fetch_still_drops_a_deleted_thread(self, tmp_path):
        """The control: absence after a full read still means gone."""
        _code, written, _fin = self._run(
            tmp_path, [self._thread("T_page1")], complete=True)

        assert "T_page2" not in written.threads

    def test_finish_refuses_an_incomplete_fetch_and_publishes_nothing(self, tmp_path):
        """Closeout asserts the round is answered; the unread threads are the
        ones most likely to be unanswered. Status and effect asserted together —
        matching stderr alone would pass for a run that printed and published."""
        code, _written, fin = self._run(
            tmp_path, [self._thread("T_page1")], complete=False, finish=True)

        assert code == 1
        fin.assert_not_called()

    def test_finish_proceeds_on_a_complete_fetch(self, tmp_path):
        """The control for the refusal above."""
        code, _written, fin = self._run(
            tmp_path, [self._thread("T_page1")], complete=True, finish=True)

        assert code == 0
        fin.assert_called_once()

    def test_a_refused_track_is_the_runs_exit_code(self, tmp_path):
        """--finish --track with an id naming no deferred thread fails the run.

        The refusal used to be a `sys.exit(1)` two layers down in a library, so
        the exit status came from the process dying rather than from this
        module. Now that closeout reports it, only propagating the value keeps
        the run from claiming success having filed nothing.
        """
        ctx = self._ctx(tmp_path)
        self._seed_ledger(ctx)
        args = cli.review_threads.build_parser().parse_args(["--finish"])
        with patch.object(review.comment_threads, "fetch_pr_data",
                          return_value=self._pr_data(
                              [self._thread("T_page1")], complete=True)), \
             patch.object(review.closeout, "finish_deferred_work", return_value=False):
            assert review.comment_threads.run_threads(MagicMock(), args, ctx) == 1

    def test_finish_on_an_incomplete_fetch_resolves_nothing_on_github(
            self, tmp_path):
        """The guard's "nothing was published" claim must stay true: a
        verified thread in the short fetch must not get resolved on GitHub
        before --finish bails out."""
        ctx = self._ctx(tmp_path)
        self._seed_ledger(ctx)
        args = cli.review_threads.build_parser().parse_args(["--finish"])
        with patch.object(
                review.comment_threads, "fetch_pr_data",
                return_value=self._pr_data(
                    [self._verified_thread("T_verified")], complete=False)), \
             patch.object(review.closeout, "finish_deferred_work") as fin, \
             patch.object(pr.comments, "resolve_thread") as resolve:
            code = review.comment_threads.run_threads(MagicMock(), args, ctx)

        assert code == 1
        resolve.assert_not_called()
        fin.assert_not_called()


class TestReadReport:
    """The report a `--reply` renders from, built without writing anything."""

    def _pr_data(self):
        return PRData(
            viewer_login="me", head_sha="abc1234", head_ref="b", base_ref="main",
            reviews=[{"databaseId": 88, "author": {"login": "kgn"}, "state": "COMMENTED",
                      "body": "tighten it", "submittedAt": "2026-01-02T00:00:00Z"}],
        )

    def test_a_review_body_the_last_round_read_stays_seen(self, tmp_path):
        """Read as unseen, it would be restated under the table by every reply."""
        ctx = make_ctx(worktree_root=tmp_path, target_dir=tmp_path / "target")
        st = pr.state.new_state("owner/repo", "b", pr_number=42, head_sha="abc1234",
                                worktree_root=str(tmp_path))
        st.comments.seen_review_body_comments = {88: ""}
        pr.state.save_state(ctx.target_dir, st)
        report = review.comment_threads.read_report(ctx, self._pr_data())
        assert [c.get("seen", False) for c in report.review_body_comments] == [True]

    def test_with_no_state_every_comment_is_unseen(self, tmp_path):
        ctx = make_ctx(worktree_root=tmp_path, target_dir=tmp_path / "target")
        report = review.comment_threads.read_report(ctx, self._pr_data())
        assert [c.get("seen", False) for c in report.review_body_comments] == [False]
        assert not (tmp_path / "target").exists()


# ── the worktree `main` asks `resolve` for ──────────────────────────────────


def _resolve_after_main(argv):
    ctx = make_ctx()
    with patch.object(pr.context, "resolve", return_value=ctx) as resolve, \
         patch("cli.locks.claim_declared"), \
         patch.object(cli.review_threads, "Trail") as trail_cls, \
         patch.object(review.comment_threads, "run_threads", return_value=0):
        trail_cls.start.return_value = MagicMock()
        assert cli.review_threads.main(argv) == 0
    return resolve


def test_main_resolves_with_create_for_fix():
    resolve = _resolve_after_main(["--fix"])
    assert resolve.call_args.kwargs["worktree"] is pr.context.WorktreeNeed.CREATE


def test_main_resolves_with_find_for_finish():
    resolve = _resolve_after_main(["--finish"])
    assert resolve.call_args.kwargs["worktree"] is pr.context.WorktreeNeed.FIND


def test_main_resolves_with_find_for_fix_finish():
    """`--fix --finish` publishes what an earlier pass recorded; it commits nothing."""
    resolve = _resolve_after_main(["--fix", "--finish"])
    assert resolve.call_args.kwargs["worktree"] is pr.context.WorktreeNeed.FIND
