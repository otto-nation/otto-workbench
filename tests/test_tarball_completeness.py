"""Verify build-otto-ai-tools-tarball packages every file the binaries need.

The tarball is a self-contained distribution of otto-ai-tools.
If a Python binary imports a module that the tarball build script doesn't
copy into lib/, installs will crash on import.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "ai" / "bin" / "build-otto-ai-tools-tarball"
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"

BIN_EXCLUDE = {"build-otto-ai-tools-tarball", "_version.py",
               "build-claude-config-tarball", "workbench-export"}

PACKAGED_BINARIES = sorted(
    p for p in BIN_DIR.iterdir()
    if p.is_file()
    and p.stat().st_mode & 0o111
    and p.name not in BIN_EXCLUDE
)


def _collect_import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        # `node.module` alone covers `from core.trail import Trail` (already the full
        # dotted module; `alias.name` there is a symbol, not a submodule). The dotted
        # combination covers the sibling shape, `from config import workbench_config`
        # (`node.module` is just the package; the submodule rides in the alias). Adding
        # both rather than choosing one is what keeps either import shape matching.
        return [node.module] + [
            f"{node.module}.{alias.name}"
            for alias in node.names
            if alias.name != "*"
        ]
    return []


def _extract_python_imports(script: Path) -> set[str]:
    """Extract all local module names imported by a Python script."""
    try:
        tree = ast.parse(script.read_text(), filename=str(script))
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        modules.update(_collect_import_names(node))
    return modules


def _lib_python_files() -> set[str]:
    """All .py files in the lib directory."""
    return {p.name for p in LIB_DIR.glob("*/*.py") if p.stem != "__init__"}


def _all_required_modules() -> set[str]:
    """Collect all local modules imported by any packaged Python binary."""
    lib_modules = {
        f"{p.parent.name}.{p.stem}"
        for p in LIB_DIR.glob("*/*.py")
        if p.stem != "__init__"
    }
    all_imports: set[str] = set()
    for binary in PACKAGED_BINARIES:
        all_imports.update(_extract_python_imports(binary))
    return all_imports & lib_modules


class TestTarballCompleteness:
    def test_all_python_modules_included(self):
        """Every module imported by packaged binaries must exist in lib/.

        The floor guards against an import-shape change that empties (or
        shrinks) the `all_imports & lib_modules` intersection while leaving
        every assertion below still vacuously green — `required` shrinking
        silently is the failure mode, not `missing` growing.
        """
        required = _all_required_modules()
        assert len(required) > 70
        missing = sorted(
            mod for mod in required
            if not (LIB_DIR / Path(*mod.split(".")).with_suffix(".py")).exists()
        )
        assert not missing, (
            f"Python modules imported but not found in {LIB_DIR}:\n"
            + "\n".join(f"  - {mod}" for mod in missing)
        )

    def test_build_script_copies_all_py(self):
        """Build script must use a glob that covers all .py files in lib/."""
        content = BUILD_SCRIPT.read_text()
        assert "*.py" in content, (
            "Build script should glob *.py to dynamically include all Python modules"
        )

    @pytest.mark.parametrize("binary", PACKAGED_BINARIES, ids=lambda p: p.name)
    def test_binary_not_excluded(self, binary):
        """Each executable in bin/ must not be in the BIN_EXCLUDE list."""
        content = BUILD_SCRIPT.read_text()
        assert 'BIN_EXCLUDE=' in content, (
            "Build script must define BIN_EXCLUDE for dynamic discovery"
        )
        exclude_match = re.search(r'BIN_EXCLUDE="([^"]*)"', content)
        assert exclude_match, "Could not parse BIN_EXCLUDE from build script"
        excludes = set(exclude_match.group(1).split("|"))
        assert binary.name not in excludes, (
            f"{binary.name} is in BIN_EXCLUDE and will not be packaged"
        )

    def test_tarball_copies_review_templates(self):
        """Review templates must be included — review-orchestrate reads them."""
        content = BUILD_SCRIPT.read_text()
        assert "review-templates" in content, (
            "review-templates directory is not referenced in the tarball build script"
        )
