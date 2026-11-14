"""Comments adapter plan/implement/escalate wiring, and the staged prompts."""

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _answering_the_owner, _fake_ctx, _git_ran,
    _triaged_round,
)

import agent.invoke
import core.log
import fix.comment_checklist
import fix.comments
import fix.comments_stages
import git.client
import git.topology
import pr.fix_state
import pr.thread_context
import pr.triage_round
from agent.registry import PHASES
from config.workbench_config import FixConfig, ModelsConfig, WorkbenchConfig
from core.phases import Phase
from pr.comments_state import ThreadState
from pr.fix import FixOutcome, Handoff, ItemOutcome
from pr.thread_models import (
    Classification, CommentItem, PRReport, ReportThread, TriageResult,
)

REVIEWER_ASK = "UNIQUE_REVIEWER_ASK_BODY"


def _cfg(*, plan_stage=True, deep="claude-opus"):
    return replace(
        WorkbenchConfig(),
        fix=replace(FixConfig(), plan_stage=plan_stage),
        models=replace(ModelsConfig(), deep=deep),
    )


def _adapter(tmp_path, cfg=None, **round_kw):
    ctx = _fake_ctx(tmp_path)
    report = PRReport(repo="owner/repo", pr_number=1)
    adapter = fix.comments.CommentFixAdapter(
        report, ctx, tmp_path, _triaged_round(**round_kw),
    )
    if cfg is not None:
        adapter.config = cfg
        fix.comments_stages.apply(adapter)
    return adapter


def _tick(path: Path, label: str, reason: str = "") -> None:
    text = path.read_text()
    old = f"- [ ] {label}"
    if reason and f"{old} — <why>" in text:
        text = text.replace(f"{old} — <why>", f"- [x] {label} — {reason}", 1)
    else:
        text = text.replace(old, f"- [x] {label}", 1)
    path.write_text(text)


def test_hooks_off_when_plan_stage_is_false(tmp_path):
    adapter = _adapter(tmp_path, cfg=_cfg(plan_stage=False))
    assert adapter.plan_phase is None
    assert adapter.implement_phase is None
    assert adapter.escalate_phase is None
    assert fix.comments_stages.active(_cfg(plan_stage=False)) is False


def test_hooks_on_when_plan_stage_is_on(tmp_path):
    with patch.object(fix.comments_stages, "unavailable", return_value=""):
        adapter = _adapter(tmp_path, cfg=_cfg(plan_stage=True))
        assert fix.comments_stages.active(_cfg(plan_stage=True)) is True
    assert adapter.plan_phase is Phase.COMMENTS_PLAN
    assert adapter.implement_phase is Phase.COMMENTS_IMPLEMENT
    assert adapter.escalate_phase is Phase.COMMENTS_ESCALATE


def test_plan_complex_for_reuses_active(tmp_path):
    with patch.object(fix.comments_stages, "unavailable", return_value=""), \
         patch(
             "fix.comments_stages.load_config_or_default",
             return_value=_cfg(plan_stage=True),
         ):
        assert fix.comments_stages.plan_complex_for(tmp_path) is True
    with patch(
        "fix.comments_stages.load_config_or_default",
        return_value=_cfg(plan_stage=False),
    ):
        assert fix.comments_stages.plan_complex_for(tmp_path) is False


def test_run_pass_passes_plan_complex_to_triage(tmp_path):
    captured = {}

    def fake_round(*_a, **kw):
        captured.update(kw)
        raise RuntimeError("stop")

    report = PRReport(repo="owner/repo", pr_number=1)
    ctx = _fake_ctx(tmp_path)
    with patch.object(pr.triage_round, "triage_the_round", side_effect=fake_round), \
         patch.object(fix.comments_stages, "plan_complex_for", return_value=True):
        with pytest.raises(RuntimeError, match="stop"):
            fix.comments.run_pass(TriageResult(), report, tmp_path, ctx)
    assert captured.get("plan_complex") is True


def test_unset_deep_tier_logs_one_error_and_stays_on_today_s_path(tmp_path):
    with patch.object(core.log, "error") as error:
        adapter = _adapter(tmp_path, cfg=_cfg(plan_stage=True, deep=None))
    error.assert_called_once()
    assert "deep tier is unset" in error.call_args.args[0]
    assert adapter.plan_phase is None


