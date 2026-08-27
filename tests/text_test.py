"""Tests for text's shared formatting helpers."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from text import plural


def test_plural_only_singular_at_one():
    assert [f"{n} file{plural(n)}" for n in (0, 1, 2)] == ["0 files", "1 file", "2 files"]
