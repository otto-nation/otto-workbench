"""gh.owner_content: the published text of a write, body files included."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh.owner_content


def test_command_text_includes_a_body_file(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("see me/lib#12\n")
    command = f"gh issue comment 1 --body-file {body}"
    text = gh.owner_content.command_text(command, tmp_path)
    assert command in text
    assert "see me/lib#12" in text


def test_command_text_includes_equals_and_field_and_input_files(tmp_path):
    for name, contents in {
        "notes.md": "BODY_FILE_EQ",
        "query.graphql": "FIELD_FILE",
        "payload.json": "INPUT_FILE",
        "desc.md": "DESC_FILE",
    }.items():
        (tmp_path / name).write_text(contents)
    command = (
        "gh issue comment 1 --body-file=notes.md; "
        "gh api graphql -F query=@query.graphql; "
        "gh api --input payload.json graphql; "
        "gh repo create x --description-file desc.md"
    )
    text = gh.owner_content.command_text(command, tmp_path)
    assert "BODY_FILE_EQ" in text
    assert "FIELD_FILE" in text
    assert "INPUT_FILE" in text
    assert "DESC_FILE" in text


def test_command_text_does_not_read_a_file_over_the_scan_bound(tmp_path):
    huge = tmp_path / "huge.md"
    huge.write_text("UNIQUE_OVERSIZE" + "a" * gh.owner_content.MAX_SCAN_BYTES)
    command = "gh issue comment 1 --body-file huge.md"
    text = gh.owner_content.command_text(command, tmp_path)
    assert "UNIQUE_OVERSIZE" not in text


def test_command_text_reads_a_file_at_the_scan_bound(tmp_path):
    marker = "UNIQUE_AT_BOUND"
    payload = marker + "b" * (gh.owner_content.MAX_SCAN_BYTES - len(marker))
    (tmp_path / "exact.md").write_text(payload)
    text = gh.owner_content.command_text(
        "gh issue comment 1 --body-file exact.md", tmp_path)
    assert marker in text


def _lie_size_zero(path: Path, monkeypatch):
    real_stat = Path.stat
    target = os.path.realpath(path)

    def lie(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if os.path.realpath(self) != target:
            return result
        values = list(result)
        values[6] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", lie)


def test_command_text_skips_an_oversize_file_even_when_stat_size_is_zero(
        tmp_path, monkeypatch):
    huge = tmp_path / "huge.md"
    huge.write_text("UNIQUE_LIE" + "a" * gh.owner_content.MAX_SCAN_BYTES)
    _lie_size_zero(huge, monkeypatch)
    text = gh.owner_content.command_text(
        "gh issue comment 1 --body-file huge.md", tmp_path)
    assert "UNIQUE_LIE" not in text


def test_command_text_reads_a_size_lying_file_within_the_cap(
        tmp_path, monkeypatch):
    proc = tmp_path / "proc.md"
    proc.write_text("PROC_BODY")
    _lie_size_zero(proc, monkeypatch)
    text = gh.owner_content.command_text(
        "gh issue comment 1 --body-file proc.md", tmp_path)
    assert "PROC_BODY" in text


def test_read_if_scannable_bounds_the_read(tmp_path, monkeypatch):
    (tmp_path / "n.md").write_text("hi")
    sizes: list[int] = []
    real_open = Path.open

    def wrapped(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        inner = handle.read

        def read(size=-1):
            sizes.append(size)
            return inner(size)

        handle.read = read
        return handle

    monkeypatch.setattr(Path, "open", wrapped)
    gh.owner_content._read_if_scannable("n.md", tmp_path)
    assert sizes == [gh.owner_content.MAX_SCAN_BYTES + 1]


def test_command_text_skips_missing_and_stdin(tmp_path):
    command = "gh issue comment 1 --body-file missing.md --body-file -"
    text = gh.owner_content.command_text(command, tmp_path)
    assert text == command


def test_command_text_resolves_a_relative_path_against_cwd(tmp_path):
    (tmp_path / "n.md").write_text("RELATIVE_HIT")
    other = tmp_path / "other"
    other.mkdir()
    command = "gh issue comment 1 --body-file n.md"
    assert "RELATIVE_HIT" in gh.owner_content.command_text(command, tmp_path)
    assert "RELATIVE_HIT" not in gh.owner_content.command_text(command, other)


def test_command_text_does_not_read_a_non_write_statements_file(tmp_path):
    (tmp_path / "read.md").write_text("READ_ONLY_MARKER")
    (tmp_path / "write.md").write_text("WRITE_MARKER")
    command = (
        "gh pr view 1 --body-file read.md; "
        "gh issue comment 1 --body-file write.md"
    )
    text = gh.owner_content.command_text(command, tmp_path)
    assert "READ_ONLY_MARKER" not in text
    assert "WRITE_MARKER" in text


def test_command_text_returns_an_unparseable_command_unchanged(tmp_path):
    command = "gh issue create --title 'x"
    assert gh.owner_content.command_text(command, tmp_path) == command


def test_argv_text_joins_args_input_and_body_file(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("FROM_FILE")
    text = gh.owner_content.argv_text(
        ("issue", "comment", "1", "--body-file", str(body)),
        "FROM_INPUT",
        tmp_path,
    )
    assert "issue comment 1" in text
    assert "FROM_INPUT" in text
    assert "FROM_FILE" in text


def test_argv_text_omits_input_when_none(tmp_path):
    text = gh.owner_content.argv_text(("issue", "comment", "1", "-b", "x"), None, tmp_path)
    assert text == "issue comment 1 -b x"


def test_argv_text_does_not_scan_stdin_dash(tmp_path):
    text = gh.owner_content.argv_text(
        ("pr", "create", "--body-file", "-"), "INLINE", tmp_path)
    assert "INLINE" in text
    assert text.count("INLINE") == 1


def test_command_text_treats_dash_F_without_equals_as_a_body_file(tmp_path):
    (tmp_path / "short.md").write_text("SHORT_F")
    text = gh.owner_content.command_text(
        "gh issue comment 1 -F short.md", tmp_path)
    assert "SHORT_F" in text


def test_command_text_includes_notes_file(tmp_path):
    (tmp_path / "notes.md").write_text("NOTES_FILE")
    spaced = gh.owner_content.command_text(
        "gh release create v1 --notes-file notes.md", tmp_path)
    equals = gh.owner_content.command_text(
        "gh release create v1 --notes-file=notes.md", tmp_path)
    assert "NOTES_FILE" in spaced
    assert "NOTES_FILE" in equals


def test_command_text_still_reads_api_field_file_and_skips_plain_values(tmp_path):
    (tmp_path / "body.md").write_text("API_AT_FILE")
    (tmp_path / "plain.md").write_text("PLAIN_VALUE_FILE")
    at_file = gh.owner_content.command_text(
        "gh api repos/x/y -X POST -F body=@body.md", tmp_path)
    plain = gh.owner_content.command_text(
        "gh api repos/x/y -X POST -F key=plain.md", tmp_path)
    assert "API_AT_FILE" in at_file
    assert "PLAIN_VALUE_FILE" not in plain


def test_command_text_reads_attached_short_field_flags(tmp_path):
    (tmp_path / "notes.md").write_text("ATTACHED_F")
    glued = gh.owner_content.command_text(
        "gh issue comment 1 -Fnotes.md", tmp_path)
    equals = gh.owner_content.command_text(
        "gh issue comment 1 -F=notes.md", tmp_path)
    assert "ATTACHED_F" in glued
    assert "ATTACHED_F" in equals


def test_command_text_expands_home_in_a_body_file_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "notes.md").write_text("HOME_BODY")
    tilde = gh.owner_content.command_text(
        "gh issue comment 1 --body-file ~/notes.md", tmp_path / "other")
    var = gh.owner_content.command_text(
        'gh issue comment 1 --body-file "$HOME/notes.md"', tmp_path / "other")
    assert "HOME_BODY" in tilde
    assert "HOME_BODY" in var


def test_command_text_reads_cat_and_redirect_operands(tmp_path):
    (tmp_path / "notes.md").write_text("FROM_CAT")
    via_cat = gh.owner_content.command_text(
        'gh issue comment 1 --body "$(cat notes.md)"', tmp_path)
    via_pipe = gh.owner_content.command_text(
        "cat notes.md | gh pr create --body-file -", tmp_path)
    via_redir = gh.owner_content.command_text(
        "gh pr create --body-file - < notes.md", tmp_path)
    via_subst = gh.owner_content.command_text(
        'gh issue comment 1 --body "$(< notes.md)"', tmp_path)
    assert "FROM_CAT" in via_cat
    assert "FROM_CAT" in via_pipe
    assert "FROM_CAT" in via_redir
    assert "FROM_CAT" in via_subst


def test_command_text_does_not_read_a_generator_pipe(tmp_path):
    (tmp_path / "notes.md").write_text("GENERATOR_SECRET")
    text = gh.owner_content.command_text(
        "generate-body | gh pr create --body-file -", tmp_path)
    assert "GENERATOR_SECRET" not in text


_HEREDOC_CREATE = (
    "cd sub && cat <<'EOF' | gh pr create -R otto-nation/svc --title t --body-file -\n"
    "{body}\n"
    "EOF"
)
_CAT_REDIRECT = (
    "cd /tmp && cat > /tmp/b.md <<EOF\n"
    "{body}\n"
    "EOF"
)


def test_relative_body_paths_skips_heredoc_and_output_redirects():
    heredoc = _HEREDOC_CREATE.format(body="plain body")
    redirect = _CAT_REDIRECT.format(body="plain body")
    assert gh.owner_content.relative_body_paths(heredoc) == ()
    assert gh.owner_content.relative_body_paths(redirect) == ()


def test_relative_body_paths_still_names_cat_and_redirect_inputs():
    assert gh.owner_content.relative_body_paths(
        "cat notes.md | gh pr create --body-file -") == ("notes.md",)
    assert gh.owner_content.relative_body_paths(
        "gh pr create --body-file - < notes.md") == ("notes.md",)
