"""Tests for bin/local/validate-tmpdir-isolation."""

from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-tmpdir-isolation"

vti = load_script("validate_tmpdir_isolation", SCRIPT)

# A mktemp pin isolates the case correctly, which is all check_file judges, so
# it stays the fixture for the isolation tests below. The separate rule that
# rejects it as a pin bats cannot collect is exercised through check_file_pins.
MKTEMP_PIN = 'setup() {\n  TMPDIR="$(mktemp -d)"\n}\n'
BATS_PIN = 'setup() {\n  TMPDIR="$BATS_TEST_TMPDIR"\n}\n'


def _check(tmp_path, source, helpers=()):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vti.check_file(str(path), list(helpers))


def _wipes(tmp_path, source):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vti.check_file_wipes(str(path))


def _pins(tmp_path, source):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vti.check_file_pins(str(path))


# ── suites that reference nothing ────────────────────────────────────────


def test_a_suite_that_never_mentions_tmpdir_passes(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  run some_command\n}\n') == []


def test_a_pin_with_no_reference_after_it_passes(tmp_path):
    assert _check(tmp_path, MKTEMP_PIN) == []


# ── the pinned forms ─────────────────────────────────────────────────────


def test_a_mktemp_pin_in_setup_covers_a_reference_in_a_test(tmp_path):
    source = MKTEMP_PIN + '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n'
    assert _check(tmp_path, source) == []


def test_a_bats_test_tmpdir_pin_covers_the_file(tmp_path):
    source = 'setup() {\n  TMPDIR="$BATS_TEST_TMPDIR"\n}\n@test "a" {\n  echo x > "$TMPDIR/f"\n}\n'
    assert _check(tmp_path, source) == []


def test_a_bats_file_tmpdir_pin_covers_the_file(tmp_path):
    """bats does not parallelise within a file, so per-file scratch is enough."""
    source = 'setup() {\n  TMPDIR="$BATS_FILE_TMPDIR/x"\n}\n@test "a" {\n  echo x > "$TMPDIR/f"\n}\n'
    assert _check(tmp_path, source) == []


