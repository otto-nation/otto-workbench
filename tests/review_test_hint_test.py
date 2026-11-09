"""The test hint's pairing and filters: which changed files have no paired test."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.grouping import (
    hint_subjects,
    pairs_with,
    subject_stem,
    unpaired_subjects,
)


@pytest.mark.parametrize(
    "test_stem, source_stem, expected",
    [
        ("grouping", "grouping", True),
        ("review_grouping", "grouping", True),
        ("review_merge", "emerge", False),
        ("merge", "review_merge", False),
        ("review_orchestrate_steps", "steps", True),
    ],
)
def test_pairs_with(test_stem, source_stem, expected):
    assert pairs_with(test_stem, source_stem) is expected


def test_hint_subjects_filters_tests_generated_docs_config_and_deletions():
    files = [
        {"path": "a.py", "additions": 3, "deletions": 0},
        {"path": "tests/a_test.py", "additions": 1, "deletions": 0},
        {"path": "api/x.pb.go", "additions": 5, "deletions": 0},
        {"path": "README.md", "additions": 2, "deletions": 0},
        {"path": "cfg.yml", "additions": 1, "deletions": 0},
        {"path": "gone.py", "additions": 0, "deletions": 9},
        {"path": "bin/tool", "additions": 4, "deletions": 0},
    ]
    assert hint_subjects(files) == ["a.py", "bin/tool"]


def test_unpaired_subjects_uses_tests_in_the_same_diff():
    source = {
        "path": "ai/lib/review/grouping.py",
        "additions": 3,
        "deletions": 0,
    }
    paired = unpaired_subjects([
        source,
        {"path": "tests/review_grouping_test.py", "additions": 1, "deletions": 0},
    ])
    assert paired == []
    assert unpaired_subjects([source]) == ["ai/lib/review/grouping.py"]


def test_subject_stem_of_a_prefixed_test_is_its_compound_stem():
    assert subject_stem("tests/review_grouping_test.py") == "review_grouping"
