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

import eval.task
import core.proc
import core.timeouts
from agent.usage import SessionUsage
import eval.scoring
from eval.scoring import ScoringResult
import agent.backend
import eval.conditions


def _make_case(root: Path, name: str, task: str = "review") -> Path:
    """A corpus case is a directory with manifest.json and src/.

    The briefs call this helper; it does not exist elsewhere in tests/, so it
    is part of this deliverable. Shape matches eval/corpus/<name>/.
    """
    case = root / name
    src = case / "src"
    src.mkdir(parents=True)
    (src / "file.py").write_text("x = 1\n")
    (case / "manifest.json").write_text(
        json.dumps({"name": name, "task": task}) + "\n",
    )
    return case


class TestTaskRegistry:
    def test_get_task_dispatches(self):
        task = eval.task.get_task("review")
        assert task.name == "review"
        assert callable(task.run)
        assert callable(task.score)

    def test_unknown_task_names_the_known_ones(self):
        with pytest.raises(KeyError) as exc:
            eval.task.get_task("nope")
        assert "review" in str(exc.value)

    def test_manifest_defaults_to_review(self):
        """The field is additive — manifests written before it still run."""
        assert eval.task.task_name({}) == "review"

    def test_manifest_task_is_honoured(self):
        assert eval.task.task_name({"task": "review"}) == "review"

    def test_corpus_manifests_declare_a_registered_task(self):
        manifests = sorted((REPO_ROOT / "eval" / "corpus").glob("*/manifest.json"))
        assert manifests, "corpus is empty"
        for path in manifests:
            manifest = json.loads(path.read_text())
            assert eval.task.get_task(eval.task.task_name(manifest)) is not None


