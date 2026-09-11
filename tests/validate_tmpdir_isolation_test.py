"""Tests for bin/local/validate-tmpdir-isolation."""

from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-tmpdir-isolation"

vti = load_script("validate_tmpdir_isolation", SCRIPT)

MKTEMP_PIN = 'setup() {\n  TMPDIR="$(mktemp -d)"\n}\n'


def _check(tmp_path, source, helpers=()):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vti.check_file(str(path), list(helpers))


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
    assert 3 in [line for line, _ in offenders]


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


# ── the repo itself ──────────────────────────────────────────────────────


def test_every_bats_suite_in_the_repo_pins_tmpdir():
    helpers = vti._helper_names(str(REPO_ROOT))
    offenders = {
        Path(path).name: vti.check_file(path, helpers)
        for path in vti.discover_suites(str(REPO_ROOT))
    }
    assert {name: found for name, found in offenders.items() if found} == {}
