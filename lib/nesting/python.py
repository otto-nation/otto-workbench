import re

from nesting.preprocess import strip_strings_and_comments
from nesting.types import Violation

_PY_OPENER = re.compile(r'^\s*(?:async\s+)?(if|for|while|with|try)\b')
_PY_CONTINUATION = re.compile(r'^\s*(elif|else|except|finally)\b')
_PY_SCOPE_RESET = re.compile(r'^\s*(?:async\s+)?(def|class)\s+(\w+)')
_PY_DECORATOR = re.compile(r'^\s*@')
_PY_TRIPLE_QUOTE = re.compile(r'"""|\'\'\'')


class _State:
    __slots__ = (
        'func_name', 'stack', 'in_multiline_string',
        'multiline_quote', 'paren_depth',
    )

    def __init__(self):
        self.func_name = '(module-level)'
        self.stack: list[tuple[int, str]] = []
        self.in_multiline_string = False
        self.multiline_quote: str | None = None
        self.paren_depth = 0


def _toggling_triple_quote(line: str) -> str | None:
    """Return the triple-quote style that appears an odd number of times, or None."""
    for quote in ('"""', "'''"):
        if line.count(quote) % 2 == 1:
            return quote
    return None


def _paren_delta(line: str) -> int:
    stripped = strip_strings_and_comments(line)
    opens = stripped.count('(') + stripped.count('[') + stripped.count('{')
    closes = stripped.count(')') + stripped.count(']') + stripped.count('}')
    return opens - closes


def _skip_line(state: _State, line: str, trimmed: str) -> bool:
    if state.in_multiline_string:
        q = state.multiline_quote
        if q and q in line and line.count(q) % 2 == 1:
            state.in_multiline_string = False
            state.multiline_quote = None
        return True

    if not trimmed or trimmed.startswith('#'):
        return True

    quote = _toggling_triple_quote(line)
    if quote:
        state.in_multiline_string = True
        state.multiline_quote = quote
        return True

    delta = _paren_delta(trimmed)
    if state.paren_depth > 0:
        state.paren_depth = max(0, state.paren_depth + delta)
        return True

    state.paren_depth = max(0, state.paren_depth + delta)

    return _PY_DECORATOR.match(trimmed) is not None


def _classify_line(
    state: _State, violations: list[Violation], lineno: int,
    indent: int, trimmed: str, max_depth: int,
) -> None:
    while state.stack and state.stack[-1][0] >= indent:
        state.stack.pop()

    sm = _PY_SCOPE_RESET.match(trimmed)
    if sm:
        state.func_name = sm.group(2)
        state.stack.clear()
        return

    if _PY_OPENER.match(trimmed):
        state.stack.append((indent, 'opener'))
        if len(state.stack) > max_depth:
            violations.append(Violation(
                line_number=lineno,
                depth=len(state.stack),
                function_name=state.func_name,
            ))
        return

    if _PY_CONTINUATION.match(trimmed):
        state.stack.append((indent, 'continuation'))


def _check_line(
    state: _State, violations: list[Violation], lineno: int,
    raw_line: str, max_depth: int,
) -> None:
    line = raw_line.rstrip('\n')
    trimmed = line.lstrip()
    if _skip_line(state, line, trimmed):
        return
    indent = len(line) - len(trimmed)
    _classify_line(state, violations, lineno, indent, trimmed, max_depth)


PYTHON_SHEBANG_RE: re.Pattern[bytes] = re.compile(rb'^#!.*(/python|/env python)')


class PythonChecker:
    EXTENSIONS: set[str] = {'.py'}
    SHEBANG_RE: re.Pattern[bytes] | None = None
    DEFAULT_MAX_DEPTH: int = 2

    def check_nesting(self, lines: list[str], max_depth: int) -> list[Violation]:
        violations: list[Violation] = []
        state = _State()
        for lineno, raw_line in enumerate(lines, 1):
            _check_line(state, violations, lineno, raw_line, max_depth)
        return violations
