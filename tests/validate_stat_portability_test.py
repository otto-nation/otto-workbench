"""Tests for bin/local/validate-stat-portability."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-stat-portability"

_loader = importlib.machinery.SourceFileLoader("validate_stat_portability", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_stat_portability", _loader)
vsp = importlib.util.module_from_spec(_spec)
sys.modules["validate_stat_portability"] = vsp
_spec.loader.exec_module(vsp)


def _check(tmp_path, source):
    path = tmp_path / "sample.sh"
    path.write_text(source)
    return vsp.check_file(str(path))


# ── accepted patterns ────────────────────────────────────────────────────


def test_helper_call_is_clean(tmp_path):
    assert _check(tmp_path, 'ts=$(file_mtime "$f") || ts=0\n') == []


def test_stat_without_a_format_flag_is_clean(tmp_path):
    """Only the format flags differ across platforms; plain stat is portable."""
    assert _check(tmp_path, 'stat "$f" >/dev/null\n') == []


def test_a_flag_that_merely_starts_with_c_is_clean(tmp_path):
    assert _check(tmp_path, 'stat --cached "$f"\n') == []


def test_a_commented_out_call_is_clean(tmp_path):
    assert _check(tmp_path, '# stat -c %Y "$f" is what this replaces\n') == []


def test_a_trailing_comment_does_not_hide_a_real_call(tmp_path):
    violations = _check(tmp_path, 'x=$(stat -c %Y "$f")  # epoch seconds\n')
    assert [line for line, _ in violations] == [1]


def test_another_command_named_after_stat_is_clean(tmp_path):
    assert _check(tmp_path, 'git diff --stat -c\n') == []


# ── rejected patterns ────────────────────────────────────────────────────


def test_gnu_short_flag_is_flagged(tmp_path):
    assert [line for line, _ in _check(tmp_path, 'x=$(stat -c %Y "$f")\n')] == [1]


def test_bsd_short_flag_is_flagged(tmp_path):
    assert [line for line, _ in _check(tmp_path, 'x=$(stat -f %m "$f")\n')] == [1]


def test_gnu_long_flags_are_flagged(tmp_path):
    violations = _check(tmp_path, 'a=$(stat --format=%a "$f")\nb=$(stat --printf=%a "$f")\n')
    assert [line for line, _ in violations] == [1, 2]


def test_a_format_flag_behind_another_flag_is_flagged(tmp_path):
    """stat -L -c %Y is the same trap with a dereference flag in front."""
    assert [line for line, _ in _check(tmp_path, 'x=$(stat -L -c %Y "$f")\n')] == [1]


def test_an_absolute_path_invocation_is_flagged(tmp_path):
    assert [line for line, _ in _check(tmp_path, 'x=$(/usr/bin/stat -c %Y "$f")\n')] == [1]


def test_a_call_split_across_a_continuation_is_flagged(tmp_path):
    """The same violation wearing a line break must not slip through."""
    violations = _check(tmp_path, 'x=$(stat \\\n  -c %Y \\\n  "$f")\n')
    assert [line for line, _ in violations] == [1]


def test_a_continued_line_reports_where_the_statement_starts(tmp_path):
    violations = _check(tmp_path, 'sleep 1\nsleep 2\nx=$(stat \\\n  -c %Y "$f")\n')
    assert [line for line, _ in violations] == [3]


def test_every_call_on_a_line_run_is_reported(tmp_path):
    source = 'x=$(stat -c %Y "$f")\nsleep 1\ny=$(stat -f %m "$f")\n'
    violations = _check(tmp_path, source)
    assert [line for line, _ in violations] == [1, 3]
    assert violations[0][1] == 'x=$(stat -c %Y "$f")'


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_bats_suites_and_extensionless_scripts():
    """Two of the four original call sites lived in .bats files."""
    scripts = vsp.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "tests" / "ui.bats") in scripts
    assert str(REPO_ROOT / "bin" / "wt-cleanup") in scripts
    assert str(REPO_ROOT / "lib" / "ai" / "session-count.sh") in scripts


def test_the_helper_itself_still_calls_stat():
    """If lib/portable.sh stops calling stat, the exemption is dead weight."""
    helper = REPO_ROOT / "lib" / "portable.sh"
    assert vsp.check_file(str(helper)) != []
    assert "lib/portable.sh" in vsp.ALLOWED_PATHS


def test_repo_is_clean():
    """The rule this validator enforces holds across the tree it walks."""
    offenders = {
        path: vsp.check_file(path)
        for path in vsp.discover_scripts(str(REPO_ROOT))
        if str(Path(path).relative_to(REPO_ROOT)) not in vsp.ALLOWED_PATHS
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_skips_the_exempt_helper(tmp_path, monkeypatch, capsys):
    helper = REPO_ROOT / "lib" / "portable.sh"
    monkeypatch.setattr(sys, "argv", ["validate-stat-portability", str(helper)])
    vsp.main()
    assert "0 files checked" in capsys.readouterr().out


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.sh"
    bad.write_text('x=$(stat -c %Y "$f")\n')
    monkeypatch.setattr(sys, "argv", ["validate-stat-portability", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vsp.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "platform-specific stat format flag" in err
    assert "lib/portable.sh" in err
