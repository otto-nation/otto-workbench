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

import eval_scoring_skill as ess
from ai_usage import SessionUsage
from eval_task import RunArtifacts, RunOptions


class TestGroupMatches:
    def test_every_token_must_be_present(self):
        assert ess.group_matches(
            ["pr", "comments", "--fix"], ["pr", "comments", "--fix"])

    def test_a_missing_token_fails_the_group(self):
        assert not ess.group_matches(
            ["pr", "--post"], ["pr", "comments", "--fix"])

    def test_surrounding_arguments_are_ignored(self):
        """So a group need not spell out every surrounding flag."""
        assert ess.group_matches(
            ["pr", "comments", "--fix"],
            ["pr", "comments", "--fix", "--repo-dir", "/tmp/x"])

    def test_order_within_a_group_is_irrelevant(self):
        assert ess.group_matches(["--fix", "pr"], ["pr", "comments", "--fix"])

    def test_a_token_must_equal_a_whole_argv_element(self):
        """Substring matching could not tell a subcommand from a flag holding it."""
        assert not ess.group_matches(
            ["git", "push"], ["git", "remote", "get-url", "--push", "origin"])
        assert ess.group_matches(["git", "push"], ["git", "push", "--force-with-lease"])
        assert ess.group_matches(["git", "push"], ["git", "-C", "/p", "push"])

    def test_a_lookalike_binary_is_a_different_command(self):
        """`["pr","rebase"]` used to match the backing script it forbids."""
        assert not ess.group_matches(
            ["pr", "rebase"], ["pr-rebase", "--branch", "x"])
        assert ess.group_matches(["pr", "rebase"], ["pr", "rebase"])

    def test_a_flag_does_not_match_its_longer_forms(self):
        """Which is why pr-comments-draft-only forbids both by name."""
        assert not ess.group_matches(
            ["pr", "--track"], ["pr", "comments", "--finish", "--track-all"])
        assert ess.group_matches(
            ["pr", "--track"], ["pr", "comments", "--finish", "--track", "T-3"])

    def test_a_joined_flag_and_value_matches_as_two_tokens(self):
        """`--track=T-3` is one element on the wire but two tokens to a manifest."""
        assert ess.group_matches(
            ["--track", "T-3"], ["pr", "comments", "--finish", "--track=T-3"])
        assert ess.group_matches(
            ["--track", "T-3"], ["pr", "comments", "--finish", "--track", "T-3"])

    def test_the_joined_element_itself_is_still_a_token(self):
        """So a group naming the literal joined form keeps working."""
        assert ess.group_matches(
            ["--track=T-3"], ["pr", "comments", "--finish", "--track=T-3"])

    def test_only_the_first_equals_splits_an_element(self):
        arg = "--filter=a=b"
        assert ess.match_tokens([arg]) == {arg, "--filter", "a=b"}

    def test_an_empty_half_contributes_no_token(self):
        """The harness issues `-c core.fsmonitor=` at startup.

        An empty token in the set would make a malformed group like
        `["git", ""]` fire on that line instead of never firing.
        """
        assert ess.match_tokens(["core.fsmonitor="]) == {
            "core.fsmonitor=", "core.fsmonitor"}
        assert ess.match_tokens(["=value"]) == {"=value", "value"}
        assert not ess.group_matches(
            ["git", ""],
            ["git", "-c", "core.fsmonitor=", "remote", "get-url", "origin"])

    def test_splitting_on_equals_cannot_resurrect_the_push_collision(self):
        """The harness startup lines carry `=` args and `--push` on one line."""
        assert not ess.group_matches(
            ["git", "push"],
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
             "remote", "get-url", "--push", "origin"])

    def test_an_empty_group_matches_nothing(self):
        """Otherwise an empty forbids entry would fire on every line."""
        assert not ess.group_matches([], ["pr", "comments", "--fix"])


