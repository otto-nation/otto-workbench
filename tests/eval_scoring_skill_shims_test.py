"""Tests for eval.scoring_skill's command shims and the fixture values substituted into them."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import eval.scoring_skill
from eval.task import RunOptions
import agent.backend

from eval_scoring_skill_support import _run, _skill_case, CORPUS


class TestWriteShims:
    def test_a_matching_rule_replays_its_stdout_and_exit(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
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
        eval.scoring_skill.write_shims({"pr": {"rules": []}}, bin_dir, case, trace)
        _run(bin_dir, "pr", "comments", "--fix")
        assert eval.scoring_skill.load_trace(str(trace)) == [["pr", "comments", "--fix"]]

    def test_an_unmatched_call_is_recorded_before_it_fails(self, tmp_path):
        """A violation the harness never anticipated still has to be gradeable."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        eval.scoring_skill.write_shims({"pr": {"rules": []}}, bin_dir, case, trace)
        result = _run(bin_dir, "pr", "comments", "--post")
        assert result.returncode == eval.scoring_skill.NO_MATCH_EXIT
        assert eval.scoring_skill.load_trace(str(trace)) == [["pr", "comments", "--post"]]

    def test_fail_is_the_default_policy(self, tmp_path):
        """An omitted on_no_match must not silently succeed."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims({"gh": {"rules": []}}, bin_dir, case, tmp_path / "t.jsonl")
        assert _run(bin_dir, "gh", "api", "graphql").returncode == eval.scoring_skill.NO_MATCH_EXIT

    def test_stdout_file_is_read_relative_to_the_case(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        (case / "report.json").write_text('{"fix_pass":{}}')
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["comments"], "stdout_file": "report.json"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert _run(bin_dir, "pr", "comments").stdout == '{"fix_pass":{}}'

    def test_the_first_matching_rule_wins(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
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
        eval.scoring_skill.write_shims(
            {"git": {"on_no_match": "passthrough", "rules": []}},
            bin_dir, case, trace,
        )
        result = _run(bin_dir, "git", "--version")
        assert result.returncode == 0
        assert "git version" in result.stdout
        assert eval.scoring_skill.load_trace(str(trace)) == [["git", "--version"]]

    def test_passthrough_still_honours_its_rules(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"git": {"on_no_match": "passthrough", "rules": [
                {"match": ["push"], "exit": 1, "stderr": "refusing"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        result = _run(bin_dir, "git", "push", "--force-with-lease")
        assert (result.returncode, result.stderr) == (1, "refusing")

    def test_a_rule_matches_whole_argv_elements(self, tmp_path):
        """`["push"]` must not intercept the harness's own `remote get-url --push`.

        The shim rules and the manifest groups run the same comparison against
        the same normalized argv, so a rule fires only on the subcommand it names.
        """
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        eval.scoring_skill.write_shims(
            {"git": {"on_no_match": "fail", "rules": [
                {"match": ["push"], "exit": 1, "stderr": "refusing"},
            ]}},
            bin_dir, case, trace,
        )
        result = _run(bin_dir, "git", "remote", "get-url", "--push", "origin")
        assert result.returncode == eval.scoring_skill.NO_MATCH_EXIT
        assert result.stderr != "refusing"
        assert eval.scoring_skill.load_trace(str(trace)) == [
            ["git", "remote", "get-url", "--push", "origin"]]

    def test_a_rule_splits_a_joined_flag_the_way_a_manifest_group_does(self, tmp_path):
        """A rule and a group have to mean the same thing on the same line."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["--track", "T-3"], "stdout": "tracked"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert _run(bin_dir, "pr", "comments", "--track=T-3").stdout == "tracked"

    def test_shims_are_executable(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims({"pr": {"rules": []}}, bin_dir, case, tmp_path / "t.jsonl")
        assert os.access(bin_dir / "pr", os.X_OK)

    def test_an_empty_match_list_is_rejected(self, tmp_path):
        """A rule that can never fire is a fixture typo, and under passthrough
        it is a silent one: the call it meant to intercept reaches the real
        binary. Stub a binary for tracing alone with no rules, not an empty one.
        """
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match="must not be empty"):
            eval.scoring_skill.write_shims(
                {"git": {"on_no_match": "passthrough", "rules": [
                    {"match": [], "stderr": "refusing", "exit": 1},
                ]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )

    def test_an_unknown_on_no_match_policy_is_rejected(self, tmp_path):
        """A typo reads as "fail", so a passthrough stub would exit 97 on every
        call — the case then fails as a scenario bug, not as the typo it is.
        """
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match="on_no_match"):
            eval.scoring_skill.write_shims(
                {"git": {"on_no_match": "passthru", "rules": []}},
                bin_dir, case, tmp_path / "t.jsonl",
            )

    @pytest.mark.parametrize("policy", eval.scoring_skill.POLICIES)
    def test_both_documented_policies_are_accepted(self, tmp_path, policy):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"git": {"on_no_match": policy, "rules": []}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert (bin_dir / "git").is_file()

    def test_a_rule_missing_match_raises_naming_the_binary(self, tmp_path):
        """A missing `match` key is a malformed fixture, not a silent catch-all."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match="gh"):
            eval.scoring_skill.write_shims(
                {"gh": {"rules": [{"stdout": "ok", "exit": 0}]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )

    def test_a_string_match_raises_instead_of_exploding_into_characters(self, tmp_path):
        """`"push"` would resolve to `['p','u','s','h']` — a rule that never fires."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match=re.escape("'push'")):
            eval.scoring_skill.write_shims(
                {"git": {"rules": [{"match": "push", "exit": 1}]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )


class TestFixtureSubstitution:
    """A case cannot spell the sha of a repo built at run time.

    Three `pr-comments` fixtures cited a literal `a1b2c3d` no commit has. It was
    inert while the cases stopped at a draft and load-bearing the moment one
    graded publishing: a model told to publish replies citing that sha checked
    it, found nothing, and declined — the care the skill asks for, scored as
    recall 0.
    """

    SUBS = {"HEAD_SHA": "0" * 40, "HEAD_SHORT": "0123abc"}

    def test_a_placeholder_in_stderr_is_replaced_with_the_fixture_sha(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["comments"], "stderr": "fixed in @@HEAD_SHORT@@\n"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl", substitutions=self.SUBS,
        )
        assert _run(bin_dir, "pr", "comments").stderr == "fixed in 0123abc\n"

    def test_a_placeholder_inside_a_stdout_file_is_replaced_too(self, tmp_path):
        """The report the skill parses is where the sha it quotes comes from."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        (case / "report.json").write_text('{"commit_sha": "@@HEAD_SHORT@@"}')
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["comments"], "stdout_file": "report.json"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl", substitutions=self.SUBS,
        )
        assert json.loads(_run(bin_dir, "pr", "comments").stdout) == {
            "commit_sha": "0123abc"}

    def test_a_placeholder_in_a_match_token_fires_the_rule_on_the_real_sha(
        self, tmp_path,
    ):
        """So a case can stub a command that takes the sha as an argument."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["--commit", "@@HEAD_SHORT@@"], "stdout": "settled"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl", substitutions=self.SUBS,
        )
        assert _run(bin_dir, "pr", "--commit", "0123abc").stdout == "settled"

    def test_the_short_form_is_the_width_the_report_field_is_documented_at(
        self, tmp_path,
    ):
        """`commit_sha` is the abbreviated sha the real command persists, and
        `git.client` owns that width rather than this module slicing its own."""
        import git.client
        src = tmp_path / "src"
        src.mkdir()
        (src / "bug.py").write_text("x = 1\n")
        repo = eval.scoring_skill.create_temp_repo(str(src), prefix="eval-subs-test-")
        try:
            subs = eval.scoring_skill.fixture_substitutions(repo)
            assert subs["HEAD_SHORT"] == git.client.abbrev(subs["HEAD_SHA"])
            kind = subprocess.run(
                ["git", "-C", repo, "cat-file", "-t", subs["HEAD_SHORT"]],
                capture_output=True, text=True)
            assert kind.stdout.strip() == "commit"
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_a_rule_with_no_placeholder_is_left_byte_for_byte_alone(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        eval.scoring_skill.write_shims(
            {"pr": {"rules": [
                {"match": ["comments"], "stdout": '{"ok": 1}', "exit": 3},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl", substitutions=self.SUBS,
        )
        result = _run(bin_dir, "pr", "comments")
        assert (result.stdout, result.returncode) == ('{"ok": 1}', 3)

    def test_an_unknown_placeholder_names_the_binary_and_itself(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match=re.escape("@@HEAD_SHAA@@")) as exc:
            eval.scoring_skill.write_shims(
                {"pr": {"rules": [
                    {"match": ["comments"], "stderr": "fixed in @@HEAD_SHAA@@"},
                ]}},
                bin_dir, case, tmp_path / "t.jsonl", substitutions=self.SUBS,
            )
        assert "pr:" in str(exc.value)

    def test_a_placeholder_with_no_substitutions_offered_is_still_an_error(
        self, tmp_path,
    ):
        """A caller with no repo must not ship the literal token to the model:
        an absent value and a misspelt name are the same fixture bug."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match=re.escape("@@HEAD_SHORT@@")):
            eval.scoring_skill.write_shims(
                {"pr": {"rules": [
                    {"match": ["comments"], "stderr": "fixed in @@HEAD_SHORT@@"},
                ]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )

    def test_a_placeholder_is_rejected_before_a_run_is_paid_for(
        self, monkeypatch, tmp_path,
    ):
        """The typo costs nothing: the raise lands before the model is invoked."""
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="go", requires=[["git", "rebase"]])
        (case_dir / "responses.json").write_text(json.dumps(
            {"pr": {"rules": [{"match": ["comments"], "stdout": "@@NOPE@@"}]}}))

        def fail_if_called(inv):
            raise AssertionError("invoke_fix ran despite an unknown placeholder")

        monkeypatch.setattr(agent.backend, "invoke_fix", fail_if_called)
        with pytest.raises(ValueError, match=re.escape("@@NOPE@@")):
            eval.scoring_skill.SkillTask().run(case_dir, RunOptions())


class TestWorktreeStubAnswersEverySwitchSpelling:
    """`wt` is fail-closed, so a rule narrower than the skill is a fixture gap.

    A rule of `["switch", "main"]` used to catch `wt switch origin/main` by
    substring. Under exact matching it would not, and the stub would exit 97
    mid-session — the session observing a hard failure, not a graded outcome.
    """

    @pytest.mark.parametrize(
        "case_name", ["pr-comments-draft-only", "pr-comments-approved"])
    @pytest.mark.parametrize("target", ["main", "origin/main"])
    def test_the_stub_returns_a_path_for_any_branch_spelling(
        self, tmp_path, case_name, target,
    ):
        case = CORPUS / case_name
        responses = json.loads((case / "responses.json").read_text())
        bin_dir = tmp_path / "bin"
        # The case's `pr` rules cite the fixture sha, and expansion is strict,
        # so a corpus file cannot be shimmed without offering one.
        eval.scoring_skill.write_shims(
            responses, bin_dir, case, tmp_path / "t.jsonl",
            substitutions=TestFixtureSubstitution.SUBS,
        )
        result = _run(bin_dir, "wt", "switch", target)
        assert result.returncode == 0
        assert json.loads(result.stdout)["path"]
