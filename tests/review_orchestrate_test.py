"""`cli.review_orchestrate` itself.

Prompt budgets, static analysis, cleanup scope, the publishing gate and pipeline choice.
"""

import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from conftest import synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Phase
import agent.backend
import pr.target
import pr.state


class TestPromptBudgetsArePreflighted:
    """A model with no recorded window is caught before the review starts.

    Every phase derives its byte ceiling from its model's context window, so a
    model that has none cannot be budgeted for. Discovering that per phase
    would mean paying for metadata and preflight collection first, then failing
    every phase in turn for the same reason.
    """

    def test_an_unresolved_alias_runs_on_the_tier_floor_and_says_so(self, ro):
        """The ordinary first-party-API setup: allowed, but not silently.

        `phase_model` returns the literal "sonnet" when AI_SONNET_MODEL is
        unset — see `TestPhaseModel`, which asserts exactly that default.
        Refusing would take out every phase on every machine not using Vertex,
        so the run continues against the tier's floor; the warning is what
        stops a smaller review from being the only evidence that happened.

        The variable is named through `ModelAlias` rather than spelled out, for
        the reason the next test gives: a hardcoded copy here is what kept
        pointing operators at `ANTHROPIC_DEFAULT_SONNET_MODEL` after the
        migration renamed it, so setting what the warning asked for fixed
        nothing.
        """
        from agent.phases import ModelAlias

        trail = MagicMock()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ok = ro._budgets_are_derivable({"sonnet": [ro.Phase.SCOUT]}, trail)
        assert ok
        assert "tier alias" in err.getvalue()
        assert ModelAlias.SONNET.env_key in err.getvalue()
        assert trail.decision.called

    def test_the_warning_names_the_variable_that_would_fix_it(self, ro):
        """The env key comes from `ModelAlias`, not from a second derivation.

        Two copies of the naming convention drift the moment a tier's key stops
        following the plain uppercase pattern, and the copy in a warning is the
        one nobody notices is wrong.
        """
        from agent.phases import ModelAlias

        with contextlib.redirect_stderr(io.StringIO()) as err:
            ro._budgets_are_derivable({"haiku": [ro.Phase.SCOUT]}, MagicMock())
        assert ModelAlias.HAIKU.env_key in err.getvalue()

    def test_a_resolved_model_warns_about_nothing(self, ro):
        trail = MagicMock()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ro._budgets_are_derivable({"claude-sonnet-5": [ro.Phase.SCOUT]}, trail)
        assert err.getvalue() == ""
        assert not trail.decision.called

    def test_an_unknown_concrete_model_still_aborts(self, ro):
        """A model nobody has measured is not a model to guess a window for."""
        with contextlib.redirect_stderr(io.StringIO()):
            ok = ro._budgets_are_derivable({"gpt-5": [ro.Phase.SCOUT]}, MagicMock())
        assert not ok

    def test_a_known_model_passes(self, ro):
        assert ro._budgets_are_derivable(
            {"claude-sonnet-5": [ro.Phase.SCOUT]}, MagicMock(),
        )

    def test_the_failure_names_the_phases_it_blocks(self, ro):
        trail = MagicMock()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ro._budgets_are_derivable({"gpt-5": [ro.Phase.SCOUT, ro.Phase.GROUP]}, trail)
        assert "scout" in err.getvalue() and "group" in err.getvalue()
        assert trail.decision.called


# ── 19c. enum_arg ───────────────────────────────────────────────────────────


class TestEnumArg:
    def test_converts_to_enum_member(self, ro):
        assert ro.enum_arg(ro.Mode)("self") is ro.Mode.SELF

    def test_error_lists_valid_choices(self, ro):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError) as exc:
            ro.enum_arg(ro.Effort)("bogus")
        assert str(exc.value) == "invalid choice: 'bogus' (choose from 'low', 'medium', 'high')"


