"""Helpers shared by the review record suites: fixtures and the parity canonicaliser."""

import json
import shutil
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.types import ReviewMeta, review_meta_from_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "review_record"


def fixture_text(case: str, name: str) -> str:
    return (FIXTURES / case / name).read_text()


def fixture_meta(case: str) -> ReviewMeta:
    return review_meta_from_dict(json.loads(fixture_text(case, "meta.json")))


def expected_text(case: str) -> str:
    """`expected.md` when the case reorders sections (D2/D4), else `input.md`."""
    path = FIXTURES / case / "expected.md"
    return path.read_text() if path.exists() else fixture_text(case, "input.md")


def canonical(md: str) -> str:
    """Parity's comparison form: no blank lines, no trailing space, no banner (D1)."""
    from review.render import BANNER

    return "\n".join(
        line.rstrip() for line in md.splitlines()
        if line.strip() and line.strip() != BANNER
    )


def copy_tree(dest: Path) -> Path:
    """Copy the fixture tree into `dest`, restoring `*.py` names from `*.py.txt`."""
    shutil.copytree(FIXTURES / "tree", dest)
    for path in sorted(dest.rglob("*.txt"), reverse=True):
        path.rename(path.with_suffix(""))
    return dest
