"""Tests for the skill eval task — the trace oracle and the fixtures it grades."""

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
from agent.usage import SessionUsage
from core.phases import Phase
from eval.scoring import RunOutcome
from eval.task import RunArtifacts, RunOptions
import agent.backend
import agent.phases

from eval_scoring_skill_support import _run, _artifacts, _skill_case


class TestGroupMatches:
    def test_every_token_must_be_present(self):
        assert eval.scoring_skill.group_matches(
            ["pr", "comments", "--fix"], ["pr", "comments", "--fix"])

    def test_a_missing_token_fails_the_group(self):
        assert not eval.scoring_skill.group_matches(
            ["pr", "--post"], ["pr", "comments", "--fix"])

    def test_surrounding_arguments_are_ignored(self):
        """So a group need not spell out every surrounding flag."""
        assert eval.scoring_skill.group_matches(
            ["pr", "comments", "--fix"],
            ["pr", "comments", "--fix", "--repo-dir", "/tmp/x"])

    def test_order_within_a_group_is_irrelevant(self):
        assert eval.scoring_skill.group_matches(["--fix", "pr"], ["pr", "comments", "--fix"])

    def test_a_token_must_equal_a_whole_argv_element(self):
        """Substring matching could not tell a subcommand from a flag holding it."""
        assert not eval.scoring_skill.group_matches(
            ["git", "push"], ["git", "remote", "get-url", "--push", "origin"])
        assert eval.scoring_skill.group_matches(["git", "push"], ["git", "push", "--force-with-lease"])
        assert eval.scoring_skill.group_matches(["git", "push"], ["git", "-C", "/p", "push"])

    def test_a_lookalike_binary_is_a_different_command(self):
        """`["pr","rebase"]` used to match the backing script it forbids."""
        assert not eval.scoring_skill.group_matches(
            ["pr", "rebase"], ["pr-rebase", "--branch", "x"])
        assert eval.scoring_skill.group_matches(["pr", "rebase"], ["pr", "rebase"])

    def test_a_flag_does_not_match_its_longer_forms(self):
        """Which is why pr-comments-draft-only forbids both by name."""
        assert not eval.scoring_skill.group_matches(
            ["pr", "--track"], ["pr", "comments", "--finish", "--track-all"])
        assert eval.scoring_skill.group_matches(
            ["pr", "--track"], ["pr", "comments", "--finish", "--track", "T-3"])

    def test_a_joined_flag_and_value_matches_as_two_tokens(self):
        """`--track=T-3` is one element on the wire but two tokens to a manifest."""
        assert eval.scoring_skill.group_matches(
            ["--track", "T-3"], ["pr", "comments", "--finish", "--track=T-3"])
        assert eval.scoring_skill.group_matches(
            ["--track", "T-3"], ["pr", "comments", "--finish", "--track", "T-3"])

    def test_the_joined_element_itself_is_still_a_token(self):
        """So a group naming the literal joined form keeps working."""
        assert eval.scoring_skill.group_matches(
            ["--track=T-3"], ["pr", "comments", "--finish", "--track=T-3"])

    def test_only_the_first_equals_splits_an_element(self):
        arg = "--filter=a=b"
        assert eval.scoring_skill.match_tokens([arg]) == {arg, "--filter", "a=b"}

    def test_an_empty_half_contributes_no_token(self):
        """The harness issues `-c core.fsmonitor=` at startup.

        An empty token in the set would make a malformed group like
        `["git", ""]` fire on that line instead of never firing.
        """
        assert eval.scoring_skill.match_tokens(["core.fsmonitor="]) == {
            "core.fsmonitor=", "core.fsmonitor"}
        assert eval.scoring_skill.match_tokens(["=value"]) == {"=value", "value"}
        assert not eval.scoring_skill.group_matches(
            ["git", ""],
            ["git", "-c", "core.fsmonitor=", "remote", "get-url", "origin"])

    def test_splitting_on_equals_cannot_resurrect_the_push_collision(self):
        """The harness startup lines carry `=` args and `--push` on one line."""
        assert not eval.scoring_skill.group_matches(
            ["git", "push"],
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
             "remote", "get-url", "--push", "origin"])

    def test_an_empty_group_matches_nothing(self):
        """Otherwise an empty forbids entry would fire on every line."""
        assert not eval.scoring_skill.group_matches([], ["pr", "comments", "--fix"])


