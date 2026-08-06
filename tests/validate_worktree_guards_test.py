"""Tests for bin/local/validate-worktree-guards."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-worktree-guards"

_loader = importlib.machinery.SourceFileLoader("validate_worktree_guards", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_worktree_guards", _loader)
vwg = importlib.util.module_from_spec(_spec)
sys.modules["validate_worktree_guards"] = vwg
_spec.loader.exec_module(vwg)


def _check(tmp_path, source):
    path = tmp_path / "sample.py"
    path.write_text(source)
    return vwg.check_file(str(path))


# ── accepted patterns ────────────────────────────────────────────────────


def test_accessor_is_clean(tmp_path):
    assert _check(tmp_path, """
def cmd_status(ctx):
    wt = ctx.require_worktree()
    return load_state(wt)
""") == []


def test_direct_guard_is_clean(tmp_path):
    assert _check(tmp_path, """
def cmd_gc(ctx):
    if ctx.worktree_root:
        prune(ctx.worktree_root)
""") == []


def test_aliased_guard_is_clean(tmp_path):
    assert _check(tmp_path, """
def cmd_ci(ctx):
    toplevel = ctx.worktree_root
    if not toplevel:
        return 1
    return run(toplevel)
""") == []


def test_ternary_guard_is_clean(tmp_path):
    assert _check(tmp_path, """
def main(ctx):
    trail(dir=str(ctx.worktree_root) if ctx.worktree_root else "/tmp")
""") == []


def test_other_receiver_is_ignored(tmp_path):
    """PRIdentity.worktree_root is a plain str, so only ctx/self match."""
    assert _check(tmp_path, """
def save(state):
    return state.identity.worktree_root
""") == []


def test_file_without_the_field_is_skipped(tmp_path):
    assert _check(tmp_path, "def f(ctx):\n    return ctx.branch\n") == []


def test_syntax_error_is_tolerated(tmp_path):
    assert _check(tmp_path, "def f(ctx:\n    ctx.worktree_root\n") == []


# ── rejected patterns ────────────────────────────────────────────────────


def test_bare_dereference_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def cmd_fix(ctx):
    state = load_state(ctx.worktree_root)
    return state
""")
    assert [(v.line, v.scope) for v in violations] == [(3, "cmd_fix")]


def test_every_unguarded_reference_is_reported(tmp_path):
    violations = _check(tmp_path, """
def cmd_status(ctx):
    state = load_state(ctx.worktree_root)
    return render(ctx.worktree_root, state)
""")
    assert [v.line for v in violations] == [3, 4]


def test_guard_in_another_scope_does_not_cover_this_one(tmp_path):
    """The guard clause has to be in the scope that dereferences."""
    violations = _check(tmp_path, """
def caller(ctx):
    if ctx.worktree_root:
        callee(ctx)

def callee(ctx):
    return run(ctx.worktree_root)
""")
    assert [(v.line, v.scope) for v in violations] == [(7, "callee")]


def test_str_coercion_is_flagged(tmp_path):
    """The quietest failure: "None" reaching git -C several frames later."""
    violations = _check(tmp_path, """
def main(ctx):
    return subprocess.run(["git", "-C", str(ctx.worktree_root), "log"])
""")
    assert [v.line for v in violations] == [3]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_extensionless_python_scripts():
    scripts = vwg.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "claude" / "bin" / "pr") in scripts
    assert str(REPO_ROOT / "ai" / "lib" / "pr_context.py") in scripts


def test_repo_is_clean():
    """The rule this validator enforces holds across the tree it walks."""
    offenders = {
        path: vwg.check_file(path)
        for path in vwg.discover_scripts(str(REPO_ROOT))
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(ctx):\n    return run(ctx.worktree_root)\n")
    monkeypatch.setattr(sys, "argv", ["validate-worktree-guards", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vwg.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "unguarded ctx.worktree_root" in err
    assert "require_worktree()" in err
