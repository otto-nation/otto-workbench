"""Synthesis claims: ingest, one repair turn, then degrade; turns grow with a source."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import cli.review_orchestrate  # noqa: F401

from review.budget import CLAIM_TURN_ALLOWANCE, MAX_CLAIMS
from review.phases import RETRY_MAX_TURNS_GROUP, _synthesis_max_turns
from review.record import load_record
from review.steps import Merged
from review.verify import PostProcessed
from review_claims_support import CLAIM_DOC, claim
from review_focus_support import DERIVED_FOCUS
from review_record_support import finding, record


class TestSynthesisClaims:
    def _make_job(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="Retries now back off.",
                             head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[{"path": "a.py"}]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode("pr"),
            focus=(),
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

    def test_synthesis_claims_land_on_the_record(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            calls.append(inv)
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve",
                "focus": DERIVED_FOCUS, "claims": [CLAIM_DOC],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        ro._phase_synthesis(job, "", 3, Merged(record()))
        assert len(calls) == 1
        rec = load_record(tmp_path)
        assert rec.claims == (claim(),)

    def test_a_synthesis_missing_claims_gets_a_repair_turn(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        with_claims = {
            "summary": "s", "verdict": "Approve",
            "focus": DERIVED_FOCUS, "claims": [CLAIM_DOC],
        }
        prompts = []

        def mock_invoke(inv, **kwargs):
            prompts.append(inv.prompt)
            doc = with_claims if "Your output was rejected" in inv.prompt else {
                "summary": "s", "focus": DERIVED_FOCUS,
            }
            Path(inv.output_path).write_text(json.dumps(doc))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(record()))
        assert result.diagnosis is None
        assert len(prompts) == 2
        assert "missing_claims" in prompts[1]
        assert "was read" in prompts[1]
        assert "cannot be read" not in prompts[1]

    def test_a_second_claims_miss_publishes_not_assessed(
        self, ro, tmp_path, monkeypatch, capsys,
    ):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve", "focus": DERIVED_FOCUS,
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(record()))
        assert result.diagnosis is None
        rec = load_record(tmp_path)
        assert rec.claims is None
        rendered = Path(job.review_file).read_text()
        assert "*Not assessed this round.*" in rendered
        err = capsys.readouterr().err
        assert "missing_claims" in err
        assert " — claims are published as not assessed" in err

    def test_an_over_cap_list_degrades_rather_than_rejects(
        self, ro, tmp_path, monkeypatch,
    ):
        job = self._make_job(ro, tmp_path)
        over = {
            "summary": "s", "verdict": "Approve",
            "focus": DERIVED_FOCUS, "claims": [CLAIM_DOC] * (MAX_CLAIMS + 1),
        }

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps(over))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(record()))
        assert result.diagnosis is None
        rec = load_record(tmp_path)
        assert rec.claims is None

    def test_synthesis_turns_grow_with_a_claim_source(self, ro, tmp_path, monkeypatch):
        seen = {}

        def capture_prompt(*a, **kw):
            seen["max_turns"] = kw["max_turns"]
            return "mock prompt"

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve",
                "focus": DERIVED_FOCUS, "claims": [CLAIM_DOC],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(
            monkeypatch, run_agent=mock_invoke, build_prompt=capture_prompt,
        )
        one = Merged(record(finding("N1", "x")))
        job = self._make_job(ro, tmp_path)
        ro._phase_synthesis(job, "", 3, one)
        assert seen["max_turns"] == 15 + CLAIM_TURN_ALLOWANCE
        job.pr.body = ""
        ro._phase_synthesis(job, "", 3, one)
        assert seen["max_turns"] == 15


def test_synthesis_turns_stay_at_the_group_ceiling():
    merged = record(*(finding(f"N{i}", "x") for i in range(1, 121)))
    assert _synthesis_max_turns(merged, claim_turns=CLAIM_TURN_ALLOWANCE) == RETRY_MAX_TURNS_GROUP
