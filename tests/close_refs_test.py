"""Tests for `pr.close_refs` — the `--closes` contract `pr create` will own.

Ported from the C cases of `tests/pr_issue_link.bats` and the `--closes` cases
of `tests/parse_pr_flags.bats`. Bash stays until PR 3; these pin the Python
owner to the same acceptance, staging, presence, and append rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from config.workbench_config import IssueProvider  # noqa: E402
from pr.close_refs import (  # noqa: E402
    CloseRefError, LinkResult, append, normalise, present, stage,
)


# ── normalise ───────────────────────────────────────────────────────────────


class TestNormalise:
    def test_a_bare_number_gains_a_hash(self):
        assert normalise("941", IssueProvider.GITHUB) == "#941"

    def test_a_leading_hash_is_kept_not_doubled(self):
        assert normalise("#941", IssueProvider.GITHUB) == "#941"

    def test_a_tracker_key_is_refused_when_the_provider_is_github(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("ENG-123", IssueProvider.GITHUB)
        assert str(excinfo.value) == (
            "✗ --closes ENG-123: a tracker key only auto-closes on Linear, "
            "and issues.provider is 'github'"
        )

    def test_a_tracker_key_is_refused_when_no_provider_is_set(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("ENG-123", None)
        assert str(excinfo.value) == (
            "✗ --closes ENG-123: a tracker key only auto-closes on Linear, "
            "and issues.provider is 'unset'"
        )

    def test_a_tracker_key_is_accepted_when_the_provider_is_linear(self):
        assert normalise("ENG-123", IssueProvider.LINEAR) == "ENG-123"

    def test_a_numeric_ref_is_accepted_whatever_the_provider(self):
        assert normalise("941", IssueProvider.JIRA) == "#941"

    def test_an_unparseable_ref_is_refused(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("banana", None)
        assert "banana" in str(excinfo.value)
        assert str(excinfo.value) == (
            "✗ --closes banana: expected a GitHub issue number (941 or #941) "
            "or an uppercase tracker key (ENG-123)"
        )

    def test_a_ref_shaped_to_inject_regex_is_refused(self):
        with pytest.raises(CloseRefError):
            normalise("A.*-1", IssueProvider.LINEAR)

    def test_a_lowercase_tracker_key_is_refused_even_on_linear(self):
        with pytest.raises(CloseRefError):
            normalise("eng-123", IssueProvider.LINEAR)

    def test_a_hashed_tracker_key_is_accepted_on_linear(self):
        assert normalise("#ENG-123", IssueProvider.LINEAR) == "ENG-123"

    def test_a_hashed_tracker_key_is_refused_on_github_naming_the_stripped_key(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("#ENG-123", IssueProvider.GITHUB)
        assert str(excinfo.value) == (
            "✗ --closes ENG-123: a tracker key only auto-closes on Linear, "
            "and issues.provider is 'github'"
        )

    def test_a_double_hash_numeric_is_classified_after_stripping_one(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("##941", IssueProvider.GITHUB)
        assert str(excinfo.value) == (
            "✗ --closes #941: expected a GitHub issue number (941 or #941) "
            "or an uppercase tracker key (ENG-123)"
        )

    def test_a_hashed_unparseable_ref_is_refused_naming_the_stripped_token(self):
        with pytest.raises(CloseRefError) as excinfo:
            normalise("#banana", None)
        assert str(excinfo.value) == (
            "✗ --closes banana: expected a GitHub issue number (941 or #941) "
            "or an uppercase tracker key (ENG-123)"
        )

    def test_close_ref_error_is_the_user_facing_line(self):
        assert issubclass(CloseRefError, ValueError)
        with pytest.raises(CloseRefError) as excinfo:
            normalise("banana", None)
        assert str(excinfo.value) == (
            "✗ --closes banana: expected a GitHub issue number (941 or #941) "
            "or an uppercase tracker key (ENG-123)"
        )


# ── stage ───────────────────────────────────────────────────────────────────


class TestStage:
    def test_the_same_ref_passed_twice_is_staged_once(self):
        once = stage([], "#941")
        assert stage(once, "#941") == ["#941"]

    def test_two_spellings_of_the_same_ref_are_staged_once_after_normalise(self):
        first = normalise("941", None)
        second = normalise("#941", None)
        staged = stage(stage([], first), second)
        assert staged == ["#941"]

    def test_distinct_refs_are_kept_in_order(self):
        staged = stage(stage([], "#941"), "#942")
        assert staged == ["#941", "#942"]

    def test_stage_returns_a_new_list(self):
        original = ["#941"]
        result = stage(original, "#942")
        assert result is not original
        assert original == ["#941"]
        skipped = stage(original, "#941")
        assert skipped is not original
        assert skipped == ["#941"]


# ── present ─────────────────────────────────────────────────────────────────


class TestPresent:
    def test_closes_counts_as_present(self):
        assert present("body\n\nCloses #941", "#941")

    def test_fixes_counts_as_present(self):
        assert present("body\n\nFixes #941", "#941")

    def test_hash_1_is_not_present_when_only_hash_12_is_closed(self):
        assert not present("body\n\nCloses #12", "#1")
        assert present("body\n\nCloses #12", "#12")


# ── append ──────────────────────────────────────────────────────────────────


class TestAppend:
    def test_a_numeric_ref_is_appended_to_a_templated_body(self):
        body = "## What\n\nAdds a thing.\n\n## Why\n\nIt was missing."
        result = append(body, ["#941"])
        assert "Closes #941" in result.body

    def test_the_ref_lands_after_the_last_template_section(self):
        body = "## What\n\nAdds a thing."
        result = append(body, ["#941"])
        assert result.body.startswith("## What")
        assert result.body.endswith("Closes #941")

    def test_the_ref_is_separated_from_the_body_by_exactly_one_blank_line(self):
        result = append("## What", ["#941"])
        assert result.body == "## What\n\nCloses #941"

    def test_a_body_ending_in_newlines_gains_no_extra_blank_lines(self):
        result = append("## What\n\n", ["#941"])
        assert result.body == "## What\n\nCloses #941"

    def test_two_refs_produce_two_lines(self):
        result = append("body", ["#941", "#942"])
        assert "Closes #941" in result.body
        assert "Closes #942" in result.body
        assert result.body == "body\n\nCloses #941\n\nCloses #942"

    def test_a_second_pass_over_the_same_body_adds_nothing(self):
        once = append("body", ["#941"])
        twice = append(once.body, ["#941"])
        assert twice.body == once.body
        assert twice.linked == ()
        assert twice.already == ("#941",)

    def test_a_ref_the_body_already_closes_under_another_keyword_is_not_duplicated(self):
        result = append("body\n\nFixes #941", ["#941"])
        assert result.body.count("941") == 1
        assert result.linked == ()
        assert result.already == ("#941",)

    def test_hash_1_is_added_to_a_body_that_only_closes_hash_12(self):
        result = append("body\n\nCloses #12", ["#1"])
        assert "Closes #1" in result.body
        assert result.linked == ("#1",)
        assert result.already == ()

    def test_no_refs_leaves_the_body_untouched(self):
        result = append("body", [])
        assert result == LinkResult(body="body", linked=(), already=())

    def test_linked_and_already_tuples_split_correctly(self):
        result = append("body\n\nCloses #941", ["#941", "#942"])
        assert result.linked == ("#942",)
        assert result.already == ("#941",)
        assert result.body == "body\n\nCloses #941\n\nCloses #942"
