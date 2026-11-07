"""Owner checks on commit messages bound for an ssh-aliased Enterprise remote."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh.owner_hooks

from conftest import remote_repo

ENTERPRISE_ALIAS = "git@ghebox:platform/svc.git"
OFFENCE = "see me/lib#12"


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
