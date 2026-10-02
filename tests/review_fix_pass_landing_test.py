"""End-to-end tests of the review fix pass against a real repo — what a
landed pass leaves behind, the held commit, snapshot staging, a clean review,
and the committed-nothing gate every fix pass shares.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
import fix.engine
import git.land
import git.push
import review.fix
import review.paths
import review.types
import core.publishing
import pr.attribution
import git.client

from review_fix_pass_support import _PUSHED, git_wt, _committed_paths, _make_job, _run


def _install_failing_pre_commit(tmp_path, message: str = "gate refused") -> None:
    """Make every later `git commit` in `git_wt` fail, the way a hook does."""
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n")
    hook.chmod(0o755)


# ── end to end, against a real repo ─────────────────────────────────────────


class TestWhatALandedPassLeavesBehind:
    """One pass over a dirty worktree: what is committed, and what the review says.

    A `tsc` run before the review left a 272KB incremental cache untracked in
    the worktree; `git add -A` committed and pushed it, and the post-hoc scan
    checked off a finding on a file that was dirty before the agent started.
    """

    REVIEW = (
        "## Must fix\n"
        "- [ ] **[M1]** `src.py:1` — Was already being edited by hand\n"
        "- [ ] **[M2]** `helper.py:1` — Missing helper\n"
    )

    @patch("git.push.push", return_value=_PUSHED)
    def test_only_the_agents_own_changes_are_committed_and_credited(
        self, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "tsconfig.tsbuildinfo").write_text("272KB of cache\n")
        (git_wt / "src.py").write_text("hand-edited, not by the fix agent\n")
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            (git_wt / "build.cache").write_text("artifact\n")

        _run(job, {"M2": "fixed", "M1": "needs a person — hand edit in flight"},
             work=agent_run)

        assert _committed_paths(git_wt) == {"helper.py"}

        status = git_out(git_wt, "status", "--porcelain")
        assert "tsconfig.tsbuildinfo" in status
        assert " M src.py" in status
        assert "build.cache" not in status

        review = Path(job.review_file).read_text()
        assert "- [x] **[M2]**" in review
        assert "- [ ] **[M1]**" in review
        assert "*(skipped — hand edit in flight)*" in review
        mock_push.assert_called_once()

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_commit_message_reports_what_the_pass_settled(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")

        _run(job, {"M2": "fixed", "M1": "needs a person — needs a product decision"},
             work=agent_run)

        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "1 fixed, 1 skipped" in msg
        assert "[M2] Missing helper" in msg
        assert "[M1] needs a product decision" in msg

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_summary_reaches_the_operator_s_terminal(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        err = capsys.readouterr().err
        assert "Fix summary:" in err
        assert "[M1] by design" in err

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_truncated_pass_names_the_limit_in_the_summary_and_commit(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        """One ticked box used to look like a finished pass that skipped the rest."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")

        _run(
            job, {"M2": "fixed"},
            work=agent_run,
            stop=Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=30),
        )

        err = capsys.readouterr().err
        assert "max turns" in err
        assert "30" in err
        assert "not reached (turn limit)" in err

        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "max turns" in msg
        assert "not reached (turn limit)" in msg
        assert "no auto-fix" not in msg

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_pass_that_changed_no_files_commits_nothing(
        self, mock_push, git_wt, tmp_path,
    ):
        """Every finding declined is an answer, and answers are not edits."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()
        assert "*(declined — by design)*" in Path(job.review_file).read_text()

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_commit_the_hook_refused_still_re_renders_the_review(
        self, mock_push, git_wt, tmp_path, live_git_hooks,
    ):
        """The agent's fix is real and in the worktree; only the commit failed.

        `live_git_hooks` is what lets the hook run at all — the suite disowns
        hooks by default.
        """
        _install_failing_pre_commit(tmp_path)
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M2": "fixed"},
             work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"))

        mock_push.assert_not_called()
        assert "- [x] **[M2]**" in Path(job.review_file).read_text()


class TestTheHeldCommitIsRecorded:
    """The push is gated and the commit is not, so a pass ordinarily leaves one.

    Before the sidecar recorded it, the only trace was a `resume` line on a
    terminal the operator may already have closed: no surface knew the commit
    existed, and the review directory that held the findings held nothing about
    the work done against them.
    """

    @staticmethod
    def _landing(status, sha="abc1234", resume=""):
        return fix.engine.FixRun(
            landed=git.land.LandResult(status=status, sha=sha, resume=resume),
        )

    REVIEW = (
        "## Must fix\n"
        "- [ ] **[M1]** `a.py:1` — Missing nil check\n"
    )

    def test_the_pass_records_what_it_landed(self, git_wt, tmp_path):
        """Asserted through `run_fix_pass`, not by calling the recorder.

        Every other case here drives `_record_commit` directly, so all of them
        pass against a pass that never calls it — which is the same wiring bug
        `test_the_pass_hands_the_engine_a_gate_at_all` exists to catch one
        argument along.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        landed = git.land.LandResult(status=git.land.CommitStatus.PUSH_HELD, sha="abc1234")
        with patch.object(fix.engine, "run",
                          return_value=fix.engine.FixRun(landed=landed)):
            review.fix.run_fix_pass(job)

        meta = review.paths.read_review_meta(Path(job.artifact_dir))
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_held_commit_is_recorded_as_owed(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review.fix._record_commit(job, self._landing(git.land.CommitStatus.PUSH_HELD))

        meta = review.paths.read_review_meta(review_dir)
        assert meta.fix_commit_sha == "abc1234"
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_pushed_commit_owes_nothing(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path)
        review.fix._record_commit(job, self._landing(git.land.CommitStatus.PUSHED))

        meta = review.paths.read_review_meta(Path(job.artifact_dir))
        assert meta.fix_commit_sha == "abc1234"
        assert meta.unpushed_fix_commit == ""

    def test_every_unpushed_status_reads_as_owed(self, git_wt, tmp_path):
        """Which statuses mean "still local" is `pr.attribution`'s answer.

        Asserted over the whole enum rather than the one status the gate
        produces today, because a list kept here would be the second one and
        would drift — as a hand-written one already did, by omitting
        `push_unverified`.
        """
        job = _make_job(git_wt, tmp_path)
        for status in git.land.CommitStatus:
            review.fix._record_commit(job, self._landing(status))
            meta = review.paths.read_review_meta(Path(job.artifact_dir))
            expected = "abc1234" if pr.attribution.commit_unpushed(status) else ""
            assert meta.unpushed_fix_commit == expected, status

    def test_recording_keeps_what_the_sidecar_already_held(self, git_wt, tmp_path):
        """The sidecar is the review's attribution; a fix pass adds to it."""
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review.paths.write_review_meta(
            review_dir, review.types.ReviewMeta(repo="o/r", head_sha="deadbeef"),
        )
        review.fix._record_commit(job, self._landing(git.land.CommitStatus.PUSH_HELD))

        meta = review.paths.read_review_meta(review_dir)
        assert (meta.repo, meta.head_sha) == ("o/r", "deadbeef")
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_pass_with_no_landing_leaves_the_record_alone(self, git_wt, tmp_path):
        """Nothing to commit is not a retraction of what an earlier round said."""
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review.fix._record_commit(job, self._landing(git.land.CommitStatus.PUSH_HELD))
        review.fix._record_commit(job, fix.engine.FixRun(landed=None))

        assert review.paths.read_review_meta(review_dir).unpushed_fix_commit == "abc1234"

    def test_a_pass_that_landed_nothing_new_leaves_the_record_alone(self, git_wt, tmp_path):
        """`NO_CHANGES`/`COMMIT_FAILED` carry no new sha and must not overwrite one.

        A round that defers or declines every open finding still calls `land`
        with an empty scope, which answers `NO_CHANGES` rather than `None` —
        and a `COMMIT_FAILED` round leaves the same empty sha behind. Neither
        is a retraction of the commit an earlier round actually made.
        """
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review.fix._record_commit(job, self._landing(git.land.CommitStatus.PUSH_HELD))
        for status in (git.land.CommitStatus.NO_CHANGES, git.land.CommitStatus.COMMIT_FAILED):
            review.fix._record_commit(
                job, fix.engine.FixRun(landed=git.land.LandResult(status=status, sha="")),
            )
            meta = review.paths.read_review_meta(review_dir)
            assert meta.fix_commit_sha == "abc1234", status
            assert meta.unpushed_fix_commit == "abc1234", status

    def test_an_unrecordable_commit_does_not_take_the_sidecar_with_it(self, git_wt, tmp_path):
        """Every review lookup on the machine walks these files.

        A value the schema cannot serialise would cost the whole sidecar — the
        attribution of a review that was written correctly — rather than the one
        field it arrived in.
        """
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review.paths.write_review_meta(
            review_dir, review.types.ReviewMeta(repo="o/r", head_sha="deadbeef"),
        )
        review.fix._record_commit(job, fix.engine.FixRun(landed=MagicMock()))

        meta = review.paths.read_review_meta(review_dir)
        assert (meta.repo, meta.head_sha) == ("o/r", "deadbeef")
        assert meta.fix_commit_sha == ""

    def test_a_sidecar_predating_the_field_owes_nothing(self):
        """An unpushed commit is a positive fact, never inferred from silence."""
        assert review.types.ReviewMeta().unpushed_fix_commit == ""
        assert review.types.ReviewMeta(fix_commit_sha="abc1234").unpushed_fix_commit == ""


class TestSnapshotDiffStagesEveryShapeOfChange:
    """What the snapshot diff must survive besides a plain edit.

    Attribution is a set of path strings, so each case below is a different way
    the two snapshots can disagree about what a path is: gone, moved, or
    spelled with bytes git escapes before it prints them.
    """

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_file_the_agent_deletes_is_committed_as_a_deletion(
        self, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "dead_code.py").write_text("unused = 1\n")
        git_out(git_wt, "add", "dead_code.py")
        git_out(git_wt, "commit", "-qm", "add dead code")

        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `dead_code.py:1` — Dead code, delete it\n",
        )
        _run(job, {"N1": "fixed"}, work=lambda: (git_wt / "dead_code.py").unlink())

        assert _committed_paths(git_wt) == {"dead_code.py"}
        assert git_out(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_rename_commits_both_halves(self, mock_push, git_wt, tmp_path):
        """The old path leaves via the diff, the new one via the untracked list."""
        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `src.py:1` — Misnamed module\n",
            files=["src.py", "renamed.py"],
        )
        _run(job, {"N1": "fixed"},
             work=lambda: (git_wt / "src.py").rename(git_wt / "renamed.py"))

        tracked = git_out(git_wt, "ls-tree", "--name-only", "HEAD").split()
        assert "renamed.py" in tracked
        assert "src.py" not in tracked
        assert git_out(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_path_git_would_escape_is_staged_verbatim(
        self, mock_push, git_wt, tmp_path,
    ):
        """`core.quotePath=false` is what keeps the name a pathspec git resolves.

        Escaped, the name reaches `git add` as `caf\\303\\251...`, which matches
        nothing — and that `add` runs under `check=True`, so the whole pass dies
        on a file whose only crime is an accent.
        """
        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `café brûlé.py:1` — Needs a docstring\n",
        )
        _run(job, {"N1": "fixed"},
             work=lambda: (git_wt / "café brûlé.py").write_text("crème\n"))

        assert _committed_paths(git_wt) == {"café brûlé.py"}

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_path_dirty_before_the_pass_is_not_credited_even_when_edited(
        self, mock_push, git_wt, tmp_path,
    ):
        """The `ceiling:` in `run_fix_pass`, asserted rather than only described.

        Attribution is by path, so a path in both snapshots is in neither
        delta — the agent's edit to it goes uncommitted. The day attribution
        compares content across the snapshot, this test is what says the
        tradeoff is gone.
        """
        (git_wt / "src.py").write_text("hand edit in progress\n")
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `src.py:1` — Missing guard\n",
        )
        _run(job, {"M1": "fixed"}, work=lambda: (git_wt / "src.py").write_text(
            "hand edit in progress\nagent fix\n",
        ))

        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        assert " M src.py" in git_out(git_wt, "status", "--porcelain")
        mock_push.assert_not_called()


