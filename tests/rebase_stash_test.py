"""Tests for rebase.stash — carrying uncommitted work across a rebase."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client
import rebase.conflicts
import rebase.inspect
import rebase.stash
import rebase.types
import agent.backend
import agent.invoke


def _ok(**kwargs):
    return mock.Mock(ok=True, returncode=0, stdout="", stderr="", **kwargs)


def _failed(stderr="boom"):
    return mock.Mock(ok=False, returncode=1, stdout="", stderr=stderr,
                     combined_output=stderr)


class TestAutoStash:
    @pytest.mark.parametrize("dirty,expected", [
        ([" M ai/lib/review.py"], True),
        (["?? scratch.txt"], True),
        ([], False),
    ])
    def test_it_stashes_only_a_dirty_tree(self, dirty, expected):
        with mock.patch.object(rebase.inspect, "status_lines", return_value=dirty), \
             mock.patch.object(git.client, "run", return_value=_ok()):
            assert rebase.stash.auto_stash("/fake") is expected

    def test_untracked_files_are_stashed_too(self):
        """The pre-push hooks validate the worktree, so strays must not be in it."""
        with mock.patch.object(rebase.inspect, "status_lines",
                               return_value=["?? scratch.txt"]), \
             mock.patch.object(git.client, "run", return_value=_ok()) as run:
            rebase.stash.auto_stash("/fake")

        assert run.call_args[0] == (
            "stash", "push", "-u", "-m", rebase.stash.STASH_MSG)

    def test_a_tree_it_cannot_read_is_not_stashed(self):
        """None is "cannot tell", which must not be mistaken for clean."""
        with mock.patch.object(rebase.inspect, "status_lines", return_value=None), \
             mock.patch.object(git.client, "run") as run:
            assert rebase.stash.auto_stash("/fake") is None
        run.assert_not_called()

    def test_a_failed_stash_is_not_reported_as_stashed(self):
        with mock.patch.object(rebase.inspect, "status_lines",
                               return_value=[" M a.py"]), \
             mock.patch.object(git.client, "run", return_value=_failed()):
            assert rebase.stash.auto_stash("/fake") is None


# A stand-in for whatever ref `auto_stash_ref` resolves, not a claim about
# stack position — the tests below stub the lookup rather than exercise it.
# `TestAutoStashRef` is where position actually matters, and it asserts that
# the entry is found by message wherever it sits.
_OUR_STASH = "stash@{0}"


def _has_our_stash():
    """Patch the entry lookup so a unit test need not model `git stash list`."""
    return mock.patch.object(
        rebase.stash, "auto_stash_ref", return_value=_OUR_STASH,
    )


def _no_rebase():
    return mock.patch.object(rebase.inspect, "rebase_in_progress",
                             return_value=False)


class TestAutoStashRef:
    """Which entry to pop, resolved by message rather than assumed to be the top.

    A run that held its stash across a paused rebase pops it in a *later*
    process, by which time the operator may have stashed something of their
    own on top — and popping the top entry then restores the wrong work into a
    rebased tree.
    """

    def _listing(self, *entries):
        return mock.patch.object(git.client, "lines", return_value=list(entries))

    def test_it_finds_our_entry_below_someone_elses(self):
        with self._listing(
            "stash@{0}\x1fOn feat: my own work",
            f"stash@{{1}}\x1fOn feat: {rebase.stash.STASH_MSG}",
        ):
            assert rebase.stash.auto_stash_ref("/fake") == "stash@{1}"

    def test_a_similarly_named_entry_is_not_ours(self):
        """Matched on the whole message, not on a substring of the line."""
        with self._listing(
            f"stash@{{0}}\x1fOn feat: before {rebase.stash.STASH_MSG} experiment",
        ):
            assert rebase.stash.auto_stash_ref("/fake") == ""

    def test_no_entry_of_ours_is_the_empty_string(self):
        with self._listing("stash@{0}\x1fOn feat: unrelated"):
            assert rebase.stash.auto_stash_ref("/fake") == ""


class TestRestoreHoldsTheStashDuringARebase:
    """Popping into a conflicted index cannot work, and the advice destroyed work.

    An exit-3 run leaves the rebase in progress by design. The pop then failed,
    the rebase's own unmerged files were read as stash conflicts, and the tool
    said "resolve manually, then `git stash drop`" — dropping a stash that had
    never been applied.
    """

    def test_it_does_not_pop_while_a_rebase_is_in_progress(self, capsys):
        with _has_our_stash(), \
             mock.patch.object(rebase.inspect, "rebase_in_progress",
                               return_value=True), \
             mock.patch.object(git.client, "run") as run:
            rebase.stash.restore("/fake", rebase.types.RunMode.FIX)

        assert not any(args[:2] == ("stash", "pop") for args, _ in run.call_args_list)
        err = capsys.readouterr().err
        assert rebase.stash.STASH_MSG in err
        assert "stash drop" not in err

    def test_it_pops_once_the_rebase_is_over(self):
        with _has_our_stash(), _no_rebase(), \
             mock.patch.object(git.client, "run", return_value=_ok()) as run:
            rebase.stash.restore("/fake", rebase.types.RunMode.FIX)

        assert run.call_args[0][:3] == ("stash", "pop", _OUR_STASH)

    def test_restore_looks_up_the_stash_entry_only_once(self):
        """`restore` already has the ref from its own guard check.

        `auto_unstash` resolves `auto_stash_ref` fresh only when it is not
        handed one, so a `restore` that already resolved it must pass it
        through rather than asking `git stash list` the same question twice.
        """
        with mock.patch.object(
            rebase.stash, "auto_stash_ref", return_value=_OUR_STASH,
        ) as ref, _no_rebase(), \
             mock.patch.object(git.client, "run", return_value=_ok()):
            rebase.stash.restore("/fake", rebase.types.RunMode.FIX)

        assert ref.call_count == 1

    def test_it_is_a_no_op_when_there_is_no_auto_stash(self):
        with mock.patch.object(rebase.stash, "auto_stash_ref", return_value=""), \
             mock.patch.object(git.client, "run") as run:
            rebase.stash.restore("/fake", rebase.types.RunMode.FIX)

        run.assert_not_called()


class TestAutoUnstash:
    def test_a_clean_pop_says_so(self, capsys):
        with _has_our_stash(), \
             mock.patch.object(git.client, "run", return_value=_ok()):
            rebase.stash.auto_unstash("/fake", rebase.types.RunMode.PUSH)
        assert "Restored stashed changes" in capsys.readouterr().err

    def test_the_pop_holds_rerere_off(self):
        """A pop is a merge, so rerere replays into it as into any rebase step.

        `lifecycle` passes this to all three of its `git rebase` calls and this
        call was the gap — which meant a cached resolution could be replayed
        into the user's restored work, and this run's unreviewed AI resolutions
        recorded into the cache every later plain `git rebase` reads.
        """
        with _has_our_stash(), \
             mock.patch.object(git.client, "run", return_value=_ok()) as run:
            rebase.stash.auto_unstash("/fake", rebase.types.RunMode.PUSH)

        assert run.call_args.kwargs["config"] == {"rerere.enabled": "false"}

    def test_a_pop_that_failed_without_conflicts_names_the_stash(self, capsys):
        """Nothing is lost — the entry survives, so the message points at it."""
        with _has_our_stash(), _no_rebase(), \
             mock.patch.object(git.client, "run", return_value=_failed()), \
             mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]):
            rebase.stash.auto_unstash("/fake", rebase.types.RunMode.PUSH)

        err = capsys.readouterr().err
        assert rebase.stash.STASH_MSG in err
        assert "git stash pop" in err

    def test_a_rebases_unmerged_files_are_never_read_as_stash_conflicts(self, capsys):
        """Defense in depth behind `restore`'s guard, since the advice is lethal."""
        with _has_our_stash(), \
             mock.patch.object(rebase.inspect, "rebase_in_progress",
                               return_value=True), \
             mock.patch.object(git.client, "run", return_value=_failed()), \
             mock.patch.object(rebase.inspect, "detect_conflicts",
                               return_value=["mid_rebase.py"]):
            rebase.stash.auto_unstash("/fake", rebase.types.RunMode.FIX)

        err = capsys.readouterr().err
        assert "stash drop" not in err
        assert rebase.stash.STASH_MSG in err

    def test_conflicts_are_left_alone_when_the_run_may_not_resolve(self, capsys):
        """Without --fix the user resolves them, so say so rather than prompting."""
        with _has_our_stash(), _no_rebase(), \
             mock.patch.object(git.client, "run", return_value=_failed()), \
             mock.patch.object(rebase.inspect, "detect_conflicts",
                               return_value=["a.py"]):
            rebase.stash.auto_unstash("/fake", rebase.types.RunMode.PUSH)

        assert "resolve manually" in capsys.readouterr().err


