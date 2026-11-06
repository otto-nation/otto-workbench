"""Claims as data: types, ingest kinds, and the writer's caps."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.claim_types import ClaimEntry, ClaimSource, ClaimVerdict


def test_a_claim_entry_coerces_its_enums():
    entry = ClaimEntry("C1", "ticket", "t", "unsupported", "e")
    assert entry.source is ClaimSource.TICKET
    assert entry.verdict is ClaimVerdict.UNSUPPORTED


def test_the_verdicts_are_the_four_on_disk_values():
    assert [v.value for v in ClaimVerdict] == [
        "supported", "contradicted", "unsupported", "unverifiable",
    ]