class TestRunFixPassWhenTheSnapshotFails:
    """A snapshot git could not take must never read as an unchanged worktree.

    The difference between the two snapshots is the only list of paths the pass
    commits, so an empty one is indistinguishable from a pass that did nothing —
    which is how a `git status` killed by a SIGPIPE or a locked index ends with
    the agent's fixes discarded and the run reported as a success.
    """

    REVIEW = "## Must fix\n- [ ] **[M1]** `helper.py:1` — Missing helper\n"

    @staticmethod
    def _corrupt_index(git_wt):
        """Make every later read of the worktree's state fail, as a lock would."""
        (git_wt / ".git" / "index").write_bytes(b"garbage")

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_unreadable_worktree_stops_the_pass_before_the_agent_runs(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        """Refusing here costs nothing: the agent has not done any work yet.

        With no baseline the pass cannot tell its own edits from what was
        already in the worktree, so running the agent only produces work it
        would have to either commit wholesale or throw away.
        """
        self._corrupt_index(git_wt)
        inv = _run(_make_job(git_wt, tmp_path, self.REVIEW), {"M1": "fixed"})

        inv.assert_not_called()
        mock_push.assert_not_called()
        assert "skipping fix pass" in capsys.readouterr().err

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_agents_work_is_not_dropped_when_the_second_snapshot_fails(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        """The regression: edits survive in the worktree and the run says so."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert (git_wt / "helper.py").read_text() == "def helper(): pass\n"
        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()

        err = capsys.readouterr().err
        assert "nothing was committed or pushed" in err
        assert str(git_wt) in err

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_pass_that_could_not_attribute_its_work_re_renders_nothing(
        self, mock_push, git_wt, tmp_path,
    ):
        """A ticked box over an uncommitted fix would retire the finding for good.

        The document still calling every finding open is what sends the next
        round back over them, which is right: the commit that would have made
        them done never happened.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert Path(job.review_file).read_text() == self.REVIEW


class TestAPassWithNothingToFix:
    """What a run says when the review is clean and the branch is not.

    `--post` gates what this pass publishes, and the only thing it publishes is
    its own fix commit — which a review with nothing left to fix never makes. So
    the push is skipped correctly and silently, while the operator's own commits
    sit unpushed and the run reports success. Twice in one session that read as
    a branch that had shipped.
    """

    CLEAN = "## Must fix\n- [x] **[M1]** `helper.py:1` — Missing helper\n"

    @staticmethod
    def _with_upstream(git_wt, tmp_path, *, ahead: int):
        """Give the worktree a remote it is *ahead* commits in front of."""
        remote = tmp_path / "remote.git"
        git_out(remote.parent, "init", "-q", "--bare", str(remote))
        git_out(git_wt, "remote", "add", "origin", str(remote))
        git_out(git_wt, "push", "-q", "-u", "origin", "main")
        for n in range(ahead):
            (git_wt / f"local{n}.py").write_text("x\n")
            git_out(git_wt, "add", "-A")
            git_out(git_wt, "commit", "-qm", f"local work {n}")

    def test_it_says_the_branch_is_ahead_of_its_remote(
        self, git_wt, tmp_path, capsys, monkeypatch,
    ):
        """The gap itself: nothing to push, and commits nobody pushed."""
        monkeypatch.setattr(core.publishing, "enabled", lambda: True)
        self._with_upstream(git_wt, tmp_path, ahead=2)
        job = _make_job(git_wt, tmp_path, self.CLEAN)

        review.fix.run_fix_pass(job)

        err = capsys.readouterr().err
        assert "2 commits ahead of its remote" in err
        assert "pushed nothing" in err

    def test_one_commit_is_not_reported_in_the_plural(
        self, git_wt, tmp_path, capsys, monkeypatch,
    ):
        monkeypatch.setattr(core.publishing, "enabled", lambda: True)
        self._with_upstream(git_wt, tmp_path, ahead=1)

        review.fix.run_fix_pass(_make_job(git_wt, tmp_path, self.CLEAN))

        assert "1 commit ahead" in capsys.readouterr().err

    # passes-at-base: asserts the line is absent, and at base it is always absent
    def test_a_branch_level_with_its_remote_says_nothing(
        self, git_wt, tmp_path, capsys, monkeypatch,
    ):
        """The ordinary clean run must not grow a line that means nothing."""
        monkeypatch.setattr(core.publishing, "enabled", lambda: True)
        self._with_upstream(git_wt, tmp_path, ahead=0)

        review.fix.run_fix_pass(_make_job(git_wt, tmp_path, self.CLEAN))

        assert "ahead of its remote" not in capsys.readouterr().err

    # passes-at-base: asserts the line is absent, and at base it is always absent
    def test_a_branch_that_was_never_pushed_says_nothing(
        self, git_wt, tmp_path, capsys, monkeypatch,
    ):
        """No upstream is not a remote that is behind.

        `commits_ahead` reads an unresolvable ref as 0, which is the right
        answer here rather than a coincidence worth working around: a branch
        with no remote is not a branch whose remote is missing commits.
        """
        monkeypatch.setattr(core.publishing, "enabled", lambda: True)

        review.fix.run_fix_pass(_make_job(git_wt, tmp_path, self.CLEAN))

        assert "ahead of its remote" not in capsys.readouterr().err

    # passes-at-base: asserts the line is absent, and at base it is always absent
    def test_a_held_gate_says_nothing(self, git_wt, tmp_path, capsys, monkeypatch):
        """A run that was never going to push has nothing to report.

        Without `--post` the unpushed branch is the outcome that was asked for,
        and warning about it would fire on every local `--fix` run.
        """
        monkeypatch.setattr(core.publishing, "enabled", lambda: False)
        self._with_upstream(git_wt, tmp_path, ahead=2)

        review.fix.run_fix_pass(_make_job(git_wt, tmp_path, self.CLEAN))

        assert "ahead of its remote" not in capsys.readouterr().err


# ── the gates every fix pass shares ─────────────────────────────────────────


class TestCommittedNothing:
    """The other half of the gate, which opens on a worktree git cannot read.

    Against a real `git commit` for the same reason as the class above: which
    failures mean "the change was empty" is git's vocabulary, not this repo's.
    """

    def test_an_empty_commit_is_not_a_rejection(self, git_wt):
        result = git.client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert git.land.committed_nothing(result) is True

    def test_staged_but_unchanged_content_is_not_a_rejection(self, git_wt):
        """`add` of an unmodified file stages nothing, so the commit is empty."""
        git_out(git_wt, "add", "src.py")
        result = git.client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert git.land.committed_nothing(result) is True

    def test_a_hook_rejection_is_a_rejection(self, git_wt, tmp_path, live_git_hooks):
        """`live_git_hooks` is what lets the hook run — the suite disowns them."""
        _install_failing_pre_commit(tmp_path)
        (git_wt / "src.py").write_text("edited\n")
        git_out(git_wt, "add", "src.py")
        result = git.client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert git.land.committed_nothing(result) is False
