"""owners-check --message-file / --push: commit-msg and pre-push hook modes."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import cli.owners_check
import gh.owners

from conftest import git_in, git_out, init_repo, remote_repo

OWN = "git@github.com:otto-nation/svc.git"
OTHER = "git@github.com:me/lib.git"
GITLAB = "https://gitlab.com/acme/widget.git"
GITLAB_SSH = "git@gitlab.com:acme/widget.git"
GITHUB_ALIAS = "git@github.com-work:otto-nation/svc.git"
ZERO = "0" * 40
OFFENCE = "see me/lib#12"
OWN_REF = "see otto-nation/svc#12"
UPSTREAM_REF = "see stranger/lib#12"
SCISSORS = "# ------------------------ >8 ------------------------"


def _groups(github_owners):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})


def _broken(github_owners):
    github_owners({"work": ["acme"], "personal": ["acme"]})


def _commit(repo, message: str) -> str:
    git_in(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git_out(repo, "rev-parse", "HEAD").strip()


def _repo(tmp_path, remote: str = OWN) -> Path:
    repo = init_repo(tmp_path / "r")
    git_in(repo, "remote", "add", "origin", remote)
    return repo


def _message(tmp_path, cwd, text: str) -> int:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text)
    return cli.owners_check.main(
        ["--message-file", str(path), "--cwd", str(cwd)])


def _push(monkeypatch, repo, lines: str, remote="origin", url=OWN) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(lines))
    return cli.owners_check.main(
        ["--push", remote, url, "--cwd", str(repo)])


def _line(local_sha: str, remote_sha: str = ZERO, ref="refs/heads/main") -> str:
    return f"{ref} {local_sha} {ref} {remote_sha}"


def test_message_file_refuses_another_group(github_owners, tmp_path, capsys):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, OFFENCE) == cli.owners_check.REFUSED
    assert "me/lib#12" in capsys.readouterr().err


def test_message_file_allows_own_group(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, OWN_REF) == 0


def test_message_file_allows_an_upstream_reference(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, UPSTREAM_REF) == 0


def test_message_file_allows_with_no_remote(github_owners, tmp_path):
    _groups(github_owners)
    repo = init_repo(tmp_path / "r")
    assert _message(tmp_path, repo, OFFENCE) == 0


def test_message_file_allows_a_non_github_remote(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", GITLAB)
    assert _message(tmp_path, repo, OFFENCE) == 0


def test_message_file_allows_outside_a_repo(github_owners, tmp_path):
    _groups(github_owners)
    assert _message(tmp_path, tmp_path, OFFENCE) == 0


def test_message_file_ignores_git_comment_lines(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, f"# {OFFENCE}\nok\n") == 0


def test_message_file_honours_the_override(github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, OFFENCE) == 0
    assert gh.owners.ALLOW_CROSS_REF_ENV in capsys.readouterr().err


def test_message_file_unconfigured_allows(tmp_path):
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, OFFENCE) == 0


def test_message_file_broken_config_is_cannot_run(github_owners, tmp_path, capsys):
    _broken(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, "hello") == cli.owners_check.CANNOT_RUN
    assert "acme" in capsys.readouterr().err


def test_message_file_unreadable_is_cannot_run(tmp_path, capsys):
    missing = tmp_path / "no-such-msg"
    code = cli.owners_check.main(
        ["--message-file", str(missing), "--cwd", str(tmp_path)])
    assert code == cli.owners_check.CANNOT_RUN
    assert capsys.readouterr().err


def test_push_refuses_a_new_commit_in_the_other_group(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, "flag this ref\n\n" + OFFENCE)
    code = _push(monkeypatch, repo, _line(sha) + "\n")
    assert code == cli.owners_check.REFUSED
    err = capsys.readouterr().err
    assert sha[:7] in err
    assert "flag this ref" in err
    assert "me/lib#12" in err


def test_push_does_not_rescan_a_commit_already_on_the_remote(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    old = _commit(repo, "old offence\n\n" + OFFENCE)
    git_in(repo, "update-ref", "refs/remotes/origin/main", old)
    new = _commit(repo, "clean follow-up")
    assert _push(monkeypatch, repo, _line(new, old) + "\n") == 0


def test_push_flags_a_new_offence_not_the_old_one(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    old = _commit(repo, "old offence\n\n" + OFFENCE)
    git_in(repo, "update-ref", "refs/remotes/origin/main", old)
    new = _commit(repo, "new offence\n\n" + OFFENCE)
    code = _push(monkeypatch, repo, _line(new, old) + "\n")
    assert code == cli.owners_check.REFUSED
    err = capsys.readouterr().err
    assert "new offence" in err
    assert "old offence" not in err
    assert new[:7] in err


def test_push_new_branch_with_zero_remote_sha(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    base = _commit(repo, "base")
    git_in(repo, "update-ref", "refs/remotes/origin/main", base)
    git_in(repo, "checkout", "-q", "-b", "feat")
    sha = _commit(repo, "feat offence\n\n" + OFFENCE)
    code = _push(
        monkeypatch, repo,
        _line(sha, ZERO, "refs/heads/feat") + "\n")
    assert code == cli.owners_check.REFUSED
    err = capsys.readouterr().err
    assert "feat offence" in err
    assert sha[:7] in err


def test_push_skips_a_delete(github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, OFFENCE)
    assert _push(monkeypatch, repo, _line(ZERO, sha) + "\n") == 0


def test_push_honours_the_override(github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    repo = _repo(tmp_path)
    sha = _commit(repo, OFFENCE)
    assert _push(monkeypatch, repo, _line(sha) + "\n") == 0
    assert gh.owners.ALLOW_CROSS_REF_ENV in capsys.readouterr().err


def test_push_unparseable_stdin_is_cannot_run(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    _commit(repo, "ok")
    code = _push(monkeypatch, repo, "not-enough-fields\n")
    assert code == cli.owners_check.CANNOT_RUN
    assert capsys.readouterr().err


def test_push_ignores_blank_stdin_lines(github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, "ok")
    assert _push(monkeypatch, repo, f"\n{_line(sha)}\n\n") == 0


def test_push_non_github_destination_allows(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, OFFENCE)
    assert _push(monkeypatch, repo, _line(sha) + "\n", url=GITLAB) == 0


def test_push_destination_in_the_other_group_is_refused(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, "other dest\n\n" + OWN_REF)
    code = _push(monkeypatch, repo, _line(sha) + "\n", url=OTHER)
    assert code == cli.owners_check.REFUSED
    err = capsys.readouterr().err
    assert sha[:7] in err
    assert "other dest" in err


def test_push_broken_config_is_cannot_run_not_refused(
        github_owners, tmp_path, monkeypatch, capsys):
    _broken(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, "ok")
    code = _push(monkeypatch, repo, _line(sha) + "\n")
    assert code == cli.owners_check.CANNOT_RUN
    assert "acme" in capsys.readouterr().err


def test_override_does_not_lift_cannot_run(
        github_owners, tmp_path, monkeypatch):
    _broken(github_owners)
    monkeypatch.setenv(gh.owners.ALLOW_CROSS_REF_ENV, "1")
    repo = remote_repo(tmp_path / "r", OWN)
    assert _message(tmp_path, repo, OFFENCE) == cli.owners_check.CANNOT_RUN


def test_message_file_allows_an_ssh_gitlab_remote(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", GITLAB_SSH)
    assert _message(tmp_path, repo, OFFENCE) == 0


def test_push_to_ssh_gitlab_allows(github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, OFFENCE)
    assert _push(monkeypatch, repo, _line(sha) + "\n", url=GITLAB_SSH) == 0


def test_message_file_checks_an_ssh_github_alias(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", GITHUB_ALIAS)
    assert _message(tmp_path, repo, OFFENCE) == cli.owners_check.REFUSED


def test_push_to_a_url_does_not_rescan_the_remote_sha(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    old = _commit(repo, "old offence\n\n" + OFFENCE)
    new = _commit(repo, "clean follow-up")
    url = "https://github.com/otto-nation/svc.git"
    assert _push(
        monkeypatch, repo, _line(new, old) + "\n", remote=url, url=url) == 0


def test_push_to_a_url_flags_a_new_offence_not_the_old_one(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    old = _commit(repo, "old offence\n\n" + OFFENCE)
    new = _commit(repo, "new offence\n\n" + OFFENCE)
    url = "https://github.com/otto-nation/svc.git"
    code = _push(monkeypatch, repo, _line(new, old) + "\n", remote=url, url=url)
    assert code == cli.owners_check.REFUSED
    err = capsys.readouterr().err
    assert "new offence" in err
    assert "old offence" not in err
    assert new[:7] in err


def test_push_allows_own_group_reference_to_own_group_destination(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, OWN_REF)
    assert _push(monkeypatch, repo, _line(sha) + "\n", url=OWN) == 0


def test_push_allows_an_upstream_reference(
        github_owners, tmp_path, monkeypatch):
    _groups(github_owners)
    repo = _repo(tmp_path)
    sha = _commit(repo, UPSTREAM_REF)
    assert _push(monkeypatch, repo, _line(sha) + "\n") == 0


def test_message_file_ignores_diff_below_the_scissors(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    text = f"ok\n{SCISSORS}\ndiff --git a/x b/x\n+{OFFENCE}\n"
    assert _message(tmp_path, repo, text) == 0


def test_message_file_refuses_a_reference_above_the_scissors(
        github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    text = f"{OFFENCE}\n{SCISSORS}\ndiff --git a/x b/x\n"
    assert _message(tmp_path, repo, text) == cli.owners_check.REFUSED


def test_hook_mode_usage_error_is_cannot_run(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.owners_check.main(
            ["--message-file", str(tmp_path / "COMMIT_EDITMSG")])
    assert exc.value.code == cli.owners_check.CANNOT_RUN
    assert capsys.readouterr().err


def test_command_mode_usage_error_keeps_exit_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.owners_check.main(["--cwd", str(tmp_path)])
    assert exc.value.code == 2
    assert capsys.readouterr().err


def test_message_file_tolerates_non_utf8(github_owners, tmp_path):
    _groups(github_owners)
    repo = remote_repo(tmp_path / "r", OWN)
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_bytes(b"ok \xff\n")
    assert cli.owners_check.main(
        ["--message-file", str(path), "--cwd", str(repo)]) == 0


def test_push_invalid_local_sha_is_cannot_run(
        github_owners, tmp_path, monkeypatch, capsys):
    _groups(github_owners)
    repo = _repo(tmp_path)
    _commit(repo, "ok")
    code = _push(monkeypatch, repo, _line("-evil") + "\n")
    assert code == cli.owners_check.CANNOT_RUN
    err = capsys.readouterr().err
    assert "invalid local sha: -evil" in err