class TestMatchRequired:
    def test_groups_are_satisfied_in_order(self):
        lines = [
            ["pr", "comments", "--fix"],
            ["pr", "comments", "--finish", "--post"],
        ]
        matches = ess.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, True]

    def test_out_of_order_leaves_the_later_group_unmatched(self):
        """Drafted-before-published is the claim; both merely appearing is not."""
        lines = [
            ["pr", "comments", "--finish", "--post"],
            ["pr", "comments", "--fix"],
        ]
        matches = ess.match_required(
            [["pr", "--fix"], ["--finish", "--post"]], lines)
        assert [m.matched for m in matches] == [True, False]

    def test_an_empty_trace_matches_nothing(self):
        matches = ess.match_required([["pr", "--fix"]], [])
        assert [m.matched for m in matches] == [False]

    def test_a_match_records_the_line_that_satisfied_it_as_text(self):
        """Matching reads argv elements; reports and baselines read this string."""
        matches = ess.match_required(
            [["pr", "--fix"]], [["pr", "comments", "--fix"]])
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
            [["--post"]],
            [["pr", "comments", "--fix"], ["pr", "comments", "--post"]])
        assert fired == ["--post"]

    def test_a_clean_trace_fires_nothing(self):
        assert ess.match_forbidden(
            [["--post"]], [["pr", "comments", "--fix"]]) == []

    def test_each_group_fires_at_most_once(self):
        """Two violations of one rule are one broken rule, not two."""
        fired = ess.match_forbidden(
            [["--post"]], [["a", "--post"], ["b", "--post"]])
        assert fired == ["--post"]

    def test_every_distinct_group_is_reported(self):
        fired = ess.match_forbidden(
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
        ess.check_groups("requires", [["pr", "comments", "--fix"], ["--post"]])

    def test_an_empty_group_list_is_accepted(self):
        """A case with no forbids at all is legitimate."""
        ess.check_groups("forbids", [])

    def test_a_single_nested_group_list_is_rejected(self):
        with pytest.raises(ValueError, match=re.escape("'--post'")):
            ess.check_groups("forbids", ["--post"])

    def test_a_group_list_that_is_not_a_list_is_rejected(self):
        with pytest.raises(ValueError, match="list of token groups"):
            ess.check_groups("forbids", {"pr": "--post"})

    def test_a_group_holding_a_non_string_is_rejected(self):
        with pytest.raises(ValueError, match=re.escape("['pr', 42]")):
            ess.check_groups("requires", [["pr", 42]])

    def test_the_error_names_the_offending_group(self):
        with pytest.raises(ValueError, match="forbids group"):
            ess.check_groups("forbids", [["pr", "--track"], "--post"])


class TestLoadTrace:
    def test_each_record_becomes_one_argv_list(self, tmp_path):
        """Joining first would erase the element boundaries matching needs."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text(
            json.dumps(["pr", "comments", "--fix"]) + "\n"
            + json.dumps(["git", "status"]) + "\n"
        )
        assert ess.load_trace(str(trace)) == [
            ["pr", "comments", "--fix"], ["git", "status"]]

    def test_a_missing_trace_is_empty_not_an_error(self, tmp_path):
        """A session that ran no command produces no file; that scores 0, not a crash."""
        assert ess.load_trace(str(tmp_path / "nope.jsonl")) == []

    def test_an_unparseable_line_is_skipped(self, tmp_path):
        """A shim killed mid-write must not take the whole run's score with it."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text('["pr", "comments"]\n{ truncat\n')
        assert ess.load_trace(str(trace)) == [["pr", "comments"]]

    def test_non_string_elements_are_stringified(self, tmp_path):
        """A shim only writes strings, but a hand-edited trace must not crash matching."""
        trace = tmp_path / "trace.jsonl"
        trace.write_text(json.dumps(["pr", 42]) + "\n")
        assert ess.load_trace(str(trace)) == [["pr", "42"]]


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
        assert ess.load_trace(str(trace)) == [["pr", "comments", "--fix"]]

    def test_an_unmatched_call_is_recorded_before_it_fails(self, tmp_path):
        """A violation the harness never anticipated still has to be gradeable."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        ess.write_shims({"pr": {"rules": []}}, bin_dir, case, trace)
        result = _run(bin_dir, "pr", "comments", "--post")
        assert result.returncode == ess.NO_MATCH_EXIT
        assert ess.load_trace(str(trace)) == [["pr", "comments", "--post"]]

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
        assert ess.load_trace(str(trace)) == [["git", "--version"]]

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

    def test_a_rule_matches_whole_argv_elements(self, tmp_path):
        """`["push"]` must not intercept the harness's own `remote get-url --push`.

        The shim rules and the manifest groups run the same comparison against
        the same normalized argv, so a rule fires only on the subcommand it names.
        """
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        trace = tmp_path / "trace.jsonl"
        ess.write_shims(
            {"git": {"on_no_match": "fail", "rules": [
                {"match": ["push"], "exit": 1, "stderr": "refusing"},
            ]}},
            bin_dir, case, trace,
        )
        result = _run(bin_dir, "git", "remote", "get-url", "--push", "origin")
        assert result.returncode == ess.NO_MATCH_EXIT
        assert result.stderr != "refusing"
        assert ess.load_trace(str(trace)) == [
            ["git", "remote", "get-url", "--push", "origin"]]

    def test_a_rule_splits_a_joined_flag_the_way_a_manifest_group_does(self, tmp_path):
        """A rule and a group have to mean the same thing on the same line."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims(
            {"pr": {"rules": [
                {"match": ["--track", "T-3"], "stdout": "tracked"},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        assert _run(bin_dir, "pr", "comments", "--track=T-3").stdout == "tracked"

    def test_shims_are_executable(self, tmp_path):
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims({"pr": {"rules": []}}, bin_dir, case, tmp_path / "t.jsonl")
        assert os.access(bin_dir / "pr", os.X_OK)

    def test_an_empty_match_list_never_fires(self, tmp_path):
        """all([]) is True; an empty match must not become a catch-all rule."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        ess.write_shims(
            {"pr": {"rules": [
                {"match": [], "stdout": "should never appear", "exit": 0},
            ]}},
            bin_dir, case, tmp_path / "t.jsonl",
        )
        result = _run(bin_dir, "pr", "comments", "--fix")
        assert result.returncode == ess.NO_MATCH_EXIT

    def test_a_rule_missing_match_raises_naming_the_binary(self, tmp_path):
        """A missing `match` key is a malformed fixture, not a silent catch-all."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match="gh"):
            ess.write_shims(
                {"gh": {"rules": [{"stdout": "ok", "exit": 0}]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )

    def test_a_string_match_raises_instead_of_exploding_into_characters(self, tmp_path):
        """`"push"` would resolve to `['p','u','s','h']` — a rule that never fires."""
        bin_dir, case = tmp_path / "bin", tmp_path / "case"
        case.mkdir()
        with pytest.raises(ValueError, match=re.escape("'push'")):
            ess.write_shims(
                {"git": {"rules": [{"match": "push", "exit": 1}]}},
                bin_dir, case, tmp_path / "t.jsonl",
            )


class TestSkillBody:
    def test_frontmatter_is_stripped(self):
        """The trigger/skip metadata is routing config, not instructions."""
        body = ess.skill_body("pr-rebase")
        assert not body.startswith("---")
        assert "# PR Rebase" in body

    def test_an_unknown_skill_names_itself(self):
        with pytest.raises(FileNotFoundError) as exc:
            ess.skill_body("no-such-skill")
        assert "no-such-skill" in str(exc.value)


def _artifacts(matches, violations):
    return RunArtifacts(data={"matches": matches, "violations": violations})


class TestScore:
    def test_all_required_and_no_violations_is_a_clean_pass(self):
        matches = [ess.TraceMatch(("pr", "--fix"), True, "pr comments --fix")]
        result = ess.SkillTask().score(_artifacts(matches, []), {})
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_recall_is_the_satisfied_fraction(self):
        matches = [
            ess.TraceMatch(("a",), True, "a"),
            ess.TraceMatch(("b",), False, ""),
        ]
        result = ess.SkillTask().score(_artifacts(matches, []), {})
        assert result.recall == 0.5

    def test_any_violation_zeroes_precision(self):
        """A constraint is not a thing you get partial credit for breaking."""
        matches = [ess.TraceMatch(("a",), True, "a")]
        result = ess.SkillTask().score(_artifacts(matches, ["--post"]), {})
        assert (result.recall, result.precision) == (1.0, 0.0)

    def test_violations_are_counted_and_named(self):
        result = ess.SkillTask().score(
            _artifacts([], ["--post", "gh api"]), {})
        assert result.false_positive_count == 2
        assert result.false_positive_ids == ["--post", "gh api"]
        assert result.false_positive_ok is False

    def test_a_clean_run_is_within_the_zero_budget(self):
        result = ess.SkillTask().score(_artifacts([], []), {})
        assert result.false_positive_ok is True

    def test_the_manifest_owns_the_budget(self):
        """Zero is the default, not a hardcode — the corpus field is real."""
        result = ess.SkillTask().score(
            _artifacts([], ["--post"]), {"false_positives_max": 1})
        assert result.false_positive_ok is True

    def test_severity_accuracy_stays_at_its_zero_default(self):
        """It has no meaning here; inventing one puts noise in the baseline."""
        matches = [ess.TraceMatch(("a",), True, "a")]
        result = ess.SkillTask().score(_artifacts(matches, []), {})
        assert result.severity_accuracy == 0.0

    def test_matches_satisfy_the_serializer_contract(self):
        """eval-models._serialize_run reads these two names off every element."""
        matches = [ess.TraceMatch(("a",), True, "a run")]
        result = ess.SkillTask().score(_artifacts(matches, []), {})
        assert [m.matched_finding_id for m in result.matches if m.matched] == ["a run"]

    def test_usage_fields_pass_through_without_transposition(self):
        """A transposed field (e.g. input_tokens=usage.output_tokens) must fail this."""
        usage = SessionUsage(
            cost=1.5, input_tokens=10, output_tokens=20,
            cache_read_tokens=30, cache_write_tokens=40, duration_ms=5000,
        )
        artifacts = RunArtifacts(usage=usage, data={"matches": [], "violations": []})
        result = ess.SkillTask().score(artifacts, {})
        assert result.cost_usd == 1.5
        assert result.duration_ms == 5000
        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert result.billed_input == usage.billed_input
        assert result.cache_read_ratio == usage.cache_read_ratio


class TestTaskRegistration:
    def test_the_runner_can_resolve_it(self):
        import eval_task

        assert eval_task.get_task("skill").name == "skill"


def _skill_case(tmp_path, **manifest_fields):
    """A minimal on-disk case: a one-file src/ tree plus a manifest.json.

    create_temp_repo commits src/'s contents as "add buggy code"; an empty
    tree leaves nothing to commit and git exits non-zero.
    """
    case_dir = tmp_path / "case"
    (case_dir / "src").mkdir(parents=True)
    (case_dir / "src" / "placeholder.txt").write_text("fixture\n")
    (case_dir / "manifest.json").write_text(json.dumps(manifest_fields))
    return case_dir


class TestRunValidatesManifest:
    """A hand-written case missing a required field must name itself, not crash blind."""

    def test_a_missing_skill_field_names_the_case(self, tmp_path):
        case_dir = _skill_case(tmp_path, prompt="go")
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            ess.SkillTask().run(case_dir, RunOptions())

    def test_a_missing_prompt_field_names_the_case(self, tmp_path):
        case_dir = _skill_case(tmp_path, skill="pr-rebase")
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            ess.SkillTask().run(case_dir, RunOptions())

    @pytest.mark.parametrize("field", ["requires", "forbids"])
    def test_a_single_nested_group_fails_the_case_at_load(self, tmp_path, field):
        """Before a paid run, not after one graded against a dead group."""
        case_dir = _skill_case(
            tmp_path, skill="pr-rebase", prompt="go", **{field: ["--post"]})
        with pytest.raises(ValueError, match=re.escape(str(case_dir))):
            ess.SkillTask().run(case_dir, RunOptions())


class TestRunCleansUpOnFailure:
    def test_a_malformed_responses_file_leaves_no_temp_dirs(self, monkeypatch, tmp_path):
        """A raise after the temp repo and work dir exist must not leak either."""
        case_dir = _skill_case(tmp_path, skill="pr-rebase", prompt="go")
        (case_dir / "responses.json").write_text(json.dumps(
            {"gh": {"rules": [{"stdout": "ok"}]}}  # missing "match"
        ))

        created = []
        real_mkdtemp = ess.tempfile.mkdtemp

        def recording_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        real_create_temp_repo = ess.create_temp_repo

        def recording_create_temp_repo(*args, **kwargs):
            path = real_create_temp_repo(*args, **kwargs)
            created.append(path)
            return path

        monkeypatch.setattr(ess.tempfile, "mkdtemp", recording_mkdtemp)
        monkeypatch.setattr(ess, "create_temp_repo", recording_create_temp_repo)

        with pytest.raises(ValueError, match="gh"):
            ess.SkillTask().run(case_dir, RunOptions())

        assert created, "the fixture repo and work dir must have been created"
        assert not any(Path(p).exists() for p in created)


CORPUS = REPO_ROOT / "eval" / "corpus"


def _skill_cases():
    cases = []
    for manifest_path in sorted(CORPUS.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("task") == "skill":
            cases.append(pytest.param(manifest_path, id=manifest_path.parent.name))
    return cases


class TestSkillCasesAreNotVacuous:
    """An oracle that cannot fail, or cannot be met, measures nothing.

    This is what `reference-fix/` does for ci-fix, minus the tokens: prove each
    case is both satisfiable and failable without invoking a model.
    """

    def test_at_least_one_skill_case_exists(self):
        assert _skill_cases(), "no skill cases in the corpus"

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_the_named_skill_exists(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        assert ess.skill_body(manifest["skill"])

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_the_case_asks_for_something(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        assert manifest.get("requires"), "requires is empty — nothing to satisfy"
        assert manifest.get("prompt"), "prompt is empty — nothing to drive"

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_every_group_is_shaped_like_a_group(self, manifest_path):
        """run() rejects these too, but only once someone pays for the run."""
        manifest = json.loads(manifest_path.read_text())
        ess.check_groups("requires", manifest["requires"])
        ess.check_groups("forbids", manifest.get("forbids", []))

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_a_satisfying_trace_scores_one(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        lines = [list(group) for group in manifest["requires"]]
        matches = ess.match_required(manifest["requires"], lines)
        violations = ess.match_forbidden(manifest.get("forbids", []), lines)
        assert violations == [], (
            "the ideal trace trips its own forbids — the case cannot be passed")
        result = ess.SkillTask().score(
            _artifacts(matches, violations), manifest)
        assert (result.recall, result.precision) == (1.0, 1.0)

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_an_empty_trace_scores_zero(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        matches = ess.match_required(manifest["requires"], [])
        result = ess.SkillTask().score(_artifacts(matches, []), manifest)
        assert result.recall == 0.0

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_responses_stub_every_binary_the_oracle_grades(self, manifest_path):
        """A forbids group naming an unstubbed binary can never fire.

        Only groups that lead with a binary are checked. A group of bare flags
        (`["--post"]`) constrains whatever the case already stubs, and has no
        binary of its own to look up.
        """
        manifest = json.loads(manifest_path.read_text())
        responses = json.loads((manifest_path.parent / "responses.json").read_text())
        named = [g for g in manifest.get("forbids", []) if not g[0].startswith("-")]
        for group in named:
            assert group[0] in responses, (
                f"forbids {group} names {group[0]!r}, which no shim records")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_responses_stub_every_binary_a_required_group_names(self, manifest_path):
        """An unstubbed `requires` binary is worse than an unstubbed `forbids` one.

        No shim on PATH means the real binary runs — real GitHub, real
        credentials, since `clean_env` only strips git vars — and no trace
        line is ever recorded for the group, so it can never be satisfied.
        Same skip as the forbids check: a group leading with a bare flag
        constrains whatever the case already stubs and names no binary of
        its own.
        """
        manifest = json.loads(manifest_path.read_text())
        responses = json.loads((manifest_path.parent / "responses.json").read_text())
        named = [g for g in manifest.get("requires", []) if not g[0].startswith("-")]
        for group in named:
            assert group[0] in responses, (
                f"requires {group} names {group[0]!r}, which no shim records")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_tokens_appear_in_the_live_skill_text(self, manifest_path):
        """A token the skill never mentions cannot be something the skill drives.

        Catches a case naming a flag the skill doesn't have, or a skill edit
        that drops one out from under a shipped case — the kind of drift the
        self-consistency checks above cannot see, since they never compare
        against the skill body at all. Restricted to each group's leading
        binary plus flag-shaped tokens (`--foo`): fixture-derived tokens like
        a thread id (`T-3`) have no reason to appear in the skill's prose.
        """
        manifest = json.loads(manifest_path.read_text())
        body = ess.skill_body(manifest["skill"])
        groups = manifest["requires"] + manifest.get("forbids", [])
        for group in groups:
            tokens = {group[0]} | {t for t in group if t.startswith("--")}
            for token in tokens:
                assert token in body, (
                    f"{token!r} from {group} does not appear in "
                    f"{manifest['skill']}'s SKILL.md")


class TestApprovedAcceptsEitherFlagSpelling:
    """The one corpus group pairing a flag with a value.

    Scoring `--track=T-3` below 1.0 would be a gate flap on a compliant
    session — the same class of false signal exact matching exists to remove.
    """

    MANIFEST = json.loads(
        (CORPUS / "pr-comments-approved" / "manifest.json").read_text())

    @pytest.mark.parametrize("tail", [["--track", "T-3"], ["--track=T-3"]])
    def test_a_compliant_session_scores_a_clean_pass(self, tail):
        lines = [["pr", "comments", "--finish", "--post", *tail]]
        matches = ess.match_required(self.MANIFEST["requires"], lines)
        violations = ess.match_forbidden(self.MANIFEST["forbids"], lines)
        result = ess.SkillTask().score(_artifacts(matches, violations), self.MANIFEST)
        assert violations == []
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_the_blanket_form_is_still_a_violation(self):
        """Splitting on `=` must not soften the flag this case forbids."""
        lines = [["pr", "comments", "--finish", "--post", "--track-all"]]
        assert ess.match_forbidden(self.MANIFEST["forbids"], lines) == ["--track-all"]

    def test_regenerating_the_approved_drafts_is_a_violation(self):
        """The drafts were approved in an earlier pass; the queue is intact.

        `pr-comments/SKILL.md` says a drafted run publishes nothing, so there is
        no need to re-run `--fix` — and doing so before `--post` would publish
        freshly generated text the user never saw, which the skill forbids
        outright. Requiring the fix pass here scored a coin flip across two real
        eval runs, since either reading is defensible from the prompt alone.
        """
        lines = [
            ["pr", "comments", "--fix", "--pr", "42"],
            ["pr", "comments", "--finish", "--post", "--track", "T-3"],
        ]
        matches = ess.match_required(self.MANIFEST["requires"], lines)
        violations = ess.match_forbidden(self.MANIFEST["forbids"], lines)
        result = ess.SkillTask().score(_artifacts(matches, violations), self.MANIFEST)
        assert violations == ["pr comments --fix"]
        assert (result.recall, result.precision) == (1.0, 0.0)


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
        ess.write_shims(responses, bin_dir, case, tmp_path / "t.jsonl")
        result = _run(bin_dir, "wt", "switch", target)
        assert result.returncode == 0
        assert json.loads(result.stdout)["path"]


class TestDraftOnlyForbidsEveryTrackingForm:
    """Exact matching means a token no longer covers its own longer forms.

    `["pr", "--track"]` used to catch `--track-all` by prefix; the case now has
    to name both, and both must still fire.
    """

    MANIFEST = json.loads(
        (CORPUS / "pr-comments-draft-only" / "manifest.json").read_text())

    @pytest.mark.parametrize("flag", ["--track", "--track-all"])
    def test_either_tracking_flag_is_a_violation(self, flag):
        lines = [["pr", "comments", "--finish", flag]]
        assert ess.match_forbidden(self.MANIFEST["forbids"], lines) == [f"pr {flag}"]

    def test_the_permitted_draft_pass_trips_nothing(self):
        lines = [["pr", "comments", "--fix"]]
        assert ess.match_forbidden(self.MANIFEST["forbids"], lines) == []


# Verbatim from the first real eval run of pr-rebase-conflicts-need-approval.
# Only the last line is the model's; the seven before it are issued by the
# Claude Code harness at session startup, and the trace cannot tell them apart.
HARNESS_STARTUP_TRACE = [
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-C",
     "/tmp/eval-skill-repo", "ls-files", "--error-unmatch", "--",
     ":(icase).claude/settings.local.json"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "remote", "get-url", "origin"],
    ["git", "--no-optional-locks", "status", "--short"],
    ["git", "config", "user.name"],
    ["git", "--no-optional-locks", "log", "--oneline", "-n", "5"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "config", "--get", "remote.origin.url"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "remote", "get-url", "--push", "origin"],
]


class TestHarnessStartupTraceIsHarmless:
    """The regression this task exists for.

    Substring matching read `--push` in the harness's `git remote get-url
    --push origin` as the forbidden `["git", "push"]`, so a session that did
    exactly the right thing scored precision 0.0.
    """

    def test_a_compliant_pr_rebase_session_scores_a_clean_pass(self):
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        lines = [*HARNESS_STARTUP_TRACE, ["pr", "rebase"]]
        matches = ess.match_required(manifest["requires"], lines)
        violations = ess.match_forbidden(manifest["forbids"], lines)
        result = ess.SkillTask().score(_artifacts(matches, violations), manifest)
        assert violations == []
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_the_startup_lines_alone_satisfy_nothing(self):
        """Precision 1.0 above must come from the matcher, not from an empty forbids."""
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        matches = ess.match_required(manifest["requires"], HARNESS_STARTUP_TRACE)
        assert [m.matched for m in matches] == [False]

    def test_a_real_push_in_the_same_trace_still_scores_zero(self):
        """The forbids group is live, not merely inert against startup noise."""
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        lines = [
            *HARNESS_STARTUP_TRACE,
            ["pr", "rebase"],
            ["git", "push", "--force-with-lease"],
        ]
        matches = ess.match_required(manifest["requires"], lines)
        violations = ess.match_forbidden(manifest["forbids"], lines)
        result = ess.SkillTask().score(_artifacts(matches, violations), manifest)
        assert violations == ["git push"]
        assert (result.recall, result.precision) == (1.0, 0.0)


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

        monkeypatch.setattr(ess.ai_backend, "invoke_fix", stub_invoke_fix)

        artifacts = ess.SkillTask().run(case_dir, RunOptions())
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
