"""Owner checks on commit messages bound for an ssh-aliased Enterprise remote."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import fix.engine
import gh.owner_hooks
import gh.owners
import git.client
import git.land
import git.push
import pr.branch_sync
import rebase.land
import rebase.prepush
import review.closeout
from git.land import CommitStatus

from conftest import git_in, git_out, init_repo, remote_repo
from fix_engine_support import StubAdapter, _run, head, landed, snapshots  # noqa: F401

ENTERPRISE_ALIAS = "git@ghebox:platform/svc.git"
OWN = "git@github.com:otto-nation/svc.git"
OFFENCE = "see me/lib#12"
ZERO = "0" * 40


@pytest.fixture(autouse=True)
def _identity_ssh(ssh_hosts):
    ssh_hosts({})


def test_message_to_an_enterprise_alias_is_scanned(github_owners, ssh_hosts, tmp_path):
    github_owners({"work": ["ghe.corp.example/platform"], "personal": ["me"]})
    ssh_hosts({"ghebox": "ghe.corp.example"})
    repo = remote_repo(tmp_path / "r", ENTERPRISE_ALIAS)
    verdict = gh.owner_hooks.check_message(OFFENCE, repo)
    assert verdict.outcome is gh.owner_hooks.HookOutcome.REFUSE


def test_message_to_an_alias_resolving_nowhere_known_is_allowed(github_owners, ssh_hosts, tmp_path):
    github_owners({"work": ["ghe.corp.example/platform"], "personal": ["me"]})
    ssh_hosts({"ghebox": "gitlab.example.org"})
    repo = remote_repo(tmp_path / "r", ENTERPRISE_ALIAS)
    verdict = gh.owner_hooks.check_message(OFFENCE, repo)
    assert verdict.outcome is gh.owner_hooks.HookOutcome.ALLOW


def _repo(tmp_path):
    repo = init_repo(tmp_path / "r")
    git_in(repo, "remote", "add", "origin", OWN)
    return repo


def _commit(repo, message):
    git_in(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git_out(repo, "rev-parse", "HEAD").strip()


def _outgoing(repo, message):
    sha = _commit(repo, message)
    ref = "refs/heads/main"
    return git.push.Outgoing("origin", OWN, (f"{ref} {sha} {ref} {ZERO}",))


def _groups(github_owners):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})


def test_refuse_outgoing_raises_on_a_cross_group_commit(github_owners, tmp_path):
    _groups(github_owners)
    repo = _repo(tmp_path)
    with pytest.raises(gh.owners.OwnerBoundaryError) as raised:
        gh.owner_hooks.refuse_outgoing(repo, _outgoing(repo, f"feat: x\n\n{OFFENCE}"))
    assert "me/lib#12" in str(raised.value)


def test_refuse_outgoing_allows_and_warns_when_the_check_cannot_run(github_owners, tmp_path, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    outgoing = git.push.Outgoing("origin", OWN, ("not a ref line",))
    gh.owner_hooks.refuse_outgoing(repo, outgoing)
    assert capsys.readouterr().err.strip()


def test_refuse_outgoing_honours_the_cross_ref_override(github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    repo = _repo(tmp_path)
    gh.owner_hooks.refuse_outgoing(repo, _outgoing(repo, OFFENCE))
    assert "me/lib#12" in capsys.readouterr().err


def test_refuse_outgoing_allows_a_clean_push(github_owners, tmp_path, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    gh.owner_hooks.refuse_outgoing(repo, _outgoing(repo, "feat: clean"))
    assert capsys.readouterr().err == ""


# ── every workbench push asks ───────────────────────────────────────────────

_PUSHED = git.push.PushResult(git.push.PushStatus.PUSHED, "abc1234", "main")
_HELD = git.push.PushResult(git.push.PushStatus.HELD, "abc1234", "main")
_LANDED = git.land.LandResult(CommitStatus.PUSHED, "abc1234")


def _is_the_check(kwargs):
    return kwargs["owner_check"] is gh.owner_hooks.refuse_outgoing


def test_branch_sync_pushes_with_the_owner_check(tmp_path):
    with patch.object(git.push, "push", return_value=_PUSHED) as push:
        pr.branch_sync._push(
            tmp_path, "feat", remote="origin", args=(), outcome=pr.branch_sync.SyncOutcome.PUSHED,
            message="", log=print, trail=None)
    assert _is_the_check(push.call_args.kwargs)


def test_closeout_pushes_held_fixes_with_the_owner_check(tmp_path):
    state = SimpleNamespace(fix=SimpleNamespace(fix=SimpleNamespace(
        commit_sha="abc1234", commit_status=CommitStatus.PUSH_HELD)))
    with patch.object(git.push, "holds", return_value=False), \
         patch.object(git.push, "push", return_value=_HELD) as push, \
         patch.object(git.push, "report"):
        review.closeout.push_held_commit(state, tmp_path)
    assert _is_the_check(push.call_args.kwargs)


def test_the_fix_engine_lands_with_the_owner_check(tmp_path, landed, head):  # noqa: F811
    _run(StubAdapter(tmp_path, count=1))
    assert _is_the_check(landed.call_args.kwargs)


def test_a_rebase_push_lands_head_with_the_owner_check(tmp_path):
    with patch.object(git.land, "land_head", return_value=_LANDED) as land_head:
        rebase.land.land_rebased(str(tmp_path), args=("--force-with-lease",))
    assert _is_the_check(land_head.call_args.kwargs)


def test_a_rebuilt_regeneration_lands_with_the_owner_check(tmp_path):
    generated = rebase.prepush.GeneratedFix(excluded=["gen.txt"], rebuilt=True)
    with patch.object(git.land, "land", return_value=_LANDED) as land:
        rebase.prepush._land_rebuild(str(tmp_path), generated, args=())
    assert _is_the_check(land.call_args.kwargs)
