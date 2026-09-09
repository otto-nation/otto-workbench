"""Tests for rebase.stash — carrying uncommitted work across a rebase."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import client as git_client
from rebase import conflicts as rebase_conflicts
from rebase import inspect as rebase_inspect
from rebase import stash as rebase_stash
from rebase import types as rebase_types


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
        with mock.patch.object(rebase_inspect, "status_lines", return_value=dirty), \
             mock.patch.object(git_client, "run", return_value=_ok()):
            assert rebase_stash.auto_stash("/fake") is expected

    def test_untracked_files_are_stashed_too(self):
        """The pre-push hooks validate the worktree, so strays must not be in it."""
        with mock.patch.object(rebase_inspect, "status_lines",
                               return_value=["?? scratch.txt"]), \
             mock.patch.object(git_client, "run", return_value=_ok()) as run:
            rebase_stash.auto_stash("/fake")

        assert run.call_args[0] == (
            "stash", "push", "-u", "-m", rebase_stash.STASH_MSG)

    def test_a_tree_it_cannot_read_is_not_stashed(self):
        """None is "cannot tell", which must not be mistaken for clean."""
        with mock.patch.object(rebase_inspect, "status_lines", return_value=None), \
             mock.patch.object(git_client, "run") as run:
            assert rebase_stash.auto_stash("/fake") is None
        run.assert_not_called()

    def test_a_failed_stash_is_not_reported_as_stashed(self):
        with mock.patch.object(rebase_inspect, "status_lines",
                               return_value=[" M a.py"]), \
             mock.patch.object(git_client, "run", return_value=_failed()):
            assert rebase_stash.auto_stash("/fake") is None


class TestAutoUnstash:
    def test_a_clean_pop_says_so(self, capsys):
        with mock.patch.object(git_client, "run", return_value=_ok()):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)
        assert "Restored stashed changes" in capsys.readouterr().err

    def test_a_pop_that_failed_without_conflicts_names_the_stash(self, capsys):
        """Nothing is lost — the entry survives, so the message points at it."""
        with mock.patch.object(git_client, "run", return_value=_failed()), \
             mock.patch.object(rebase_inspect, "detect_conflicts", return_value=[]):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)

        err = capsys.readouterr().err
        assert rebase_stash.STASH_MSG in err
        assert "git stash pop" in err

    def test_conflicts_are_left_alone_when_the_run_may_not_resolve(self, capsys):
        """Without --fix the user resolves them, so say so rather than prompting."""
        with mock.patch.object(git_client, "run", return_value=_failed()), \
             mock.patch.object(rebase_inspect, "detect_conflicts",
                               return_value=["a.py"]):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)

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
        with mock.patch.object(git_client, "run", side_effect=fake_run), \
             mock.patch.object(rebase_inspect, "detect_conflicts",
                               side_effect=remaining), \
             mock.patch.object(rebase_stash.ai_backend, "is_available",
                               return_value=True), \
             mock.patch.object(rebase_stash.agent_invoke, "run_prompt",
                               return_value=answer) as prompt, \
             mock.patch.object(rebase_conflicts, "git_add", return_value=True):
            rebase_stash.auto_unstash(
                str(tmp_path), rebase_types.RunMode.FIX)
        return commands, prompt

    def test_a_resolved_conflict_is_written_and_the_stash_dropped(self, tmp_path):
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase_conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase_conflicts.RESOLVE_END}\n")

        commands, _ = self._run(
            tmp_path, conflicts=[name], answer=self._answer(resolved))

        assert (tmp_path / name).read_text() == "merged\n"
        assert ("stash", "drop") in commands

    def test_the_prompt_names_the_file_and_both_sides(self, tmp_path):
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase_conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase_conflicts.RESOLVE_END}\n")

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
        assert ("stash", "drop") not in commands

    def test_a_failed_prompt_leaves_the_stash(self, tmp_path):
        name = self._conflicted(tmp_path)

        commands, _ = self._run(
            tmp_path, conflicts=[name], answer=self._answer("", exit_code=1),
            resolved=False)

        assert ("stash", "drop") not in commands

    def test_a_binary_conflict_is_never_prompted_about(self, tmp_path):
        name = self._conflicted(tmp_path, name="logo.png")
        resolved = (f"{rebase_conflicts.RESOLVE_BEGIN}\nx\n"
                    f"{rebase_conflicts.RESOLVE_END}\n")

        with mock.patch.object(rebase_conflicts, "is_binary", return_value=True):
            commands, prompt = self._run(
                tmp_path, conflicts=[name], answer=self._answer(resolved),
                resolved=False)

        prompt.assert_not_called()
        assert ("stash", "drop") not in commands

    def test_a_drop_that_failed_is_reported(self, tmp_path, capsys):
        """The work is restored but the entry lingers — say so rather than claim clean."""
        name = self._conflicted(tmp_path)
        resolved = (f"{rebase_conflicts.RESOLVE_BEGIN}\nmerged\n"
                    f"{rebase_conflicts.RESOLVE_END}\n")

        self._run(tmp_path, conflicts=[name], answer=self._answer(resolved),
                  drop=_failed("stash entry is in use"))

        assert "git stash drop failed" in capsys.readouterr().err
