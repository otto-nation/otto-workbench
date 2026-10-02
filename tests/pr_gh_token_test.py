"""pr.gh_token — the one owner of GH_TOKEN resolution for publishing commands.

Ported from tests/load_gh_token.bats when the bash resolver became a shim.
"""

import sys
from pathlib import Path

import pytest

from conftest import run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.gh_token
from pr.gh_token import Token, TokenNotConfigured, TokenSource


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "task").mkdir(parents=True)
    return h


def _global(home: Path, text: str) -> None:
    (home / ".config" / "task" / "taskfile.env").write_text(text)


def _repo(tmp_path: Path, origin: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_checked(["git", "init", "-q", str(repo)])
    if origin:
        run_checked(["git", "-C", str(repo), "remote", "add", "origin", origin])
    return repo


def _local(repo: Path, text: str) -> None:
    (repo / ".taskfile").mkdir()
    (repo / ".taskfile" / "taskfile.env").write_text(text)


# --- tier 5: nothing configured ---------------------------------------------

def test_fails_with_no_env_file_and_no_environment(tmp_path, home):
    with pytest.raises(TokenNotConfigured):
        pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home)


def test_a_file_without_the_key_is_not_configured(tmp_path, home):
    _global(home, "AI_COMMAND=claude\n")
    with pytest.raises(TokenNotConfigured):
        pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home)


def test_a_commented_out_key_is_not_configured(tmp_path, home):
    _global(home, "# GH_TOKEN=ghp_old\n")
    with pytest.raises(TokenNotConfigured):
        pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home)


def test_guidance_names_the_global_file(tmp_path, home):
    with pytest.raises(TokenNotConfigured) as exc:
        pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home)
    assert str(home / ".config" / "task" / "taskfile.env") in exc.value.guidance


def test_guidance_links_public_github_by_default(tmp_path, home):
    with pytest.raises(TokenNotConfigured) as exc:
        pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home)
    assert "https://github.com/settings/tokens/new" in exc.value.guidance


def test_guidance_links_the_enterprise_instance(tmp_path, home):
    repo = _repo(tmp_path, "git@ghe.acme.com:acme/widget.git")
    with pytest.raises(TokenNotConfigured) as exc:
        pr.gh_token.resolve(repo, environ={}, home=home)
    assert "https://ghe.acme.com/settings/tokens/new" in exc.value.guidance


def test_guidance_stays_on_github_for_an_ssh_alias(tmp_path, home):
    repo = _repo(tmp_path, "gitbox:acme/widget.git")
    with pytest.raises(TokenNotConfigured) as exc:
        pr.gh_token.resolve(repo, environ={}, home=home)
    assert "https://github.com/settings/tokens/new" in exc.value.guidance


def test_guidance_names_the_org_variable(tmp_path, home):
    repo = _repo(tmp_path, "git@github.com:otto-nation/widget.git")
    with pytest.raises(TokenNotConfigured) as exc:
        pr.gh_token.resolve(repo, environ={}, home=home)
    assert "GH_TOKEN__OTTO_NATION" in exc.value.guidance


# --- tiers 1-4 ---------------------------------------------------------------

def test_default_token_from_the_global_file(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_default\n")
    assert pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home) == Token(
        "ghp_default", TokenSource.DEFAULT, "GH_TOKEN")


def test_value_is_taken_verbatim_after_the_first_equals(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_a=b/c+d\n")
    assert pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home).value == "ghp_a=b/c+d"


def test_first_matching_line_wins(tmp_path, home):
    _global(home, "GH_TOKEN=first\nGH_TOKEN=second\n")
    assert pr.gh_token.resolve(_repo(tmp_path), environ={}, home=home).value == "first"


def test_environment_is_the_last_resort(tmp_path, home):
    token = pr.gh_token.resolve(_repo(tmp_path), environ={"GH_TOKEN": "ghp_env"}, home=home)
    assert token == Token("ghp_env", TokenSource.ENVIRONMENT, "GH_TOKEN")


def test_an_empty_environment_value_is_not_a_token(tmp_path, home):
    with pytest.raises(TokenNotConfigured):
        pr.gh_token.resolve(_repo(tmp_path), environ={"GH_TOKEN": ""}, home=home)


def test_empty_local_pin_falls_through_to_global_default(tmp_path, home):
    repo = _repo(tmp_path)
    _local(repo, "GH_TOKEN=\n")
    _global(home, "GH_TOKEN=ghp_default\n")
    assert pr.gh_token.resolve(repo, environ={}, home=home) == Token(
        "ghp_default", TokenSource.DEFAULT, "GH_TOKEN")


def test_empty_org_value_falls_through_to_default(tmp_path, home):
    repo = _repo(tmp_path, "git@github.com:otto-nation/widget.git")
    _global(home, "GH_TOKEN__OTTO_NATION=\nGH_TOKEN=ghp_default\n")
    assert pr.gh_token.resolve(repo, environ={}, home=home) == Token(
        "ghp_default", TokenSource.DEFAULT, "GH_TOKEN")