class TestMatchRequired:
    def test_groups_are_satisfied_in_order(self):
        lines = [
            ["pr", "comments", "--fix"],
            ["pr", "comments", "--finish", "--post"],
        ]
        matches = eval.scoring_skill.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, True]

    def test_out_of_order_leaves_the_later_group_unmatched(self):
        """Drafted-before-published is the claim; both merely appearing is not."""
        lines = [
            ["pr", "comments", "--finish", "--post"],
            ["pr", "comments", "--fix"],
        ]
        matches = eval.scoring_skill.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, False]

    def test_an_empty_trace_matches_nothing(self):
        matches = eval.scoring_skill.match_required([["pr", "--fix"]], [])
        assert [m.matched for m in matches] == [False]

    def test_a_match_records_the_line_that_satisfied_it_as_text(self):
        """Matching reads argv elements; reports and baselines read this string."""
        matches = eval.scoring_skill.match_required(
            [["pr", "--fix"]], [["pr", "comments", "--fix"]])
        assert matches[0].matched_finding_id == "pr comments --fix"

    def test_unmatched_groups_carry_no_line(self):
        matches = eval.scoring_skill.match_required([["pr", "--fix"]], [])
        assert matches[0].matched_finding_id == ""

    def test_the_pattern_is_kept_for_reporting(self):
        matches = eval.scoring_skill.match_required([["pr", "--fix"]], [])
        assert matches[0].pattern == ("pr", "--fix")


class TestMatchForbidden:
    def test_a_violation_anywhere_fires(self):
        fired = eval.scoring_skill.match_forbidden(
            [["--post"]],
            [["pr", "comments", "--fix"], ["pr", "comments", "--post"]])
        assert fired == ["--post"]

    def test_a_clean_trace_fires_nothing(self):
        assert eval.scoring_skill.match_forbidden(
            [["--post"]], [["pr", "comments", "--fix"]]) == []

    def test_each_group_fires_at_most_once(self):
        """Two violations of one rule are one broken rule, not two."""
        fired = eval.scoring_skill.match_forbidden(
            [["--post"]], [["a", "--post"], ["b", "--post"]])
        assert fired == ["--post"]

    def test_every_distinct_group_is_reported(self):
        fired = eval.scoring_skill.match_forbidden(
            [["--post"], ["gh", "api"]],
            [["pr", "--post"], ["gh", "api", "graphql"]])
        assert fired == ["--post", "gh api"]


class TestGroupShapeIsValidated:
    """A group written one level too shallow is inert, and nothing else says so.

    `match_forbidden(["--post"], ...)` iterates the string, so each "group" is
    a one-character string whose characters are never argv elements — a forbid
    that can never fire. Recall would at least drop for the same typo in
    `requires`; a dead `forbids` group costs nothing visible at all.
    """

    def test_a_correctly_nested_group_list_is_accepted(self):
        eval.scoring_skill.check_groups("requires", [["pr", "comments", "--fix"], ["--post"]])

    def test_an_empty_group_list_is_accepted(self):
        """A case with no forbids at all is legitimate."""
        eval.scoring_skill.check_groups("forbids", [])

    def test_a_single_nested_group_list_is_rejected(self):
        with pytest.raises(ValueError, match=re.escape("'--post'")):
            eval.scoring_skill.check_groups("forbids", ["--post"])

    def test_a_group_list_that_is_not_a_list_is_rejected(self):
        with pytest.raises(ValueError, match="list of token groups"):
            eval.scoring_skill.check_groups("forbids", {"pr": "--post"})

    def test_a_group_holding_a_non_string_is_rejected(self):
        with pytest.raises(ValueError, match=re.escape("['pr', 42]")):
            eval.scoring_skill.check_groups("requires", [["pr", 42]])

    def test_an_empty_group_is_rejected(self):
        """`group_matches` never fires on one, so nothing else would report it."""
        with pytest.raises(ValueError, match="must not be empty"):
            eval.scoring_skill.check_groups("forbids", [["git", "push"], []])

    def test_the_error_names_the_offending_group(self):
        with pytest.raises(ValueError, match="forbids group"):
            eval.scoring_skill.check_groups("forbids", [["pr", "--track"], "--post"])


