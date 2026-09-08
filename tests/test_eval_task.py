"""Tests for the eval task registry — the seam between runner and scorer."""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from eval import task as eval_task
from core import proc
from core import timeouts
from agent.usage import SessionUsage
from eval import scoring as eval_scoring
from eval.scoring import ScoringResult


class TestTaskRegistry:
    def test_get_task_dispatches(self):
        task = eval_task.get_task("review")
        assert task.name == "review"
        assert callable(task.run)
        assert callable(task.score)

    def test_unknown_task_names_the_known_ones(self):
        with pytest.raises(KeyError) as exc:
            eval_task.get_task("nope")
        assert "review" in str(exc.value)

    def test_manifest_defaults_to_review(self):
        """The field is additive — manifests written before it still run."""
        assert eval_task.task_name({}) == "review"

    def test_manifest_task_is_honoured(self):
        assert eval_task.task_name({"task": "review"}) == "review"

    def test_corpus_manifests_declare_a_registered_task(self):
        manifests = sorted((REPO_ROOT / "eval" / "corpus").glob("*/manifest.json"))
        assert manifests, "corpus is empty"
        for path in manifests:
            manifest = json.loads(path.read_text())
            assert eval_task.get_task(eval_task.task_name(manifest)) is not None


class TestRunArtifacts:
    def test_defaults_are_empty_not_absent(self):
        artifacts = eval_task.RunArtifacts()
        assert artifacts.exit_code == 0
        assert artifacts.temp_dirs == []
        assert artifacts.data == {}

    def test_usage_defaults_to_unmeasured_zero(self):
        assert eval_task.RunArtifacts().usage.cost == 0.0


class TestCreateTempRepo:
    """The fixture builder runs git through `proc.run`, so the bound is named."""

    @staticmethod
    def _case(tmp_path: Path) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        (src / "bug.py").write_text("def f():\n    pass\n")
        return src

    @classmethod
    @contextlib.contextmanager
    def _repo(cls, tmp_path: Path):
        """Builds the fixture and guarantees cleanup, so each test only names its assertion."""
        repo = Path(eval_task.create_temp_repo(str(cls._case(tmp_path)), prefix="eval-test-"))
        try:
            yield repo
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_builds_an_eval_branch_carrying_the_sources(self, tmp_path):
        with self._repo(tmp_path) as repo:
            assert (repo / "bug.py").read_text() == "def f():\n    pass\n"
            branch = proc.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                              timeout=timeouts.LOCAL)
            assert branch.stdout.strip() == "eval"

    def test_the_inherited_git_env_does_not_reach_the_fixture(self, tmp_path, monkeypatch):
        """`clean_env` drops GIT_DIR; merging back over os.environ would restore it."""
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere.git"))
        with self._repo(tmp_path) as repo:
            assert (repo / ".git").is_dir()

    def test_a_failing_step_raises_with_gits_own_words(self, tmp_path):
        """Calls the private `_git_step` directly, not `create_temp_repo`, to isolate
        the error-message contract from the rest of the fixture build."""
        with pytest.raises(RuntimeError) as exc:
            eval_task._git_step(["git", "-C", str(tmp_path)], ["log"], eval_task.clean_env())
        assert "git log failed" in str(exc.value)
        assert "not a git repository" in str(exc.value)

    def test_a_killed_step_names_the_signal_rather_than_blaming_git(self, tmp_path):
        """#970's symptom: a loaded machine kills the step and git says nothing.

        The message used to be `git commit --allow-empty -m initial failed` and
        nothing more, which reads as git having objected to the arguments.

        A real signal rather than a patched result: the negative return code has
        to survive the trip back through `subprocess` for this to prove anything.
        """
        killed = ["sh", "-c", "kill -PIPE $$", "git"]
        with pytest.raises(RuntimeError) as exc:
            eval_task._git_step(killed, ["commit", "--allow-empty", "-m", "initial"],
                                eval_task.clean_env())
        message = str(exc.value)
        assert "git commit --allow-empty -m initial failed" in message
        assert "SIGPIPE (signal 13)" in message
        assert "re-run rather than bisect" in message


class _StubTask:
    """Stands in for a real task so the runner can be exercised without an LLM."""

    name = "stub"

    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.opts = None

    def run(self, case_dir, opts):
        self.opts = opts
        return eval_task.RunArtifacts(
            usage=SessionUsage(cost=0.25),
            temp_dirs=[self.temp_dir],
            data={"summary": "stub ran"},
        )

    def score(self, artifacts, manifest):
        return ScoringResult("", "", 0, recall=0.5, cost_usd=artifacts.usage.cost)


@pytest.fixture
def stub_run(monkeypatch, tmp_path):
    """Register a stub task and return (task, entry, args) for the runner."""
    temp_dir = tmp_path / "scratch"
    temp_dir.mkdir()
    task = _StubTask(str(temp_dir))
    monkeypatch.setitem(eval_task._TASK_FACTORIES, "stub", lambda: task)
    entry = {
        "name": "case-a",
        "case_dir": str(tmp_path),
        "src_dir": str(tmp_path),
        "manifest": {"task": "stub"},
    }
    args = argparse.Namespace(
        runs=1, effort="medium", timeout=42, verbose=False, keep_temp=False,
    )
    return task, entry, args


