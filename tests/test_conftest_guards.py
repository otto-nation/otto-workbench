"""Tests for conftest's cross-test guards.

The config guard compares the repo's git config around every test, so anything
that writes to that file from outside the test process fails whichever test
happened to be running. worktrunk does exactly that — restamping per-branch
markers, hint counters and the recently-used branch list — and so does any
concurrent git operation, through the branch tracking entries it adds and
prunes. These cover both, along with the lines the exemption draws: worktrunk's
user config in the same section stays guarded, the same key name in another
section stays guarded, and a leaked test is still caught by the identity it
writes.

Catching is half of it: the guard restores the bytes it snapshotted, so a run
that trips it leaves the shared config as it found it. That half is driven here
by a real `git config` aimed at a repository the caller did not name, which is
the shape the leak arrives in.

The review-env guard covers the other direction — config arriving from the
developer's shell rather than from another process.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import (
    _assert_config_unchanged, _clear_review_env, _describe_config_change,
    _guarded_lines, _load_lib, _review_env_keys, _section_of, init_worktree,
    seed_repo,
)

gitenv = _load_lib("gitenv")

_CONFIG = b"""[core]
\trepositoryformatversion = 0
[user]
\temail = dev@example.com
[worktrunk]
\tdefault-branch = main
\thistory = main
[worktrunk "hints"]
\tshell-integration = 4
[worktrunk "state.main"]
\tmarker = {\\"marker\\":\\"\xf0\x9f\xa4\x96\\",\\"set_at\\":1786075417}
[branch "main"]
\tremote = origin
[commit]
\tgpgsign = false
"""


def _rewritten(old: bytes, new: bytes) -> bytes:
    assert old in _CONFIG
    return _CONFIG.replace(old, new)


class TestExternalWritesAreIgnored:
    def test_a_refreshed_marker_reads_as_no_change(self):
        after = _rewritten(b"1786075417", b"1786075999")
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_new_worktrunk_section_reads_as_no_change(self):
        after = _CONFIG + b'[worktrunk "state.feat"]\n\tmarker = {}\n'
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_key_added_to_the_external_section_reads_as_no_change(self):
        after = _rewritten(
            b'[worktrunk "state.main"]\n',
            b'[worktrunk "state.main"]\n\tsticky = true\n',
        )
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_rewritten_branch_history_reads_as_no_change(self):
        """`wt switch` in any worktree of this repo rewrites it, mid-run included."""
        after = _rewritten(b"\thistory = main", b"\thistory = feat,main")
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_branch_history_disappearing_reads_as_no_change(self):
        after = _rewritten(b"\thistory = main\n", b"")
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_history_opening_its_own_section_reads_as_no_change(self):
        """The first `wt switch` in a repo writes the section and the key at once."""
        before = _rewritten(b"[worktrunk]\n\tdefault-branch = main\n\thistory = main\n", b"")
        after = before + b"[worktrunk]\n\thistory = feat\n"
        assert _guarded_lines(after) == _guarded_lines(before)

    def test_a_bumped_hint_counter_reads_as_no_change(self):
        after = _rewritten(b"shell-integration = 4", b"shell-integration = 5")
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_new_branch_section_reads_as_no_change(self):
        """Another worktree branching is the commonest write of all."""
        after = _CONFIG + b'[branch "feat"]\n\tremote = origin\n\tmerge = refs/heads/feat\n'
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_retargeted_tracking_entry_reads_as_no_change(self):
        after = _rewritten(b"\tremote = origin", b"\tremote = upstream")
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)

    def test_a_removed_worktrunk_section_reads_as_no_change(self):
        after = _rewritten(
            b'[worktrunk "state.main"]\n\tmarker = '
            b'{\\"marker\\":\\"\xf0\x9f\xa4\x96\\",\\"set_at\\":1786075417}\n',
            b"",
        )
        assert _guarded_lines(after) == _guarded_lines(_CONFIG)


class TestRealWritesAreStillCaught:
    def test_a_changed_identity_is_a_change(self):
        after = _rewritten(b"dev@example.com", b"test@example.com")
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_section_appended_at_the_end_is_a_change(self):
        after = _CONFIG + b"[user]\n\tname = Test\n"
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_an_edit_to_an_existing_owned_section_is_a_change(self):
        after = _rewritten(b"repositoryformatversion = 0", b"repositoryformatversion = 1")
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_worktrunk_user_config_is_a_change(self):
        """`default-branch` is user config in the same section as `history`."""
        after = _rewritten(b"default-branch = main", b"default-branch = trunk")
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_the_same_key_in_another_section_is_a_change(self):
        """The exemption is a `(section, key)` pair, not a key name anywhere."""
        after = _CONFIG + b"[fetch]\n\thistory = 1\n"
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_section_merely_starting_with_worktrunk_is_a_change(self):
        """A prefix match on the section name would exempt an unrelated section."""
        after = _CONFIG + b"[worktrunkish]\n\tvalue = 1\n"
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_key_inside_the_section_after_an_external_one_is_a_change(self):
        """The exemption ends at the next header, it does not run to EOF."""
        after = _rewritten(b"gpgsign = false", b"gpgsign = true")
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_leaked_test_repo_is_caught_by_its_identity(self):
        """What bounds the cost of exempting `branch`.

        A test that escapes into the real repo sets up a repo: identity first,
        branches as a side effect of committing. The identity write is what the
        guard was built for, and it is still here.
        """
        after = _rewritten(b"dev@example.com", b"test@test.com") + (
            b'[branch "tmp"]\n\tremote = origin\n'
        )
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_config_that_appears_is_a_change(self):
        assert _guarded_lines(_CONFIG) != _guarded_lines(None)

    def test_a_section_opened_for_a_guarded_key_is_a_change(self):
        """Only headers left empty by an exemption are dropped, not headers as such."""
        after = _CONFIG + b"[gc]\n\tauto = 0\n"
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)


class TestTheFailureNamesTheKey:
    def test_it_reports_both_sides_of_the_change(self):
        after = _rewritten(b"dev@example.com", b"test@example.com")
        described = _describe_config_change(
            _guarded_lines(_CONFIG), _guarded_lines(after),
        )
        assert "-email = dev@example.com" in described
        assert "+email = test@example.com" in described

    def test_it_leaves_the_untouched_lines_out(self):
        after = _rewritten(b"dev@example.com", b"test@example.com")
        described = _describe_config_change(
            _guarded_lines(_CONFIG), _guarded_lines(after),
        )
        assert "repositoryformatversion" not in described

    def test_it_stays_quiet_when_nothing_moved(self):
        assert not _describe_config_change(
            _guarded_lines(_CONFIG), _guarded_lines(_CONFIG),
        ).strip()

    def test_it_reports_a_reorder_rather_than_going_quiet(self):
        described = _describe_config_change([b"[user]", b"\tx = 1"], [b"\tx = 1", b"[user]"])
        assert described


class TestTheGuardItself:
    """The check the fixture runs at teardown, over a stand-in config file.

    A real file rather than a path that names nothing: the guard writes now, and
    a check that never let it write would not notice if it stopped.
    """

    @staticmethod
    def _check(tmp_path, after: bytes | None, before: bytes | None = _CONFIG):
        path = tmp_path / "config"
        if after is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(after)
        _assert_config_unchanged(path, before, after)
        return path

    def test_an_untouched_config_passes(self, tmp_path):
        self._check(tmp_path, _CONFIG)

    def test_a_concurrent_marker_write_passes(self, tmp_path):
        refreshed = _rewritten(b"1786075417", b"1786075999")
        assert self._check(tmp_path, refreshed).read_bytes() == refreshed

    def test_a_write_to_a_guarded_key_fails(self, tmp_path):
        with pytest.raises(AssertionError, match=re.escape(str(tmp_path))):
            self._check(tmp_path, _rewritten(b"dev@example.com", b"test@example.com"))

    def test_the_failure_names_the_key(self, tmp_path):
        with pytest.raises(AssertionError, match=r"\+email = test@example.com"):
            self._check(tmp_path, _rewritten(b"dev@example.com", b"test@example.com"))

    def test_the_failure_does_not_accuse_the_running_test(self, tmp_path):
        """A leak out of another process lands on whichever test was in flight."""
        with pytest.raises(AssertionError, match="need not be this test"):
            self._check(tmp_path, _rewritten(b"dev@example.com", b"test@example.com"))

    def test_a_config_created_mid_test_fails(self, tmp_path):
        with pytest.raises(AssertionError, match=r"\+\[core\]"):
            self._check(tmp_path, _CONFIG, before=None)


class TestTheGuardRestoresWhatItCatches:
    """The other half of catching a leak: the operator should not have to undo it."""

    def test_the_snapshotted_bytes_come_back(self, tmp_path):
        path = tmp_path / "config"
        path.write_bytes(_rewritten(b"dev@example.com", b"test@example.com"))

        with pytest.raises(AssertionError):
            _assert_config_unchanged(path, _CONFIG, path.read_bytes())

        assert path.read_bytes() == _CONFIG

    def test_a_config_that_did_not_exist_is_removed_again(self, tmp_path):
        path = tmp_path / "config"
        path.write_bytes(_CONFIG)

        with pytest.raises(AssertionError):
            _assert_config_unchanged(path, None, path.read_bytes())

        assert not path.exists()

    def test_an_external_write_is_left_where_it_landed(self, tmp_path):
        """Restoring an exempt write would undo worktrunk's own state for it."""
        refreshed = _rewritten(b"1786075417", b"1786075999")
        path = tmp_path / "config"
        path.write_bytes(refreshed)

        _assert_config_unchanged(path, _CONFIG, refreshed)

        assert path.read_bytes() == refreshed


