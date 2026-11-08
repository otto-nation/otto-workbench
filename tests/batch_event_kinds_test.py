"""`commits_unrecorded` is a known event kind.

Revert: drop it from EVENT_KINDS.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402


def test_commits_unrecorded_is_a_known_kind():
    # Revert: remove commits_unrecorded from EVENT_KINDS.
    assert "commits_unrecorded" in batch.events.EVENT_KINDS


def test_unknown_kind_raises():
    # Revert: drop the membership check in emit.
    try:
        batch.events.emit("not_a_kind")
    except ValueError as exc:
        assert "not_a_kind" in str(exc)
    else:
        raise AssertionError("emit accepted an unknown kind")
