"""Tests for bin/local/validate-file-size and lib/file_size.py."""

import sys
from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-file-size"

sys.path.insert(0, str(REPO_ROOT / "lib"))
from file_size import python_code_lines, shell_code_lines  # noqa: E402

vfs = load_script("validate_file_size", SCRIPT)


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _lines(n: int) -> str:
    """*n* copies of a throwaway statement — a body big enough to trip a cap."""
    return "x = 1\n" * n


# ── The counter: Python ──────────────────────────────────────────────────────

def test_a_blank_line_is_not_code():
    assert python_code_lines("x = 1\n\n\ny = 2\n") == 2


def test_a_bare_comment_is_not_code():
    assert python_code_lines("# why\nx = 1\n") == 1


def test_a_trailing_comment_is_code():
    """The line holds a statement; the comment rides on it."""
    assert python_code_lines("x = 1  # why\n") == 1


def test_a_module_docstring_is_not_code():
    assert python_code_lines('"""Doc.\n\nMore.\n"""\nx = 1\n') == 1


def test_a_function_docstring_is_not_code():
    source = 'def f():\n    """Doc.\n\n    More.\n    """\n    return 1\n'
    assert python_code_lines(source) == 2


def test_a_class_docstring_is_not_code():
    source = 'class C:\n    """Doc."""\n\n    x = 1\n'
    assert python_code_lines(source) == 2


def test_a_hash_inside_a_string_is_not_a_comment():
    assert python_code_lines('x = "# not a comment"\n') == 1


def test_a_string_that_is_not_a_docstring_is_code():
    """Only the first statement of a body is a docstring."""
    assert python_code_lines('x = 1\n"""just a string expression"""\n') == 2


def test_an_unparseable_file_counts_its_non_blank_lines():
    """A syntax error must not read as a small file."""
    assert python_code_lines("def f(\n  oops\n\nx = 1\n") == 3


# ── The counter: shell ───────────────────────────────────────────────────────

def test_shell_blank_and_comment_lines_are_not_code():
    assert shell_code_lines("#!/usr/bin/env bash\n# why\n\nset -e\n") == 1


def test_a_heredoc_body_is_code():
    """Counted deliberately: otherwise a heredoc is a way under the cap."""
    source = "cat <<EOF\none\ntwo\nEOF\n"
    assert shell_code_lines(source) == 4


def test_a_comment_inside_a_heredoc_is_code():
    source = "cat <<EOF\n# printed, not a comment\nEOF\n"
    assert shell_code_lines(source) == 3


def test_the_line_that_closes_a_multi_line_string_is_code():
    """The closing line holds the quote that ends the span, so it is code.

    The strip leaves it empty and the span is shut by the time it returns, so
    reading the state after the strip drops it — 35 lines across 16 of this
    repo's own in-scope shell files, every one of them under the cap rather
    than over it.
    """
    assert shell_code_lines('echo "a\nb"\n') == 2
    assert shell_code_lines("echo 'x\ny'\n") == 2
    assert shell_code_lines('echo "a\nb"\necho after\n') == 3


def test_a_hash_inside_a_shell_string_is_not_a_comment():
    assert shell_code_lines('echo "has # inside"\n') == 1


def test_a_quoted_heredoc_delimiter_is_recognised():
    source = "cat <<'EOF'\nbody\nEOF\n"
    assert shell_code_lines(source) == 3


def test_a_blank_and_comment_line_inside_a_quoted_heredoc_is_code():
    """A quoted delimiter must not fall back to scanning the body as code."""
    source = "cat <<'USAGE'\nUsage:\n\n# not a real comment\nUSAGE\n"
    assert shell_code_lines(source) == 5


def test_a_double_quoted_heredoc_delimiter_is_recognised():
    source = 'cat <<"USAGE"\nUsage:\n\n# not a real comment\nUSAGE\n'
    assert shell_code_lines(source) == 5


def test_a_heredoc_mentioned_in_a_comment_opens_nothing():
    """lib/ai/commit.sh explains its unquoted `<<EOF` in a comment above it.

    Reading the delimiter off the raw line finds this one and swallows the
    rest of the file waiting for a terminator that never comes.
    """
    source = "echo hi\n# see <<EOF for details\n\n\necho bye\n"
    assert shell_code_lines(source) == 2


def test_a_heredoc_written_inside_a_string_opens_nothing():
    source = 'x="note <<EOF here"\n\n\necho done\n'
    assert shell_code_lines(source) == 2


def test_a_here_string_is_not_a_heredoc():
    """`<<<` takes a word, not a body — there are no following lines to take."""
    source = 'grep x <<< "$v"\n\n# c\necho done\n'
    assert shell_code_lines(source) == 2


def test_a_tab_stripping_heredoc_is_recognised():
    source = "cat <<-EOF\n\tbody\n\n\tEOF\necho after\n"
    assert shell_code_lines(source) == 5


def test_a_heredoc_sharing_its_line_with_a_pipe_is_recognised():
    source = "cat <<EOF | grep x\nbody\n\nEOF\necho after\n"
    assert shell_code_lines(source) == 5


# ── Discovery ────────────────────────────────────────────────────────────────

