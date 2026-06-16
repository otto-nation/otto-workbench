"""Verify build-claude-review-tarball packages every file the binaries need.

The tarball is a self-contained distribution of claude-review for Homebrew.
If a Python binary imports a module that the tarball build script doesn't
copy into lib/, the Homebrew install will crash on import.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "ai" / "claude" / "bin" / "build-claude-review-tarball"
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"

PACKAGED_BINARIES = sorted(
    p for p in BIN_DIR.iterdir()
    if p.is_file()
    and p.stat().st_mode & 0o111
    and (p.name.startswith("review-") or p.name.startswith("validate-review-"))
)


def _extract_python_imports(script: Path) -> set[str]:
    """Extract all local module names imported by a Python script."""
    try:
        tree = ast.parse(script.read_text(), filename=str(script))
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


def _lib_python_files() -> set[str]:
    """All .py files in the lib directory."""
    return {p.name for p in LIB_DIR.glob("*.py")}


def _all_required_modules() -> set[str]:
    """Collect all local modules imported by any packaged Python binary."""
    lib_modules = {p.stem for p in LIB_DIR.glob("*.py")}
    required: set[str] = set()
    for binary in PACKAGED_BINARIES:
        for mod in _extract_python_imports(binary):
            if mod in lib_modules:
                required.add(mod)
    return required


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
    def test_binary_matched_by_glob(self, binary):
        """Each review-*/validate-review-* binary must be matched by the
        build script's glob patterns."""
        content = BUILD_SCRIPT.read_text()
        has_glob = "review-*" in content or "validate-review-*" in content
        has_explicit = binary.name in content
        assert has_glob or has_explicit, (
            f"{binary.name} is not matched by any glob or explicit cp in the build script"
        )

    def test_tarball_copies_review_templates(self):
        """Review templates must be included — review-orchestrate reads them."""
        content = BUILD_SCRIPT.read_text()
        assert "review-templates" in content, (
            "review-templates directory is not referenced in the tarball build script"
        )