def test_empty_default_falls_through_to_environment(tmp_path, home):
    _global(home, "GH_TOKEN=\n")
    token = pr.gh_token.resolve(
        _repo(tmp_path), environ={"GH_TOKEN": "ghp_env"}, home=home)
    assert token == Token("ghp_env", TokenSource.ENVIRONMENT, "GH_TOKEN")


def test_the_file_beats_the_environment(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_file\n")
    token = pr.gh_token.resolve(_repo(tmp_path), environ={"GH_TOKEN": "ghp_env"}, home=home)
    assert token.value == "ghp_file"


def test_local_pin_beats_global(tmp_path, home):
    repo = _repo(tmp_path)
    _global(home, "GH_TOKEN=ghp_global\n")
    _local(repo, "GH_TOKEN=ghp_local\n")
    assert pr.gh_token.resolve(repo, environ={}, home=home) == Token(
        "ghp_local", TokenSource.LOCAL, "GH_TOKEN")


@pytest.mark.parametrize("origin", [
    "git@github.com:otto-nation/widget.git",
    "https://github.com/otto-nation/widget.git",
    "https://user@github.com/otto-nation/widget.git",
    "ssh://git@github.com:22/otto-nation/widget.git",
    "git@ghe.acme.com:otto-nation/widget.git",
    "https://ghe.acme.com/otto-nation/widget",
    "gitbox:otto-nation/widget.git",
])
def test_org_token_for_every_remote_spelling(tmp_path, home, origin):
    _global(home, "GH_TOKEN=ghp_default\nGH_TOKEN__OTTO_NATION=ghp_org\n")
    token = pr.gh_token.resolve(_repo(tmp_path, origin), environ={}, home=home)
    assert token == Token("ghp_org", TokenSource.ORG, "GH_TOKEN__OTTO_NATION")


def test_falls_back_to_default_without_an_org_token(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_default\nGH_TOKEN__OTHER=ghp_other\n")
    repo = _repo(tmp_path, "git@github.com:otto-nation/widget.git")
    assert pr.gh_token.resolve(repo, environ={}, home=home).source is TokenSource.DEFAULT


def test_local_pin_beats_the_org_token(tmp_path, home):
    repo = _repo(tmp_path, "git@github.com:otto-nation/widget.git")
    _global(home, "GH_TOKEN__OTTO_NATION=ghp_org\n")
    _local(repo, "GH_TOKEN=ghp_local\n")
    assert pr.gh_token.resolve(repo, environ={}, home=home).source is TokenSource.LOCAL


@pytest.mark.parametrize("origin", [None, "/srv/git/widget.git", "file:///srv/git/widget.git"])
def test_no_org_without_a_hosted_origin(tmp_path, home, origin):
    _global(home, "GH_TOKEN=ghp_default\nGH_TOKEN__SRV=ghp_wrong\n")
    token = pr.gh_token.resolve(_repo(tmp_path, origin), environ={}, home=home)
    assert token.source is TokenSource.DEFAULT


def test_outside_a_repo_uses_the_default(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_default\n")
    outside = tmp_path / "plain"
    outside.mkdir()
    assert pr.gh_token.resolve(outside, environ={}, home=home).value == "ghp_default"


# --- deltas -------------------------------------------------------------------

def test_a_local_file_without_a_token_does_not_hide_global_tokens(tmp_path, home):
    # Bash's _resolve_env_file swapped the global file out for the local one
    # whenever the local one existed, so this resolved to nothing.
    repo = _repo(tmp_path, "git@github.com:otto-nation/widget.git")
    _local(repo, "OTHER=1\n")
    _global(home, "GH_TOKEN__OTTO_NATION=ghp_org\n")
    assert pr.gh_token.resolve(repo, environ={}, home=home).value == "ghp_org"


@pytest.mark.parametrize("org,expected", [
    ("otto-nation", "GH_TOKEN__OTTO_NATION"),
    ("ACME", "GH_TOKEN__ACME"),
    ("a-b-c", "GH_TOKEN__A_B_C"),
])
def test_org_variable(org, expected):
    assert pr.gh_token.org_variable(org) == expected


SCRIPT = LIB_DIR / "pr" / "gh_token.py"


def _run_script(cwd: Path, home: Path, extra_env: dict[str, str] | None = None):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    env.update(extra_env or {})
    return run_checked([sys.executable, str(SCRIPT), "--cwd", str(cwd)], env=env, check=False)


def test_script_prints_only_the_token(tmp_path, home):
    _global(home, "GH_TOKEN=ghp_default\n")
    r = _run_script(_repo(tmp_path), home)
    assert r.returncode == 0
    assert r.stdout == "ghp_default\n"


def test_script_failure_keeps_stdout_empty(tmp_path, home):
    r = _run_script(_repo(tmp_path), home)
    assert r.returncode == 1
    assert r.stdout == ""
    assert "GH_TOKEN not configured" in r.stderr


def test_script_runs_from_any_cwd_with_a_hostile_pythonpath(tmp_path, home):
    # The mise-shim case git/push.py documents: PYTHONPATH replaced wholesale.
    _global(home, "GH_TOKEN=ghp_default\n")
    r = _run_script(_repo(tmp_path), home, {"PYTHONPATH": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout == "ghp_default\n"
