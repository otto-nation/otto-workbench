"""Tests for eval.scoring_cifix: the verify oracle, agent gating, and scoring.

The corpus tests are the important ones. They prove, without spending a token,
that every ci-fix case fails before the fix and passes after it — an oracle that
never fails is worth nothing, and one that never passes is unwinnable.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent.usage import SessionUsage
from core.phases import Phase
from eval.scoring_cifix import CiFixTask, run_verify, verify_command
from eval.scoring import RunOutcome, ScoringResult, aggregate_runs
from eval.task import RunArtifacts, RunOptions, get_task, outcome_for
import agent.backend
import agent.phases

CORPUS = REPO_ROOT / "eval" / "corpus"

VERIFY_TIMEOUT = 60


def _cifix_cases() -> list[Path]:
    cases = []
    for manifest in sorted(CORPUS.glob("*/manifest.json")):
        if json.loads(manifest.read_text()).get("task") == "ci-fix":
            cases.append(manifest.parent)
    return cases


CIFIX_CASES = _cifix_cases()
CASE_IDS = [c.name for c in CIFIX_CASES]


# ── TestVerifyCommand ───────────────────────────────────────────────────────


class TestVerifyCommand:
    def test_reads_the_manifest_field(self):
        assert verify_command({"verify": ["make", "test"]}) == ["make", "test"]

    def test_defaults_to_the_case_verify_script(self):
        assert verify_command({}) == ["bash", "verify.sh"]

    def test_does_not_hand_out_the_shared_default(self):
        returned = verify_command({})
        returned.append("mutated")
        assert verify_command({}) == ["bash", "verify.sh"]


# ── TestRunVerify ───────────────────────────────────────────────────────────


class TestRunVerify:
    def test_captures_exit_code_and_output(self, tmp_path):
        (tmp_path / "verify.sh").write_text("echo nope >&2\nexit 3\n")
        code, output = run_verify(str(tmp_path), {}, VERIFY_TIMEOUT)
        assert code == 3
        assert output == "nope"

    def test_a_timeout_is_a_failure_with_a_reason(self, tmp_path):
        (tmp_path / "verify.sh").write_text("sleep 5\n")
        code, output = run_verify(str(tmp_path), {}, 1)
        assert code == 1
        assert "timed out" in output

    def test_an_unrunnable_command_is_a_failure_not_a_crash(self, tmp_path):
        manifest = {"verify": ["definitely-not-a-real-binary"]}
        code, output = run_verify(str(tmp_path), manifest, VERIFY_TIMEOUT)
        assert code == 1
        assert "could not run" in output


# ── TestCorpusOracle ────────────────────────────────────────────────────────


@pytest.mark.skipif(not CIFIX_CASES, reason="no ci-fix cases in the corpus")
@pytest.mark.parametrize("case_dir", CIFIX_CASES, ids=CASE_IDS)
class TestCorpusOracle:
    """Every ci-fix case must be both genuinely broken and genuinely fixable."""

    def test_fails_before_the_fix(self, case_dir, tmp_path):
        manifest = json.loads((case_dir / "manifest.json").read_text())
        shutil.copytree(case_dir / "src", tmp_path / "repo")
        code, _ = run_verify(str(tmp_path / "repo"), manifest, VERIFY_TIMEOUT)
        assert code != 0, f"{case_dir.name}: verify passes on the unfixed source"

    def test_passes_after_the_reference_fix(self, case_dir, tmp_path):
        manifest = json.loads((case_dir / "manifest.json").read_text())
        repo = tmp_path / "repo"
        shutil.copytree(case_dir / "src", repo)
        reference = case_dir / "reference-fix"
        assert reference.is_dir(), f"{case_dir.name}: no reference-fix/ to prove it"
        for item in reference.rglob("*"):
            if item.is_file():
                shutil.copy2(item, repo / item.relative_to(reference))
        code, output = run_verify(str(repo), manifest, VERIFY_TIMEOUT)
        assert code == 0, f"{case_dir.name}: reference fix does not pass — {output}"

    def test_declares_a_failure_summary(self, case_dir):
        manifest = json.loads((case_dir / "manifest.json").read_text())
        assert manifest.get("failure_summary")


# ── TestCiFixTaskRun ────────────────────────────────────────────────────────


def _case(tmp_path: Path, verify_body: str, task: str = "ci-fix") -> Path:
    case_dir = tmp_path / "case"
    (case_dir / "src").mkdir(parents=True)
    (case_dir / "manifest.json").write_text(
        json.dumps({"name": "stub-case", "task": task}))
    (case_dir / "src" / "verify.sh").write_text(verify_body)
    return case_dir


def _run_cifix_case(opts: RunOptions) -> RunArtifacts:
    """Drive a ci-fix run far enough that invoke_fix is called.

    There is no shared harness helper for this; existing tests inline
    `CiFixTask().run(case_dir, RunOptions(...))`. The fixture fails
    pre-verify so the agent is reached. Temp dirs are cleaned before return.
    """
    case_root = Path(tempfile.mkdtemp(prefix="eval-cifix-case-"))
    try:
        artifacts = CiFixTask().run(_case(case_root, "exit 1\n"), opts)
        _rm(artifacts)
        return artifacts
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


class TestCiFixTaskRun:
    def test_skips_the_agent_when_the_fixture_already_passes(self, tmp_path, monkeypatch):
        """A fixture that does not fail proves nothing — it must not cost money."""
        calls = []
        monkeypatch.setattr(agent.backend, "invoke_fix",
                            lambda *a, **kw: calls.append(kw) or 0)
        case_dir = _case(tmp_path, "exit 0\n")

        artifacts = CiFixTask().run(case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert calls == []
        assert artifacts.data["fixture_ok"] is False
        assert artifacts.data["fixed"] is False
        assert artifacts.exit_code != 0
        # A broken fixture measures the fixture, not the model — scoring it as
        # zero would put a fixture bug into the baseline as a quality drop.
        assert artifacts.outcome is RunOutcome.NOT_RUN
        _rm(artifacts)

    def test_reports_fixed_when_the_agent_makes_verify_pass(self, tmp_path, monkeypatch):
        def fake_fix(inv):
            Path(inv.add_dirs[0], "fixed").write_text("yes")
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake_fix)
        case_dir = _case(tmp_path, "test -f fixed\n")

        artifacts = CiFixTask().run(case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert artifacts.data["fixed"] is True
        assert artifacts.data["summary"] == "fixed"
        _rm(artifacts)

    def test_reports_still_failing_when_the_agent_does_nothing(self, tmp_path, monkeypatch):
        def fake_fix(inv):
            Path(inv.session_log).write_text(json.dumps({
                "type": "result",
                "total_cost_usd": 0.05,
                "usage": {"input_tokens": 40, "output_tokens": 8},
            }) + "\n")
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake_fix)
        case_dir = _case(tmp_path, "test -f fixed\n")

        artifacts = CiFixTask().run(case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert artifacts.data["fixed"] is False
        assert artifacts.data["summary"] == "still failing"
        # The agent ran and did not fix it. That is a score of zero, not a
        # dead invocation — exit 0 says the invocation itself completed.
        assert artifacts.outcome is RunOutcome.MEASURED
        _rm(artifacts)

    def test_hands_the_agent_the_repo_and_the_failure(self, tmp_path, monkeypatch):
        seen = {}

        def fake_fix(inv):
            seen.update(vars(inv))
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake_fix)
        case_dir = _case(tmp_path, "echo 'boom happened' >&2\nexit 2\n")

        artifacts = CiFixTask().run(
            case_dir, RunOptions(model="sonnet", timeout=VERIFY_TIMEOUT))

        repo_dir = seen["add_dirs"][0]
        assert repo_dir in seen["prompt"]
        assert "boom happened" in seen["prompt"]
        assert "bash verify.sh" in seen["prompt"]
        assert seen["model"] == "sonnet"
        assert seen["task"] == "eval-ci-fix"
        _rm(artifacts)

    def test_empty_model_resolves_through_phase_model_before_invoke_fix(
            self, tmp_path, monkeypatch):
        seen = {}

        def fake_fix(inv):
            seen["model"] = inv.model
            seen["thinking"] = inv.thinking
            seen["provider"] = inv.provider
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake_fix)
        monkeypatch.setattr(
            agent.phases, "phase_model",
            lambda phase, explicit, cfg=None: "resolved-ci-fix",
        )
        monkeypatch.setattr(
            agent.phases, "phase_thinking",
            lambda phase, effort=None, cfg=None: "low",
        )
        monkeypatch.setattr(
            agent.phases, "phase_provider", lambda cfg=None: "anthropic",
        )
        case_dir = _case(tmp_path, "exit 1\n")

        artifacts = CiFixTask().run(
            case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert seen["model"] == "resolved-ci-fix"
        assert seen["thinking"] == "low"
        assert seen["provider"] == "anthropic"
        assert CiFixTask.phase is Phase.CI_FIX
        _rm(artifacts)

    def test_ci_fix_env_override_reaches_invoke_fix(self, tmp_path, monkeypatch):
        seen = {}

        def fake_fix(inv):
            seen["model"] = inv.model
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake_fix)
        monkeypatch.setenv("WORKBENCH_AI_CI_FIX_MODEL", "env-ci-fix-model")
        case_dir = _case(tmp_path, "exit 1\n")

        artifacts = CiFixTask().run(
            case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert seen["model"] == "env-ci-fix-model"
        assert seen["model"]
        _rm(artifacts)

    def test_cleans_up_nothing_itself_but_reports_its_temp_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend, "invoke_fix",
                            lambda *a, **kw: 0)
        case_dir = _case(tmp_path, "exit 1\n")

        artifacts = CiFixTask().run(case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert len(artifacts.temp_dirs) == 2
        assert all(Path(d).is_dir() for d in artifacts.temp_dirs)
        _rm(artifacts)


def _rm(artifacts: RunArtifacts) -> None:
    for path in artifacts.temp_dirs:
        shutil.rmtree(path, ignore_errors=True)


# ── TestCiFixTaskScore ──────────────────────────────────────────────────────


class TestCiFixTaskScore:
    def test_a_fixed_case_scores_one(self):
        artifacts = RunArtifacts(data={"fixed": True})
        result = CiFixTask().score(artifacts, {})
        assert result.recall == 1.0
        assert result.precision == 1.0

    def test_an_unfixed_case_scores_zero(self):
        result = CiFixTask().score(RunArtifacts(data={"fixed": False}), {})
        assert result.recall == 0.0
        assert result.precision == 0.0

    def test_carries_the_token_metrics_the_ratchet_gates_on(self):
        artifacts = RunArtifacts(
            data={"fixed": True},
            usage=SessionUsage(cost=0.4, input_tokens=100, output_tokens=20,
                               cache_read_tokens=900),
        )
        result = CiFixTask().score(artifacts, {})
        assert result.billed_input == 1000
        assert result.cache_read_ratio == 0.9
        assert result.output_tokens == 20
        assert result.cost_usd == 0.4


# ── TestTaskRegistration ────────────────────────────────────────────────────


class TestTaskRegistration:
    def test_the_registry_resolves_ci_fix(self):
        assert get_task("ci-fix").name == "ci-fix"


class TestCiFixOutcome:
    """A backend failure must not reach the baseline as a fix the model missed."""

    def test_a_dead_invocation_is_not_a_failed_fix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend, "invoke_fix",
                            lambda *a, **kw: 1)
        case_dir = _case(tmp_path, "test -f fixed\n")

        artifacts = CiFixTask().run(case_dir, RunOptions(timeout=VERIFY_TIMEOUT))

        assert artifacts.outcome is RunOutcome.NOT_RUN
        assert not CiFixTask().score(artifacts, {}).measured
        _rm(artifacts)

    def test_the_outcome_reaches_the_score(self):
        artifacts = RunArtifacts(data={"fixed": False}, outcome=RunOutcome.NOT_RUN)
        assert CiFixTask().score(artifacts, {}).outcome is RunOutcome.NOT_RUN


class TestConditionReachesAgent:
    """A ci-fix run serves the agent a backend-neutral rules_home."""

    def test_the_condition_reaches_the_agent_as_an_absolute_rules_home(
            self, tmp_path, monkeypatch):
        seen = {}

        def fake_invoke_fix(inv):
            seen["rules_home"] = inv.rules_home
            seen["env"] = inv.env
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix",
                            fake_invoke_fix)
        opts = RunOptions(condition="trimmed",
                          rules_home=str(tmp_path / "cc-trimmed"))
        _run_cifix_case(opts)

        assert seen["rules_home"] == str(tmp_path / "cc-trimmed")
        assert Path(seen["rules_home"]).is_absolute()
        assert seen["env"] is None

    def test_the_agent_env_is_complete_because_a_partial_one_strips_path_and_home(
            self, tmp_path, monkeypatch):
        """Eval no longer builds env: a partial mapping was the layering defect.

        Completeness (PATH, HOME unchanged) is asserted at the Claude backend,
        where the mapping now lives.
        """
        seen = {}

        def fake_invoke_fix(inv):
            seen["env"] = inv.env
            seen["rules_home"] = inv.rules_home
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix",
                            fake_invoke_fix)
        opts = RunOptions(condition="trimmed", rules_home=str(tmp_path / "cc"))
        _run_cifix_case(opts)

        assert seen["env"] is None
        assert seen["rules_home"] == str(tmp_path / "cc")

    def test_no_config_dir_leaves_the_env_inherited_as_it_is_today(self, monkeypatch):
        seen = {}

        def fake_invoke_fix(inv):
            seen["env"] = inv.env
            seen["rules_home"] = inv.rules_home
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix",
                            fake_invoke_fix)
        _run_cifix_case(RunOptions())
        assert seen["env"] is None
        assert seen["rules_home"] == ""


class TestZeroTokenGuard:
    """A CLI that declines to start must not average in as a 0% score."""

    def test_a_run_that_billed_nothing_and_exited_zero_is_not_a_measurement(self):
        """The CLAUDE_CONFIG_DIR arm's worst failure: the CLI declines to start,
        prints a notice, bills nothing and exits 0. Averaged in as a zero it would
        read as a real arm scoring 0% rather than as an arm that never ran."""
        usage = SessionUsage(cost=0.0, duration_ms=1200)
        assert outcome_for(usage) is RunOutcome.NOT_RUN

    def test_an_unmeasured_run_is_excluded_from_the_arm_mean(self):
        good = ScoringResult(entry_name="e", model="m", run_index=0, recall=1.0,
                             billed_input=40000, outcome=RunOutcome.MEASURED)
        dead = ScoringResult(entry_name="e", model="m", run_index=1, recall=0.0,
                             billed_input=0, outcome=RunOutcome.NOT_RUN)
        agg = aggregate_runs([good, dead])
        assert agg["recall_mean"] == 1.0, (
            "a run that never happened is not a 0% score")
        assert agg["billed_input_mean"] == 40000
        assert agg["runs_measured"] == 1
        assert agg["runs_attempted"] == 2
