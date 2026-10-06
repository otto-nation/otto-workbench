"""Cheap syntax checks used after an AI conflict resolution."""

import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc
import core.syntax
import core.timeouts


needs_gofmt = pytest.mark.skipif(
    shutil.which("gofmt") is None, reason="gofmt not installed",
)

_VALID_GO = "package p\n\nfunc f() {}\n"
_BROKEN_GO = "package p\n\nfunc f() {\n}\n}\n"


class TestGo:
    @needs_gofmt
    def test_an_extra_brace_fails(self):
        result = core.syntax.check("x.go", _BROKEN_GO)
        assert not result.ok
        assert "expected declaration" in result.detail

    @needs_gofmt
    def test_valid_go_passes(self):
        assert core.syntax.check("x.go", _VALID_GO).ok

    @needs_gofmt
    def test_formatting_differences_do_not_fail(self):
        messy = "package p\nfunc f(){x:=1;_=x}\n"
        result = core.syntax.check("server.go", messy)
        assert result.ok

    def test_missing_gofmt_passes(self):
        with mock.patch("shutil.which", return_value=None):
            assert core.syntax.check("x.go", _BROKEN_GO).ok


class TestPython:
    def test_valid_passes(self):
        assert core.syntax.check("mod.py", "x = 1\n").ok

    def test_unclosed_paren_fails(self):
        result = core.syntax.check("mod.py", "def f(\n")
        assert not result.ok
        assert result.detail


class TestJson:
    def test_valid_passes(self):
        assert core.syntax.check("a.json", '{"a": 1}').ok

    def test_leading_bom_passes(self):
        assert core.syntax.check("a.json", '\ufeff{"a": 1}').ok

    def test_truncated_fails(self):
        result = core.syntax.check("a.json", "{")
        assert not result.ok
        assert result.detail


class TestYaml:
    def test_valid_passes(self):
        assert core.syntax.check("a.yml", "foo: 1\n").ok

    def test_invalid_fails(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")
        result = core.syntax.check("a.yaml", ":\n  [")
        assert not result.ok
        assert result.detail

    def test_missing_pyyaml_passes(self):
        with mock.patch.dict(sys.modules, {"yaml": None}):
            assert core.syntax.check("a.yml", ":\n  [").ok


class TestPathologicalNesting:
    def test_json_nested_past_the_recursion_limit_passes(self):
        assert core.syntax.check("a.json", "[" * 200_000).ok

    def test_yaml_nested_past_the_recursion_limit_passes(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")
        assert core.syntax.check("a.yaml", "[" * 200_000).ok

    @pytest.mark.parametrize("exc", [RecursionError, MemoryError])
    def test_python_that_exhausts_the_parser_passes(self, exc):
        with mock.patch("ast.parse", side_effect=exc):
            assert core.syntax.check("mod.py", "x = 1\n").ok


class TestToml:
    def test_valid_passes(self):
        assert core.syntax.check("a.toml", "foo = 1\n").ok

    def test_unclosed_fails(self):
        result = core.syntax.check("a.toml", "foo = [")
        assert not result.ok
        assert result.detail


class TestShell:
    def test_valid_sh_passes(self):
        assert core.syntax.check("setup.sh", "echo hi\n").ok

    def test_unclosed_if_fails(self):
        result = core.syntax.check("setup.bash", "if true; then\n")
        assert not result.ok
        assert result.detail

    def test_shebang_without_suffix_is_checked(self):
        result = core.syntax.check("bin/setup", "#!/usr/bin/env bash\nif true; then\n")
        assert not result.ok

    @pytest.mark.parametrize("shebang", ["#!/bin/zsh", "#!/usr/bin/env dash"])
    def test_a_sh_suffix_with_another_interpreter_shebang_passes(self, shebang):
        # Not bash, so `bash -n` rejecting it says nothing about the file.
        assert core.syntax.check("a.sh", f"{shebang}\nif true; then\n").ok

    def test_a_sh_suffix_with_a_bash_shebang_is_still_checked(self):
        assert not core.syntax.check("a.sh", "#!/bin/bash\nif true; then\n").ok

    def test_bats_without_a_bash_shebang_passes(self):
        # `@test` is not bash; checking it would false-fail every bats file.
        assert core.syntax.check("x.bats", '@test "x" {\n  true\n}\n').ok


class TestFailOpen:
    def test_unknown_suffix_passes(self):
        assert core.syntax.check("main.rs", "fn main() { leftover").ok

    def test_timeout_passes(self):
        timed_out = core.proc.CmdResult(
            returncode=core.proc.TIMEOUT_RETURNCODE, stderr="timed out",
        )
        with mock.patch("shutil.which", return_value="/usr/bin/gofmt"), \
             mock.patch("core.proc.run", return_value=timed_out):
            assert core.syntax.check("x.go", _BROKEN_GO).ok

    def test_tool_is_bounded(self):
        with mock.patch("shutil.which", return_value="/usr/bin/gofmt"), \
             mock.patch("core.proc.run", return_value=core.proc.CmdResult()) as run:
            core.syntax.check("x.go", _VALID_GO)
        assert run.call_args.kwargs["timeout"] == core.timeouts.LOCAL
        assert run.call_args.kwargs["input_text"] == _VALID_GO
