"""Helpers shared by the review_merge suites."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import ingest_review
from review.merge import merge_records
from review.record import ReviewRecord
from review.types import SEVERITIES


def _review(**by_key: str) -> ReviewRecord:
    """One group's record, built from severity-keyed markdown snippets."""
    parts = [f"## {s.section}\n{by_key[s.key]}" for s in SEVERITIES if by_key.get(s.key)]
    return ingest_review("\n".join(parts) + "\n", strict=False) if parts else ReviewRecord()


def _merge(**by_key: str) -> ReviewRecord:
    return merge_records([_review(**by_key)])


def _triage(*lines: str) -> ReviewRecord:
    return ingest_review("## File Triage\n" + "\n".join(lines) + "\n", strict=False)