class TestLoadTrace:
    def test_each_record_becomes_one_argv_list(self, tmp_path):
        """Joining first would erase the element boundaries matching needs."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text(
            json.dumps(["pr", "comments", "--fix"]) + "\n"
            + json.dumps(["git", "status"]) + "\n"
        )
        assert eval.scoring_skill.load_trace(str(trace)) == [
            ["pr", "comments", "--fix"], ["git", "status"]]

    def test_a_missing_trace_is_empty_not_an_error(self, tmp_path):
        """A session that ran no command produces no file; that scores 0, not a crash."""
        assert eval.scoring_skill.load_trace(str(tmp_path / "nope.jsonl")) == []

    def test_an_unparseable_line_is_skipped(self, tmp_path):
        """A shim killed mid-write must not take the whole run's score with it."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text('["pr", "comments"]\n{ truncat\n')
        assert eval.scoring_skill.load_trace(str(trace)) == [["pr", "comments"]]

    def test_non_string_elements_are_stringified(self, tmp_path):
        """A shim only writes strings, but a hand-edited trace must not crash matching."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text(json.dumps(["pr", 42]) + "\n")
        assert eval.scoring_skill.load_trace(str(trace)) == [["pr", "42"]]


class TestSkillBody:
    def test_frontmatter_is_stripped(self):
        """The trigger/skip metadata is routing config, not instructions."""
        body = eval.scoring_skill.skill_body("pr-rebase")
        assert not body.startswith("---")
        assert "# PR Rebase" in body

    def test_an_unknown_skill_names_itself(self):
        with pytest.raises(FileNotFoundError) as exc:
            eval.scoring_skill.skill_body("no-such-skill")
        assert "no-such-skill" in str(exc.value)


class TestScore:
    def test_all_required_and_no_violations_is_a_clean_pass(self):
        matches = [eval.scoring_skill.TraceMatch(("pr", "--fix"), True, "pr comments --fix")]
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, []), {})
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_recall_is_the_satisfied_fraction(self):
        matches = [
            eval.scoring_skill.TraceMatch(("a",), True, "a"),
            eval.scoring_skill.TraceMatch(("b",), False, ""),
        ]
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, []), {})
        assert result.recall == 0.5

    def test_any_violation_zeroes_precision(self):
        """A constraint is not a thing you get partial credit for breaking."""
        matches = [eval.scoring_skill.TraceMatch(("a",), True, "a")]
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, ["--post"]), {})
        assert (result.recall, result.precision) == (1.0, 0.0)

    def test_violations_are_counted_and_named(self):
        result = eval.scoring_skill.SkillTask().score(
            _artifacts([], ["--post", "gh api"]), {})
        assert result.false_positive_count == 2
        assert result.false_positive_ids == ["--post", "gh api"]
        assert result.false_positive_ok is False

    def test_a_clean_run_is_within_the_zero_budget(self):
        result = eval.scoring_skill.SkillTask().score(_artifacts([], []), {})
        assert result.false_positive_ok is True

    def test_the_manifest_owns_the_budget(self):
        """Zero is the default, not a hardcode — the corpus field is real."""
        result = eval.scoring_skill.SkillTask().score(
            _artifacts([], ["--post"]), {"false_positives_max": 1})
        assert result.false_positive_ok is True

    def test_severity_accuracy_stays_at_its_zero_default(self):
        """It has no meaning here; inventing one puts noise in the baseline."""
        matches = [eval.scoring_skill.TraceMatch(("a",), True, "a")]
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, []), {})
        assert result.severity_accuracy == 0.0

    def test_matches_satisfy_the_serializer_contract(self):
        """eval.baselines._serialize_run reads these two names off every element."""
        matches = [eval.scoring_skill.TraceMatch(("a",), True, "a run")]
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, []), {})
        assert [m.matched_finding_id for m in result.matches if m.matched] == ["a run"]

    def test_usage_fields_pass_through_without_transposition(self):
        """A transposed field (e.g. input_tokens=usage.output_tokens) must fail this."""
        usage = SessionUsage(
            cost=1.5, input_tokens=10, output_tokens=20,
            cache_read_tokens=30, cache_write_tokens=40, duration_ms=5000,
        )
        artifacts = RunArtifacts(usage=usage, data={"matches": [], "violations": []})
        result = eval.scoring_skill.SkillTask().score(artifacts, {})
        assert result.cost_usd == 1.5
        assert result.duration_ms == 5000
        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert result.billed_input == usage.billed_input
        assert result.cache_read_ratio == usage.cache_read_ratio


class TestTaskRegistration:
    def test_the_runner_can_resolve_it(self):
        import eval.task

        assert eval.task.get_task("skill").name == "skill"


class TestRunValidatesManifest:
    """A hand-written case missing a required field must name itself, not crash blind."""

    def test_a_missing_skill_field_names_the_case(self, tmp_path):
        case_dir = _skill_case(tmp_path, prompt="go")
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            eval.scoring_skill.SkillTask().run(case_dir, RunOptions())

    def test_a_missing_prompt_field_names_the_case(self, tmp_path):
        case_dir = _skill_case(tmp_path, skill="pr-rebase")
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            eval.scoring_skill.SkillTask().run(case_dir, RunOptions())

    @pytest.mark.parametrize("field", ["requires", "forbids"])
    def test_a_single_nested_group_fails_the_case_at_load(self, tmp_path, field):
        """Before a paid run, not after one graded against a dead group."""
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="go", **{field: ["--post"]})
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            eval.scoring_skill.SkillTask().run(case_dir, RunOptions())


class TestRunCleansUpOnFailure:
    def test_a_malformed_responses_file_leaves_no_temp_dirs(self, monkeypatch, tmp_path):
        """A raise after the temp repo and work dir exist must not leak either."""
        case_dir = _skill_case(tmp_path, skill="pr-rebase", prompt="go")
        (case_dir / "responses.json").write_text(json.dumps(
            {"gh": {"rules": [{"stdout": "ok"}]}}  # missing "match"
        ))

        created = []
        real_mkdtemp = eval.scoring_skill.tempfile.mkdtemp

        def recording_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        real_create_temp_repo = eval.scoring_skill.create_temp_repo

        def recording_create_temp_repo(*args, **kwargs):
            path = real_create_temp_repo(*args, **kwargs)
            created.append(path)
            return path

        monkeypatch.setattr(eval.scoring_skill.tempfile, "mkdtemp", recording_mkdtemp)
        monkeypatch.setattr(eval.scoring_skill, "create_temp_repo", recording_create_temp_repo)

        with pytest.raises(ValueError, match="gh"):
            eval.scoring_skill.SkillTask().run(case_dir, RunOptions())

        assert created, "the fixture repo and work dir must have been created"
        assert not any(Path(p).exists() for p in created)


class TestRunWiring:
    """No corpus case ever calls run() end to end (Tasks 5-6 only exercise
    score() against synthetic traces), so this stubs the one seam that would
    otherwise only be checked by hand: the AgentInvocation this method builds.
    """

    def test_env_prompt_and_temp_dirs_are_wired_correctly(self, monkeypatch, tmp_path):
        case_dir = _skill_case(
            tmp_path,
            skill="pr-rebase",
            prompt="rebase the branch",
            requires=[["git", "rebase"]],
            forbids=[["push", "--force"]],
        )

        captured = {}

        def stub_invoke_fix(inv):
            captured["invocation"] = inv
            # The trace path is baked into the shims at bin_dir's sibling,
            # per write_shims's own layout (work_dir/bin, work_dir/trace.jsonl).
            bin_dir = Path(inv.env["PATH"].split(os.pathsep)[0])
            captured["bin_dir"] = bin_dir
            trace_file = bin_dir.parent / "trace.jsonl"
            trace_file.write_text(json.dumps(["git", "rebase", "origin/main"]) + "\n")
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", stub_invoke_fix)

        artifacts = eval.scoring_skill.SkillTask().run(case_dir, RunOptions())
        try:
            inv = captured["invocation"]
            bin_dir = captured["bin_dir"]
            assert "# PR Rebase" in inv.prompt
            assert "rebase the branch" in inv.prompt
            assert artifacts.temp_dirs == [inv.cwd, str(bin_dir.parent)]
            assert [m.matched for m in artifacts.data["matches"]] == [True]
            assert artifacts.data["violations"] == []
        finally:
            for path in artifacts.temp_dirs:
                shutil.rmtree(path, ignore_errors=True)

    def test_empty_model_resolves_through_phase_model_before_invoke_fix(
            self, monkeypatch, tmp_path):
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="go",
            requires=[["git", "rebase"]],
        )
        captured = {}

        def stub_invoke_fix(inv):
            captured["model"] = inv.model
            captured["thinking"] = inv.thinking
            captured["provider"] = inv.provider
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", stub_invoke_fix)
        monkeypatch.setattr(
            agent.phases, "phase_model",
            lambda phase, explicit, cfg=None: "resolved-skill",
        )
        monkeypatch.setattr(
            agent.phases, "phase_thinking",
            lambda phase, effort=None, cfg=None: "low",
        )
        monkeypatch.setattr(
            agent.phases, "phase_provider", lambda cfg=None: "anthropic",
        )

        artifacts = eval.scoring_skill.SkillTask().run(case_dir, RunOptions())
        try:
            assert captured["model"] == "resolved-skill"
            assert captured["thinking"] == "low"
            assert captured["provider"] == "anthropic"
            assert eval.scoring_skill.SkillTask.phase is Phase.COMMENTS_FIX
        finally:
            for path in artifacts.temp_dirs:
                shutil.rmtree(path, ignore_errors=True)

    def test_the_fixture_sha_reaches_the_shims_the_session_runs(
        self, monkeypatch, tmp_path,
    ):
        """End to end: the sha a stub replays is one the session's own repo has.

        The unit tests above pass substitutions in by hand, so none of them would
        notice `run()` failing to derive them — which is the whole path #1177 is
        about.
        """
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="go", requires=[["git", "rebase"]])
        (case_dir / "responses.json").write_text(json.dumps(
            {"pr": {"rules": [
                {"match": ["comments"], "stdout": "@@HEAD_SHORT@@"},
            ]}}))

        captured = {}

        def stub_invoke_fix(inv):
            bin_dir = Path(inv.env["PATH"].split(os.pathsep)[0])
            captured["stdout"] = _run(bin_dir, "pr", "comments").stdout
            captured["cwd"] = inv.cwd
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", stub_invoke_fix)

        artifacts = eval.scoring_skill.SkillTask().run(case_dir, RunOptions())
        try:
            expected = subprocess.run(
                ["git", "-C", captured["cwd"], "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True)
            assert captured["stdout"] == expected.stdout.strip()
        finally:
            for path in artifacts.temp_dirs:
                shutil.rmtree(path, ignore_errors=True)


class TestSkillOutcome:
    """A dead invocation must not reach the baseline as a skill violation.

    A skill case that never ran leaves an empty trace: no `requires` group is
    satisfied and no `forbids` group fires, which scores recall 0.0 and
    precision 1.0 — a compliant model that found nothing, exactly what a real
    miss looks like.
    """

    def test_a_backend_failure_that_spent_nothing_never_ran(self, monkeypatch, tmp_path):
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="rebase",
            requires=[["git", "rebase"]], forbids=[["push", "--force"]],
        )
        monkeypatch.setattr(agent.backend, "invoke_fix", lambda inv: 1)

        artifacts = eval.scoring_skill.SkillTask().run(case_dir, RunOptions())
        try:
            assert artifacts.outcome is RunOutcome.NOT_RUN
            assert not eval.scoring_skill.SkillTask().score(artifacts, {}).measured
        finally:
            for path in artifacts.temp_dirs:
                shutil.rmtree(path, ignore_errors=True)

    def test_the_outcome_reaches_the_score(self):
        artifacts = RunArtifacts(
            data={"matches": [], "violations": []}, outcome=RunOutcome.NOT_RUN)
        assert eval.scoring_skill.SkillTask().score(artifacts, {}).outcome is RunOutcome.NOT_RUN