class TestRunArtifacts:
    def test_defaults_are_empty_not_absent(self):
        artifacts = eval.task.RunArtifacts()
        assert artifacts.exit_code == 0
        assert artifacts.temp_dirs == []
        assert artifacts.data == {}

    def test_usage_defaults_to_unmeasured_zero(self):
        assert eval.task.RunArtifacts().usage.cost == 0.0


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
        repo = Path(eval.task.create_temp_repo(str(cls._case(tmp_path)), prefix="eval-test-"))
        try:
            yield repo
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_builds_an_eval_branch_carrying_the_sources(self, tmp_path):
        with self._repo(tmp_path) as repo:
            assert (repo / "bug.py").read_text() == "def f():\n    pass\n"
            branch = core.proc.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                              timeout=core.timeouts.LOCAL)
            assert branch.stdout.strip() == "eval"

    def test_origin_head_names_the_branch_the_case_forked_from(self, tmp_path):
        """The fetch that wires up `origin` runs on `eval`, so git aimed the
        symref at `origin/eval`. Trunk resolution reads it first, so the review
        pipeline diffed the branch against itself and every review case scored
        recall 0 on a clean, fully-billed run."""
        with self._repo(tmp_path) as repo:
            head = core.proc.run(
                ["git", "-C", str(repo), "symbolic-ref", "refs/remotes/origin/HEAD"],
                timeout=core.timeouts.LOCAL)
            assert head.stdout.strip() == "refs/remotes/origin/main"

    def test_the_resolved_trunk_yields_a_range_holding_the_sources(self, tmp_path):
        """Asserting the symref alone would not catch this: the pipeline reaches
        trunk through `resolve_default_branch`, and the empty diff it produced
        is the symptom that cost the run. Resolve the base the way the pipeline
        does, then diff against it."""
        import git.topology
        with self._repo(tmp_path) as repo:
            base = git.topology.default_branch(str(repo))
            assert base == "main"
            diff = core.proc.run(
                ["git", "-C", str(repo), "diff", "--name-only", f"origin/{base}...HEAD"],
                timeout=core.timeouts.LOCAL)
            assert diff.stdout.split() == ["bug.py"]

    def test_the_inherited_git_env_does_not_reach_the_fixture(self, tmp_path, monkeypatch):
        """`clean_env` drops GIT_DIR; merging back over os.environ would restore it."""
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere.git"))
        with self._repo(tmp_path) as repo:
            assert (repo / ".git").is_dir()

    def test_the_fixture_head_sha_names_a_commit_the_repo_has(self, tmp_path):
        """The whole point: a case citing this sha cites something that resolves."""
        with self._repo(tmp_path) as repo:
            sha = eval.task.fixture_head_sha(str(repo))
            kind = core.proc.run(["git", "-C", str(repo), "cat-file", "-t", sha],
                            timeout=core.timeouts.LOCAL)
            assert kind.stdout.strip() == "commit"

    def test_an_inherited_git_dir_does_not_redirect_the_head_read(
        self, tmp_path, monkeypatch,
    ):
        """`-C` loses to `GIT_DIR`, so an unsanitised read answers with the
        calling checkout's HEAD — a sha that resolves everywhere except the
        fixture, which is quieter than the placeholder it replaces."""
        with self._repo(tmp_path) as repo:
            expected = eval.task.fixture_head_sha(str(repo))
            monkeypatch.setenv("GIT_DIR", str(REPO_ROOT / ".git"))
            assert eval.task.fixture_head_sha(str(repo)) == expected

    def test_an_unbuildable_repo_raises_rather_than_returning_an_empty_sha(
        self, tmp_path,
    ):
        """An empty sha substituted into a rule's `match` produces a rule that
        can never fire — the failure `check_group` exists to prevent."""
        with pytest.raises(RuntimeError) as exc:
            eval.task.fixture_head_sha(str(tmp_path))
        assert "git rev-parse HEAD failed" in str(exc.value)

    def test_a_failing_step_raises_with_gits_own_words(self, tmp_path):
        """Calls the private `_git_step` directly, not `create_temp_repo`, to isolate
        the error-message contract from the rest of the fixture build."""
        with pytest.raises(RuntimeError) as exc:
            eval.task._git_step(["git", "-C", str(tmp_path)], ["log"], eval.task.clean_env())
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
            eval.task._git_step(killed, ["commit", "--allow-empty", "-m", "initial"],
                                eval.task.clean_env())
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
        return eval.task.RunArtifacts(
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
    monkeypatch.setitem(eval.task._TASK_FACTORIES, "stub", lambda: task)
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
        assert task.opts == eval.task.RunOptions(
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
        em._report_run(eval.task.RunArtifacts(), result, 1)
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
    once. Usage is what classifies: an exit code is not consulted, so a
    zero-token exit-0 run is NOT_RUN rather than a 0% score.
    """

    def test_a_clean_exit_is_measured(self):
        assert eval.task.outcome_for(
            SessionUsage(input_tokens=80)) is eval.scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_that_spent_money_is_measured(self):
        """An agent that ran, worked, and failed produced a real result."""
        assert eval.task.outcome_for(
            SessionUsage(cost=0.31)) is eval.scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_that_burned_tokens_is_measured(self):
        """A run can do real work and still report no cost — a stubbed or free model."""
        assert eval.task.outcome_for(
            SessionUsage(input_tokens=900)) is eval.scoring.RunOutcome.MEASURED

    def test_a_non_zero_exit_with_nothing_spent_never_ran(self):
        assert eval.task.outcome_for(SessionUsage()) is eval.scoring.RunOutcome.NOT_RUN

    def test_cache_reads_alone_count_as_work(self):
        """total_tokens covers the cache fields; billed_input alone would miss them."""
        assert eval.task.outcome_for(
            SessionUsage(cache_read_tokens=4000)) is eval.scoring.RunOutcome.MEASURED

    def test_artifacts_default_to_measured(self):
        """A task that never classifies keeps today's behaviour."""
        assert eval.task.RunArtifacts().measured


class TestReportUnmeasuredRun:
    def test_a_dead_run_reports_no_score(self, em, capsys):
        """recall 0% for a run that never happened is the confusion, not the report."""
        result = ScoringResult(
            "", "", 0, recall=0.0, outcome=eval.scoring.RunOutcome.NOT_RUN)
        em._report_run(eval.task.RunArtifacts(), result, 3)
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
    def _output(measured: int, attempted: int, model: str = "sonnet") -> dict:
        return {
            "backend": "claude", "effort": "low", "runs_per_entry": attempted,
            "entries": {"case-a": {model: {
                "recall_mean": 1.0, "precision_mean": 1.0,
                "runs_measured": measured, "runs_attempted": attempted,
            }}},
        }

    def test_a_complete_pass_is_saved(self, em, tmp_path, capsys):
        code = em._run_post_eval(self._args(tmp_path), self._output(3, 3), tmp_path)
        assert code == 0
        path = tmp_path / "results" / "claude-sonnet.json"
        assert path.is_file()
        assert json.loads(path.read_text())["backend"] == "claude"

    def test_an_unresolved_model_is_not_saved(self, em, tmp_path, capsys):
        code = em._run_post_eval(
            self._args(tmp_path), self._output(3, 3, model="(default)"), tmp_path,
        )
        assert code == 3
        assert not (tmp_path / "results").exists()
        assert "model was not resolved" in capsys.readouterr().err

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

    def test_two_backends_write_distinct_files(self, em, tmp_path):
        metrics = {
            "recall_mean": 1.0, "precision_mean": 1.0,
            "runs_measured": 3, "runs_attempted": 3,
        }
        claude = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {"case-a": {"sonnet": metrics}},
        }
        pi = {
            "backend": "pi", "effort": "low", "runs_per_entry": 3,
            "entries": {"case-a": {"sonnet": metrics}},
        }
        results = str(tmp_path / "results")
        em._save_baselines(claude, results)
        em._save_baselines(pi, results)
        names = sorted(p.name for p in (tmp_path / "results").glob("*.json"))
        assert names == ["claude-sonnet.json", "pi-sonnet.json"]


class TestBaselineBackendCompare:
    """A Pi run must not be judged against a Claude-recorded file."""

    @staticmethod
    def _args(tmp_path):
        return argparse.Namespace(
            compare=True, save_baselines=False,
            results_dir=str(tmp_path / "results"),
        )

    @staticmethod
    def _metrics(recall: float) -> dict:
        return {
            "recall_mean": recall, "precision_mean": 1.0,
            "severity_accuracy_mean": 1.0, "false_positive_mean": 0.0,
        }

    def test_a_different_backend_baseline_is_not_a_regression(
        self, em, tmp_path, capsys,
    ):
        results = tmp_path / "results"
        results.mkdir()
        pi = em._baseline_document(
            "sonnet", "low", 1, {"case-a": self._metrics(1.0)}, "pi",
        )
        (results / "pi-sonnet.json").write_text(json.dumps(pi) + "\n")
        current = {
            "backend": "claude",
            "entries": {"case-a": {"sonnet": self._metrics(0.0)}},
        }
        code = em._run_comparison(self._args(tmp_path), current, tmp_path)
        assert code == 0
        assert "No baselines found for comparison" in capsys.readouterr().err

    def test_the_same_backend_still_compares(self, em, tmp_path, capsys):
        results = tmp_path / "results"
        results.mkdir()
        claude = em._baseline_document(
            "sonnet", "low", 1, {"case-a": self._metrics(1.0)}, "claude",
        )
        pi = em._baseline_document(
            "sonnet", "low", 1, {"case-a": self._metrics(1.0)}, "pi",
        )
        (results / "claude-sonnet.json").write_text(json.dumps(claude) + "\n")
        (results / "pi-sonnet.json").write_text(json.dumps(pi) + "\n")
        current = {
            "backend": "claude",
            "entries": {"case-a": {"sonnet": self._metrics(0.0)}},
        }
        code = em._run_comparison(self._args(tmp_path), current, tmp_path)
        assert code == 2
        err = capsys.readouterr().err
        assert "regression" in err


class TestServedModelLabel:
    def test_empty_model_records_the_served_model(self, em, stub_run):
        task, entry, args = stub_run

        def run(_case_dir, _opts):
            return eval.task.RunArtifacts(
                usage=SessionUsage(
                    cost=0.25, cost_by_model={"claude-opus-5": 0.25},
                ),
                temp_dirs=[task.temp_dir],
                data={"summary": "stub ran"},
            )

        task.run = run
        result = em._run_single(entry, "", "(default)", 0, args)
        assert result.model == "claude-opus-5"

    def test_an_ambiguous_session_log_keeps_the_placeholder(self, em, stub_run):
        task, entry, args = stub_run

        def run(_case_dir, _opts):
            return eval.task.RunArtifacts(
                usage=SessionUsage(cost=0.25, cost_by_model={
                    "claude-opus-5": 0.2, "claude-sonnet-5": 0.05,
                }),
                temp_dirs=[task.temp_dir],
                data={"summary": "stub ran"},
            )

        task.run = run
        result = em._run_single(entry, "", "(default)", 0, args)
        assert result.model == "(default)"


class TestTaskFilter:
    """--task narrows the corpus to one kind before any model is invoked."""

    def test_the_task_filter_selects_every_case_of_one_kind(self, tmp_path, em):
        _make_case(tmp_path, "a", task="ci-fix")
        _make_case(tmp_path, "b", task="ci-fix")
        _make_case(tmp_path, "c", task="review")
        found = em.discover_entries(str(tmp_path), "", "ci-fix")
        assert sorted(e["name"] for e in found) == ["a", "b"]

    def test_the_two_filters_narrow_together(self, tmp_path, em):
        _make_case(tmp_path, "a", task="ci-fix")
        _make_case(tmp_path, "b", task="ci-fix")
        assert [e["name"] for e in em.discover_entries(str(tmp_path), "a", "ci-fix")] == ["a"]

    def test_a_task_filter_matching_nothing_exits_rather_than_running_an_empty_pass(
        self, tmp_path, em,
    ):
        _make_case(tmp_path, "a", task="review")
        with pytest.raises(SystemExit):
            em.discover_entries(str(tmp_path), "", "ci-fix")

    def test_run_eval_passes_the_task_filter_to_discover(self, tmp_path, em, capsys):
        corpus = tmp_path / "corpus"
        _make_case(corpus, "ci-only-a", task="ci-fix")
        _make_case(corpus, "ci-only-b", task="ci-fix")
        _make_case(corpus, "review-only-a", task="review")
        em.run_eval(_args(tmp_path, task="ci-fix", dry_run=True))
        err = capsys.readouterr().err
        assert "ci-only-a" in err
        assert "ci-only-b" in err
        assert "review-only-a" not in err
        assert "Total runs: 2" in err


def _recording_task(calls):
    """A get_task stand-in that records RunOptions without invoking a model."""

    def _get_task(_name=""):
        class Rec:
            name = "stub"

            def run(self, case_dir, opts):
                calls.append({
                    "rules_home": opts.rules_home,
                    "condition": opts.condition,
                })
                return eval.task.RunArtifacts(
                    usage=SessionUsage(cost=0.01, input_tokens=10),
                    data={"summary": "stub"},
                )

            def score(self, artifacts, manifest):
                return ScoringResult("", "", 0, recall=1.0, cost_usd=0.01)

        return Rec()

    return _get_task


def _args(tmp_path, **overrides):
    """Namespace for run_eval. The briefs call _args(); it did not exist."""
    ns = dict(
        corpus=str(tmp_path / "corpus"),
        entry="",
        task="",
        models="",
        effort="low",
        timeout=42,
        verbose=False,
        keep_temp=False,
        dry_run=False,
        runs=1,
        conditions="full",
        output="",
        save_baselines=False,
        compare=False,
        results_dir=str(tmp_path / "results"),
    )
    ns.update(overrides)
    return argparse.Namespace(**ns)


def _fake_claude(path: Path) -> Path:
    """A classify()-clean CLAUDE_CONFIG_DIR the runner can copy, not ~/.claude."""
    rules = path / "rules"
    rules.mkdir(parents=True)
    (rules / "general.md").write_text("# general\n")
    return path


def test_empty_conditions_is_an_error(tmp_path, em):
    (tmp_path / "corpus").mkdir()
    args = _args(tmp_path, conditions="", dry_run=True)
    with pytest.raises(SystemExit, match="unknown condition"):
        em.run_eval(args)


def test_each_condition_gets_its_own_seeded_tree_and_row(tmp_path, monkeypatch, em):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    _fake_claude(tmp_path / "claude")
    monkeypatch.setenv("AI_BACKEND", "claude")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str((tmp_path / "claude").resolve()))
    calls = []
    monkeypatch.setattr(em, "get_task", _recording_task(calls))
    args = _args(tmp_path, conditions="full,trimmed", runs=1)
    em.run_eval(args)
    dirs = {c["rules_home"] for c in calls}
    assert len(dirs) == 2, "each arm needs its own tree"
    assert all(Path(d).is_absolute() for d in dirs)


