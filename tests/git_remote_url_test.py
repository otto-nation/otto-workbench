"""git.remote_url: one reading of a remote URL, and the remotes a checkout has."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git.remote_url

from conftest import git_in, remote_repo

VECTORS = [
    ("https://github.com/acme/widget.git", "acme/widget.git", True, "github.com", False),
    ("https://user:tok@ghe.corp:8443/acme/widget", "acme/widget", True, "ghe.corp", False),
    ("git@github.com:acme/widget.git", "acme/widget.git", True, "github.com", True),
    ("git@github.com-work:acme/widget.git", "acme/widget.git", True, "github.com-work", True),
    ("gitbox:acme/widget.git", "acme/widget.git", True, "", True),
    ("ssh://git@ghe.corp:2222/acme/widget.git", "acme/widget.git", True, "ghe.corp", True),
    ("file://localhost/srv/git/widget.git", "srv/git/widget.git", False, "", False),
    ("/srv/git/widget.git", "/srv/git/widget.git", False, "", False),
]


@pytest.mark.parametrize("url,path,hosted,host,ssh", VECTORS)
def test_parse_reads_path_host_and_transport(url, path, hosted, host, ssh):
    assert git.remote_url.parse(url) == git.remote_url.RemoteUrl(path, hosted, host, ssh)


def test_remotes_lists_every_fetch_url(tmp_path):
    repo = remote_repo(tmp_path / "fork", "git@github.com:me/widget.git")
    git_in(repo, "remote", "add", "upstream", "https://github.com/acme/widget.git")
    found = git.remote_url.remotes(repo)
    assert found == [
        git.remote_url.Remote("origin", "git@github.com:me/widget.git"),
        git.remote_url.Remote("upstream", "https://github.com/acme/widget.git"),
    ]


def test_remotes_is_empty_for_a_repo_without_any(tmp_path):
    repo = tmp_path / "bare"
    repo.mkdir()
    git_in(repo, "init", "-q")
    assert git.remote_url.remotes(repo) == []


def test_remotes_is_none_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git.remote_url.remotes(plain) is None


@pytest.mark.parametrize("url,alias", [
    ("git@ghebox:a/b", "ghebox"),
    ("ssh://git@ghebox:2222/a/b", "ghebox"),
    ("git@ghe.x:a/b", "ghe.x"),
    ("https://github.com/a/b", ""),
])
def test_ssh_alias_names_both_ssh_spellings(url, alias):
    assert git.remote_url.ssh_alias(url) == alias


def test_ssh_hostname_reads_ssh_dash_g(ssh_hosts):
    log = ssh_hosts({"ghebox": "GHE.Corp.Example"})
    assert git.remote_url.ssh_hostname("ghebox") == "ghe.corp.example"
    assert log.read_text().strip() == "-G ghebox"


def test_ssh_hostname_is_empty_when_ssh_fails_or_is_missing(ssh_hosts, monkeypatch, tmp_path):
    ssh_hosts({}, fail=True)
    assert git.remote_url.ssh_hostname("ghebox") == ""
    git.remote_url.ssh_hostname.cache_clear()
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert git.remote_url.ssh_hostname("ghebox") == ""


def test_ssh_hostname_never_passes_an_option_shaped_alias(ssh_hosts, tmp_path):
    log = ssh_hosts({})
    assert git.remote_url.ssh_hostname("-oProxyCommand=x") == ""
    assert git.remote_url.ssh_hostname("") == ""
    assert not log.exists()


def test_ssh_hostname_asks_once_per_alias(ssh_hosts):
    log = ssh_hosts({"ghebox": "ghe.corp.example"})
    git.remote_url.ssh_hostname("ghebox")
    git.remote_url.ssh_hostname("ghebox")
    assert len(log.read_text().splitlines()) == 1
