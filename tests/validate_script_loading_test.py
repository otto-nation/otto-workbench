"""Tests for bin/local/validate-script-loading."""

import sys
import textwrap
from pathlib import Path

import pytest
from conftest import load_script, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-script-loading"

vsl = load_script("validate_script_loading", SCRIPT)


def _check(source: str):
    return vsl.check_source(textwrap.dedent(source), "sample.py")


# ── accepted patterns ────────────────────────────────────────────────────


def test_asking_conftest_for_the_module_is_clean():
    assert _check("""
        from conftest import load_script
        mod = load_script("thing", BIN_DIR / "thing")
    """) == []


def test_a_fresh_exec_through_conftest_is_clean():
    assert _check("""
        from conftest import exec_fresh
        mod = exec_fresh("thing", PATH)
    """) == []


def test_a_plain_import_is_clean():
    """import_module resolves through sys.modules, so it cannot make a copy."""
    assert _check("""
        import importlib
        mod = importlib.import_module("thing")
    """) == []


def test_reading_the_module_table_is_clean():
    assert _check("""
        import sys
        mod = sys.modules.get("thing")
        assert "thing" in sys.modules
    """) == []


def test_another_objects_modules_attribute_is_clean():
    """Only `sys.modules` is the interpreter's table."""
    assert _check("""
        registry.modules["thing"] = built
        del registry.modules["thing"]
    """) == []


# ── rejected patterns ────────────────────────────────────────────────────


def test_the_loader_chain_is_flagged():
    violations = _check("""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("thing", str(PATH))
        spec = importlib.util.spec_from_loader("thing", loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    """)
    assert [what for _, what in violations] == [
        "SourceFileLoader()", "spec_from_loader()", "module_from_spec()", "exec_module()",
    ]


def test_a_from_import_of_the_loader_is_flagged():
    """The rule reads the call, not how the name got into scope."""
    violations = _check("""
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader("thing", str(PATH))
    """)
    assert [what for _, what in violations] == ["SourceFileLoader()"]


def test_spec_from_file_location_is_flagged():
    """The .py half of the same defect — lib modules have an owner too."""
    violations = _check("""
        spec = importlib.util.spec_from_file_location("gitenv", MODULE)
    """)
    assert [what for _, what in violations] == ["spec_from_file_location()"]


def test_registering_a_module_is_flagged():
    violations = _check("""
        import sys
        sys.modules["thing"] = mod
    """)
    assert violations == [(3, "writes sys.modules[...]")]


def test_dropping_a_registered_module_is_flagged():
    """A session fixture that deletes a name another module still holds a
    reference to is the teardown half of the same defect."""
    violations = _check("""
        import sys
        del sys.modules["thing"]
    """)
    assert violations == [(3, "writes sys.modules[...]")]


def test_setdefault_on_the_module_table_is_flagged():
    violations = _check("""
        import sys
        sys.modules.setdefault("thing", mod)
    """)
    assert [what for _, what in violations] == ["sys.modules.setdefault()"]


def test_one_line_is_reported_once():
    violations = _check("""
        import sys
        sys.modules["a"] = sys.modules["b"] = mod
    """)
    assert violations == [(3, "writes sys.modules[...]")]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discovery_finds_the_python_test_modules():
    files = vsl.discover_files(str(REPO_ROOT))
    assert str(Path(__file__).resolve()) in files
    assert all(f.endswith(".py") for f in files)


def test_discovery_reaches_a_subdirectory(tmp_path):
    """A module in a subdirectory runs in the same interpreter as the rest, so
    a copy built there collides exactly as one at the top level would."""
    nested = tmp_path / "tests" / "test_nested"
    nested.mkdir(parents=True)
    (nested / "nested_test.py").write_text("from conftest import load_script\n")
    cache = tmp_path / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "stale.py").write_text("import sys\nsys.modules['thing'] = mod\n")

    assert vsl.discover_files(str(tmp_path)) == [str(nested / "nested_test.py")]


def test_a_violation_in_a_subdirectory_fails_the_run(tmp_path, monkeypatch, capsys):
    nested = tmp_path / "tests" / "test_nested"
    nested.mkdir(parents=True)
    (nested / "nested_test.py").write_text("import sys\nsys.modules['thing'] = mod\n")
    monkeypatch.setattr(vsl, "_WORKBENCH_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-script-loading", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vsl.main()

    assert exc.value.code == 1
    assert "tests/test_nested/nested_test.py" in capsys.readouterr().err


def test_the_owner_exemption_is_one_exact_path(tmp_path, monkeypatch, capsys):
    """`tests/conftest.py` is the owner. A `conftest.py` in a subdirectory is a
    test module like any other, and an exemption keyed on the basename would
    have opened a hole for it the moment the walk went recursive."""
    tests = tmp_path / "tests"
    (tests / "test_nested").mkdir(parents=True)
    violation = "import sys\nsys.modules['thing'] = mod\n"
    (tests / "conftest.py").write_text(violation)
    (tests / "test_nested" / "conftest.py").write_text(violation)
    monkeypatch.setattr(vsl, "_WORKBENCH_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-script-loading", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vsl.main()

    err = capsys.readouterr().err
    assert exc.value.code == 1
    assert "tests/test_nested/conftest.py" in err
    # One of one: the owner was skipped, the nested namesake was not.
    assert "1 of 1 files" in err


def test_an_unparseable_file_is_reported_not_raised(tmp_path, capsys):
    """A syntax error is this validator's finding to report, not a traceback
    out of ast.parse — silence would read as "clean" for a file never checked."""
    broken = tmp_path / "broken_test.py"
    broken.write_text("def (:\n")

    assert vsl.check_file(str(broken)) == []
    assert "unparseable, not checked" in capsys.readouterr().err


def test_the_exempt_file_is_the_one_still_doing_the_loading():
    """If conftest stops executing modules, the exemption is dead weight."""
    assert vsl.check_file(str(REPO_ROOT / vsl.OWNER)) != []


def test_the_suite_is_clean():
    """No test module builds a second copy of a script the suite already has."""
    offenders = {
        path: vsl.check_file(path)
        for path in vsl.discover_files(str(REPO_ROOT))
        if Path(path).relative_to(REPO_ROOT) != Path(vsl.OWNER)
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad_test.py"
    bad.write_text("import sys\nsys.modules['thing'] = mod\n")
    monkeypatch.setattr(sys, "argv", ["validate-script-loading", "--quiet", str(bad)])

    with pytest.raises(SystemExit) as exc:
        vsl.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "executes a module of its own" in err
    assert "load_script" in err


def test_main_is_silent_about_a_clean_file(tmp_path, monkeypatch, capsys):
    good = tmp_path / "good_test.py"
    good.write_text("from conftest import load_script\n")
    monkeypatch.setattr(sys, "argv", ["validate-script-loading", "--quiet", str(good)])

    vsl.main()

    assert "1 files checked" in capsys.readouterr().out


def test_validate_all_discovers_this_validator():
    """The entry point globs bin/local/validate-*, so no list needs editing."""
    listed = run_checked([str(REPO_ROOT / "bin" / "local" / "validate-all"), "--list"],
                         cwd=REPO_ROOT)

    assert str(SCRIPT) in listed.stdout.splitlines()
