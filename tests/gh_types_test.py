"""What a GitHub PR read returns."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh_types  # noqa: E402


def _metadata(**over):
    base = dict(
        title="t", body="b", head="feat/x", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=2, files=[],
    )
    return gh_types.PRMetadata(**(base | over))


def test_total_lines_is_both_sides_of_the_churn():
    assert _metadata().total_lines == 15


def test_pr_context_defaults_to_empty_json_arrays():
    ctx = gh_types.PRContext()

    assert (ctx.reviews, ctx.review_comments, ctx.comments) == ("[]", "[]", "[]")


def _make_meta(total_lines: int) -> gh_types.PRMetadata:
    return gh_types.PRMetadata(
        title="test",
        body="",
        head="feature",
        base="main",
        head_sha="abc1234",
        additions=total_lines,
        deletions=0,
        changed_files=1,
        files=[{"path": "a.py", "additions": total_lines, "deletions": 0}],
    )


class TestFileStatsThreshold:
    """The effort preset owns the threshold; file_stats must not re-derive it."""

    def test_low_effort_uses_wider_threshold(self):
        from agent_types import EFFORT_PRESETS
        from phases import Effort

        low = EFFORT_PRESETS[Effort.LOW].multi_phase_line_threshold
        assert _make_meta(750).file_stats(low) == ""

    def test_medium_effort_uses_narrower_threshold(self):
        from agent_types import EFFORT_PRESETS
        from phases import Effort

        medium = EFFORT_PRESETS[Effort.MEDIUM].multi_phase_line_threshold
        assert "a.py" in _make_meta(750).file_stats(medium)
