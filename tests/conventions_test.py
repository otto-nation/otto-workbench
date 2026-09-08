"""Tests for core.conventions — commit-header conventions from lib/conventions.sh."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core import conventions


class TestCommitTypes:
    """commit_types reads from lib/conventions.sh."""

    def test_includes_standard_types(self):
        types = conventions.commit_types()
        assert "fix" in types
        assert "feat" in types
        assert "test" in types
        assert "refactor" in types

    def test_returns_list(self):
        assert isinstance(conventions.commit_types(), list)


class TestValidCommitHeader:
    """valid_commit_header validates against the repo's conventions."""

    @pytest.mark.parametrize("subject,expected", [
        ("fix: correct the import path", True),
        ("feat(auth): add OAuth login", True),
        ("test(review): drop the reviews_dir kwarg", True),
        ("refactor(ai): restructure the module", True),
        ("nonsense(scope): not a real type", False),
        ("no colon here at all", False),
        ("fix: ", False),
        ("fix: ends with a period.", False),
        ("fix: " + "x" * 80, False),
        ("", False),
        ("feat!: remove deprecated flag", True),
        ("feat(auth)!: remove legacy token support", True),
    ])
    def test_valid_commit_header(self, subject, expected):
        assert conventions.valid_commit_header(subject) is expected

    def test_max_length(self):
        assert conventions.COMMIT_HEADER_MAX == 72
