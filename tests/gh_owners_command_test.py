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
