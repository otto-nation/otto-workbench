"""Comments-domain wiring for the plan / implement / escalate stages.

The engine's hooks are defaulted off. This module turns them on for a comments
pass when ``fix.plan_stage`` is set and the plan model can actually run. Off,
or when the deep tier is unset or a light model is forced, the adapter is
today's ``comments_fix`` path — one error line, nothing else.
"""

# doc-group: pipeline

from __future__ import annotations

import agent.models
import agent.phases
import core.log
import git.client
import git.topology
from config.workbench_config import load_config_or_default
from core.phases import Phase
from pr.comments_state import ThreadState
from pr.fix import Handoff
from pr.thread_models import Classification, CommentItem

_SKIP_CLASSIFICATIONS = frozenset({
    Classification.QUESTION,
    Classification.CONFLICTING,
})


def apply(adapter) -> None:
    """Set or clear the staged-pass hooks from config."""
    cfg = adapter.config or load_config_or_default(adapter.workdir)
    adapter.config = cfg
    if not cfg.fix.plan_stage:
        _clear(adapter)
        return
    reason = unavailable(cfg)
    if reason:
        core.log.error(reason)
        _clear(adapter)
        return
    adapter.plan_phase = Phase.COMMENTS_PLAN
    adapter.implement_phase = Phase.COMMENTS_IMPLEMENT
    adapter.escalate_phase = Phase.COMMENTS_ESCALATE


def active(cfg) -> bool:
    """True when the staged comments path (and complex→fixable routing) will run."""
    return bool(cfg.fix.plan_stage) and not unavailable(cfg)


def plan_complex_for(workdir) -> bool:
    """Whether triage should send high-complexity valids to the agent."""
    return active(load_config_or_default(workdir))


def unavailable(cfg) -> str:
    """Why the plan stage cannot run, or empty when it can."""
    if not cfg.models.deep:
        return (
            "plan stage cannot run: deep tier is unset — "
            "using today's comments_fix path"
        )
    try:
        agent.phases.resolve_phase_model(Phase.COMMENTS_PLAN, None, cfg)
    except (agent.phases.ReviewModelTooLight, agent.models.UnsetTier) as exc:
        return f"plan stage cannot run: {exc} — using today's comments_fix path"
    return ""


def _clear(adapter) -> None:
    adapter.plan_phase = None
    adapter.implement_phase = None
    adapter.escalate_phase = None


def escalates(adapter, outcome, staged=None) -> bool:
    """C1, plus skip question / conflicting / contested (never staged)."""
    entry = _entry(adapter, outcome.id)
    if entry is not None and entry.classification in _SKIP_CLASSIFICATIONS:
        return False
    thread = getattr(adapter, "threads_by_id", {}).get(outcome.id)
    states = {str(getattr(entry, "state", "")), str(getattr(thread, "state", ""))}
    if ThreadState.CONTESTED.value in states:
        return False
    if outcome.handoff is Handoff.PLAN_NEEDS_HUMAN:
        return True
    import fix.engine
    return fix.engine.FixAdapter.escalates(adapter, outcome, staged)


def pr_files(adapter) -> frozenset[str] | None:
    """Names from ``git diff --name-only <merge-base default>...HEAD``."""
    default = git.topology.default_branch_cached(adapter.workdir)
    if not default:
        return None
    base = git.client.out("merge-base", default, "HEAD", cwd=adapter.workdir)
    if not base:
        return None
    names = git.client.lines(
        "diff", "--name-only", f"{base}...HEAD", cwd=adapter.workdir,
    )
    return frozenset(names)


def _entry(adapter, item_id: str) -> CommentItem | None:
    round_ = getattr(adapter, "round", None)
    if round_ is None:
        return None
    entries = (
        entry for bucket in
        (round_.fixable, round_.fixable_items, round_.needs_human)
        for entry in bucket
    )
    return next((entry for entry in entries if entry.id == item_id), None)
