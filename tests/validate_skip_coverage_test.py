"""Tests for bin/local/validate-skip-coverage.

The two fixtures that matter most are DEFECT and LEGITIMATE below. Their skip
lines are byte-identical — that is the whole reason this check reads the test
body instead of the guard — so a change that makes one of them pass and the
other fail is the change this suite exists to catch.
"""

from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-skip-coverage"

vsc = load_script("validate_skip_coverage", SCRIPT)

GUARD = 'if [[ "$OSTYPE" != "darwin"* ]]; then skip "macOS only"; return; fi'

# Reduced from tests/maintenance_auto_tasks.bats as it stood at e2494cb7, the
# commit that introduced the defect. The subject is a tracked template.
DEFECT = f"""\
@test "the rendered plist abandons the process group" {{
  {GUARD}
  local rendered="$TEST_HOME/rendered.plist"
  sed -e "s|__INTERVAL__|43200|g" \\
    "$REPO_ROOT/maintenance/maintenance.plist.template" > "$rendered"
  grep -A1 '<key>AbandonProcessGroup</key>' "$rendered" | grep -q '<true/>'
}}
"""

# Reduced from tests/install_symlink.bats:70-83. Same guard line, but the
# subject is BSD `ln -sfh` behaviour observed on $TMPDIR fixtures.
LEGITIMATE = f"""\
@test "re-running ln -sfh on an existing file symlink replaces the symlink" {{
  {GUARD}
  echo "original" > "$TMPDIR/file.txt"
  ln -s "$TMPDIR/file.txt" "$TMPDIR/link"
  ln -sfh "$TMPDIR/file.txt" "$TMPDIR/link"
  [ -L "$TMPDIR/link" ]
}}
"""


def _check(tmp_path, source):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vsc.check_file(str(path))


def test_the_defect_fires(tmp_path):
    findings = _check(tmp_path, DEFECT)
    assert len(findings) == 1
    _, name, guard, reads = findings[0]
    assert name == "the rendered plist abandons the process group"
    assert "OSTYPE" in guard
    assert "maintenance.plist.template" in reads


def test_the_legitimate_platform_skip_passes(tmp_path):
    # The false-positive guard. These two fixtures differ only in the body.
    assert _check(tmp_path, LEGITIMATE) == []


def test_the_two_fixtures_share_a_guard_line(tmp_path):
    # Pins the premise the whole check rests on. If someone edits one fixture's
    # guard, the pair stops testing the discrimination it was built to test and
    # the suite above would still pass.
    assert GUARD in DEFECT
    assert GUARD in LEGITIMATE


def test_the_portable_fix_is_silent(tmp_path):
    # What the hand fix at fc76ca33 did: drop the skip, read the file portably.
    assert _check(tmp_path, DEFECT.replace(f"  {GUARD}\n", "")) == []


def test_a_repo_path_reached_through_a_variable_fires(tmp_path):
    source = f"""\
@test "indirect" {{
  {GUARD}
  local tmpl="$REPO_ROOT/maintenance/maintenance.plist.template"
  grep -q '<true/>' "$tmpl"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_a_repo_path_reached_via_bats_test_filename_fires(tmp_path):
    # The idiom this repo actually uses three times over (tests/bin_scripts.bats,
    # tests/claude_settings.bats, tests/registry_permissions.bats): deriving the
    # repo root by walking up from the running test file.
    source = f"""\