def test_forced_light_model_logs_one_error_and_stays_off(tmp_path):
    with patch.object(
        fix.comments_stages.agent.phases, "resolve_phase_model",
        side_effect=fix.comments_stages.agent.phases.ReviewModelTooLight(
            Phase.COMMENTS_PLAN, "models.deep", "haiku",
        ),
    ), patch.object(core.log, "error") as error:
        adapter = _adapter(tmp_path, cfg=_cfg(plan_stage=True))
    error.assert_called_once()
    assert adapter.plan_phase is None


@pytest.mark.parametrize("classification", [
    Classification.QUESTION, Classification.CONFLICTING,
])
def test_escalates_skips_classification(tmp_path, classification):
    adapter = _adapter(tmp_path, needs_human=[CommentItem(
        id="t1", classification=classification, state=ThreadState.NEW,
    )])
    outcome = ItemOutcome(
        id="t1", outcome=FixOutcome.NEEDS_HUMAN, handoff=Handoff.PLAN_FAILED,
    )
    assert adapter.escalates(outcome) is False


def test_escalates_skips_contested(tmp_path):
    adapter = _adapter(tmp_path, needs_human=[CommentItem(
        id="t1", classification=Classification.ACTIONABLE_SUGGESTION,
        state=ThreadState.CONTESTED,
    )])
    adapter.threads_by_id = {
        "t1": ReportThread(id="t1", state=ThreadState.CONTESTED),
    }
    outcome = ItemOutcome(
        id="t1", outcome=FixOutcome.NEEDS_HUMAN, handoff=Handoff.PLAN_FAILED,
    )
    assert adapter.escalates(outcome) is False


def test_escalates_plan_needs_human(tmp_path):
    adapter = _adapter(tmp_path, fixable=[CommentItem(
        id="t1", classification=Classification.ACTIONABLE_SUGGESTION,
        state=ThreadState.NEW,
    )])
    outcome = ItemOutcome(
        id="t1", outcome=FixOutcome.NEEDS_HUMAN, handoff=Handoff.PLAN_NEEDS_HUMAN,
    )
    assert adapter.escalates(outcome) is True


def test_registry_names_the_staged_templates():
    assert PHASES[Phase.COMMENTS_PLAN].template_for() == "plan-comments.md"
    assert PHASES[Phase.COMMENTS_IMPLEMENT].template_for() == "implement-plan.md"
    assert PHASES[Phase.COMMENTS_ESCALATE].template_for() == "escalate-comments.md"


def test_implement_prompt_does_not_include_the_raw_comment():
    text = (
        REPO_ROOT / "ai" / "lib" / "review-templates" / "implement-plan.md"
    ).read_text()
    assert "not shown the review comment" in text
    assert "untrusted" not in text.lower()


def _force_stages(adapter):
    adapter.plan_phase = Phase.COMMENTS_PLAN
    adapter.implement_phase = Phase.COMMENTS_IMPLEMENT
    adapter.escalate_phase = Phase.COMMENTS_ESCALATE


def _write_plan(artifacts, plan):
    for path in sorted(artifacts.glob("plan-tracking-*.md")):
        if plan != "fix":
            _tick(path, "needs a person", "design call")
            continue
        _tick(path, "fix")
        path.write_text(path.read_text().replace(
            "- [x] fix",
            "- [x] fix\n\nFiles: f.go\nChange: edit f\nTest: test_x\n",
            1,
        ))


def _write_escalate(artifacts, escalate, reason, analysis):
    box = escalate or "needs a person"
    for path in sorted(artifacts.glob("escalate-tracking-*.md")):
        _tick(path, box, reason)
        if analysis:
            path.write_text(path.read_text().replace(
                f"- [x] {box}", f"Analysis: {analysis}\n\n- [x] {box}", 1,
            ))


