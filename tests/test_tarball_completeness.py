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
BUILD_SCRIPT = REPO_ROOT / "ai" / "claude" / "bin" / "build-otto-ai-tools-tarball"
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"

BIN_EXCLUDE = {"build-otto-ai-tools-tarball", "_version.py"}

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
        return [node.module]
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
    return {p.name for p in LIB_DIR.glob("*.py")}


def _all_required_modules() -> set[str]:
    """Collect all local modules imported by any packaged Python binary."""
    lib_modules = {p.stem for p in LIB_DIR.glob("*.py")}
    all_imports: set[str] = set()
    for binary in PACKAGED_BINARIES:
        all_imports.update(_extract_python_imports(binary))
    return all_imports & lib_modules


class TestTarballCompleteness:
    def test_all_python_modules_included(self):
        """Every module imported by packaged binaries must exist in lib/."""
        required = _all_required_modules()
        missing = sorted(
            mod for mod in required
            if not (LIB_DIR / f"{mod}.py").exists()
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
