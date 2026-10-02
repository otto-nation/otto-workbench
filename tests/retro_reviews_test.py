"""Tests for retro.reviews — local self-review scan.

Moved from tests/retro_scan.bats. The scan names the directories it read so
the consume record can quote them back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from retro.reviews import scan_local_reviews  # noqa: E402
from retro.rules import load_rules  # noqa: E402
from retro_support import make_rules_dir  # noqa: E402


def test_scan_local_reviews_returns_consumed_dir_names(tmp_path):
    reviews_dir = tmp_path / "state" / "reviews" / "myrepo-self-1"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "review.md").write_text(
        "# Self-Review: myrepo — branch\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- [ ] **[M1]** `src/main.go` — Missing error handling for secret token write\n"
    )
    make_rules_dir(tmp_path / "wb")
    rules = load_rules(tmp_path / "wb")
    counts = {r["filename"]: {"matched": 0} for r in rules}
    scan = scan_local_reviews(tmp_path / "state" / "reviews", rules, counts)
    assert len(scan.consumed) == 1
    assert scan.consumed[0].dir_name == "myrepo-self-1"