def test_a_python_file_without_an_extension_is_found(tmp_path):
    """The ai/bin entry points are Python with no .py — the largest scripts."""
    _write(tmp_path, "ai/bin/thing", "#!/usr/bin/env python3\nx = 1\n")
    found = {p.name: lang for p, lang in vfs.discover(tmp_path)}
    assert found == {"thing": "python"}


def test_a_shell_file_is_found_by_shebang(tmp_path):
    _write(tmp_path, "bin/thing", "#!/usr/bin/env bash\nset -e\n")
    assert [lang for _, lang in vfs.discover(tmp_path)] == ["shell"]


def test_markdown_is_not_source(tmp_path):
    _write(tmp_path, "ai/notes.md", "# heading\n" * 900)
    assert vfs.discover(tmp_path) == []


def test_typescript_is_declared_out_of_scope(tmp_path):
    """Excluded on purpose, not by falling through unnoticed: see _NOT_SOURCE."""
    _write(tmp_path, "ai/pi/extensions/thing.ts", "const x = 1;\n" * 900)
    assert vfs.discover(tmp_path) == []


def test_tests_are_out_of_scope(tmp_path):
    """#910 owns the suites; this gate would only duplicate it, loudly."""
    _write(tmp_path, "tests/big_test.py", _lines(900))
    assert vfs.discover(tmp_path) == []


def test_a_directory_outside_the_scan_roots_is_skipped(tmp_path):
    _write(tmp_path, "site/app.py", _lines(900))
    assert vfs.discover(tmp_path) == []


def test_pycache_is_skipped(tmp_path):
    _write(tmp_path, "ai/__pycache__/x.py", _lines(900))
    assert vfs.discover(tmp_path) == []


# ── The gate ─────────────────────────────────────────────────────────────────

def test_a_file_at_the_cap_passes(tmp_path):
    _write(tmp_path, "ai/a.py", _lines(10))
    assert vfs.over_cap(tmp_path, 10) == []


def test_a_file_one_over_the_cap_is_reported(tmp_path):
    _write(tmp_path, "ai/a.py", _lines(11))
    assert vfs.over_cap(tmp_path, 10) == [("ai/a.py", 11)]


def test_prose_does_not_count_against_the_cap(tmp_path):
    """The whole point: a documented file is not a large one."""
    _write(tmp_path, "ai/a.py", '"""Doc.\n' + "prose\n" * 50 + '"""\n' + _lines(5))
    assert vfs.over_cap(tmp_path, 10) == []


def test_violations_are_reported_worst_first(tmp_path):
    _write(tmp_path, "ai/small.py", _lines(12))
    _write(tmp_path, "ai/big.py", _lines(30))
    assert [name for name, _ in vfs.over_cap(tmp_path, 10)] == ["ai/big.py", "ai/small.py"]


def test_a_new_file_over_the_cap_fails(tmp_path):
    """The case the gate exists for: phase 5 produced two of these unnoticed."""
    _write(tmp_path, "ai/a.py", _lines(11))
    assert vfs.main(["--max-lines", "10", "--quiet", str(tmp_path)]) == 1


def test_an_unreadable_file_names_itself_instead_of_raising(tmp_path):
    """A gate over a whole tree must say which file it choked on.

    The counter's `read_text` raises `PermissionError` from three frames down,
    where the traceback names pathlib rather than the file.
    """
    locked = _write(tmp_path, "ai/locked.py", _lines(3))
    locked.chmod(0o000)
    try:
        with pytest.raises(SystemExit) as caught:
            vfs.main(["--max-lines", "10", str(tmp_path)])
    finally:
        locked.chmod(0o644)
    assert "ai/locked.py" in str(caught.value)


def test_a_known_file_over_the_cap_does_not_fail(tmp_path, monkeypatch):
    """Pre-existing debt is #853's and #911's; blocking on it blocks every push."""
    monkeypatch.setitem(vfs.KNOWN_OVER, "ai/a.py", "#853")
    _write(tmp_path, "ai/a.py", _lines(11))
    assert vfs.main(["--max-lines", "10", "--quiet", str(tmp_path)]) == 0


def test_a_new_file_fails_even_beside_a_known_one(tmp_path, monkeypatch):
    """An exemption must not carry cover for anything but itself."""
    monkeypatch.setitem(vfs.KNOWN_OVER, "ai/known.py", "#853")
    _write(tmp_path, "ai/known.py", _lines(11))
    _write(tmp_path, "ai/new.py", _lines(11))
    assert vfs.main(["--max-lines", "10", "--quiet", str(tmp_path)]) == 1


def test_it_passes_when_nothing_is_over(tmp_path):
    _write(tmp_path, "ai/a.py", _lines(5))
    assert vfs.main(["--max-lines", "10", "--quiet", str(tmp_path)]) == 0


def test_the_exemptions_are_exactly_what_is_over_the_cap():
    """Pins the list so it can only shrink.

    An entry added to KNOWN_OVER to quiet a newly-oversized file fails here, and
    so does an entry left behind after its file came under the cap. #911's gate
    lands when this set is empty.
    """
    over = {name for name, _ in vfs.over_cap(REPO_ROOT, vfs.MAX_CODE_LINES)}
    assert over == set(vfs.KNOWN_OVER)


def test_every_exemption_names_the_issue_that_owns_it():
    """An exemption with no owner is a permanent one wearing a temporary label."""
    assert all(owner.startswith("#") for owner in vfs.KNOWN_OVER.values())
