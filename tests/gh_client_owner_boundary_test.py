"""gh.client refuses a GitHub write outside our owners before gh runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import cli.boundary
import cli.locks
import cli.pr_describe
import cli.review_orchestrate
import fix.comment_replies
import gh.client
import gh.owners
import pr.attribution
import pr.comments
import pr.context
import pr.describe
import pr.gh_token
import pr.thread_models
import pr.thread_replies
import review.orchestrate
from core.proc import CmdResult
from git.land import CommitStatus

from conftest import remote_repo


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch):
    monkeypatch.delenv(gh.owners.ALLOW_UPSTREAM_ENV, raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.delenv("GH_REPO", raising=False)


def test_an_upstream_write_never_reaches_gh(stub_gh, github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    calls = stub_gh("echo '{}'")
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        gh.client.api("repos/stranger/lib/issues", method="POST", input_text="{}")
    assert "stranger" in str(exc.value)
    assert not calls.exists()


def test_an_own_write_runs(stub_gh, github_owners):
    github_owners({"work": ["otto-nation"]})
    calls = stub_gh("echo '{}'")
    assert gh.client.api("repos/otto-nation/svc/issues", method="POST", input_text="{}").ok
    assert "repos/otto-nation/svc/issues" in calls.read_text()


def test_unconfigured_refuses_and_names_the_fix(stub_gh):
    stub_gh("echo '{}'")
    with pytest.raises(gh.owners.OwnerBoundaryError) as exc:
        gh.client.run("issue", "create", "--repo", "otto-nation/svc", "--title", "x")
    assert gh.owners.FIX_COMMAND in str(exc.value)


def test_a_read_is_never_checked(stub_gh):
    calls = stub_gh("echo '[]'")
    assert gh.client.api("repos/stranger/lib/issues").ok
    assert calls.exists()


def test_pr_create_is_checked_against_the_worktree_remotes(stub_gh, github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    stub_gh("echo https://github.com/x/y/pull/1")
    upstream = remote_repo(tmp_path / "up", "git@github.com:stranger/lib.git")
    with pytest.raises(gh.owners.OwnerBoundaryError):
        gh.client.run("pr", "create", "--title", "t", "--body-file", "-", cwd=upstream, input_text="b")
    own = remote_repo(tmp_path / "own", "git@github.com:otto-nation/svc.git")
    assert gh.client.run("pr", "create", "--title", "t", "--body-file", "-", cwd=own, input_text="b").ok


def test_a_graphql_mutation_is_checked_against_its_repo_hint(stub_gh, github_owners):
    github_owners({"work": ["otto-nation"]})
    stub_gh("echo '{}'")
    document = '{"query": "mutation { resolveReviewThread(input: {threadId: \\"x\\"}) { thread { id } } }"}'
    with pytest.raises(gh.owners.OwnerBoundaryError):
        gh.client.graphql("", input_text=document, target_repo="stranger/lib")
    assert gh.client.graphql("", input_text=document, target_repo="otto-nation/svc").ok


def test_an_enterprise_repo_flag_is_matched_on_its_host(stub_gh, github_owners):
    github_owners({"work": ["ghe.corp/platform"]})
    stub_gh("echo ok")
    assert gh.client.run("issue", "create", "--repo", "ghe.corp/platform/svc", "--title", "x").ok
    with pytest.raises(gh.owners.OwnerBoundaryError):
        gh.client.run("issue", "create", "--repo", "platform/svc", "--title", "x")


def test_the_users_override_lets_an_upstream_write_through(stub_gh, github_owners, monkeypatch, capsys):
    github_owners({"work": ["otto-nation"]})
    calls = stub_gh("echo '{}'")
    monkeypatch.setenv(gh.owners.ALLOW_UPSTREAM_ENV, "1")
    assert gh.client.api("repos/stranger/lib/issues", method="POST", input_text="{}").ok
    assert calls.exists()
    err = capsys.readouterr().err
    assert "stranger" in err
    assert f"{gh.owners.ALLOW_UPSTREAM_ENV}=1" in err


def test_an_entry_point_reports_a_refusal_as_exit_one(capsys):
    verdict = gh.owners.Verdict.refuse("stranger is not one of our GitHub owners")

    @cli.boundary.reports_owner_boundary
    def main(argv):
        raise gh.owners.OwnerBoundaryError(verdict)

    assert main([]) == cli.boundary.OWNER_BOUNDARY_EXIT
    assert cli.boundary.OWNER_BOUNDARY_EXIT == 1
    assert "stranger is not one of our GitHub owners" in capsys.readouterr().err


def test_resolve_thread_passes_the_repo_hint(monkeypatch, publishing_on):
    seen: list[str] = []

    def fake_graphql(query, *, input_text=None, target_repo="", **kwargs):
        seen.append(target_repo)
        return CmdResult(0, stdout="{}")

    monkeypatch.setattr(gh.client, "graphql", fake_graphql)
    assert pr.comments.resolve_thread("PRRT_1", repo="otto-nation/svc") is True
    assert seen == ["otto-nation/svc"]
    seen.clear()
    assert pr.comments.resolve_thread("PRRT_1") is True
    assert seen == [""]


def test_a_decorated_cli_main_maps_a_refusal_to_exit_one(capsys, monkeypatch):
    verdict = gh.owners.Verdict.refuse("stranger is not one of our GitHub owners")

    def refuse(**kwargs):
        raise gh.owners.OwnerBoundaryError(verdict)

    monkeypatch.setattr(pr.context, "resolve", refuse)
    assert cli.pr_describe.main([]) == cli.boundary.OWNER_BOUNDARY_EXIT
    err = capsys.readouterr().err
    assert "stranger is not one of our GitHub owners" in err
    assert "Refused at the owner boundary" in err


def _trail():
    class Trail:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def error(self, action, detail, data=None):
            self.calls.append((action, detail))

    return Trail()


def test_reraise_trails_a_refusal_as_policy():
    trail = _trail()
    refusal = gh.owners.OwnerBoundaryError(
        gh.owners.Verdict.refuse("stranger is not one of our GitHub owners"),
    )
    with pytest.raises(gh.owners.OwnerBoundaryError):
        try:
            raise refusal
        except Exception as exc:
            cli.boundary.reraise(trail, exc)
    assert trail.calls == [("owner_boundary", "stranger is not one of our GitHub owners")]


def test_reraise_leaves_every_other_failure_to_the_shared_recorder():
    trail = _trail()
    with pytest.raises(RuntimeError):
        try:
            raise RuntimeError("boom")
        except Exception as exc:
            cli.boundary.reraise(trail, exc)
    assert trail.calls == [("unexpected_error", "boom")]


def test_review_orchestrate_maps_a_refusal_to_exit_one(capsys, monkeypatch, tmp_path):
    verdict = gh.owners.Verdict.refuse("stranger is not one of our GitHub owners")

    def refuse(*args, **kwargs):
        raise gh.owners.OwnerBoundaryError(verdict)

    monkeypatch.setattr(review.orchestrate, "run_orchestrate", refuse)
    review_file = tmp_path / "review.md"
    review_file.write_text("# review\n")
    rc = cli.review_orchestrate.main([
        "--pr", "1",
        "--review-file", str(review_file),
        "--repo-dir", str(tmp_path),
        "--repo", "stranger/lib",
    ])
    assert rc == cli.boundary.OWNER_BOUNDARY_EXIT
    err = capsys.readouterr().err
    assert "stranger is not one of our GitHub owners" in err
    assert "Refused at the owner boundary" in err


def test_stub_gh_puts_yq_on_path_wherever_it_lives(stub_gh, monkeypatch, tmp_path):
    yq_dir = tmp_path / "opt-homebrew-bin"
    yq_dir.mkdir()
    yq = yq_dir / "yq"
    yq.write_text("#!/bin/sh\nexit 0\n")
    yq.chmod(0o755)
    monkeypatch.setenv("PATH", f"{yq_dir}:/usr/bin:/bin")
    stub_gh("echo ok")
    assert str(yq_dir) in os.environ["PATH"].split(":")


def test_settle_fixed_hints_an_enterprise_host(monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(pr.thread_replies, "reply_to_fixed", lambda *a, **k: ())
    monkeypatch.setattr(
        pr.comments, "resolve_thread",
        lambda tid, repo="": seen.append(repo) or True,
    )
    item = pr.thread_models.CommentItem(id="t1")
    threads = {"t1": pr.thread_models.ReportThread(id="t1")}
    cp = pr.attribution.CommitPushResult(
        sha="abc", status=CommitStatus.PUSHED, error="",
    )
    fix.comment_replies.settle_fixed(
        [item], threads, repo="acme/svc", pr_number=1, cp=cp,
        wt_path=tmp_path, host="ghe.corp",
    )
    assert seen == ["ghe.corp/acme/svc"]


def test_a_cli_write_refusal_propagates_from_the_client(
    stub_gh, github_owners, tmp_path, monkeypatch, capsys,
):
    github_owners({"x": ["otto-nation"]})
    calls = stub_gh("echo ok")
    wt = tmp_path / "wt"
    wt.mkdir()
    ctx = pr.context.ResolvedContext(
        repo="stranger/lib",
        branch="feat",
        pr_number=7,
        worktree_root=wt,
        head_sha="deadbeef",
        target_dir=tmp_path / "target",
    )
    monkeypatch.setattr(pr.context, "resolve", lambda **kwargs: ctx)
    monkeypatch.setattr(cli.locks, "claim_declared", lambda *a, **k: None)
    monkeypatch.setattr(pr.gh_token, "ready_for_publishing", lambda worktree: True)
    body = "## Summary\n\nsame\n\n## Changes\n\nsame\n\n## Testing\n\nsame"
    monkeypatch.setattr(pr.describe, "_fetch_pr_body", lambda *a, **k: ("old title", body))
    rc = cli.pr_describe.main(["--post", "--title", "feat: x", "--body", body])
    assert rc == cli.boundary.OWNER_BOUNDARY_EXIT
    err = capsys.readouterr().err
    assert "Refused at the owner boundary" in err
    assert "stranger" in err
    written = calls.read_text() if calls.exists() else ""
    assert "pr edit" not in written
