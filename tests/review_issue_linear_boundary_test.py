"""Linear filing refuses cross-group references in title and body."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import REPO_ROOT, make_ctx, remote_repo

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc
import gh.owners
import review.deferred_issue
import review.finding_issue
import review.issue
from pr.thread_models import CommentItem
from review.deferred_issue import TRACK_ALL
from review.issue import IssueProviderInfo, create_issue, update_issue
from review.record import Disposition, RecordFinding, ReviewRecord
from review.store import write_review
from review.types import ReviewMeta

CROSS_REF = "see me/lib#12"
OWN_REMOTE = "git@github.com:otto-nation/svc.git"
OTHER_REMOTE = "git@github.com:me/notes.git"
OWNERS = {"work": ["otto-nation"], "personal": ["me"]}


@pytest.fixture(autouse=True)
def _publishing_allowed(publishing_on):
    """These cover what a write does once it is allowed."""


def _linear_spy(monkeypatch):
    """Record every ``linear`` argv; never spawn the real CLI.

    Other commands (git remotes for ``check_text``) keep the real runner.
    """
    calls: list[list[str]] = []
    real = core.proc.run

    def fake_run(cmd, **kwargs):
        name = Path(str(cmd[0])).name if cmd else ""
        if name != "linear":
            return real(cmd, **kwargs)
        calls.append(list(cmd))
        if "create" in cmd:
            return core.proc.CmdResult(stdout="Created ENG-456: title")
        if "view" in cmd:
            return core.proc.CmdResult(
                stdout='{"url": "https://linear.app/team/issue/ENG-456"}')
        return core.proc.CmdResult(stdout="[]")

    monkeypatch.setattr(review.issue.core.proc, "run", fake_run)
    return calls


def _groups(github_owners):
    github_owners(OWNERS)


def _job(path, pr_number="7"):
    return SimpleNamespace(
        repo="owner/repo",
        pr_number=pr_number,
        host="",
        wt_path=str(path),
        artifact_dir=str(path),
        pr=SimpleNamespace(head="feat/x"),
    )


def _seed(path, *findings):
    write_review(
        path / "review.md",
        ReviewRecord(findings=findings),
        ReviewMeta(repo="owner/repo"),
    )


def _finding(summary):
    return RecordFinding(
        id="M1", severity="M", path="a.py", line=1,
        body=summary, disposition=Disposition.UNREACHED,
        reason="", checkbox=True,
    )


def _linear_info():
    return IssueProviderInfo(name="linear", options={"team": "ENG"})


def _file_findings(path, summary):
    _seed(path, _finding(summary))
    info = _linear_info()
    with patch.object(review.issue, "ensure_issue_provider", return_value=info), \
            patch.object(review.issue, "load_issue_provider", return_value=info):
        return review.finding_issue.file_findings(path, _job(path), TRACK_ALL)


def _deferred(path, body):
    return review.deferred_issue.create_deferred_issue(
        "linear", body, make_ctx(worktree_root=path), "owner/repo", 42,
        {"team": "ENG"}, None, publishing_open=True,
    )


def _deferred_update(path, body):
    return review.deferred_issue.update_deferred_issue(
        "linear", "ENG-1", body, "owner/repo", None, cwd=path,
    )


def _create_or_update(path, body):
    info = _linear_info()
    entry = CommentItem(id="t1", summary=body, file="a.go", line=1, reason=body)
    with patch.object(review.issue, "ensure_issue_provider", return_value=info):
        return review.deferred_issue.create_or_update_deferred_issue(
            [entry], "owner/repo", 42, {}, make_ctx(worktree_root=path),
            "ENG-1", None,
        )


def test_a_cross_group_body_never_reaches_linear(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        create_issue("linear", "ENG", "title", CROSS_REF, cwd=own)
    assert "me/lib#12" in str(exc.value)
    assert exc.value.verdict.references
    assert calls == []


def test_the_cross_ref_override_runs_linear(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    result = create_issue("linear", "ENG", "title", CROSS_REF, cwd=own)
    assert result.filed is True
    assert any("create" in cmd for cmd in calls)
    err = capsys.readouterr().err
    assert "me/lib#12" in err
    assert f"{gh.owners.ALLOW_CROSS_REF_ENV}=1" in err


def test_text_with_no_grouped_references_runs_linear(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    result = create_issue("linear", "ENG", "title", "plain body", cwd=own)
    assert result.filed is True
    assert any("create" in cmd for cmd in calls)


def test_unconfigured_owners_allow_linear_filing(tmp_path, monkeypatch):
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    result = create_issue("linear", "ENG", "title", CROSS_REF, cwd=own)
    assert result.filed is True
    assert any("create" in cmd for cmd in calls)


def test_the_same_text_is_allowed_in_the_referenced_group(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    same = remote_repo(tmp_path / "same", OTHER_REMOTE)
    result = create_issue("linear", "ENG", "title", CROSS_REF, cwd=same)
    assert result.filed is True
    assert any("create" in cmd for cmd in calls)


def test_an_unresolvable_cwd_refuses_a_grouped_reference(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    missing = tmp_path / "not-a-repo"
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        create_issue("linear", "ENG", "title", CROSS_REF, cwd=missing)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_file_findings_refuses_a_cross_group_body(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        _file_findings(own, CROSS_REF)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_file_findings_uses_the_review_worktree(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    same = remote_repo(tmp_path / "same", OTHER_REMOTE)
    result = _file_findings(same, CROSS_REF)
    assert result is not None and result.filed is True
    assert any("create" in cmd for cmd in calls)


def test_create_deferred_issue_refuses_a_cross_group_body(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        _deferred(own, CROSS_REF)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_create_deferred_issue_uses_the_review_worktree(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    same = remote_repo(tmp_path / "same", OTHER_REMOTE)
    result = _deferred(same, CROSS_REF)
    assert result.filed is True
    assert any("create" in cmd for cmd in calls)


def test_a_cross_group_title_never_reaches_linear(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        create_issue("linear", "ENG", CROSS_REF, "plain body", cwd=own)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_a_refused_body_never_creates_labels(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError):
        create_issue(
            "linear", "ENG", "title", CROSS_REF, cwd=own,
            opts={"team": "ENG", "labels": ["follow-up"]},
        )
    assert calls == []


def test_a_malformed_owners_map_is_not_lifted_by_the_override(
        github_owners, tmp_path, monkeypatch):
    github_owners({"work": ["acme"], "personal": ["ACME"]})
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        create_issue("linear", "ENG", "title", CROSS_REF, cwd=own)
    assert not exc.value.verdict.references
    assert calls == []


def test_an_update_with_a_cross_group_body_never_reaches_linear(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        update_issue("linear", "ENG-1", CROSS_REF, cwd=own)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_the_cross_ref_override_runs_linear_update(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    assert update_issue("linear", "ENG-1", CROSS_REF, cwd=own) is True
    assert any("update" in cmd for cmd in calls)
    err = capsys.readouterr().err
    assert "me/lib#12" in err
    assert f"{gh.owners.ALLOW_CROSS_REF_ENV}=1" in err


def test_an_update_with_no_grouped_references_runs_linear(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    assert update_issue("linear", "ENG-1", "plain body", cwd=own) is True
    assert any("update" in cmd for cmd in calls)


def test_update_deferred_issue_refuses_a_cross_group_body(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        _deferred_update(own, CROSS_REF)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_update_deferred_issue_uses_the_review_worktree(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    same = remote_repo(tmp_path / "same", OTHER_REMOTE)
    result = _deferred_update(same, CROSS_REF)
    assert result.filed is True
    assert any("update" in cmd for cmd in calls)


def test_create_or_update_deferred_issue_refuses_a_cross_group_body(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    own = remote_repo(tmp_path / "own", OWN_REMOTE)
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        _create_or_update(own, CROSS_REF)
    assert "me/lib#12" in str(exc.value)
    assert calls == []


def test_create_or_update_deferred_issue_uses_the_review_worktree(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    calls = _linear_spy(monkeypatch)
    same = remote_repo(tmp_path / "same", OTHER_REMOTE)
    result = _create_or_update(same, CROSS_REF)
    assert result.filed is True
    assert any("update" in cmd for cmd in calls)
