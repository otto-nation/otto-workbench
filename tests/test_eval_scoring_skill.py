"""Tests for the skill eval task — the trace oracle and the fixtures it grades."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import eval_scoring_skill as ess


class TestGroupMatches:
    def test_every_token_must_be_present(self):
        assert ess.group_matches(["pr", "comments", "--fix"], "pr comments --fix")

    def test_a_missing_token_fails_the_group(self):
        assert not ess.group_matches(["pr", "--post"], "pr comments --fix")

    def test_tokens_match_as_substrings_not_argv_elements(self):
        """So a group need not spell out every surrounding flag."""
        assert ess.group_matches(
            ["pr", "comments", "--fix"], "pr comments --fix --repo-dir /tmp/x")

    def test_order_within_a_group_is_irrelevant(self):
        assert ess.group_matches(["--fix", "pr"], "pr comments --fix")

    def test_an_empty_group_matches_nothing(self):
        """Otherwise an empty forbids entry would fire on every line."""
        assert not ess.group_matches([], "pr comments --fix")


class TestMatchRequired:
    def test_groups_are_satisfied_in_order(self):
        lines = ["pr comments --fix", "pr comments --finish --post"]
        matches = ess.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, True]

    def test_out_of_order_leaves_the_later_group_unmatched(self):
        """Drafted-before-published is the claim; both merely appearing is not."""
        lines = ["pr comments --finish --post", "pr comments --fix"]
        matches = ess.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, False]

    def test_an_empty_trace_matches_nothing(self):
        matches = ess.match_required([["pr", "--fix"]], [])
        assert [m.matched for m in matches] == [False]

    def test_a_match_records_the_line_that_satisfied_it(self):
        matches = ess.match_required([["pr", "--fix"]], ["pr comments --fix"])
        assert matches[0].matched_finding_id == "pr comments --fix"

    def test_unmatched_groups_carry_no_line(self):
        matches = ess.match_required([["pr", "--fix"]], [])
        assert matches[0].matched_finding_id == ""

    def test_the_pattern_is_kept_for_reporting(self):
        matches = ess.match_required([["pr", "--fix"]], [])
        assert matches[0].pattern == ("pr", "--fix")


class TestMatchForbidden:
    def test_a_violation_anywhere_fires(self):
        fired = ess.match_forbidden(
            [["--post"]], ["pr comments --fix", "pr comments --post"])
        assert fired == ["--post"]

    def test_a_clean_trace_fires_nothing(self):
        assert ess.match_forbidden([["--post"]], ["pr comments --fix"]) == []

    def test_each_group_fires_at_most_once(self):
        """Two violations of one rule are one broken rule, not two."""
        fired = ess.match_forbidden([["--post"]], ["a --post", "b --post"])
        assert fired == ["--post"]

    def test_every_distinct_group_is_reported(self):
        fired = ess.match_forbidden(
            [["--post"], ["gh", "api"]], ["pr --post", "gh api graphql"])
        assert fired == ["--post", "gh api"]


class TestLoadTrace:
    def test_each_record_becomes_one_joined_line(self, tmp_path):
        trace = tmp_path / "trace.jsonl"
        trace.write_text(
            json.dumps(["pr", "comments", "--fix"]) + "\n"
            + json.dumps(["git", "status"]) + "\n"
        )
        assert ess.load_trace(str(trace)) == ["pr comments --fix", "git status"]

    def test_a_missing_trace_is_empty_not_an_error(self, tmp_path):
        """A session that ran no command produces no file; that scores 0, not a crash."""
        assert ess.load_trace(str(tmp_path / "nope.jsonl")) == []

    def test_an_unparseable_line_is_skipped(self, tmp_path):
        """A shim killed mid-write must not take the whole run's score with it."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text('["pr", "comments"]\n{ truncat\n')
        assert ess.load_trace(str(trace)) == ["pr comments"]


def _run(bin_dir, name, *args):
    """Invoke a generated shim the way the session would — by name, off PATH."""
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        [name, *args], capture_output=True, text=True, env=env)


class TestWriteShims:
    def test_a_matching_rule_replays_its_stdout_and_exit(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims(
            {"pr": {"rules": [
                {"match": ["comments", "--fix"], "stdout": '{"ok":1}', "exit": 0},
            ]}},
            bin_dir, case, tmp_path / "trace.jsonl",
        )
        result = _run(bin_dir, "pr", "comments", "--fix")
        assert (result.returncode, result.stdout) == (0, '{"ok":1}')

    def test_every_call_is_recorded_with_the_binary_name_first(self, tmp_path):
        """argv[0] is a temp path that changes each run; the name is what matches."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        ess.write_shims({"pr": {"rules": []}}, bin_dir, case, trace)
        _run(bin_dir, "pr", "comments", "--fix")
        assert ess.load_trace(str(trace)) == ["pr comments --fix"]

    def test_an_unmatched_call_is_recorded_before_it_fails(self, tmp_path):
        """A violation the harness never anticipated still has to be gradeable."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        ess.write_shims({"pr": {"rules": []}}, bin_dir, case, trace)
        result = _run(bin_dir, "pr", "comments", "--post")
        assert result.returncode == ess.NO_MATCH_EXIT
        assert ess.load_trace(str(trace)) == ["pr comments --post"]

    def test_fail_is_the_default_policy(self, tmp_path):
        """An omitted on_no_match must not silently succeed."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims({"gh": {"rules": []}}, bin_dir, case, tmp_path / "t.jsonl")
        assert _run(bin_dir, "gh", "api", "graphql").returncode == ess.NO_MATCH_EXIT

    def test_stdout_file_is_read_relative_to_the_case(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        (case / "report.json").write_text('{"fix_pass":{}}')
        ess.write_shims(
            {"pr": {"rules": [
                {"match": ["comments"], "stdout_file": "report.json"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert _run(bin_dir, "pr", "comments").stdout == '{"fix_pass":{}}'

    def test_the_first_matching_rule_wins(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims(
            {"pr": {"rules": [
                {"match": ["comments", "--fix"], "stdout": "first"},
                {"match": ["comments"], "stdout": "second"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert _run(bin_dir, "pr", "comments", "--fix").stdout == "first"

    def test_passthrough_execs_the_real_binary(self, tmp_path):
        """git status must still work; only the rules that matter are intercepted."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        ess.write_shims(
            {"git": {"on_no_match": "passthrough", "rules": []}},
            bin_dir, case, trace,
        )
        result = _run(bin_dir, "git", "--version")
        assert result.returncode == 0
        assert "git version" in result.stdout
        assert ess.load_trace(str(trace)) == ["git --version"]

    def test_passthrough_still_honours_its_rules(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims(
            {"git": {"on_no_match": "passthrough", "rules": [
                {"match": ["push"], "exit": 1, "stderr": "refusing"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        result = _run(bin_dir, "git", "push", "--force-with-lease")
        assert (result.returncode, result.stderr) == (1, "refusing")

    def test_shims_are_executable(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims({"pr": {"rules": []}}, bin_dir, case, tmp_path / "t.jsonl")
        assert os.access(bin_dir / "pr", os.X_OK)
