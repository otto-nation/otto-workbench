"""Verify build-claude-review-tarball packages every file the binaries need.

The tarball is a self-contained distribution of claude-review for Homebrew.
If a Python binary imports a review_* module that the tarball build script
doesn't copy into lib/, the Homebrew install will crash on import.
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

PYTHON_BINARIES = [
    BIN_DIR / "review-orchestrate",
    BIN_DIR / "review-post",
]


def _extract_review_module_imports(script: Path) -> set[str]:
    """Extract review_* module names imported by a Python script."""
    tree = ast.parse(script.read_text(), filename=str(script))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("review_"):
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("review_"):
                modules.add(node.module)
    return modules


def _extract_tarball_copied_files(script: Path) -> set[str]:
    """Extract filenames that the build script copies into the tarball.

    Looks for cp commands that copy from the lib directory into $DEST/lib/.
    """
    content = script.read_text()
    copied: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("cp "):
            continue
        # Match cp commands that reference lib/ Python files
        # e.g.: cp "$SCRIPT_DIR/../lib/review_common.py" "$DEST/lib/"
        # e.g.: cp "$SCRIPT_DIR/../lib/"review_*.py "$DEST/lib/"
        m = re.search(r'(review_\w+\.py)', line)
        if m:
            copied.add(m.group(1))
        # Also match glob patterns like review_*.py
        if 'review_*.py' in line:
            for py_file in LIB_DIR.glob("review_*.py"):
                copied.add(py_file.name)
    return copied


def _all_required_python_modules() -> set[str]:
    """Collect all review_* modules imported by any packaged Python binary."""
    modules: set[str] = set()
    for binary in PYTHON_BINARIES:
        modules |= _extract_review_module_imports(binary)
    return modules


class TestTarballCompleteness:
    def test_all_python_modules_included(self):
        """Every review_* module imported by packaged binaries must be
        copied into the tarball's lib/ directory."""
        required = _all_required_python_modules()
        required_files = {f"{mod}.py" for mod in required}
        copied = _extract_tarball_copied_files(BUILD_SCRIPT)

        missing = sorted(required_files - copied)
        assert not missing, (
            f"build-claude-review-tarball does not copy these Python modules "
            f"into the tarball, but they are imported by packaged binaries:\n"
            + "\n".join(f"  - {f}" for f in missing)
        )

    def test_required_modules_exist_on_disk(self):
        """Every review_* module imported by packaged binaries must exist
        in the source lib/ directory."""
        required = _all_required_python_modules()
        missing = sorted(
            mod for mod in required
            if not (LIB_DIR / f"{mod}.py").exists()
        )
        assert not missing, (
            f"Python modules imported but not found in {LIB_DIR}:\n"
            + "\n".join(f"  - {mod}" for mod in missing)
        )

    @pytest.mark.parametrize("binary", PYTHON_BINARIES, ids=lambda p: p.name)
    def test_binary_included_in_tarball(self, binary):
        """Each Python binary must be copied into the tarball's bin/."""
        content = BUILD_SCRIPT.read_text()
        assert binary.name in content, (
            f"{binary.name} is not referenced in the tarball build script"
        )

    def test_tarball_copies_review_templates(self):
        """Review templates must be included — review-orchestrate reads them."""
        content = BUILD_SCRIPT.read_text()
        assert "review-templates" in content, (
            "review-templates directory is not referenced in the tarball build script"
        )
