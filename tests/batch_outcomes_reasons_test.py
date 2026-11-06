"""Failure reasons named from emitter text: mise, quota, owners, and a refused commit."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend  # noqa: E402
import agent.invoke  # noqa: E402
import agent.session  # noqa: E402
import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import gh.owners  # noqa: E402
from batch.model import Step, StepStatus  # noqa: E402
from core.quota_throttle import QuotaThrottle  # noqa: E402

ITEM = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
                        worktree="/wt")


@pytest.mark.parametrize("lines, reason, detail", [
    (["mise ERROR Config files in ~/wt/.mise.toml are not trusted."],
     "mise_untrusted",
     "mise ERROR Config files in ~/wt/.mise.toml are not trusted."),
    (["✗ commit failed: mise ERROR Config files in /wt/.mise.toml are not trusted."],
     "mise_untrusted",
     "commit failed: mise ERROR Config files in /wt/.mise.toml are not trusted."),
    (["✗ You've hit your monthly spend limit"],
     "quota_exhausted",
     "You've hit your monthly spend limit"),
    (["⚠ Quota exhausted on opus — backing off 30s", "✗ ai prompt failed"],
     "quota_exhausted",
     "Quota exhausted on opus — backing off 30s"),
], ids=["mise", "commit-then-mise", "spend-limit", "quota-before-prompt"])
def test_classify_failure_names_the_emitted_line(lines, reason, detail):
    found = batch.outcomes.classify_failure(lines)
    assert found.reason.value == reason
    assert found.detail == detail


def test_quota_marker_is_the_throttles_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    QuotaThrottle().report_exhausted("opus")
    found = batch.outcomes.classify_failure(capsys.readouterr().err.splitlines())
    assert found.reason is batch.outcomes.FailureReason.QUOTA_EXHAUSTED


def test_quota_marker_is_run_agents_text(monkeypatch, tmp_path, capsys):
    remaining = [1, 0]
    monkeypatch.setattr(
        agent.backend, "invoke_agent", lambda inv: remaining.pop(0))
    monkeypatch.setattr(agent.session, "is_quota_error", lambda log_path: True)
    monkeypatch.setattr(agent.invoke.time, "sleep", lambda seconds: None)
    inv = agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), model="sonnet",
        session_log=str(tmp_path / "s.jsonl"),
    )
    agent.invoke.run_agent(inv)
    found = batch.outcomes.classify_failure(capsys.readouterr().err.splitlines())
    assert found.reason is batch.outcomes.FailureReason.QUOTA_EXHAUSTED


def test_owners_marker_is_the_emitters_text():
    err = gh.owners.OwnerBoundaryError(
        gh.owners.Verdict.refuse(gh.owners.UNCONFIGURED))
    found = batch.outcomes.classify_failure([f"✗ {err}"])
    assert found.reason is batch.outcomes.FailureReason.OWNERS_UNCONFIGURED


def test_ci_clean_exit_with_commit_failed_is_failed():
    r = batch.outcomes.classify(
        Step.CI, 0, "{}", item=ITEM, log_tail=["✗ commit failed: hook"])
    assert r.status is StepStatus.FAILED
    assert r.decisions[0].payload["reason"] == "commit_failed"


def test_comments_clean_exit_refused_by_untrusted_mise_is_failed():
    r = batch.outcomes.classify(
        Step.COMMENTS, 0, "{}", item=ITEM,
        log_tail=["commit failed: mise ERROR Config files in /wt/.mise.toml are not trusted."])
    assert r.status is StepStatus.FAILED
    assert r.decisions[0].payload["reason"] == "mise_untrusted"
