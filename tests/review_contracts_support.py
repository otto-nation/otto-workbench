"""Helpers shared by the review_contracts_* suites."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"
BIN_DIR = REPO_ROOT / "ai" / "bin"
AGENTS_DIR = REPO_ROOT / "ai" / "claude" / "agents"

# Insert lib dir so we can import the review modules directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def _template_files() -> set[str]:
    """Return the set of .md filenames in the review-templates directory."""
    return {p.name for p in TEMPLATE_DIR.glob("*.md")}