class TestStaticAnalysisIntegration:
    DEEP = (
        "#!/bin/bash\n"
        "func() {\n"
        "  if true; then\n"
        "    for x in a; do\n"
        "      while true; do\n"
        "        echo deep\n"
        "      done\n"
        "    done\n"
        "  fi\n"
        "}\n"
    )

    def _job(self, tmp_path, review_file, changed_files):
        """A job in the two fields the injector reads, plus the one it writes."""
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(tmp_path)
        job.pr.files = changed_files
        job.static_results = []
        return job

    def test_static_analysis_injected_into_review(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")
        (tmp_path / "deep.sh").write_text(self.DEEP)

        changed_files = [{"path": "deep.sh", "additions": 10, "deletions": 0}]
        ro._inject_static_analysis_section(
            self._job(tmp_path, review_file, changed_files),
        )

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "Nesting depth" in result
        assert result.index("## Static Analysis") < result.index("## Verdict")

    def test_the_violations_reach_the_job_the_fix_pass_reads(self, ro, tmp_path):
        """The fix pass takes its work from here rather than re-running the checkers.

        Re-deriving them there would measure a tree this section has already
        described, so the two could disagree about what is wrong with the code.
        """
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")
        (tmp_path / "deep.sh").write_text(self.DEEP)

        changed_files = [{"path": "deep.sh", "additions": 10, "deletions": 0}]
        job = self._job(tmp_path, review_file, changed_files)
        ro._inject_static_analysis_section(job)

        violations = [v for r in job.static_results for v in r.violations]
        assert violations, "the deep script's violation did not reach the job"
        assert all(v.id for v in violations), "a violation with no id is unaddressable"
        # The same ids the section declares, or the fix pass's outcomes have no
        # line to be written back to.
        for violation in violations:
            assert f"**[{violation.id}]**" in review_file.read_text()

    def test_a_failing_checker_does_not_take_the_review_with_it(
        self, ro, tmp_path, monkeypatch,
    ):
        """A reporting step must not destroy a review that is already written.

        The section is injected after every agent has run and been paid for.
        An exception here reached `main`, so the run ended with no verdict
        stamped, no JSON result, and no fix pass — over a review that was
        finished and on disk.
        """
        review_file = tmp_path / "review.md"
        original = "## Summary\nLooks good.\n\n## Verdict\nApprove"
        review_file.write_text(original)
        (tmp_path / "deep.sh").write_text(self.DEEP)

        def _boom(*_a, **_k):
            raise RuntimeError("checker exploded")

        monkeypatch.setattr(ro, "run_static_analysis", _boom)
        changed_files = [{"path": "deep.sh", "additions": 10, "deletions": 0}]
        job = self._job(tmp_path, review_file, changed_files)

        assert ro._inject_static_analysis_section(job) is None
        assert review_file.read_text() == original
        assert job.static_results == []

    def test_static_analysis_skipped_when_no_applicable_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        original = "## Summary\nLooks good.\n\n## Verdict\nApprove"
        review_file.write_text(original)

        changed_files = [{"path": "README.md", "additions": 5, "deletions": 0}]
        job = self._job(tmp_path, review_file, changed_files)
        ro._inject_static_analysis_section(job)

        assert review_file.read_text() == original
        assert job.static_results == []

    def test_static_analysis_clean_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")

        clean_script = tmp_path / "clean.sh"
        clean_script.write_text("#!/bin/bash\necho hello\n")

        changed_files = [{"path": "clean.sh", "additions": 2, "deletions": 0}]
        ro._inject_static_analysis_section(
            self._job(tmp_path, review_file, changed_files),
        )

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "All checks passed" in result

    def test_no_base_logs_why_analysis_is_unscoped(self, ro, tmp_path, capsys):
        """An empty `job.pr.base` falls back to whole-file analysis silently
        except for this line — without it there is nothing to read at the point
        base resolution was skipped, only `_static_items`'s later, violation-
        gated warning."""
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")
        (tmp_path / "clean.sh").write_text("#!/bin/bash\necho hello\n")

        job = self._job(
            tmp_path, review_file, [{"path": "clean.sh", "additions": 2, "deletions": 0}],
        )
        job.pr.base = ""
        ro._inject_static_analysis_section(job)

        assert "no base to diff against" in capsys.readouterr().err


class TestCleanupScope:
    """What `run_orchestrate` leaves in the review directory when it returns.

    Driven through the whole run rather than through the sweep, because the
    leak this covers was in the order the phases run and not in any one of
    them: the pipeline swept as it returned, and the fix pass wrote its log
    afterwards. Each phase is replaced by something that writes the files a
    real one leaves behind, which is all the sweep can see.
    """

    _REVIEW = synthetic_review(meta="status: completed", summary="All good.")

    @staticmethod
    def _args(ro, review_file, repo_dir, **overrides):
        from types import SimpleNamespace

        defaults = {
            "pr": "1", "review_file": str(review_file), "repo_dir": str(repo_dir),
            "mode": ro.Mode.PR, "effort": ro.Effort.MEDIUM,
            "prior_review": "", "issue": "", "issue_context": "",
            "generator_version": "", "model": "", "recover_sha": "",
            "target_dir": str(repo_dir), "max_parallel": 2,
            "max_cost": 20.0, "max_groups": None,
            "no_holistic": False, "no_scout": False, "no_group": False,
            "no_synthesis": False, "no_disprove": True, "disprove": None,
            "generated": False, "fix": False, "base": "",
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _run(self, ro, monkeypatch, tmp_path, pipeline=None, out=None, **arg_overrides):
        """Drive `run_orchestrate` over *tmp_path* with every phase faked.

        Pass *out* to keep the result JSON the run prints; the default throws
        it away, since what most of these tests read is the directory.
        """
        import fix.engine  # the `ro` fixture has already put `ai/lib` on the path

        review_dir = tmp_path / "review-dir"
        review_dir.mkdir()
        review_file = review_dir / "review.md"

        pr_meta = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc123",
            additions=10, deletions=0, changed_files=1,
            files=[{"path": "a.py", "additions": 10, "deletions": 0}],
        )

        def _pipeline(job, **_kwargs):
            Path(job.review_file).write_text(self._REVIEW)
            Path(job.session_log).write_text("{}\n")
            (review_dir / "meta.json").write_text("{}")
            (review_dir / "disprove.jsonl").write_text("{}\n")
            (review_dir / "prompt-single.md").write_text("PROMPT")

        def _fix(job, _trail=None, **_kwargs):
            artifacts = Path(job.artifact_dir)
            (artifacts / "fix.jsonl").write_text("{}\n")
            (artifacts / fix.engine.TRACKING_FILENAME).write_text("## <!-- fix:M1 -->\n")

        monkeypatch.setattr(agent.backend, "preflight", lambda *a, **k: True)
        monkeypatch.setattr(pr.state, "load_state", lambda *a, **k: None)
        monkeypatch.setattr(
            ro, "fetch_metadata",
            lambda *a, **k: ro.RunContext(pr_meta, ro.PRContext(), None),
        )
        # A real record rather than a mock: `_run_phases` sizes the run off
        # the preflight's delta fields, which a mock answers with objects that
        # do not compare against the thresholds.
        monkeypatch.setattr(
            ro, "collect_preflight_data",
            lambda job: ro.PreflightData(
                diff="", commit_log="", file_contents={}, file_permissions={},
                claude_md="", architecture_md="",
            ),
        )
        monkeypatch.setattr(ro, "run_single_agent", pipeline or _pipeline)
        monkeypatch.setattr(ro, "run_static_analysis", lambda *a, **k: [])
        monkeypatch.setattr(ro, "run_fix_pass", _fix)

        args = self._args(ro, review_file, tmp_path, **arg_overrides)
        with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
            ro.run_orchestrate(MagicMock(), args, "org/repo", str(review_dir / "session.jsonl"))
        return review_dir

    def _host_for_origin(self, ro, monkeypatch, tmp_path, identity):
        """The host `run_orchestrate` puts on the job, for a given origin read.

        Captured off the `ReviewJob` rather than read back from `meta.json`:
        the faked pipeline writes that file itself, so the sidecar on disk is
        the fake's and says nothing about what the run resolved.
        """
        seen = {}

        def _capture(job, **_kwargs):
            seen["host"] = job.host
            Path(job.review_file).write_text(self._REVIEW)
            Path(job.session_log).write_text("{}\n")

        monkeypatch.setattr(
            pr.target, "repo_identity_from_origin", lambda *a, **k: identity)
        self._run(ro, monkeypatch, tmp_path, pipeline=_capture)
        return seen["host"]

    def test_the_origins_host_is_stamped_when_it_names_the_same_repo(
        self, ro, monkeypatch, tmp_path,
    ):
        """The ordinary run: `--repo` and the checkout's origin agree."""
        identity = pr.target.RepoIdentity(
            label="org/repo", key="org-repo", host="ghe.acme.com")
        assert self._host_for_origin(
            ro, monkeypatch, tmp_path, identity) == "ghe.acme.com"

    def test_an_origin_naming_another_repo_does_not_lend_its_host(
        self, ro, monkeypatch, tmp_path,
    ):
        """A host is evidence about the remote it was read from, and no other.

        `--repo` may name a repo the checkout is not — a fork, or a scratch
        tree with an unrelated origin. Stamping that origin's forge onto this
        review renders every permalink on an instance the reviewed repo is not
        served from, which is worse than the generic default: a wrong
        enterprise link looks authoritative and 404s.
        """
        identity = pr.target.RepoIdentity(
            label="other/elsewhere", key="other-elsewhere", host="ghe.acme.com")
        assert self._host_for_origin(ro, monkeypatch, tmp_path, identity) == ""

    # passes-at-base: a checkout with no origin had no host to stamp before this change either
    def test_no_origin_leaves_the_host_unset(self, ro, monkeypatch, tmp_path):
        assert self._host_for_origin(ro, monkeypatch, tmp_path, None) == ""

    def test_the_fix_passes_leavings_do_not_outlive_the_run(
        self, ro, monkeypatch, tmp_path,
    ):
        """The sweep runs after the fix pass, never before it.

        Its log and the checklist its agent answered on are both diagnostic,
        and the exact listing below is what says neither survived.
        """
        review_dir = self._run(ro, monkeypatch, tmp_path, fix=True)

        assert not (review_dir / "fix.jsonl").exists()
        assert sorted(p.name for p in review_dir.iterdir()) == [
            "meta.json", "review.md", "session.jsonl",
        ]

    def test_every_verify_chunk_is_swept_not_just_the_first(
        self, ro, monkeypatch, tmp_path,
    ):
        """The sweep globs the gate's checklists; it does not name one.

        A gate that ran in two chunks leaves two files. Unlinking one by name
        leaves the rest beside the deliverable, which is the leak the exact
        listing below is here to catch.
        """
        def _two_chunk_gate(job, **_kwargs):
            # This hook replaces the pipeline outright, so the standard `_fix`
            # stub never runs and nothing writes `fix-tracking.md`. Anything a
            # later change adds to that stub has to be added here too, or the
            # exact listing below stops covering it.
            Path(job.review_file).write_text(self._REVIEW)
            Path(job.session_log).write_text("{}\n")
            review_dir = Path(job.artifact_dir)
            (review_dir / "meta.json").write_text("{}")
            for chunk in (1, 2):
                (review_dir / f"verify-tracking-{chunk}.md").write_text(
                    "## answers\n")

        review_dir = self._run(
            ro, monkeypatch, tmp_path, pipeline=_two_chunk_gate, fix=True,
        )

        assert sorted(p.name for p in review_dir.iterdir()) == [
            "meta.json", "review.md", "session.jsonl",
        ]

    def test_a_run_without_the_fix_pass_is_swept_too(self, ro, monkeypatch, tmp_path):
        review_dir = self._run(ro, monkeypatch, tmp_path)

        assert not (review_dir / "disprove.jsonl").exists()
        assert not (review_dir / "prompt-single.md").exists()
        assert (review_dir / "review.md").exists()

    def test_a_failed_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """A pipeline that exits non-zero leaves everything for diagnosis."""
        def _fails(job, **_kwargs):
            (Path(job.artifact_dir) / "disprove.jsonl").write_text("{}\n")
            (Path(job.artifact_dir) / "prompt-single.md").write_text("PROMPT")
            raise SystemExit(1)

        with pytest.raises(SystemExit) as exc:
            self._run(ro, monkeypatch, tmp_path, pipeline=_fails, fix=True)

        assert exc.value.code == 1
        review_dir = tmp_path / "review-dir"
        assert (review_dir / "disprove.jsonl").exists()
        assert (review_dir / "prompt-single.md").exists()

    def test_a_partial_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """Failed groups are not an exception, but they still block the sweep.

        `pr review --recover` resumes from pipeline.json and the outputs of the
        groups that did succeed, so a sweep here would strand the recovery.
        """
        import core.serde
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.paths import FILENAME_PIPELINE_STATE
        from review.state import PipelineState

        def _partial(job, **_kwargs):
            review_dir = Path(job.artifact_dir)
            Path(job.review_file).write_text(self._REVIEW)
            (review_dir / "group-1.md").write_text("finding")
            state = PipelineState(
                head_sha="abc123", group_names=["a", "b"], groups_done=[1],
                groups_failed={2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)},
                done={Phase.SYNTHESIS},
            )
            (review_dir / FILENAME_PIPELINE_STATE).write_text(
                json.dumps(core.serde.to_dict(state)),
            )

        review_dir = self._run(ro, monkeypatch, tmp_path, pipeline=_partial, fix=True)

        assert (review_dir / "group-1.md").exists()
        assert (review_dir / "pipeline.json").exists()
        assert (review_dir / "fix.jsonl").exists()

    def test_a_failed_sweep_still_reports_the_review(self, ro, monkeypatch, tmp_path):
        """The result JSON is printed after the sweep, so the sweep must not eat it.

        `unlink(missing_ok=True)` only suppresses FileNotFoundError; a
        read-only filesystem raises. review reads the review it just
        paid for out of this JSON, so failing to delete a log cannot be what
        loses it.
        """
        import review.gc

        def _explode(review_dir):
            raise OSError(30, "Read-only file system", str(review_dir / "disprove.jsonl"))

        monkeypatch.setattr(review.gc, "cleanup_intermediates", _explode)

        out = io.StringIO()
        review_dir = self._run(ro, monkeypatch, tmp_path, out=out)

        assert json.loads(out.getvalue()) == {
            "review_file": str(review_dir / "review.md"),
            "session_log": str(review_dir / "session.jsonl"),
            "mode": ro.Pipeline.SINGLE,
        }
        assert (review_dir / "disprove.jsonl").exists()


