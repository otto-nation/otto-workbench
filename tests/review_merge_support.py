"""Helpers shared by the review_merge suites."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.merge import merge_records
from review.record import ReviewRecord, TriageEntry
from review_record_support import record


def _group_of(*findings) -> ReviewRecord:
    """One group's record holding `findings`."""
    return record(*findings)


def _merged_of(*findings) -> ReviewRecord:
    """`findings` as one group, merged."""
    return merge_records([record(*findings)])


def _triaged(*entries: TriageEntry) -> ReviewRecord:
    """A record holding only `entries` as its File Triage."""
    return ReviewRecord(triage=entries)
