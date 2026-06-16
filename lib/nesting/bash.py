import re

from nesting.preprocess import strip_strings_and_comments
from nesting.types import Violation

_OPENERS = re.compile(r'\b(if|for|while|until|case)\b')
_CLOSERS = re.compile(r'\b(fi|done|esac)\b')
_FUNC_DEF = re.compile(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)\s*\{')
_FUNC_END = re.compile(r'^\}\s*$')
_HEREDOC_START = re.compile(r"<<-?\s*'?([A-Za-z_]\w*)'?")
_CMD_SUB_OPEN = re.compile(r'\$\(')
_BATS_TEST = re.compile(r'^@test\b')


class _State:
    __slots__ = ('func_name', 'depth', 'sub_depth', 'heredoc_end', 'in_function')

    def __init__(self):
        self.func_name = '(top-level)'
        self.depth = 0
        self.sub_depth = 0
        self.heredoc_end = None
        self.in_function = False


def _preprocess_line(state: _State, raw_line: str) -> str | None:
    line = raw_line.rstrip('\n')

    if state.heredoc_end is not None:
        if line.strip() == state.heredoc_end:
            state.heredoc_end = None
        return None

    m = _HEREDOC_START.search(line)
    if m:
        state.heredoc_end = m.group(1)

    stripped = strip_strings_and_comments(line)

    opens = len(_CMD_SUB_OPEN.findall(stripped))
    state.sub_depth += opens
    if state.sub_depth <= 0:
        return stripped

    net_close = stripped.count(')') - (stripped.count('(') - opens)
    if net_close > 0:
        state.sub_depth = max(0, state.sub_depth - net_close)
    return None


def _handle_function(state: _State, stripped: str, raw_line: str) -> bool:
    fm = _FUNC_DEF.match(stripped)
    if fm:
        if stripped.rstrip().endswith('}'):
            return True
        state.depth = 0
        state.in_function = True
        state.func_name = fm.group(1)
        return True

    if _BATS_TEST.match(stripped):
        state.depth = 0
        state.in_function = True
        m = re.search(r'"([^"]+)"', raw_line)
        state.func_name = m.group(1) if m else '(test)'
        return True

    if state.in_function and _FUNC_END.match(stripped):
        state.in_function = False
        state.depth = 0
        state.func_name = '(top-level)'
        return True

    return False


def _check_line(
    state: _State, violations: list[Violation], lineno: int,
    raw_line: str, max_depth: int,
) -> None:
    stripped = _preprocess_line(state, raw_line)
    if stripped is None or _handle_function(state, stripped, raw_line):
        return

    opener_count = len(_OPENERS.findall(stripped))
    closer_count = len(_CLOSERS.findall(stripped))
    matched = min(opener_count, closer_count)

    state.depth += opener_count - matched
    if state.depth > max_depth:
        violations.append(Violation(
            line_number=lineno,
            depth=state.depth,
            function_name=state.func_name,
        ))
    state.depth = max(0, state.depth - (closer_count - matched))


class BashChecker:
    EXTENSIONS: set[str] = {'.sh', '.bash', '.bats'}
    SHEBANG_RE: re.Pattern[bytes] | None = re.compile(rb'^#!.*(/(ba)?sh|/env (ba)?sh)')
    DEFAULT_MAX_DEPTH: int = 2

    def check_nesting(self, lines: list[str], max_depth: int) -> list[Violation]:
        violations: list[Violation] = []
        state = _State()
        for lineno, raw_line in enumerate(lines, 1):
            _check_line(state, violations, lineno, raw_line, max_depth)
        return violations