@test "derived via filename" {{
  {GUARD}
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  grep -q 'x' "$repo_root/some/tracked/file"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_a_variable_name_prefix_does_not_taint_a_distinct_variable(tmp_path):
    # `target` and `targetdir` are unrelated variables. Tainting one must not
    # cause a read through the other to be reported.
    source = f"""\
@test "prefix collision" {{
  {GUARD}
  local target="$REPO_ROOT/x.template"
  local targetdir="$TMPDIR/other"
  grep -q 'x' "$targetdir/file"
}}
"""
    assert _check(tmp_path, source) == []


def test_untainting_a_variable_does_not_withdraw_a_distinct_finding(tmp_path):
    # Reassigning `target` away from the repo must not withdraw a finding that
    # only mentions the unrelated, still-tainted `targetdir`.
    source = f"""\
@test "prefix collision on withdrawal" {{
  {GUARD}
  local targetdir="$REPO_ROOT/x.template"
  grep -q 'x' "$targetdir/file"
  local target="$TMPDIR/other"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_an_exported_repo_path_variable_is_tainted(tmp_path):
    source = f"""\
@test "exported" {{
  {GUARD}
  export tmpl="$REPO_ROOT/maintenance/maintenance.plist.template"
  grep -q '<true/>' "$tmpl"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_an_identifier_prefixed_by_the_keyword_local_is_not_misparsed(tmp_path):
    # `local\s+` must be consumed only as the keyword, not swallow into an
    # identifier that happens to start with the same letters.
    source = f"""\
@test "prefix of local" {{
  {GUARD}
  localvar="$REPO_ROOT/x.template"
  grep -q 'x' "$localvar"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_a_variable_reassigned_away_from_the_repo_stops_counting(tmp_path):
    source = f"""\
@test "reassigned" {{
  {GUARD}
  local target="$REPO_ROOT/x.template"
  target="$TMPDIR/local-copy"
  grep -q 'x' "$target"
}}
"""
    assert _check(tmp_path, source) == []


def test_non_platform_skips_pass(tmp_path):
    # The three real shapes in this repo: a missing tool, a missing fixture,
    # and environment scoping. None is a platform claim.
    for guard in (
        'command -v task > /dev/null || skip "task not installed"',
        '[[ -f "$registry" ]] || skip "no registry"',
        '[[ -z "${CI:-}" ]] || skip "local only"',
    ):
        source = f"""\
@test "not a platform skip" {{
  {guard}
  grep -q 'x' "$REPO_ROOT/some/tracked/file"
}}
"""
        assert _check(tmp_path, source) == [], guard


def test_a_skip_in_setup_is_not_reported(tmp_path):
    # A file-level skip is a deliberate statement about the whole suite, not
    # one case quietly losing its assertion.
    source = f"""\
setup() {{
  {GUARD}
}}

@test "reads a tracked file" {{
  grep -q 'x' "$REPO_ROOT/some/tracked/file"
}}
"""
    assert _check(tmp_path, source) == []


def test_the_marker_suppresses_with_a_reason(tmp_path):
    source = f"""\
# platform-only: launchd's plist loader exists on no other OS
{DEFECT}"""
    assert _check(tmp_path, source) == []


def test_a_bare_marker_does_not_suppress(tmp_path):
    # Same grammar as `ceiling:` and `passes-at-base:` — a marker with no
    # reason after the colon declares nothing.
    source = f"""\
# platform-only:
{DEFECT}"""
    assert len(_check(tmp_path, source)) == 1


def test_an_unrelated_comment_above_the_test_does_not_suppress(tmp_path):
    source = f"""\
# platform-only: a real reason, but for the test below this one
@test "unrelated" {{
  true
}}

{DEFECT}"""
    assert len(_check(tmp_path, source)) == 1


def test_a_comment_mentioning_skip_is_not_a_skip(tmp_path):
    source = """\
@test "commented" {
  # we used to skip this on darwin, but OSTYPE handling was fixed
  grep -q 'x' "$REPO_ROOT/some/tracked/file"
}
"""
    assert _check(tmp_path, source) == []


def test_a_hash_inside_a_quoted_string_is_not_a_comment(tmp_path):
    # The defect's own body greps for an XML tag containing no hash, but the
    # neighbouring plist work does — a naive comment strip would cut the line
    # before its $REPO_ROOT and miss the finding.
    source = f"""\
@test "hash in a string" {{
  {GUARD}
  grep -c '#' "$REPO_ROOT/maintenance/maintenance.plist.template"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_a_nested_brace_does_not_end_the_body_early(tmp_path):
    # A subshell or function closing at column 0 inside a case would end the
    # block early under a naive parser, dropping the assertion that carries the
    # repo reference.
    source = f"""\
@test "nested" {{
  {GUARD}
  run bash -c '
    echo nested
'
  grep -q 'x' "$REPO_ROOT/some/tracked/file"
}}
"""
    assert len(_check(tmp_path, source)) == 1


def test_the_real_suites_are_clean():
    # The check must be silent on the tree as it stands, or it is a gate that
    # gets turned off rather than one that gets obeyed.
    offenders = [
        path for path in vsc.discover_suites(str(REPO_ROOT))
        if vsc.check_file(path)
    ]
    assert offenders == []
