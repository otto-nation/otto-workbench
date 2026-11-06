"""gh.owners.check_command: body files, cd, and shell-borne paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh.owners

_WRITE = "gh issue comment 1 -R otto-nation/svc"


@pytest.mark.parametrize("command", [
    "gh issue comment 1 -R otto-nation/svc --body 'see me/lib#12'; "
    "gh issue comment 2 -R me/lib --body x",
    "gh issue comment 2 -R me/lib --body x; "
    "gh issue comment 1 -R otto-nation/svc --body 'see me/lib#12'",
])
def test_check_command_judges_content_against_every_write_group(
        github_owners, tmp_path, command):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    verdict = gh.owners.check_command(command, str(tmp_path))
    assert not verdict.ok and "me/lib#12" in verdict.reason
    assert verdict.references


def test_check_command_refuses_a_relative_body_file_after_cd(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    (tmp_path / "sub").mkdir()
    verdict = gh.owners.check_command(
        "cd sub && gh issue comment 1 -R otto-nation/svc --body-file b.md",
        str(tmp_path))
    assert not verdict.ok
    assert "b.md" in verdict.reason
    assert "absolute path" in verdict.reason
    assert "general.md § Cross-Group References" in verdict.reason
    assert not verdict.references


def test_check_command_scans_an_absolute_body_file_after_cd(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "sub").mkdir()
    body = tmp_path / "b.md"
    body.write_text("see me/lib#12\n")
    verdict = gh.owners.check_command(
        f"cd sub && gh issue comment 1 -R otto-nation/svc --body-file {body}",
        str(tmp_path))
    assert not verdict.ok and "me/lib#12" in verdict.reason
    assert verdict.references


def test_check_command_resolves_a_relative_body_file_without_cd(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "b.md").write_text("see me/lib#12\n")
    verdict = gh.owners.check_command(
        "gh issue comment 1 -R otto-nation/svc --body-file b.md", str(tmp_path))
    assert not verdict.ok and "me/lib#12" in verdict.reason


def _shell_body_commands(path: str) -> tuple[str, ...]:
    return (
        f'{_WRITE} --body "$(cat {path})"',
        f"{_WRITE} --body-file {path}",
        f"{_WRITE} -F{path}",
        f"{_WRITE} -F={path}",
        f"cat {path} | {_WRITE} --body-file -",
        f"{_WRITE} --body-file - < {path}",
    )


def test_check_command_refuses_shell_borne_cross_group_bodies(
        github_owners, tmp_path, monkeypatch):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    monkeypatch.setenv("HOME", str(tmp_path))
    notes = tmp_path / "notes.md"
    notes.write_text("see me/lib#12\n")
    commands = _shell_body_commands("notes.md") + (
        f'{_WRITE} --body-file ~/notes.md',
        f'{_WRITE} --body-file "$HOME/notes.md"',
    )
    for command in commands:
        verdict = gh.owners.check_command(command, str(tmp_path))
        assert not verdict.ok and "me/lib#12" in verdict.reason, command
        assert verdict.references, command


def test_check_command_allows_shell_borne_bodies_without_cross_refs(
        github_owners, tmp_path, monkeypatch):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    monkeypatch.setenv("HOME", str(tmp_path))
    notes = tmp_path / "notes.md"
    notes.write_text("plain body\n")
    commands = _shell_body_commands("notes.md") + (
        f'{_WRITE} --body-file ~/notes.md',
        f'{_WRITE} --body-file "$HOME/notes.md"',
    )
    for command in commands:
        assert gh.owners.check_command(command, str(tmp_path)).ok, command


def test_check_command_allows_a_generator_pipe(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "notes.md").write_text("see me/lib#12\n")
    verdict = gh.owners.check_command(
        f"generate-body | {_WRITE} --body-file -", str(tmp_path))
    assert verdict.ok


_HEREDOC_CREATE = (
    "cd sub && cat <<'EOF' | gh pr create -R otto-nation/svc --title t --body-file -\n"
    "{body}\n"
    "EOF"
)
_CAT_REDIRECT = (
    "cd /tmp && cat > /tmp/b.md <<EOF\n"
    "{body}\n"
    "EOF\n"
    "gh pr create -R otto-nation/svc --title t --body-file /tmp/b.md"
)


def test_check_command_allows_a_heredoc_pipe_after_cd(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "sub").mkdir()
    command = _HEREDOC_CREATE.format(body="plain body")
    assert gh.owners.check_command(command, str(tmp_path)).ok


_SPACED_HEREDOCS = (
    "cd sub && cat << 'EOF' | gh pr create -R otto-nation/svc --title t --body-file -\n"
    "{body}\n"
    "EOF",
    "cd sub && cat << EOF | gh pr create -R otto-nation/svc --title t --body-file -\n"
    "{body}\n"
    "EOF",
    "cd sub && cat <<- EOF | gh pr create -R otto-nation/svc --title t --body-file -\n"
    "{body}\n"
    "EOF",
)


def test_check_command_allows_a_spaced_heredoc_pipe_after_cd(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "sub").mkdir()
    for template in _SPACED_HEREDOCS:
        command = template.format(body="plain body")
        assert gh.owners.check_command(command, str(tmp_path)).ok, command


def test_check_command_refuses_a_cross_group_heredoc_after_cd(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "sub").mkdir()
    command = _HEREDOC_CREATE.format(body="see me/lib#12")
    verdict = gh.owners.check_command(command, str(tmp_path))
    assert not verdict.ok and "me/lib#12" in verdict.reason
    assert verdict.references


def test_check_command_refuses_a_spaced_cross_group_heredoc_after_cd(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "sub").mkdir()
    for template in _SPACED_HEREDOCS:
        command = template.format(body="see me/lib#12")
        verdict = gh.owners.check_command(command, str(tmp_path))
        assert not verdict.ok and "me/lib#12" in verdict.reason, command
        assert verdict.references, command


def test_check_command_does_not_treat_here_string_words_as_paths(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "plain").write_text("see me/lib#12\n")
    command = (
        "cat <<< plain text | gh pr create -R otto-nation/svc "
        "--title t --body-file -"
    )
    assert gh.owners.check_command(command, str(tmp_path)).ok


def test_check_command_allows_cat_output_redirect_after_cd(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    command = _CAT_REDIRECT.format(body="plain body")
    assert gh.owners.check_command(command, str(tmp_path)).ok


def test_check_command_still_scans_cat_and_redirect_inputs(
        github_owners, tmp_path):
    github_owners({"work": ["otto-nation"], "personal": ["me"]})
    (tmp_path / "notes.md").write_text("see me/lib#12\n")
    via_cat = gh.owners.check_command(
        f"cat notes.md | {_WRITE} --body-file -", str(tmp_path))
    via_redir = gh.owners.check_command(
        f"{_WRITE} --body-file - < notes.md", str(tmp_path))
    via_fd = gh.owners.check_command(
        f"cat 2> err.log notes.md | {_WRITE} --body-file -", str(tmp_path))
    assert not via_cat.ok and "me/lib#12" in via_cat.reason
    assert not via_redir.ok and "me/lib#12" in via_redir.reason
    assert not via_fd.ok and "me/lib#12" in via_fd.reason
