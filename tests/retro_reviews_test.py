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
from review_record_support import finding, record, store_record  # noqa: E402


# passes-at-base: moved verbatim from tests/retro_scan.bats; subject unchanged
def test_scan_local_reviews_returns_consumed_dir_names(tmp_path):
    reviews_dir = tmp_path / "state" / "reviews" / "myrepo-self-1"
    reviews_dir.mkdir(parents=True)
    store_record(
        reviews_dir / "review.md",
        record(finding(
            "M1", "Missing error handling for secret token write",
            path="src/main.go", line=None, checkbox=True,
        )),
    )
    make_rules_dir(tmp_path / "wb")
    rules = load_rules(tmp_path / "wb")
    counts = {r["filename"]: {"matched": 0} for r in rules}
    scan = scan_local_reviews(tmp_path / "state" / "reviews", rules, counts)
    assert len(scan.consumed) == 1
    assert scan.consumed[0].dir_name == "myrepo-self-1"
