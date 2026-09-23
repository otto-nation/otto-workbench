"""Count the code in a file, with its prose left out.

A total-line count measures documentation as much as complexity. Across
``ai/lib`` the prose share runs 19–51%, so the best-documented modules are the
ones a total-line cap binds hardest: ``pr/summary_model.py`` is 708 lines of
which 270 are code, while the densest module in the tree, ``gh/pr_reads.py``,
carries 647 code lines under a total of 941. A cap on totals fires on the first
and spares the second, which is the wrong way round.

The guidelines require the prose it would tax — ``ceiling:`` markers naming a
trigger, a comment on every silent fallback, module docstrings that record why
an alternative was rejected. Counting it against a budget prices explanation,
and the cheapest way under any line budget is to delete the explanation and
keep the complexity.

So: code lines only. Blank lines, comments and docstrings are not code. What
remains is what a reader has to hold in their head.

Heredoc bodies count as code. A heredoc in these scripts is usually a generated
file or a usage block, and while that is closer to data than to logic, a line
of it is still a line someone maintains — and treating heredocs as free would
make "move it into a heredoc" a way under the cap.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import warnings
from pathlib import Path

from nesting.preprocess import strip_shell_line

# Mirrors nesting.bash's, deliberately: both answer "does a heredoc body start
# on this line", and the answer has to be the same in a nesting count and a
# size count or one of them is reading a different file than the other.
_HEREDOC_START = re.compile(r"<<-?\s*[\"']?([A-Za-z_]\w*)[\"']?")


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Every line held by a docstring, at any level of *tree*."""
    held: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # get_docstring(clean=False) to leave the source text alone: the span
        # is what matters here, not the content.
        if ast.get_docstring(node, clean=False) and node.body:
            first = node.body[0]
            held.update(range(first.lineno, first.end_lineno + 1))
    return held


def _comment_lines(source: str) -> set[int]:
    """Every line whose token stream contributes only a comment.

    Tokenising rather than matching ``#``: a ``#`` inside a string literal is
    not a comment, and a regex cannot tell the two apart.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file that will not tokenise still gets counted, just without its
        # comments recognised. Over-counting a broken file is the safe way to
        # be wrong: the alternative is a syntax error reading as a small file.
        return set()
    return {t.start[0] for t in tokens if t.type == tokenize.COMMENT}


def python_code_lines(source: str) -> int:
    """Code lines in Python *source* — no blanks, comments or docstrings.

    A line carrying both code and a trailing comment is code, because the
    comment set only holds lines the comment token starts.
    """
    try:
        # A file elsewhere in the tree may hold an invalid escape sequence or
        # another construct the compiler warns about. That is its problem, not
        # this counter's, and letting the warning through would make a size
        # report look like a failure in the file being measured.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except SyntaxError:
        # Same reasoning as the tokenize failure above.
        return sum(1 for line in source.splitlines() if line.strip())

    lines = source.splitlines()
    blank = {i for i, line in enumerate(lines, 1) if not line.strip()}
    docstrings = _docstring_lines(tree)
    # `x = 1  # why` is code with a comment on it, and the comment token starts
    # on that line — so subtracting every commented line would undercount it.
    # Only lines that are *nothing but* a comment come out.
    bare_comments = {
        i for i in _comment_lines(source)
        if lines[i - 1].lstrip().startswith("#")
    }
    return len(lines) - len(blank | docstrings | bare_comments)


class _ShellScan:
    """Line-at-a-time shell scan, carrying heredoc and quote state across lines.

    A class rather than locals in a loop because both kinds of quote and an
    open heredoc all survive a line break, and threading four values through a
    helper reads worse than naming the state once.
    """

    def __init__(self) -> None:
        self.code = 0
        self.heredoc_end: str | None = None
        self.in_squote = False
        self.in_dquote = False

    def feed(self, line: str) -> None:
        if self.heredoc_end is not None:
            self._in_heredoc(line)
        elif line.strip():
            self._outside_heredoc(line)

    def _in_heredoc(self, line: str) -> None:
        self.code += 1
        if line.strip() == self.heredoc_end:
            self.heredoc_end = None

    def _outside_heredoc(self, line: str) -> None:
        stripped, self.in_squote, self.in_dquote = strip_shell_line(
            line, self.in_squote, self.in_dquote,
        )
        if not stripped.strip():
            # Nothing survived the strip: either a whole-line comment, or a
            # line wholly inside a quoted span. An open quote means the line
            # still carries content, so it counts; a comment does not.
            self.code += int(self.in_squote or self.in_dquote)
            return

        match = _HEREDOC_START.search(stripped)
        if match:
            self.heredoc_end = match.group(1)
        self.code += 1


def shell_code_lines(source: str) -> int:
    """Code lines in shell *source* — no blanks, no whole-line comments.

    Quote state carries across lines in bash, so the strip is stateful: a
    ``#`` inside an open string is not a comment, and an apostrophe in a
    comment must not open a span. That logic already exists for the nesting
    checker and is reused rather than written twice.
    """
    state = _ShellScan()
    for raw in source.splitlines():
        state.feed(raw.rstrip("\n"))
    return state.code


def code_lines(path: Path, language: str) -> int:
    """Code lines in *path*, read as *language* (``python`` or ``shell``)."""
    source = path.read_text(encoding="utf-8", errors="replace")
    if language == "python":
        return python_code_lines(source)
    return shell_code_lines(source)
