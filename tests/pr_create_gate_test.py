"""The self-review gate on `pr.create.run_create`."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from pr_create_support import (  # noqa: E402
    COVERED, UNCOVERED, h, run_create, run_create_ungated,  # noqa: F401
)
import git.client  # noqa: E402
from pr.create import CreateOptions  # noqa: E402


def test_uncovered_refuses_before_token_nesting_push_ai_gh(h, capsys):
    h.repo()
    assert run_create_ungated(h.ctx(), CreateOptions(), self_review=UNCOVERED) == 1
    err = capsys.readouterr().err
    assert "✗ No self-review covers HEAD" in err
    assert "no self-review yet" in err
    assert "pr review --self --fix" in err
    assert "→ Review file: /reviews/missing.md" in err
    assert 'pr create --draft --skip-review "<their reason>"' in err
    assert h.events == []
    assert not h.remote_has_branch()


def test_the_gate_receives_worktree_head(h):
    h.repo()
    seen: list[str] = []

    def gate(head):
        seen.append(head)
        return COVERED(head)

    assert run_create_ungated(h.ctx(), CreateOptions(), self_review=gate) == 0
    assert seen == [git.client.head_sha(h.wt)]


def test_covered_runs_full_order(h):
    h.repo()
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert h.events == ["token", "nesting", "push", "ai", "gh"]


@pytest.mark.parametrize("ctx_kw, opts", [
    ({"pr_number": 812}, CreateOptions()),
    ({"branch": "main"}, CreateOptions()),
], ids=["existing-pr", "default-branch"])
def test_preflight_refusals_precede_the_gate(h, ctx_kw, opts):
    h.repo()
    seen: list[str] = []

    def gate(head):
        seen.append(head)
        return UNCOVERED(head)

    assert run_create_ungated(h.ctx(**ctx_kw), opts, self_review=gate) == 1
    assert seen == []
    assert h.events == []


def test_no_verify_does_not_skip_the_gate(h, capsys):
    h.repo()
    opts = CreateOptions(no_verify=True)
    assert run_create_ungated(h.ctx(), opts, self_review=UNCOVERED) == 1
    assert "✗ No self-review covers HEAD" in capsys.readouterr().err
    assert h.events == []
    assert not h.remote_has_branch()


def _decision(trail):
    trail.decision.assert_called_once()
    args, kwargs = trail.decision.call_args
    assert args[0] == "self_review_gate"
    return args, kwargs


def test_skip_review_opens_the_pr_and_records_the_operator_reason(h, capsys):
    h.repo()
    trail = MagicMock()
    opts = CreateOptions(skip_review="user asked to skip")
    assert run_create_ungated(
        h.ctx(), opts, self_review=UNCOVERED, trail=trail,
    ) == 0
    err = capsys.readouterr().err
    assert "⚠ Self-review skipped: user asked to skip" in err
    assert "already covers HEAD" not in err
    assert h.events == ["token", "nesting", "push", "ai", "gh"]
    _, kwargs = _decision(trail)
    assert kwargs["reason"] == "user asked to skip"
    assert kwargs["data"]["outcome"] == "skipped"
    assert kwargs["data"]["covered"] is False


def test_skip_review_when_already_covered(h, capsys):
    h.repo()
    trail = MagicMock()
    opts = CreateOptions(skip_review="just create the PR")
    assert run_create_ungated(
        h.ctx(), opts, self_review=COVERED, trail=trail,
    ) == 0
    assert "⚠ Self-review skipped: just create the PR (a self-review already covers HEAD)" in (
        capsys.readouterr().err
    )
    _, kwargs = _decision(trail)
    assert kwargs["data"]["outcome"] == "skipped"
    assert kwargs["data"]["covered"] is True


def test_refusal_is_recorded_on_the_trail(h):
    h.repo()
    trail = MagicMock()
    assert run_create_ungated(
        h.ctx(), CreateOptions(), self_review=UNCOVERED, trail=trail,
    ) == 1
    args, kwargs = _decision(trail)
    assert kwargs["reason"] == "no self-review yet"
    data = kwargs["data"]
    assert data["outcome"] == "refused"
    assert data["head_sha"] == git.client.head_sha(h.wt)
    assert data["review_file"] == "/reviews/missing.md"
    assert data["dry_run"] is False
    assert data["covered"] is False


def test_dry_run_warns_without_refusing(h, capsys):
    h.repo()
    trail = MagicMock()
    opts = CreateOptions(dry_run=True)
    assert run_create_ungated(
        h.ctx(), opts, self_review=UNCOVERED, trail=trail,
    ) == 0
    err = capsys.readouterr().err
    assert "⚠ no self-review yet — pr create without --dry-run would refuse" in err
    assert "pr review --self --fix" in err
    assert h.events == ["ai"]
    _, kwargs = _decision(trail)
    assert kwargs["data"]["outcome"] == "preview"
    assert kwargs["data"]["dry_run"] is True


def test_run_create_without_a_gate_is_type_error(h):
    h.repo()
    with pytest.raises(TypeError):
        run_create_ungated(h.ctx(), CreateOptions())