class TestThePublishingGate:
    """`--post` is forwarded here because the fix pass lives in this process.

    `review` decides whether a run may publish, but the fix pass runs in
    this process — today a subprocess, later an in-process call — so a gate
    opened only in the parent would never reach the push the pass makes.
    """

    def _gate_at_first_work(self, ro, monkeypatch, tmp_path, argv):
        """Whether the run could publish by the time it started doing anything."""
        import core.publishing

        seen = {}

        def stop(*args, **kwargs):
            seen["enabled"] = core.publishing.enabled()
            raise SystemExit(0)

        monkeypatch.setattr(ro, "detect_repo", stop)
        monkeypatch.setattr(sys, "argv", [
            "review-orchestrate", "--mode", "self",
            "--review-file", str(tmp_path / "review.md"),
            "--repo-dir", str(tmp_path), *argv,
        ])
        with pytest.raises(SystemExit):
            ro.main()
        return seen["enabled"]

    def test_a_fix_run_without_post_cannot_publish(self, ro, monkeypatch, tmp_path):
        assert self._gate_at_first_work(ro, monkeypatch, tmp_path, ["--fix"]) is False

    def test_post_opens_the_gate_before_anything_runs(self, ro, monkeypatch, tmp_path):
        assert self._gate_at_first_work(
            ro, monkeypatch, tmp_path, ["--fix", "--post"],
        ) is True


