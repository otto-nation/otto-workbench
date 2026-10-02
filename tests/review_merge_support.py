"""Helpers shared by the review_merge suites."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.types import SEVERITIES


def _sections(**by_key: str) -> dict[str, str]:
    """One group's severity sections, keyed the way the merge holds them."""
    return {s.key: by_key.get(s.key, "") for s in SEVERITIES}