def test_a_pi_backend_seeds_from_pi_layers_not_claude(tmp_path, monkeypatch, em):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    seen = {}

    def fake_prepare(kind, dest, *, workbench_dir=None):
        seen["kind"] = kind
        dest = Path(dest)
        (dest / "rules").mkdir(parents=True)
        (dest / "rules" / "general.md").write_text("# g\n")
        return dest.resolve()

    monkeypatch.setattr(agent.backend, "selected_backend", lambda: agent.backend.Backend.PI)
    monkeypatch.setattr(eval.conditions, "prepare_seed_source", fake_prepare)
    calls = []
    monkeypatch.setattr(em, "get_task", _recording_task(calls))
    em.run_eval(_args(tmp_path, conditions="full,trimmed", runs=1))
    assert seen["kind"] == "pi"
    assert len({c["rules_home"] for c in calls}) == 2


def test_a_missing_pi_source_fails_loudly_rather_than_seeding_empty(
    tmp_path, monkeypatch, em,
):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")

    def fake_prepare(kind, dest, *, workbench_dir=None):
        raise eval.conditions.MissingRuleSource("Pi rule layers produced no files")

    monkeypatch.setattr(agent.backend, "selected_backend", lambda: agent.backend.Backend.PI)
    monkeypatch.setattr(eval.conditions, "prepare_seed_source", fake_prepare)
    with pytest.raises(SystemExit, match="Pi rule layers produced no files"):
        em.run_eval(_args(tmp_path, conditions="full", runs=1))