class TestAutoUnstashResolution:
    """The --fix path: the AI merges the user's work into the rebased file.

    A pop conflict is the user's own uncommitted work meeting a moved base, so
    every branch that gives up has to leave the stash entry intact and say so —
    dropping it on a partial resolution is how the work would be lost.
    """

    @staticmethod
    def _conflicted(tmp_path, name="a.py", body="<<<<<<< Updated upstream\n"):
        (tmp_path / name).write_text(body)
        return name

    @staticmethod
    def _answer(text, exit_code=0):
        return mock.Mock(exit_code=exit_code, text=text)

    def _run(self, tmp_path, *, conflicts, answer, drop=None, resolved=True):
        """Drive one resolution pass, returning the git commands it ran.

        ``resolved`` is what the second conflict scan finds: a file the pass
        could not resolve still carries its markers, so git still reports it.
        """
        commands = []

        def fake_run(*args, **kwargs):
            commands.append(args)
            if args[:2] == ("stash", "pop"):
                return _failed()
            if args[:2] == ("stash", "drop"):
                return drop if drop is not None else _ok()
            return _ok()

        remaining = [conflicts, [] if resolved else conflicts]
        with mock.patch.object(git.client, "run", side_effect=fake_run), \
             _has_our_stash(), _no_rebase(), \
             mock.patch.object(rebase.inspect, "detect_conflicts",
                               side_effect=remaining), \
             mock.patch.object(agent.backend, "is_available",
                               return_value=True), \
             mock.patch.object(agent.invoke, "run_prompt",
                               return_value=answer) as prompt, \
             mock.patch.object(rebase.conflicts, "git_add", return_value=True):
            rebase.stash.auto_unstash(
                str(tmp_path), rebase.types.RunMode.FIX)
        return commands, prompt

    def test_a_resolved_conflict_is_written_and_the_stash_dropped(self, tmp_path):
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase.conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase.conflicts.RESOLVE_END}\n")

        commands, _ = self._run(
            tmp_path, conflicts=[name], answer=self._answer(resolved))

        assert (tmp_path / name).read_text() == "merged\n"
        assert ("stash", "drop", _OUR_STASH) in commands

    def test_the_prompt_names_the_file_and_both_sides(self, tmp_path):
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase.conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase.conflicts.RESOLVE_END}\n")

        _, prompt = self._run(
            tmp_path, conflicts=[name], answer=self._answer(resolved))

        text = prompt.call_args[0][1]
        assert name in text
        assert "Stashed changes" in text and "Updated upstream" in text

    def test_an_unparseable_answer_leaves_the_file_and_the_stash(self, tmp_path):
        """No markers means no resolution — the original must survive intact."""
        name = self._conflicted(tmp_path, body="<<<<<<< Updated upstream\nmine\n")

        commands, _ = self._run(
            tmp_path, conflicts=[name],
            answer=self._answer("I could not work out the merge"), resolved=False)

        assert (tmp_path / name).read_text() == "<<<<<<< Updated upstream\nmine\n"
        assert ("stash", "drop", _OUR_STASH) not in commands

    def test_a_failed_prompt_leaves_the_stash(self, tmp_path):
        name = self._conflicted(tmp_path)

        commands, _ = self._run(
            tmp_path, conflicts=[name], answer=self._answer("", exit_code=1),
            resolved=False)

        assert ("stash", "drop", _OUR_STASH) not in commands

    def test_a_binary_conflict_is_never_prompted_about(self, tmp_path):
        name = self._conflicted(tmp_path, name="logo.png")
        resolved = (f"{rebase.conflicts.RESOLVE_BEGIN}\nx\n"
                    f"{rebase.conflicts.RESOLVE_END}\n")

        with mock.patch.object(rebase.conflicts, "is_binary", return_value=True):
            commands, prompt = self._run(
                tmp_path, conflicts=[name], answer=self._answer(resolved),
                resolved=False)

        prompt.assert_not_called()
        assert ("stash", "drop", _OUR_STASH) not in commands

    def test_a_drop_that_failed_is_reported(self, tmp_path, capsys):
        """The work is restored but the entry lingers — say so rather than claim clean."""
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase.conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase.conflicts.RESOLVE_END}\n")

        self._run(tmp_path, conflicts=[name], answer=self._answer(resolved),
                  drop=_failed("stash entry is in use"))

        assert "git stash drop failed" in capsys.readouterr().err
