"""Tests for bin/local/validate-new-tests.

The diff parsing and the marker grammar are pure and tested directly. The
runner half — create a base worktree, stage the changed test files, run the
suite there — is exercised through `_added_tests` and `_changed_test_support`
against real `git diff` output in a fixture repo, because the thing most likely
to break is which lines it reads as a new test, not the subprocess plumbing.
"""

import subprocess
from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-new-tests"

vnt = load_script("validate_new_tests", SCRIPT)


def _repo(tmp_path):
    """A git repo with one commit, returned as its path."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.bats").write_text('@test "already here" {\n  true\n}\n')
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    return tmp_path


def _commit(repo, message="change"):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)


# ── Which lines are a new test ───────────────────────────────────────────────


def test_an_added_bats_test_is_found(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text(
        '@test "already here" {\n  true\n}\n\n@test "brand new" {\n  true\n}\n'
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert [t.name for t in found] == ["brand new"]


def test_an_added_pytest_test_is_found(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "b_test.py").write_text("def test_new_thing():\n    assert True\n")
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert [t.name for t in found] == ["test_new_thing"]
    # Both pytest naming conventions are in use in this repo.
    assert found[0].file.endswith("b_test.py")


def test_an_untouched_test_is_not_reported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text(
        '@test "already here" {\n  true\n}\n\n@test "new one" {\n  true\n}\n'
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert "already here" not in [t.name for t in found]


def test_a_removed_test_is_not_reported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text("")
    _commit(repo)

    assert vnt._added_tests("main~1", repo) == []


def test_a_diff_with_no_test_changes_finds_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src.sh").write_text("echo hi\n")
    _commit(repo)

    assert vnt._added_tests("main~1", repo) == []


# ── The escape hatch ─────────────────────────────────────────────────────────


def test_a_marked_test_carries_its_reason(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text(
        '@test "already here" {\n  true\n}\n\n'
        "# passes-at-base: asserts behaviour this change preserves\n"
        '@test "preserved" {\n  true\n}\n'
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert len(found) == 1
    assert found[0].marker_reason == "asserts behaviour this change preserves"


def test_a_marker_with_no_reason_is_not_a_marker(tmp_path):
    # Same grammar as `ceiling:` — a bare marker declares nothing, and letting
    # it suppress the finding would make the escape hatch the default.
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text(
        '@test "already here" {\n  true\n}\n\n'
        "# passes-at-base:\n"
        '@test "unjustified" {\n  true\n}\n'
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert found[0].marker_reason is None


def test_a_marker_two_lines_up_does_not_apply(tmp_path):
    # It has to sit immediately above the test, or it is ambiguous which test
    # it excuses.
    repo = _repo(tmp_path)
    (repo / "tests" / "a.bats").write_text(
        '@test "already here" {\n  true\n}\n\n'
        "# passes-at-base: a reason\n"
        "\n"
        '@test "far away" {\n  true\n}\n'
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert found[0].marker_reason is None


def test_a_pytest_marker_applies_to_the_def_below_it(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "b_test.py").write_text(
        "# passes-at-base: covers a path this change leaves alone\n"
        "def test_preserved():\n    assert True\n"
    )
    _commit(repo)

    found = vnt._added_tests("main~1", repo)
    assert found[0].marker_reason == "covers a path this change leaves alone"


# ── Which support files travel with the suite ────────────────────────────────


def test_changed_helpers_are_staged_too(tmp_path):
    # A suite copied into the base worktree without its changed helper fails
    # with `command not found`, which reads as "the test correctly fails at
    # base" and proves nothing.
    repo = _repo(tmp_path)
    (repo / "tests" / "test_helper.bash").write_text("helper() { true; }\n")
    (repo / "tests" / "a.bats").write_text('@test "x" {\n  true\n}\n')
    _commit(repo)

    support = vnt._changed_test_support("main~1", repo)
    assert "tests/test_helper.bash" in support


def test_a_changed_fixture_is_staged(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "fixtures").mkdir()
    (repo / "tests" / "fixtures" / "thing.json").write_text("{}\n")
    _commit(repo)

    support = vnt._changed_test_support("main~1", repo)
    assert "tests/fixtures/thing.json" in support


def test_source_changes_are_not_staged(tmp_path):
    # Staging a source file would defeat the whole check: the base worktree
    # would hold the fix, and every new test would pass there.
    repo = _repo(tmp_path)
    (repo / "src.sh").write_text("echo hi\n")
    _commit(repo)

    assert vnt._changed_test_support("main~1", repo) == []


# ── Findings ─────────────────────────────────────────────────────────────────


def test_a_test_passing_at_base_is_a_finding():
    tests = [vnt.NewTest("tests/a.bats", "vacuous", 3, None)]
    findings = vnt._findings(tests, passed_at_base={"vacuous"})
    assert [f.name for f in findings] == ["vacuous"]


def test_a_test_failing_at_base_is_not_a_finding():
    tests = [vnt.NewTest("tests/a.bats", "real", 3, None)]
    assert vnt._findings(tests, passed_at_base=set()) == []


def test_a_marked_test_passing_at_base_is_not_a_finding():
    tests = [vnt.NewTest("tests/a.bats", "preserved", 3, "behaviour kept")]
    assert vnt._findings(tests, passed_at_base={"preserved"}) == []


def test_a_marked_test_that_fails_at_base_is_a_stale_marker():
    # The marker says "this passes at base"; it does not. Either the test
    # changed or the marker was wrong — both worth saying, because a marker
    # nobody rechecks is how the hatch becomes the default.
    tests = [vnt.NewTest("tests/a.bats", "moved on", 3, "used to be preserved")]
    findings = vnt._findings(tests, passed_at_base=set())
    assert [f.kind for f in findings] == ["stale-marker"]


# ── The vacuity discriminator ────────────────────────────────────────────────
# Passing at base alone is right about one time in eight on this repo's own
# history — a negative or back-compat case passes at base by design. The pair
# below is what separates the real defect from those, and the two cases that
# matter are taken verbatim from the commit that prompted the tool.


def test_an_absence_assertion_with_no_creation_is_vacuous():
    # The real one, from tests/pi_extensions.bats at c372843d: it asserted
    # _shared was not installed, in a fixture that never put a _shared
    # anywhere. It passed on an empty directory.
    body = '''@test "a shared module is not installed as an extension" {
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/_shared" ]
}'''
    assert vnt._asserts_absence_it_never_created(body) == "_shared"


def test_the_same_test_with_a_fixture_is_not_vacuous():
    # The corrected version, from the same file at 79e7de0a. It creates the
    # directory the installer might wrongly pick up, so the absence assertion
    # now has something to be about.
    body = '''@test "a shared module is not installed as an extension" {
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/_shared"
  printf 'export function helper() {}\\n' > "$FAKE_WORKBENCH/ai/pi/extensions/_shared/util.ts"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/_shared" ]
}'''
    assert vnt._asserts_absence_it_never_created(body) is None


def test_a_negative_case_with_no_absence_assertion_is_not_vacuous():
    # "allows gh issue create when the branch has no review" — one of the seven
    # legitimate tests the base-pass filter flagged. It asserts an exit status,
    # not an absence, so this filter clears it.
    body = '''@test "guard: allows gh issue create when the branch has no review" {
  sandbox="$(_review_sandbox "isaac/fix/thing")"
  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create"}}'
  [ "$status" -eq 0 ]
}'''
    assert vnt._asserts_absence_it_never_created(body) is None


def test_a_file_absence_assertion_is_read_too():
    body = '''@test "writes nothing" {
  run_thing
  [ ! -f "$OUT/report.md" ]
}'''
    assert vnt._asserts_absence_it_never_created(body) == "report.md"


def test_a_python_absence_assertion_is_read():
    body = '''def test_nothing_written(tmp_path):
    run_thing(tmp_path)
    assert not (tmp_path / "ledger.json").exists()
'''
    assert vnt._asserts_absence_it_never_created(body) == "ledger.json"


def test_a_python_test_that_creates_it_first_is_not_vacuous():
    body = '''def test_left_alone(tmp_path):
    (tmp_path / "ledger.json").write_text("{}")
    run_thing(tmp_path)
    assert not (tmp_path / "ledger.json").exists()
'''
    assert vnt._asserts_absence_it_never_created(body) is None


def test_a_bare_variable_subject_is_not_guessed_at():
    # "$SOME_PATH" with no leaf names nothing this can reason about, and
    # guessing would be the false positive that gets the check disabled.
    body = '''@test "x" {
  [ ! -e "$SOME_PATH" ]
}'''
    assert vnt._asserts_absence_it_never_created(body) is None


# ── Ranking ──────────────────────────────────────────────────────────────────


def test_a_vacuous_test_outranks_a_plain_base_pass(tmp_path):
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "a.bats").write_text(
        '@test "vacuous" {\n  run_thing\n  [ ! -e "$OUT/never_made" ]\n}\n'
    )
    tests = [vnt.NewTest("tests/a.bats", "vacuous", 1, None)]

    findings = vnt._findings(tests, passed_at_base={"vacuous"}, repo=tmp_path)
    assert [f.kind for f in findings] == ["vacuous"]
    assert findings[0].subject == "never_made"


def test_a_base_pass_without_the_shape_stays_the_weak_finding(tmp_path):
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "a.bats").write_text('@test "negative" {\n  run x\n  [ "$status" -eq 0 ]\n}\n')
    tests = [vnt.NewTest("tests/a.bats", "negative", 1, None)]

    findings = vnt._findings(tests, passed_at_base={"negative"}, repo=tmp_path)
    assert [f.kind for f in findings] == ["passes-at-base"]


def test_a_vacuous_shape_that_fails_at_base_is_not_reported(tmp_path):
    # The conjunction is the point. A test with this shape that genuinely fails
    # without the change is constraining something, whatever its shape.
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "a.bats").write_text('@test "real" {\n  [ ! -e "$OUT/never_made" ]\n}\n')
    tests = [vnt.NewTest("tests/a.bats", "real", 1, None)]

    assert vnt._findings(tests, passed_at_base=set(), repo=tmp_path) == []
