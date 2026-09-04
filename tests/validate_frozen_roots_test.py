"""Tests for bin/local/validate-frozen-roots."""

import sys
from pathlib import Path

import pytest
from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-frozen-roots"

vfr = load_script("validate_frozen_roots", SCRIPT)


def _check(tmp_path, source):
    path = tmp_path / "sample.py"
    path.write_text(source)
    return vfr.check_file(str(path))


# ── accepted patterns ────────────────────────────────────────────────────


def test_a_root_resolved_in_a_function_is_clean(tmp_path):
    assert _check(tmp_path, """
import workbench_paths

def ledger_dir():
    return workbench_paths.state_dir() / "usage"
""") == []


def test_a_bare_subdirectory_name_is_clean(tmp_path):
    """A name is not a path — sharing one is how two modules agree on a subtree."""
    assert _check(tmp_path, """
import workbench_paths

LEDGER_DIRNAME = "usage"
""") == []


def test_a_root_resolved_in_a_method_is_clean(tmp_path):
    assert _check(tmp_path, """
import workbench_paths

class Ledger:
    def path(self):
        return workbench_paths.state_dir() / "usage"
""") == []


def test_a_default_resolved_by_a_factory_is_clean(tmp_path):
    """`default_factory` defers the call to instantiation, which is the point."""
    assert _check(tmp_path, """
import workbench_paths
from dataclasses import dataclass, field

@dataclass
class Opts:
    root: object = field(default_factory=workbench_paths.state_dir)
""") == []


def test_a_lambda_body_is_clean(tmp_path):
    """A lambda defers its call exactly as a `def` does."""
    assert _check(tmp_path, """
import workbench_paths

GET_DIR = lambda: workbench_paths.state_dir() / "usage"
""") == []


def test_a_lambda_default_is_flagged(tmp_path):
    """Only the body is deferred — a parameter default evaluates at import."""
    violations = _check(tmp_path, """
import workbench_paths

GET_DIR = lambda root=workbench_paths.state_dir(): root
""")
    assert [v.root for v in violations] == ["workbench_paths.state_dir"]


def test_file_without_the_module_is_skipped(tmp_path):
    assert _check(tmp_path, 'ROOT = state_dir() / "usage"\n') == []


def test_syntax_error_is_tolerated_but_reported(tmp_path, capsys):
    assert _check(tmp_path, "import workbench_paths\ndef f(:\n") == []
    assert "unparseable, not checked" in capsys.readouterr().err


# ── rejected patterns ────────────────────────────────────────────────────


def test_a_module_level_constant_is_flagged(tmp_path):
    violations = _check(tmp_path, """
import workbench_paths

LEDGER_DIR = workbench_paths.state_dir() / "usage"
""")
    assert [(v.line, v.name, v.root) for v in violations] == [
        (4, "LEDGER_DIR", "workbench_paths.state_dir"),
    ]


def test_a_bare_root_call_is_flagged(tmp_path):
    """Nothing has to be appended for the value to be stale."""
    violations = _check(tmp_path, """
import workbench_paths

TRAIL = workbench_paths.trail_dir()
""")
    assert [v.root for v in violations] == ["workbench_paths.trail_dir"]


def test_a_package_imported_module_call_is_flagged(tmp_path):
    """`from core import workbench_paths` is the shape every caller now uses —
    it binds the module name through the layer package rather than plainly, and
    must freeze exactly as hard as a bare `import workbench_paths` does."""
    violations = _check(tmp_path, """
from core import workbench_paths

TRAIL = workbench_paths.trail_dir()
""")
    assert [v.root for v in violations] == ["workbench_paths.trail_dir"]


def test_a_dotted_from_import_is_flagged(tmp_path):
    """`from core.workbench_paths import state_dir` names the module one level
    deeper than `_imported_roots` used to accept — it must freeze exactly as
    hard as the bare `from workbench_paths import state_dir` shape does."""
    violations = _check(tmp_path, """
from core.workbench_paths import state_dir

ROOT = state_dir()
""")
    assert [(v.line, v.root) for v in violations] == [(4, "state_dir")]


