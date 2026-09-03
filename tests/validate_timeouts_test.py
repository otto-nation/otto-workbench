"""Tests for bin/local/validate-timeouts."""

import sys
from pathlib import Path

import pytest
from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-timeouts"

vt = load_script("validate_timeouts", SCRIPT)


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


def test_a_kwargs_splat_counts_as_a_bound(tmp_path):
    """The argument may be in the dict, and an AST walk cannot read it."""
    assert _check(tmp_path, """
def forward(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)
""") == []


def test_a_call_that_is_not_a_runner_needs_no_bound(tmp_path):
    """Only the launchers are held to this — `runner.run` is somebody's method."""
    assert _check(tmp_path, """
def go(runner, cmd):
    return runner.run(cmd)
""") == []


def test_popen_needs_no_bound(tmp_path):
    """It takes none; the one `communicate` takes belongs to that call."""
    assert _check(tmp_path, """
def stream(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE)
""") == []


def test_syntax_error_is_tolerated_but_reported(tmp_path, capsys):
    assert _check(tmp_path, "def f(cmd:\n    proc.run(cmd, timeout=5)\n") == []
    assert "unparseable, not checked" in capsys.readouterr().err


# ── rejected patterns ────────────────────────────────────────────────────


def test_a_numeric_literal_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def fetch(cmd):
    return proc.run(cmd, timeout=30)
""")
    assert [(v.line, v.found) for v in violations] == [(3, "timeout=30")]
    assert violations[0].suggestion == "a tier from `timeouts`"


def test_a_float_literal_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def probe(cmd):
    return proc.run(cmd, timeout=5.0)
""")
    assert [(v.line, v.found) for v in violations] == [(3, "timeout=5.0")]


def test_bare_none_is_flagged(tmp_path):
    """Silence and a decision to go unbounded must not look the same."""
    violations = _check(tmp_path, """
def push(cmd):
    return proc.run(cmd, timeout=None)
""")
    assert [(v.line, v.found) for v in violations] == [(3, "timeout=None")]
    assert violations[0].suggestion == "timeouts.UNBOUNDED"


def test_a_literal_parameter_default_is_flagged(tmp_path):
    """Every caller that omits the argument inherits this one."""
    violations = _check(tmp_path, """
def try_run(cmd, *, timeout=10):
    return subprocess.run(cmd, timeout=timeout)
""")
    assert [(v.line, v.found) for v in violations] == [(2, "timeout=10")]


def test_a_positional_default_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def try_run(cmd, timeout=10):
    return subprocess.run(cmd, timeout=timeout)
""")
    assert [(v.line, v.found) for v in violations] == [(2, "timeout=10")]


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
    assert [(v.line, v.found) for v in violations] == [(3, "timeout=3")]


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


# ── an omitted bound ─────────────────────────────────────────────────────


def test_a_launch_with_no_bound_is_flagged(tmp_path):
    """Nothing was written down, so nothing was decided."""
    violations = _check(tmp_path, """
def fetch(cmd):
    return proc.run(cmd)
""")
    assert [(v.line, v.found) for v in violations] == [(3, "proc.run(...) with no timeout=")]
    assert violations[0].suggestion == "a tier from `timeouts`"


@pytest.mark.parametrize("runner", [
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
])
def test_every_subprocess_runner_is_covered(tmp_path, runner):
    violations = _check(tmp_path, f"def fetch(cmd):\n    return {runner}(cmd)\n")
    assert [v.found for v in violations] == [f"{runner}(...) with no timeout="]


def test_an_omission_and_a_literal_are_reported_together(tmp_path):
    """Both are the same finding — a bound the table does not own."""
    violations = _check(tmp_path, """
def a(cmd):
    return proc.run(cmd)

def b(cmd):
    return proc.run(cmd, timeout=5)
""")
    assert [(v.line, v.found) for v in violations] == [
        (3, "proc.run(...) with no timeout="),
        (6, "timeout=5"),
    ]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_extensionless_python_scripts():
    scripts = vt.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "bin" / "pr") in scripts
    assert str(REPO_ROOT / "ai" / "lib" / "proc.py") in scripts


def test_the_mcp_server_is_covered():
    """It was exempt on the reading that `uv run` put ai/lib out of its reach.

    It puts that directory on `sys.path` itself — that is how it imports
    `tool_registry` — so `timeouts` was importable all along and the exemption
    only meant the file went unchecked.
    """
    server = REPO_ROOT / "ai" / "claude" / "mcps" / "server.py"
    assert server.is_file()
    assert str(server) in vt.discover_scripts(str(REPO_ROOT))
    assert vt.check_file(str(server)) == []


def test_a_named_non_python_file_is_not_reported_unparseable(monkeypatch, capsys, tmp_path):
    """Naming a file the check never covered says nothing about it."""
    other = tmp_path / "hook.sh"
    other.write_text("#!/usr/bin/env bash\nexec gh pr view \"$@\"\n")
    monkeypatch.setattr(sys, "argv", ["validate-timeouts", str(other)])
    vt.main()
    captured = capsys.readouterr()
    assert "unparseable" not in captured.err
    assert "0 files checked" in captured.out


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
