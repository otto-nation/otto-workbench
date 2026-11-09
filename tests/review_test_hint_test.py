"""The test hint's pairing and filters: which changed files have no paired test."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Phase
import review.registry
from review.budget import MAX_TEST_HINT_BYTES
from review.grouping import (
    hint_subjects,
    pairs_with,
    subject_stem,
    unpaired_subjects,
)
from review.prompt_sections import _build_test_hint_section
from review_prompt_support import _make_job, _make_preflight


@pytest.mark.parametrize(
    "test_stem, source_stem, expected",
    [
        ("grouping", "grouping", True),
        ("review_grouping", "grouping", True),
        ("review_merge", "emerge", False),
        ("merge", "review_merge", False),
        ("review_orchestrate_steps", "steps", True),
        ("remerge", "merge", False),
        ("profile", "file", False),
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


def test_unpaired_subjects_does_not_pair_across_a_name_boundary():
    files = [
        {"path": "pkg/merge.py", "additions": 3, "deletions": 0},
        {"path": "tests/remerge_test.py", "additions": 1, "deletions": 0},
    ]
    assert unpaired_subjects(files) == ["pkg/merge.py"]


def test_subject_stem_of_a_prefixed_test_is_its_compound_stem():
    assert subject_stem("tests/review_grouping_test.py") == "review_grouping"


def test_the_section_is_empty_without_paths():
    assert _build_test_hint_section([]) == ""


def test_the_section_lists_each_path_and_says_it_is_advisory():
    section = _build_test_hint_section(["pkg/a.py", "bin/tool"])
    assert section.startswith("\n## Test hint\n")
    assert "## Test hint" in section
    assert (
        "From file names only: these changed files have no test in this PR "
        "whose name pairs with theirs. Not a finding by itself."
    ) in section
    assert "- `pkg/a.py`" in section
    assert "- `bin/tool`" in section


def test_the_section_is_capped_with_a_count_of_the_rest():
    paths = [f"pkg/{'x' * 40}_{i}.py" for i in range(500)]
    section = _build_test_hint_section(paths)
    assert len(section.encode()) <= MAX_TEST_HINT_BYTES
    last = section.splitlines()[-1]
    listed = sum(1 for line in section.splitlines() if line.startswith("- `"))
    omitted = int(last.split()[-2])
    assert "…and " in last
    assert last.endswith("more")
    assert listed + omitted == 500


def test_a_group_hint_excludes_a_source_paired_by_a_test_in_another_group():
    job = _make_job(_make_preflight())
    job.pr.files = [
        {"path": "pkg/grouping.py", "additions": 3, "deletions": 0},
        {"path": "tests/review_grouping_test.py", "additions": 1, "deletions": 0},
        {"path": "lib/orphan.py", "additions": 2, "deletions": 0},
    ]
    grouped = review.registry.build_prompt(
        Phase.GROUP, job, max_turns=15,
        group_idx=1, group_count=2, group_name="pkg",
        group_files_formatted="- pkg/grouping.py",
        group_file_paths=["pkg/grouping.py"],
    )
    writer = review.registry.build_prompt(Phase.SINGLE, job, max_turns=15)
    assert "## Test hint" not in grouped
    writer_hint = writer.split("## Test hint", 1)[1].split("\n## ", 1)[0]
    assert "`lib/orphan.py`" in writer_hint
    assert "`pkg/grouping.py`" not in writer_hint