class TestTheInheritedGitEnvironment:
    """The other end of the leak: the environment that redirects git at all."""

    def test_no_override_survives_into_a_test(self):
        """Asked against lib/gitenv.py's list rather than conftest's own, so a
        conftest that clears a shorter one than the gates do fails here."""
        assert [n for n in gitenv.GIT_ENV_OVERRIDES if n in os.environ] == []


class TestALeakedIdentityIsCaughtAndUndone:
    """The whole guard, driven by the write it exists for.

    `git config` is run for real, against a repository the caller did not name:
    git reads GIT_DIR ahead of the directory `-C` moved to, which is how a test
    that builds a repo under tmp_path writes its identity somewhere else. The
    pre-push hook exports GIT_DIR, so this is the shape the leak arrives in.
    """

    @staticmethod
    def _leak(tmp_path) -> tuple[Path, bytes]:
        elsewhere = seed_repo(tmp_path / "elsewhere")
        config = elsewhere / ".git" / "config"
        before = config.read_bytes()

        named = init_worktree(tmp_path / "named")
        subprocess.run(
            ["git", "-C", str(named), "config", "user.email", "test@example.com"],
            check=True, capture_output=True,
            env={**os.environ, "GIT_DIR": str(elsewhere / ".git")},
        )
        return config, before

    def test_the_write_lands_on_the_repo_git_dir_names(self, tmp_path):
        """The premise: without this, the rest of the class proves nothing."""
        config, before = self._leak(tmp_path)
        assert b"test@example.com" in config.read_bytes()
        assert b"test@example.com" not in before

    def test_the_guard_catches_it(self, tmp_path):
        config, before = self._leak(tmp_path)
        with pytest.raises(AssertionError, match=r"\+\s*email = test@example.com"):
            _assert_config_unchanged(config, before, config.read_bytes())

    def test_the_guard_undoes_it(self, tmp_path):
        config, before = self._leak(tmp_path)
        with pytest.raises(AssertionError):
            _assert_config_unchanged(config, before, config.read_bytes())
        assert config.read_bytes() == before


