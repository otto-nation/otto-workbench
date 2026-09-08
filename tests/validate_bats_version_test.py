"""Tests for bin/local/validate-bats-version."""

import sys
from pathlib import Path

import pytest
from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-bats-version"

vbv = load_script("validate_bats_version", SCRIPT)

DECLARATION = "bats_require_minimum_version 1.5.0\n"


def _check(tmp_path, source):
    path = tmp_path / "sample.bats"
    path.write_text(source)
    return vbv.check_file(str(path))


# ── suites that need no declaration ──────────────────────────────────────


def test_a_bare_run_needs_no_declaration(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  run some_command\n}\n') is None


def test_a_run_whose_argument_starts_with_a_dash_is_not_a_flag(tmp_path):
    """`run cmd --flag` passes the flag to the command; run() never sees it."""
    assert _check(tmp_path, '@test "a" {\n  run diff --brief a b\n}\n') is None


def test_a_commented_out_flagged_run_needs_no_declaration(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  # run ! false\n  run false\n}\n') is None


def test_a_word_ending_in_run_is_not_a_run(tmp_path):
    assert _check(tmp_path, '@test "a" {\n  dry_run ! false\n}\n') is None


# ── suites that need one and have it ─────────────────────────────────────


def test_a_declared_bang_flag_passes(tmp_path):
    assert _check(tmp_path, DECLARATION + '@test "a" {\n  run ! false\n}\n') is None


def test_a_declared_exit_code_flag_passes(tmp_path):
    assert _check(tmp_path, DECLARATION + '@test "a" {\n  run -0 true\n}\n') is None


def test_a_declared_long_flag_passes(tmp_path):
    assert _check(tmp_path, DECLARATION + '@test "a" {\n  run --separate-stderr cmd\n}\n') is None


def test_a_declaration_below_the_required_version_is_flagged(tmp_path):
    source = 'bats_require_minimum_version 1.0.0\n' + '@test "a" {\n  run ! false\n}\n'
    reason, offenders = _check(tmp_path, source)
    assert "below the required 1.5.0" in reason
    assert [line for line, _ in offenders] == [3]


def test_a_declaration_at_exactly_the_required_version_passes(tmp_path):
    assert _check(tmp_path, DECLARATION + '@test "a" {\n  run ! false\n}\n') is None


def test_a_declaration_above_the_required_version_passes(tmp_path):
    source = 'bats_require_minimum_version 1.10.0\n' + '@test "a" {\n  run ! false\n}\n'
    assert _check(tmp_path, source) is None


def test_a_flagged_run_on_a_one_line_test_body_is_seen(tmp_path):
    _, offenders = _check(tmp_path, '@test "a" { run ! false; }\n')
    assert [line for line, _ in offenders] == [1]


def test_a_hash_inside_a_quoted_string_is_not_a_comment(tmp_path):
    reason, offenders = _check(tmp_path, '@test "a" {\n  echo "tag #foo"; run ! false\n}\n')
    assert "without bats_require_minimum_version" in reason
    assert [line for line, _ in offenders] == [2]


# ── suites that need one and lack it ─────────────────────────────────────


def test_an_undeclared_bang_flag_is_flagged(tmp_path):
    reason, offenders = _check(tmp_path, '@test "a" {\n  run ! false\n}\n')
    assert "without bats_require_minimum_version" in reason
    assert [line for line, _ in offenders] == [2]


def test_an_undeclared_exit_code_flag_is_flagged(tmp_path):
    reason, offenders = _check(tmp_path, '@test "a" {\n  run -127 missing_cmd\n}\n')
    assert [line for line, _ in offenders] == [2]


def test_an_undeclared_keep_empty_lines_flag_is_flagged(tmp_path):
    reason, offenders = _check(tmp_path, '@test "a" {\n  run --keep-empty-lines cmd\n}\n')
    assert [line for line, _ in offenders] == [2]


def test_a_bare_double_dash_is_flagged(tmp_path):
    """run() sets has_flags before breaking on `--`, so BW02 fires for it too."""
    reason, offenders = _check(tmp_path, '@test "a" {\n  run -- cmd\n}\n')
    assert [line for line, _ in offenders] == [2]


def test_every_undeclared_flagged_run_is_reported(tmp_path):
    source = '@test "a" {\n  run ! false\n  run -0 true\n  run plain\n}\n'
    _, offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [2, 3]


def test_a_flagged_run_after_a_semicolon_is_seen(tmp_path):
    _, offenders = _check(tmp_path, '@test "a" {\n  cd "$d"; run ! false\n}\n')
    assert [line for line, _ in offenders] == [2]


# ── declaration order ────────────────────────────────────────────────────


def test_a_declaration_below_the_first_flagged_run_is_flagged(tmp_path):
    """bats evaluates the file top to bottom, so a late declaration is no declaration."""
    source = '@test "a" {\n  run ! false\n}\n\n' + DECLARATION
    reason, offenders = _check(tmp_path, source)
    assert "after a flagged `run`" in reason
    assert [line for line, _ in offenders] == [2]


def test_only_the_runs_above_the_declaration_are_reported(tmp_path):
    source = '@test "a" {\n  run ! false\n}\n' + DECLARATION + '@test "b" {\n  run -0 true\n}\n'
    _, offenders = _check(tmp_path, source)
    assert [line for line, _ in offenders] == [2]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discovery_finds_the_suites_this_issue_was_filed_about():
    suites = vbv.discover_suites(str(REPO_ROOT))
    assert str(REPO_ROOT / "tests" / "export_config.bats") in suites
    assert str(REPO_ROOT / "tests" / "install_targeted.bats") in suites


def test_repo_is_clean():
    """The rule holds across every suite the validator walks."""
    offenders = {
        path: vbv.check_file(path)
        for path in vbv.discover_suites(str(REPO_ROOT))
    }
    assert {p: v for p, v in offenders.items() if v is not None} == {}


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.bats"
    bad.write_text('@test "a" {\n  run ! false\n}\n')
    monkeypatch.setattr(sys, "argv", ["validate-bats-version", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vbv.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "bats_require_minimum_version" in err


def test_main_exits_0_on_a_clean_suite(tmp_path, monkeypatch, capsys):
    good = tmp_path / "good.bats"
    good.write_text(DECLARATION + '@test "a" {\n  run ! false\n}\n')
    monkeypatch.setattr(sys, "argv", ["validate-bats-version", "--quiet", str(good)])
    vbv.main()
    assert "every flagged `run` is declared" in capsys.readouterr().out
