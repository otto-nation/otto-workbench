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
    def test_a_satisfying_trace_scores_one(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        lines = [" ".join(group) for group in manifest["requires"]]
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
            assert inv.env["PATH"].split(os.pathsep)[0] == str(bin_dir)
            assert "# PR Rebase" in inv.prompt
            assert "rebase the branch" in inv.prompt
            assert artifacts.temp_dirs == [inv.cwd, str(bin_dir.parent)]
            assert [m.matched for m in artifacts.data["matches"]] == [True]
            assert artifacts.data["violations"] == []
        finally:
            for path in artifacts.temp_dirs:
                shutil.rmtree(path, ignore_errors=True)