class TestSectionNames:
    def test_a_plain_section(self):
        assert _section_of(b"[core]") == (b"core", b"")

    def test_a_subsection(self):
        assert _section_of(b'[worktrunk "state.main"]') == (b"worktrunk", b"state.main")

    def test_a_subsection_holding_a_bracket(self):
        assert _section_of(b'[branch "feat[1]"]') == (b"branch", b"feat[1]")

    def test_a_branch_named_like_the_state_namespace(self):
        assert _section_of(b'[branch "state.main"]') == (b"branch", b"state.main")

    def test_a_section_merely_starting_with_worktrunk(self):
        assert _section_of(b"[worktrunkish]") == (b"worktrunkish", b"")

    def test_a_value_line_is_not_a_section(self):
        assert _section_of(b"marker = {}") is None


class TestReviewEnvGuard:
    def test_the_running_test_sees_no_review_config(self):
        """The contract itself: this assertion is what an exported var breaks."""
        assert _review_env_keys() == []

    def test_the_shell_value_is_hidden_then_restored(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_THINKING", "xhigh")
        guard = _clear_review_env.__wrapped__()

        next(guard)
        assert "CLAUDE_REVIEW_THINKING" not in os.environ

        next(guard, None)
        assert os.environ["CLAUDE_REVIEW_THINKING"] == "xhigh"

    def test_a_var_the_test_set_does_not_survive_it(self, monkeypatch):
        guard = _clear_review_env.__wrapped__()
        next(guard)

        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-haiku-4-5")
        next(guard, None)
        assert "CLAUDE_REVIEW_SCOUT_MODEL" not in os.environ