def _seed_claude_and_stub_task(tmp_path, monkeypatch, em, get_task):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    _fake_claude(tmp_path / "claude")
    monkeypatch.setenv("AI_BACKEND", "claude")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str((tmp_path / "claude").resolve()))
    monkeypatch.setattr(em, "get_task", get_task)


def test_a_two_arm_run_prints_the_ab_table(tmp_path, monkeypatch, em, capsys):
    _seed_claude_and_stub_task(tmp_path, monkeypatch, em, _recording_task([]))
    em.run_eval(_args(tmp_path, conditions="full,trimmed", runs=1))
    out = capsys.readouterr().out
    assert "| Arm |" in out, "two-arm stdout must include the A/B table"
    assert "delta" in out


def test_a_single_arm_run_does_not_print_the_ab_table(
    tmp_path, monkeypatch, em, capsys,
):
    _seed_claude_and_stub_task(tmp_path, monkeypatch, em, _recording_task([]))
    em.run_eval(_args(tmp_path, conditions="full", runs=1))
    out = capsys.readouterr().out
    assert "| Arm |" not in out
    assert "delta" not in out


def _task_trimmed_never_ran():
    def _get_task(_name=""):
        class Rec:
            name = "stub"
            condition = "full"

            def run(self, case_dir, opts):
                self.condition = opts.condition
                return eval.task.RunArtifacts(
                    usage=SessionUsage(cost=0.01, input_tokens=10),
                    data={"summary": "stub"},
                )

            def score(self, artifacts, manifest):
                outcome = (
                    eval.scoring.RunOutcome.NOT_RUN
                    if self.condition == "trimmed"
                    else eval.scoring.RunOutcome.MEASURED
                )
                return ScoringResult(
                    "", "", 0, recall=1.0, billed_input=40000, outcome=outcome,
                )

        return Rec()

    return _get_task


def test_an_unmeasured_trimmed_arm_is_not_zero_in_the_ab_table(
    tmp_path, monkeypatch, em, capsys,
):
    _seed_claude_and_stub_task(tmp_path, monkeypatch, em, _task_trimmed_never_ran())
    em.run_eval(_args(tmp_path, conditions="full,trimmed", runs=1))
    out = capsys.readouterr().out
    assert "| Arm |" in out, "two-arm stdout must include the A/B table"
    ab = out.split("| Arm |", 1)[1]
    trimmed_row = ab.split("trimmed", 1)[1].split("\n")[0]
    assert "0%" not in trimmed_row, (
        "an arm that never ran must not read as 0% in the A/B table"
    )
    assert "unmeasured" in trimmed_row.lower()
