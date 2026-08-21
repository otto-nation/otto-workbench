"""Tests for bin/local/validate-timeouts."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-timeouts"

_loader = importlib.machinery.SourceFileLoader("validate_timeouts", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_timeouts", _loader)
vt = importlib.util.module_from_spec(_spec)
sys.modules["validate_timeouts"] = vt
_spec.loader.exec_module(vt)


def _check(tmp_path, source):
    path = tmp_path / "sample.py"
    path.write_text(source)
    return vt.check_file(str(path))


# ── accepted patterns ────────────────────────────────────────────────────


def test_a_tier_is_clean(tmp_path):
    assert _check(tmp_path, """
def fetch(cmd):
    return proc.run(cmd, timeout=timeouts.NETWORK)
""") == []


def test_named_unbounded_is_clean(tmp_path):
    """Opting out is a decision, so long as it is spelled as one."""
    assert _check(tmp_path, """
def commit(cmd):
    return proc.run(cmd, timeout=timeouts.UNBOUNDED)
""") == []


def test_a_forwarded_bound_is_clean(tmp_path):
    """The site the bound came from is checked on its own."""
    assert _check(tmp_path, """
def wrapper(cmd, *, timeout):
    return subprocess.run(cmd, timeout=timeout)
""") == []


def test_a_forwarded_attribute_is_clean(tmp_path):
    """opts.timeout is a budget threaded from the command line, not a literal."""
    assert _check(tmp_path, """
def verify(cmd, opts):
    return subprocess.run(cmd, timeout=opts.timeout)
""") == []


def test_a_tier_as_a_parameter_default_is_clean(tmp_path):
    assert _check(tmp_path, """
def try_run(cmd, *, timeout=timeouts.UNBOUNDED):
    return subprocess.run(cmd, timeout=timeout)
""") == []


def test_another_keyword_is_ignored(tmp_path):
    """--wait-timeout is a poll deadline, not a subprocess bound."""
    assert _check(tmp_path, """
def add_args(parser):
    parser.add_argument("--wait-timeout", type=int, default=900)
""") == []


def test_file_without_the_keyword_is_skipped(tmp_path):
    assert _check(tmp_path, "def f(cmd):\n    return proc.run(cmd)\n") == []


def test_syntax_error_is_tolerated_but_reported(tmp_path, capsys):
    assert _check(tmp_path, "def f(cmd:\n    proc.run(cmd, timeout=5)\n") == []
    assert "unparseable, not checked" in capsys.readouterr().err


# ── rejected patterns ────────────────────────────────────────────────────


def test_a_numeric_literal_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def fetch(cmd):
    return proc.run(cmd, timeout=30)
""")
    assert [(v.line, v.literal) for v in violations] == [(3, "30")]
    assert violations[0].suggestion == "a tier from `timeouts`"


def test_a_float_literal_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def probe(cmd):
    return proc.run(cmd, timeout=5.0)
""")
    assert [(v.line, v.literal) for v in violations] == [(3, "5.0")]


def test_bare_none_is_flagged(tmp_path):
    """Silence and a decision to go unbounded must not look the same."""
    violations = _check(tmp_path, """
def push(cmd):
    return proc.run(cmd, timeout=None)
""")
    assert [(v.line, v.literal) for v in violations] == [(3, "None")]
    assert violations[0].suggestion == "timeouts.UNBOUNDED"


def test_a_literal_parameter_default_is_flagged(tmp_path):
    """Every caller that omits the argument inherits this one."""
    violations = _check(tmp_path, """
def try_run(cmd, *, timeout=10):
    return subprocess.run(cmd, timeout=timeout)
""")
    assert [(v.line, v.literal) for v in violations] == [(2, "10")]


def test_a_positional_default_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def try_run(cmd, timeout=10):
    return subprocess.run(cmd, timeout=timeout)
""")
    assert [(v.line, v.literal) for v in violations] == [(2, "10")]


def test_a_required_keyword_only_bound_is_not_a_default(tmp_path):
    """kw_defaults carries a None placeholder for a parameter with no default."""
    assert _check(tmp_path, """
def run(cmd, *, timeout, cwd=None):
    return subprocess.run(cmd, timeout=timeout)
""") == []


def test_arithmetic_on_a_tier_is_flagged(tmp_path):
    """A multiplier is as untraceable as the number it multiplies."""
    violations = _check(tmp_path, """
def slow(cmd):
    return proc.run(cmd, timeout=timeouts.LOCAL * 3)
""")
    assert [(v.line, v.literal) for v in violations] == [(3, "3")]


def test_a_conditional_literal_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def maybe(cmd, slow):
    return proc.run(cmd, timeout=30 if slow else timeouts.LOCAL)
""")
    assert [v.line for v in violations] == [3]


def test_every_offending_site_is_reported(tmp_path):
    violations = _check(tmp_path, """
def a(cmd):
    return proc.run(cmd, timeout=5)

def b(cmd):
    return proc.run(cmd, timeout=None)
""")
    assert [v.line for v in violations] == [3, 6]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_extensionless_python_scripts():
    scripts = vt.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "claude" / "bin" / "pr") in scripts
    assert str(REPO_ROOT / "ai" / "lib" / "proc.py") in scripts


def test_the_mcp_server_is_exempt():
    """It runs with ai/lib nowhere on sys.path, so it cannot import the table."""
    exempt = REPO_ROOT / "ai" / "claude" / "mcps" / "server.py"
    assert exempt.is_file(), "the exemption names a file that no longer exists"
    assert str(exempt) not in vt.discover_scripts(str(REPO_ROOT))


def test_repo_is_clean():
    """The rule this validator enforces holds across the tree it walks."""
    offenders = {
        path: vt.check_file(path)
        for path in vt.discover_scripts(str(REPO_ROOT))
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(cmd):\n    return proc.run(cmd, timeout=30)\n")
    monkeypatch.setattr(sys, "argv", ["validate-timeouts", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vt.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not taken from `timeouts`" in err
    assert "ai/lib/timeouts.py" in err
