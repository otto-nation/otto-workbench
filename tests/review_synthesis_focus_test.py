"""Synthesis coverage: every area is answered, and a second miss degrades."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import cli.review_orchestrate  # noqa: F401

from review.record import FocusSource, load_record
from review.steps import Merged
from review.verify import PostProcessed
from review_focus_support import DERIVED_FOCUS, user_area
from review_record_support import record


class TestSynthesisFocus:
    def _make_job(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[{"path": "a.py"}]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode("pr"),
            focus=(user_area("Asked"),),
        )

    @staticmethod
    def _patch_pipeline(monkeypatch, **overrides):
        import agent.retry
        import review.outcome
        import review.phases
        import review.steps
        owners = {
            "build_prompt": review.steps,
            "post_process_record": review.outcome,
            "run_agent": review.phases,
            "try_recover_output": agent.retry,
        }
        defaults = {
            "build_prompt": lambda *a, **kw: "mock prompt",
            "post_process_record": lambda rec, *a, **kw: PostProcessed(rec, None),
        }
        defaults.update(overrides)
        for name, func in defaults.items():
            monkeypatch.setattr(owners[name], name, func)

    def test_synthesis_coverage_lands_on_the_record(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve",
                "focus": [
                    {"id": "F1", "coverage": "checked F1"},
                    *DERIVED_FOCUS,
                ],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        ro._phase_synthesis(job, "", 3, Merged(record()))
        rec = load_record(tmp_path)
        assert [(a.id, a.source, a.coverage) for a in rec.focus] == [
            ("F1", FocusSource.USER, "checked F1"),
            ("F2", FocusSource.DERIVED, "checked"),
        ]

    def test_a_synthesis_missing_coverage_gets_a_repair_turn(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        covered = {
            "summary": "s", "verdict": "Approve",
            "focus": [{"id": "F1", "coverage": "now covered"}, *DERIVED_FOCUS],
        }
        prompts = []

        def mock_invoke(inv, **kwargs):
            prompts.append(inv.prompt)
            doc = covered if "Your output was rejected" in inv.prompt else {"summary": "s"}
            Path(inv.output_path).write_text(json.dumps(doc))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(record()))
        assert result.diagnosis is None
        assert len(prompts) == 2 and "missing_coverage" in prompts[1]
        rec = load_record(tmp_path)
        assert rec.focus[0].coverage == "now covered"

    def test_a_second_coverage_miss_publishes_the_area_unassessed(
        self, ro, tmp_path, monkeypatch, capsys,
    ):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({"summary": "s", "verdict": "Approve"}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(record()))
        assert result.diagnosis is None
        rec = load_record(tmp_path)
        assert rec.focus[0].id == "F1" and rec.focus[0].coverage == ""
        assert rec.summary == "s"
        rendered = Path(job.review_file).read_text()
        assert "*Not assessed this round.*" in rendered
        err = capsys.readouterr().err
        assert "missing_coverage" in err
        assert "areas with no entry are published as not assessed" in err