def test_a_pin_in_setup_file_covers_the_file(tmp_path):
    source = ('setup_file() {\n  TMPDIR="$(mktemp -d)"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    assert _check(tmp_path, source) == []


def test_an_exported_pin_counts(tmp_path):
    source = 'setup() {\n  export TMPDIR="$(mktemp -d)"\n}\n@test "a" {\n  echo x > "$TMPDIR/f"\n}\n'
    assert _check(tmp_path, source) == []


def test_a_pin_through_an_intermediate_variable_resolves(tmp_path):
    """SANDBOX="$(mktemp -d)" ... export TMPDIR="$SANDBOX" is the pre_push_hook form."""
    source = ('setup() {\n  SANDBOX="$(mktemp -d)"\n  export TMPDIR="$SANDBOX"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    assert _check(tmp_path, source) == []


def test_a_pin_through_a_bats_rooted_variable_resolves(tmp_path):
    """SHARED_DIR="$BATS_FILE_TMPDIR/x" ... TMPDIR="$SHARED_DIR" is validate_registries' form."""
    source = ('setup() {\n  SHARED_DIR="$BATS_FILE_TMPDIR/validate"\n  TMPDIR="$SHARED_DIR"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    assert _check(tmp_path, source) == []


def test_a_pin_inside_the_test_that_needs_it_covers_that_test(tmp_path):
    source = '@test "a" {\n  TMPDIR="$(mktemp -d)"\n  echo x > "$TMPDIR/f"\n}\n'
    assert _check(tmp_path, source) == []


# ── the unpinned forms ───────────────────────────────────────────────────


def test_a_reference_with_no_pin_anywhere_is_reported(tmp_path):
    offenders = _check(tmp_path, '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    assert [line for line, _ in offenders] == [2]


def test_the_braced_form_is_reported_too(tmp_path):
    offenders = _check(tmp_path, '@test "a" {\n  echo x > "${TMPDIR}/f"\n}\n')
    assert [line for line, _ in offenders] == [2]


def test_a_pin_in_one_test_does_not_cover_another(tmp_path):
    source = ('@test "a" {\n  TMPDIR="$(mktemp -d)"\n  echo x > "$TMPDIR/f"\n}\n'
              '@test "b" {\n  echo x > "$TMPDIR/g"\n}\n')
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [6]


def test_a_reference_above_its_own_scopes_pin_is_reported(tmp_path):
    source = '@test "a" {\n  echo x > "$TMPDIR/f"\n  TMPDIR="$(mktemp -d)"\n}\n'
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [2]


def test_a_pin_in_teardown_does_not_cover_the_tests(tmp_path):
    """teardown runs after the body that already wrote to the shared directory."""
    source = ('teardown() {\n  TMPDIR="$(mktemp -d)"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [5]


def test_bats_run_tmpdir_is_not_a_safe_root(tmp_path):
    """BATS_RUN_TMPDIR is shared by the whole run — the very collision at issue."""
    source = ('setup() {\n  TMPDIR="$BATS_RUN_TMPDIR"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [5]


def test_a_command_prefix_assignment_pins_nothing_after_it(tmp_path):
    """TMPDIR=x cmd scopes to that command only; the next line is unpinned again."""
    source = '@test "a" {\n  TMPDIR="$scratch" some_cmd\n  echo x > "$TMPDIR/f"\n}\n'
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [3]


# ── comments ─────────────────────────────────────────────────────────────


def test_a_commented_reference_is_not_a_reference(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  # writes to $TMPDIR/f\n  run cmd\n}\n') == []


def test_a_commented_pin_does_not_count_as_one(tmp_path):
    source = ('setup() {\n  # TMPDIR="$(mktemp -d)"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [5]


# ── longer names sharing the prefix ──────────────────────────────────────


def test_a_longer_variable_ending_in_tmpdir_is_not_a_reference(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  echo x > "$BATS_TEST_TMPDIR/f"\n}\n') == []


def test_a_longer_variable_starting_with_tmpdir_is_not_a_reference(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  echo x > "$TMPDIR_LOCAL/f"\n}\n') == []


def test_a_longer_variable_is_not_a_pin_either(tmp_path):
    source = ('setup() {\n  SHARED_TMPDIR="$(mktemp -d)"\n}\n'
              '@test "a" {\n  echo x > "$TMPDIR/f"\n}\n')
    offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [5]


# ── helper calls ─────────────────────────────────────────────────────────


def test_calling_a_tmpdir_writing_helper_unpinned_is_reported(tmp_path):
    source = '@test "a" {\n  make_fake_gh 0 out\n}\n'
    offenders = _check(tmp_path, source, helpers=["make_fake_gh"])
    assert [line for line, _ in offenders] == [2]


def test_calling_a_tmpdir_writing_helper_under_a_pin_passes(tmp_path):
    source = MKTEMP_PIN + '@test "a" {\n  make_fake_gh 0 out\n}\n'
    assert _check(tmp_path, source, helpers=["make_fake_gh"]) == []


def test_a_helper_name_embedded_in_a_longer_word_is_not_a_call(tmp_path):
    source = '@test "a" {\n  run xmake_fake_ghx\n}\n'
    assert _check(tmp_path, source, helpers=["make_fake_gh"]) == []


def test_helpers_are_discovered_from_the_real_test_helper(tmp_path):
    """The list is derived, so a new $TMPDIR-writing helper is covered on landing."""
    assert "make_fake_gh" in vti._helper_names(str(REPO_ROOT))


# ── removing $TMPDIR itself ──────────────────────────────────────────────
#
# bats runs teardown() even when setup() failed partway, so a setup that dies
# before its pin line reaches teardown with TMPDIR still naming the machine's
# real temp directory. `rm -rf "$TMPDIR"` there removes all of it. bats already
# collects BATS_TEST_TMPDIR after every case, so the line is never needed.


def test_a_teardown_that_removes_tmpdir_is_reported(tmp_path):
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR"\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_a_one_line_teardown_is_reported(tmp_path):
    """The `; }` terminator is the shape the original bug shipped in."""
    source = BATS_PIN + 'teardown() { rm -rf "$TMPDIR"; }\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [4]


def test_the_unquoted_and_braced_spellings_are_reported(tmp_path):
    source = BATS_PIN + 'teardown() {\n  rm -fr $TMPDIR\n  rm -rf "${TMPDIR}"\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5, 6]


def test_a_removal_in_a_test_body_is_reported(tmp_path):
    """Not restricted to teardown: the same line runs on the same unpinned value."""
    source = BATS_PIN + '@test "a" {\n  rm -rf "$TMPDIR"\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_removing_a_path_under_tmpdir_is_allowed(tmp_path):
    """A subpath cannot escape the pin, and rebuilding a fixture is legitimate."""
    source = BATS_PIN + '@test "a" {\n  rm -rf "$TMPDIR/repo"\n  rm -rf "$TMPDIR/a" "$TMPDIR/b"\n}\n'
    assert _wipes(tmp_path, source) == []


def test_removing_another_variable_is_allowed(tmp_path):
    source = BATS_PIN + 'teardown() {\n  rm -rf "$SCRATCH"\n}\n'
    assert _wipes(tmp_path, source) == []


def test_a_commented_out_removal_is_not_reported(tmp_path):
    source = BATS_PIN + 'teardown() {\n  # rm -rf "$TMPDIR"\n}\n'
    assert _wipes(tmp_path, source) == []


def test_an_end_of_options_marker_does_not_hide_the_wipe(tmp_path):
    """`rm -rf -- "$TMPDIR"` is an ordinary spelling, not an evasion."""
    source = BATS_PIN + 'teardown() {\n  rm -rf -- "$TMPDIR"\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_a_trailing_redirect_does_not_hide_the_wipe(tmp_path):
    """`2>/dev/null` on the end is idiomatic here and must not shield the line."""
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR" 2>/dev/null\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_stacked_trailing_redirects_do_not_hide_the_wipe(tmp_path):
    """`>/dev/null 2>&1` is the house style for a cleanup line elsewhere in this
    suite, and stacking a second redirect must not shield the wipe either."""
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR" >/dev/null 2>&1\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_a_trailing_comment_does_not_hide_the_wipe(tmp_path):
    """strip_comments only blanks whole-line comments, so the terminator must
    accept a trailing `#` itself."""
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR"  # cleanup\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_a_wipe_chained_with_and_is_reported(tmp_path):
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR" && echo done\n}\n'
    assert [line for line, _ in _wipes(tmp_path, source)] == [5]


def test_a_subpath_with_a_trailing_redirect_is_still_allowed(tmp_path):
    """The redirect clause must not turn a legitimate subpath removal into a
    finding — the suffix is what makes it safe, wherever the line ends."""
    source = BATS_PIN + 'teardown() {\n  rm -rf "$TMPDIR/repo" 2>/dev/null\n}\n'
    assert _wipes(tmp_path, source) == []


# ── pins bats will not clean up ──────────────────────────────────────────


def test_a_mktemp_pin_is_reported(tmp_path):
    assert [line for line, _ in _pins(tmp_path, MKTEMP_PIN)] == [2]


def test_a_mktemp_pin_through_an_intermediate_variable_is_reported(tmp_path):
    source = 'setup() {\n  SANDBOX="$(mktemp -d)"\n  export TMPDIR="$SANDBOX"\n}\n'
    assert [line for line, _ in _pins(tmp_path, source)] == [3]


def test_a_bats_pin_is_accepted(tmp_path):
    assert _pins(tmp_path, BATS_PIN) == []


def test_a_resolved_bats_pin_is_accepted(tmp_path):
    """pwd -P on bats scratch: macOS reaches /var/folders through /private/var."""
    source = 'setup() {\n  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"\n}\n'
    assert _pins(tmp_path, source) == []


def test_mktemp_on_another_variable_is_not_a_tmpdir_pin(tmp_path):
    source = 'setup() {\n  TMPDIR="$BATS_TEST_TMPDIR"\n  SCRATCH="$(mktemp -d)"\n}\n'
    assert _pins(tmp_path, source) == []


def test_a_variable_reassigned_away_from_mktemp_stops_being_reported(tmp_path):
    source = ('setup() {\n  SANDBOX="$(mktemp -d)"\n  SANDBOX="$BATS_TEST_TMPDIR"\n'
              '  TMPDIR="$SANDBOX"\n}\n')
    assert _pins(tmp_path, source) == []


# ── the repo itself ──────────────────────────────────────────────────────


def test_every_bats_suite_in_the_repo_pins_tmpdir():
    helpers = vti._helper_names(str(REPO_ROOT))
    offenders = {
        Path(path).name: vti.check_file(path, helpers)
        for path in vti.discover_suites(str(REPO_ROOT))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def test_no_bats_suite_in_the_repo_removes_tmpdir():
    offenders = {
        Path(path).name: vti.check_file_wipes(path)
        for path in vti.discover_suites(str(REPO_ROOT))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


def test_every_pin_in_the_repo_is_bats_owned_scratch():
    offenders = {
        Path(path).name: vti.check_file_pins(path)
        for path in vti.discover_suites(str(REPO_ROOT))
    }
    assert {name: found for name, found in offenders.items() if found} == {}


# ── the pin common_setup states on every suite's behalf ──────────────────


def test_common_setup_covers_a_file_that_pins_nothing_itself(tmp_path):
    """A suite calling common_setup inherits its pin and needs none of its own."""
    source = 'setup() {\n  common_setup\n  FOO="$TMPDIR/x"\n}\n'
    path = tmp_path / "sample.bats"
    path.write_text(source)
    assert vti.check_file(str(path), [], helper_pins=True) == []


def test_a_file_without_common_setup_is_still_reported(tmp_path):
    """The helper's pin covers its callers only — it is not a blanket pass."""
    source = 'setup() {\n  FOO="$TMPDIR/x"\n}\n'
    path = tmp_path / "sample.bats"
    path.write_text(source)
    assert vti.check_file(str(path), [], helper_pins=True) == [(2, 'FOO="$TMPDIR/x"')]


def test_common_setup_covers_nothing_when_the_helper_stops_pinning(tmp_path):
    """With the helper's pin gone, a caller relying on it is unpinned again.

    The repo-wide check passes only because common_setup pins TMPDIR. Were that
    line deleted, 100-odd suites would silently go back to sharing the machine
    directory, so the coverage has to collapse with it rather than persist as a
    property of calling the helper.
    """
    source = 'setup() {\n  common_setup\n  FOO="$TMPDIR/x"\n}\n'
    path = tmp_path / "sample.bats"
    path.write_text(source)
    assert vti.check_file(str(path), [], helper_pins=False) == [(3, 'FOO="$TMPDIR/x"')]


def test_the_real_helper_pins_tmpdir_in_common_setup():
    """The repo-wide pass above is load-bearing on this being true."""
    assert vti._helper_pins_tmpdir(str(REPO_ROOT)) is True


def test_a_helper_whose_common_setup_does_not_pin_reads_as_unpinned(tmp_path):
    helper_dir = tmp_path / "tests"
    helper_dir.mkdir()
    (helper_dir / "test_helper.bash").write_text(
        'common_setup() {\n  unset GIT_DIR\n}\n'
    )
    assert vti._helper_pins_tmpdir(str(tmp_path)) is False