def test_a_dotted_aliased_from_import_is_flagged(tmp_path):
    """The alias on a dotted `from` import is still the name that freezes."""
    violations = _check(tmp_path, """
from core.workbench_paths import state_dir as sd

ROOT = sd()
""")
    assert [v.root for v in violations] == ["sd"]


def test_a_bare_dotted_import_is_flagged(tmp_path):
    """`import core.workbench_paths` binds only `core` in scope, so the call
    site has to spell the whole path back out — `core.workbench_paths.state_dir()`
    — and that full chain is what has to be recognised, not a single hop."""
    violations = _check(tmp_path, """
import core.workbench_paths

ROOT = core.workbench_paths.state_dir()
""")
    assert [v.root for v in violations] == ["core.workbench_paths.state_dir"]


def test_a_bare_dotted_import_aliased_is_flagged(tmp_path):
    """`import core.workbench_paths as wp` is a single-hop alias one dotted
    level deeper than `import workbench_paths as wp` — same freeze either way."""
    violations = _check(tmp_path, """
import core.workbench_paths as wp

ROOT = wp.state_dir()
""")
    assert [v.root for v in violations] == ["wp.state_dir"]


def test_an_imported_resolver_is_flagged(tmp_path):
    """`from ... import state_dir` drops the module name, not the freeze."""
    violations = _check(tmp_path, """
from workbench_paths import state_dir

ROOT = state_dir()
""")
    assert [(v.line, v.root) for v in violations] == [(4, "state_dir")]


def test_an_aliased_resolver_is_flagged(tmp_path):
    violations = _check(tmp_path, """
from workbench_paths import cache_dir as cd

ROOT = cd() / "vertex-quota"
""")
    assert [v.root for v in violations] == ["cd"]


def test_an_aliased_module_is_flagged(tmp_path):
    """`import ... as wp` renames the module, not what the call freezes."""
    violations = _check(tmp_path, """
import workbench_paths as wp

ROOT = wp.state_dir() / "usage"
""")
    assert [v.root for v in violations] == ["wp.state_dir"]


def test_an_immediately_invoked_lambda_is_flagged(tmp_path):
    """A lambda called where it is written defers nothing."""
    violations = _check(tmp_path, """
import workbench_paths

ROOT = (lambda: workbench_paths.state_dir())()
""")
    assert [v.root for v in violations] == ["workbench_paths.state_dir"]


def test_a_class_attribute_is_flagged(tmp_path):
    """A class body runs at import exactly as the module body does."""
    violations = _check(tmp_path, """
import workbench_paths

class Ledger:
    ROOT = workbench_paths.state_dir()
""")
    assert [(v.line, v.name) for v in violations] == [(5, "ROOT")]


def test_an_annotated_assignment_is_flagged(tmp_path):
    violations = _check(tmp_path, """
import workbench_paths
from pathlib import Path

REVIEWS: Path = workbench_paths.reviews_dir()
""")
    assert [(v.line, v.name) for v in violations] == [(5, "REVIEWS")]


def test_a_root_buried_in_an_expression_is_flagged(tmp_path):
    violations = _check(tmp_path, """
import workbench_paths

REGISTRY = str(workbench_paths.projects_registry().resolve())
""")
    assert [v.root for v in violations] == ["workbench_paths.projects_registry"]


def test_every_offending_site_is_reported(tmp_path):
    violations = _check(tmp_path, """
import workbench_paths

A = workbench_paths.state_dir()
B = workbench_paths.cache_dir()
""")
    assert [v.line for v in violations] == [4, 5]


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_extensionless_python_scripts():
    scripts = vfr.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "bin" / "pr") in scripts
    assert str(REPO_ROOT / "ai" / "lib" / "agent" / "usage.py") in scripts


def test_repo_is_clean():
    """The rule this validator enforces holds across the tree it walks."""
    offenders = {
        path: vfr.check_file(path)
        for path in vfr.discover_scripts(str(REPO_ROOT))
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("import workbench_paths\nROOT = workbench_paths.state_dir()\n")
    monkeypatch.setattr(sys, "argv", ["validate-frozen-roots", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vfr.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "frozen at import" in err
    assert "ai/lib/core/workbench_paths.py" in err
