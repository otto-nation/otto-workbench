"""Tests for bin/local/validate-entry-points."""

from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-entry-points"

vep = load_script("validate_entry_points", SCRIPT)

PIN = '''_AI_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if os.environ.get("WORKBENCH_AI_LIB_DIR"):
    sys.path.insert(0, str(_AI_LIB_DIR.parent / "bin"))
    from _libdir import pinned_ai_lib_dir
    del sys.path[0]
    _AI_LIB_DIR = pinned_ai_lib_dir()
sys.path.insert(0, str(_AI_LIB_DIR))
'''

HEADER = '#!/usr/bin/env python3\n"""A tool."""\n\nimport os\nimport sys\nfrom pathlib import Path\n\n'


def _shim(body_before_import: str = "", tail: str = "sys.exit(main(sys.argv[1:]))\n",
          module: str = "tool") -> str:
    return (HEADER + body_before_import + PIN
            + f"\nfrom cli.{module} import main  # noqa: E402\n\n" + tail)


@pytest.fixture
def root(tmp_path):
    cli = tmp_path / "ai" / "lib" / "cli"
    cli.mkdir(parents=True)
    (cli / "tool.py").write_text("def main(argv=None):\n    return 0\n")
    return tmp_path


def _reasons(source, root):
    return [v.reason for v in vep.check_source(source, root)]


def test_a_shim_passes(root):
    assert vep.check_source(_shim(), root) == []


def test_a_direct_call_to_the_entry_passes(root):
    assert vep.check_source(_shim(tail="main()\n"), root) == []


def test_a_path_binding_before_the_pin_passes(root):
    source = _shim('BIN_DIR = Path(__file__).resolve().parent\n',
                   tail="sys.exit(main(sys.argv[1:], bin_dir=BIN_DIR))\n")
    assert vep.check_source(source, root) == []


def test_a_help_preamble_before_the_pin_passes(root):
    preamble = ('if "--help" in sys.argv or "-h" in sys.argv:\n'
                '    print(__doc__)\n    sys.exit(0)\n\n')
    assert vep.check_source(_shim(preamble), root) == []


def test_a_function_in_the_script_is_refused(root):
    source = _shim("def helper():\n    return 1\n\n")
    assert any("defines `helper`" in r for r in _reasons(source, root))


def test_a_class_in_the_script_is_refused(root):
    source = _shim("class Thing:\n    pass\n\n")
    assert any("defines `Thing`" in r for r in _reasons(source, root))


def test_an_import_of_another_ai_lib_package_is_refused(root):
    source = _shim().replace("from cli.tool import main",
                             "from core.version import version_string  # noqa: E402\n"
                             "from cli.tool import main")
    assert any("imports something other" in r for r in _reasons(source, root))


def test_a_constant_in_the_script_is_refused(root):
    source = _shim().replace("\nfrom cli.tool", "\nLIMIT = 5\nfrom cli.tool")
    assert any("LIMIT = 5" in r for r in _reasons(source, root))


def test_a_script_with_no_cli_import_is_refused(root):
    source = HEADER + PIN + "\nprint('working')\n"
    assert any("no `from cli." in r for r in _reasons(source, root))


def test_work_after_the_entry_call_is_refused(root):
    source = _shim(tail="sys.exit(main(sys.argv[1:]))\nprint('after')\n")
    reasons = _reasons(source, root)
    assert sum("print('after')" in r for r in reasons) == 1
    assert sum("last statement must call" in r for r in reasons) == 1


def test_a_cli_module_that_does_not_exist_is_refused(root):
    assert any("does not exist" in r for r in _reasons(_shim(module="missing"), root))


def test_a_cli_module_without_the_entry_is_refused(root):
    (root / "ai" / "lib" / "cli" / "bare.py").write_text("def other():\n    pass\n")
    assert any("defines no `main`" in r for r in _reasons(_shim(module="bare"), root))


def test_a_standalone_script_is_out_of_scope():
    """A script that never places ai/lib on its path is a program, not an entry point."""
    assert not vep.in_scope(HEADER + "def main():\n    print('hi')\n\nmain()\n")
    assert vep.in_scope(_shim())


def test_discover_skips_helper_modules_and_non_python(tmp_path):
    bin_dir = tmp_path / "ai" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "tool").write_text(_shim())
    (bin_dir / "_libdir.py").write_text(HEADER)
    (bin_dir / "shell-tool").write_text("#!/usr/bin/env bash\necho hi\n")
    assert vep.discover(tmp_path) == [bin_dir / "tool"]


def test_main_exits_1_and_names_the_script(tmp_path, capsys):
    bad = tmp_path / "tool"
    bad.write_text(_shim("def helper():\n    return 1\n\n"))
    assert vep.main(["--quiet", str(bad)]) == 1
    assert "not a shim over ai/lib/cli" in capsys.readouterr().err


def test_every_shim_in_this_repo_passes():
    """The shims the repo ships; a body creeping back into one fails here."""
    shims = [p for p in vep.discover(REPO_ROOT) if vep.in_scope(p.read_text())]
    assert len(shims) >= 17
    offenders = {
        str(p.relative_to(REPO_ROOT)): vep.check_source(p.read_text(), REPO_ROOT)
        for p in shims
    }
    assert {k: v for k, v in offenders.items() if v} == {}
