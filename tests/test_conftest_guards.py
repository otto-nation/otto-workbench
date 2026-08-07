"""Tests for the shared-config guard in conftest.

The guard compares the repo's git config around every test, so anything that
writes to that file from outside the test process fails whichever test happened
to be running. worktrunk does exactly that — it stamps a per-branch marker with
a timestamp — which is what these cover.
"""

from pathlib import Path

import pytest

from conftest import (
    _assert_config_unchanged, _describe_config_change, _guarded_lines, _section_name,
)

_CONFIG = b"""[core]
\trepositoryformatversion = 0
[user]
\temail = dev@example.com
[worktrunk "state.main"]
\tmarker = {\\"marker\\":\\"\xf0\x9f\xa4\x96\\",\\"set_at\\":1786075417}
[branch "main"]
\tremote = origin
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

    def test_a_key_inside_the_section_after_an_external_one_is_a_change(self):
        after = _rewritten(b"\tremote = origin", b"\tremote = evil")
        assert _guarded_lines(after) != _guarded_lines(_CONFIG)

    def test_a_config_that_appears_is_a_change(self):
        assert _guarded_lines(_CONFIG) != _guarded_lines(None)


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

    def test_it_reports_a_reorder_rather_than_going_quiet(self):
        described = _describe_config_change([b"[user]", b"\tx = 1"], [b"\tx = 1", b"[user]"])
        assert described


class TestTheGuardItself:
    """The check the fixture runs at teardown, over a stand-in config path."""

    @staticmethod
    def _check(after: bytes | None, before: bytes | None = _CONFIG):
        _assert_config_unchanged(Path("/repo/.git/config"), before, after)

    def test_an_untouched_config_passes(self):
        self._check(_CONFIG)

    def test_a_concurrent_marker_write_passes(self):
        self._check(_rewritten(b"1786075417", b"1786075999"))

    def test_a_write_to_a_guarded_key_fails(self):
        with pytest.raises(AssertionError, match="/repo/.git/config"):
            self._check(_rewritten(b"dev@example.com", b"test@example.com"))

    def test_the_failure_names_the_key(self):
        with pytest.raises(AssertionError, match=r"\+email = test@example.com"):
            self._check(_rewritten(b"dev@example.com", b"test@example.com"))

    def test_a_config_created_mid_test_fails(self):
        with pytest.raises(AssertionError, match=r"\+\[core\]"):
            self._check(_CONFIG, before=None)


class TestSectionNames:
    def test_a_plain_section(self):
        assert _section_name(b"[core]") == b"core"

    def test_a_subsection(self):
        assert _section_name(b'[worktrunk "state.main"]') == b"worktrunk"

    def test_a_subsection_holding_a_bracket(self):
        assert _section_name(b'[branch "feat[1]"]') == b"branch"

    def test_a_value_line_is_not_a_section(self):
        assert _section_name(b"marker = {}") is None