class TestRunnerDispatch:
    def test_dispatches_on_the_manifest_task(self, em, stub_run):
        task, entry, args = stub_run
        em._run_single(entry, "claude-opus-5", "opus", 0, args)
        assert task.opts == eval_task.RunOptions(
            model="claude-opus-5", effort="medium", timeout=42, verbose=False,
        )

    def test_runner_fills_identity_the_scorer_leaves_blank(self, em, stub_run):
        _, entry, args = stub_run
        result = em._run_single(entry, "claude-opus-5", "opus", 2, args)
        assert (result.entry_name, result.model, result.run_index) == ("case-a", "opus", 2)
        assert result.recall == 0.5

    def test_temp_dirs_are_removed(self, em, stub_run):
        task, entry, args = stub_run
        em._run_single(entry, "", "(default)", 0, args)
        assert not Path(task.temp_dir).exists()

    def test_keep_temp_leaves_them(self, em, stub_run):
        task, entry, args = stub_run
        args.keep_temp = True
        em._run_single(entry, "", "(default)", 0, args)
        assert Path(task.temp_dir).exists()


class TestReportRun:
    """false_positives_max is only a budget if exceeding it is visible."""

    def _report(self, em, capsys, *, fp_count, fp_ok):
        result = ScoringResult(
            "", "", 0, recall=1.0,
            false_positive_count=fp_count, false_positive_ok=fp_ok,
        )
        em._report_run(eval_task.RunArtifacts(), result, 1)
        return capsys.readouterr().err

    def test_over_budget_is_called_out(self, em, capsys):
        assert "FP: 5 (over budget)" in self._report(
            em, capsys, fp_count=5, fp_ok=False)

    def test_within_budget_is_not_annotated(self, em, capsys):
        err = self._report(em, capsys, fp_count=2, fp_ok=True)
        assert "FP: 2" in err
        assert "over budget" not in err


class TestOutcomeFor:
    """Classifying an invocation that produced nothing (#1001).

    The poisoned baseline was found by three cases reading `$0.00 / 4s` at
    once. Both halves of that signature are needed: an exit code alone cannot
    tell a dead invocation from an agent that worked and gave up.
    """

    def test_a_clean_exit_is_measured(self):
        assert eval_task.outcome_for(0, SessionUsage()) is eval_scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_that_spent_money_is_measured(self):
        """An agent that ran, worked, and failed produced a real result."""
        assert eval_task.outcome_for(1, SessionUsage(cost=0.31)) is eval_scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_that_burned_tokens_is_measured(self):
        """A run can do real work and still report no cost — a stubbed or free model."""
        assert eval_task.outcome_for(
            1, SessionUsage(input_tokens=900)) is eval_scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_with_nothing_spent_never_ran(self):
        assert eval_task.outcome_for(1, SessionUsage()) is eval_scoring.RunOutcome.NOT_RUN

    def test_cache_reads_alone_count_as_work(self):
        """total_tokens covers the cache fields; billed_input alone would miss them."""
        assert eval_task.outcome_for(
            1, SessionUsage(cache_read_tokens=4000)) is eval_scoring.RunOutcome.MEASURED

    def test_artifacts_default_to_measured(self):
        """A task that never classifies keeps today's behaviour."""
        assert eval_task.RunArtifacts().measured


class TestReportUnmeasuredRun:
    def test_a_dead_run_reports_no_score(self, em, capsys):
        """recall 0% for a run that never happened is the confusion, not the report."""
        result = ScoringResult(
            "", "", 0, recall=0.0, outcome=eval_scoring.RunOutcome.NOT_RUN)
        em._report_run(eval_task.RunArtifacts(), result, 3)
        err = capsys.readouterr().err
        assert "not scored" in err
        assert "recall" not in err


class TestSaveBaselineRefusal:
    """--save-baselines writes a model's file wholesale from one pass (#1001).

    One window of backend failures would therefore replace a good baseline with
    zeros, and nothing downstream catches it: validate-eval-baselines checks
    corpus coverage, not plausibility.
    """

    @staticmethod
    def _args(tmp_path):
        return argparse.Namespace(
            save_baselines=True, compare=False, results_dir=str(tmp_path / "results"),
        )

    @staticmethod
    def _output(measured: int, attempted: int) -> dict:
        return {
            "effort": "low", "runs_per_entry": attempted,
            "entries": {"case-a": {"sonnet": {
                "recall_mean": 1.0, "precision_mean": 1.0,
                "runs_measured": measured, "runs_attempted": attempted,
            }}},
        }

    def test_a_complete_pass_is_saved(self, em, tmp_path, capsys):
        code = em._run_post_eval(self._args(tmp_path), self._output(3, 3), tmp_path)
        assert code == 0
        assert (tmp_path / "results" / "sonnet.json").is_file()

    def test_a_pass_with_dead_runs_is_refused(self, em, tmp_path, capsys):
        code = em._run_post_eval(self._args(tmp_path), self._output(1, 3), tmp_path)
        assert code == 3
        assert not (tmp_path / "results").exists()

    def test_the_refusal_names_the_entry_and_its_counts(self, em, tmp_path, capsys):
        em._run_post_eval(self._args(tmp_path), self._output(1, 3), tmp_path)
        err = capsys.readouterr().err
        assert "case-a / sonnet: 1/3 runs measured" in err
        assert "--entry" in err

    def test_an_existing_baseline_survives_the_refusal(self, em, tmp_path):
        """The overwritten file is the thing that cannot be re-attempted."""
        results = tmp_path / "results"
        results.mkdir()
        good = results / "sonnet.json"
        good.write_text('{"keep": true}\n')
        em._run_post_eval(self._args(tmp_path), self._output(0, 3), tmp_path)
        assert good.read_text() == '{"keep": true}\n'