class TestSelfReviewPipeline:
    """A small self-review stays single-agent without dropping disprove."""

    def _job(self, ro, tmp_path, mode, lines=681, files=6):
        from core.phases import Effort

        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(
                "t", "", "head", "main", "abc123", lines, 0, files, [],
            ),
            ctx=ro.PRContext(), wt_path=str(tmp_path),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            effort=Effort.MEDIUM, mode=mode,
        )

    def test_self_review_stays_single_where_a_pr_review_goes_multi(
        self, ro, tmp_path, monkeypatch,
    ):
        from review.phases import _should_disprove

        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
        (tmp_path / "config").mkdir()
        pr_job = self._job(ro, tmp_path, ro.Mode.PR)
        self_job = self._job(ro, tmp_path, ro.Mode.SELF)
        pr_pipeline, *_ = ro._choose_pipeline(pr_job)
        self_pipeline, *_ = ro._choose_pipeline(self_job)
        assert pr_pipeline is ro.Pipeline.MULTI
        assert self_pipeline is ro.Pipeline.SINGLE
        assert _should_disprove(self_job) is True

    def test_an_explicit_effort_moves_the_self_review_threshold(
        self, ro, tmp_path, monkeypatch,
    ):
        """`--effort` wins here as it does everywhere else in the chain.

        It used to set every budget and leave this decision on the config key,
        so a diff too large for one agent stayed single-agent and the run spent
        its turns reading without ever writing the review. The flag looked like
        it worked and did not.
        """
        from core.phases import Effort

        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
        (tmp_path / "config").mkdir()
        # 681 lines: over medium's 500, under low's 1000 — the band where the
        # two presets disagree, which is the only place the flag can show.
        job = self._job(ro, tmp_path, ro.Mode.SELF)

        default_pipeline, *_ = ro._choose_pipeline(job)
        flagged_pipeline, *_ = ro._choose_pipeline(job, Effort.MEDIUM)

        assert default_pipeline is ro.Pipeline.SINGLE
        assert flagged_pipeline is ro.Pipeline.MULTI

    # passes-at-base: the config key was already the value this reads
    def test_the_config_key_still_answers_when_no_flag_is_passed(
        self, ro, tmp_path, monkeypatch,
    ):
        """The flag is an override, not a replacement for `review.self_effort`."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.yml").write_text(
            "review:\n  self_effort: medium\n",
        )
        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_dir))
        job = self._job(ro, tmp_path, ro.Mode.SELF)

        pipeline, *_ = ro._choose_pipeline(job)

        assert pipeline is ro.Pipeline.MULTI