def _staged_run_fix(artifacts, *, plan="fix", implement="fixed", escalate=None,
                    implement_reason="test_x — asserts x",
                    escalate_reason="ask the author", analysis=""):
    captured = []

    def run_fix(phase, prompt, **kwargs):
        captured.append((phase, prompt, kwargs))
        if phase is Phase.COMMENTS_PLAN:
            _write_plan(artifacts, plan)
        elif phase is Phase.COMMENTS_IMPLEMENT:
            tracking = artifacts / "fix-tracking.md"
            _tick(tracking, implement, implement_reason)
        elif phase is Phase.COMMENTS_ESCALATE:
            _write_escalate(artifacts, escalate, escalate_reason, analysis)
        elif phase is Phase.COMMENTS_VERIFY:
            return agent.invoke.FixResult(0, None)
        else:
            tracking = artifacts / "fix-tracking.md"
            if tracking.exists():
                _tick(tracking, "fixed", implement_reason)
        return agent.invoke.FixResult(0, None)

    run_fix.captured = captured  # type: ignore[attr-defined]
    return run_fix


def _run_pass(tmp_path, run_fix):
    item = CommentItem(
        id="t1", file="f.go", line=10, reviewer="kgn", summary="t1 summary",
        classification="actionable_suggestion", verification="valid",
        complexity="low", state=ThreadState.NEW, body=REVIEWER_ASK,
    )
    report = PRReport(
        repo="owner/repo", pr_number=1,
        threads=[ReportThread(
            id="t1", file="f.go", line=10,
            comments=[{"databaseId": 100, "body": REVIEWER_ASK}],
        )],
    )
    ctx = _fake_ctx(tmp_path)
    artifacts = tmp_path / "pr-comments"

    def mock_run(*cmd, **kwargs):
        return _git_ran(0, stdout="abc1234\n")

    with patch.object(fix.comments_stages, "apply", side_effect=_force_stages), \
         patch.object(agent.invoke, "run_fix", side_effect=run_fix), \
         patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
         patch.object(fix.comment_checklist, "find_and_update_main_worktree",
                      return_value=None), \
         patch.object(git.topology, "default_branch_cached", return_value="main"), \
         patch.object(pr.fix_state, "persist"), \
         patch.object(git.client, "run",
                      side_effect=_answering_the_owner(mock_run)), \
         patch("pr.comments.post_thread_reply", return_value=True), \
         patch("pr.comments.post_issue_comment", return_value="u"), \
         patch("pr.comments.resolve_thread", return_value=True):
        result = fix.comments.run_pass(
            TriageResult(threads=[item]), report, tmp_path, ctx, verify=False,
        )
    return SimpleNamespace(result=result, artifacts=artifacts, run_fix=run_fix)


def test_run_pass_plan_implement_fixed(tmp_path):
    stub = _staged_run_fix(tmp_path / "pr-comments")
    run = _run_pass(tmp_path, stub)
    phases = [phase for phase, _prompt, _kw in stub.captured]
    assert Phase.COMMENTS_PLAN in phases
    assert Phase.COMMENTS_IMPLEMENT in phases
    assert [t.id for t in run.result.fixed] == ["t1"]
    assert run.result.planned == 1
    implement_prompts = [
        prompt for phase, prompt, _kw in stub.captured
        if phase is Phase.COMMENTS_IMPLEMENT
    ]
    assert implement_prompts
    assert REVIEWER_ASK not in implement_prompts[0]
    assert "edit f" in implement_prompts[0]


def test_run_pass_plan_needs_human_escalate_resolves(tmp_path):
    stub = _staged_run_fix(
        tmp_path / "pr-comments", plan="needs a person",
        escalate="fixed", escalate_reason="test_x",
    )
    run = _run_pass(tmp_path, stub)
    phases = [phase for phase, _prompt, _kw in stub.captured]
    assert Phase.COMMENTS_PLAN in phases
    assert Phase.COMMENTS_ESCALATE in phases
    assert Phase.COMMENTS_IMPLEMENT not in phases
    assert [t.id for t in run.result.fixed] == ["t1"]
    assert run.result.escalated == 1


def test_run_pass_plan_needs_human_escalate_confirms(tmp_path):
    stub = _staged_run_fix(
        tmp_path / "pr-comments", plan="needs a person",
        escalate="needs a person", escalate_reason="ask the author",
        analysis="the plan named a missing helper",
    )
    run = _run_pass(tmp_path, stub)
    assert [t.id for t in run.result.needs_human] == ["t1"]
    row = run.result.needs_human[0]
    assert row.reason == "ask the author"
    assert "missing helper" not in (row.reason or "")
    notes = list((tmp_path / "pr-comments").glob("escalate-tracking-*.md"))
    assert notes
    assert "the plan named a missing helper" in notes[0].read_text()
    assert run.result.escalated == 1
