import re

from nesting.types import Violation

_FUNC_DECL = re.compile(
    r'^\s*func\s+'
    r'(?:\([^)]*\)\s+)?'   # optional receiver
    r'(\w+)\s*\('           # function name
)
_NESTING_KEYWORD = re.compile(r'\b(if|for|switch|select)\b')
_ELSE_IF = re.compile(r'\}\s*else\s+if\b')
_ELSE = re.compile(r'\}\s*else\s*\{')
_FUNC_LITERAL = re.compile(r'\bfunc\s*\(')
_LINE_COMMENT = re.compile(r'//.*$')
_BLOCK_COMMENT_OPEN = re.compile(r'/\*')


def _advance_in_literal(ch: str, line: str, i: int, mode: str) -> tuple[int, str]:
    """Advance past one character inside a string/rune literal. Returns (new_i, mode)."""
    if mode == 'rune':
        if ch == '\\':
            return i + 2, mode
        if ch == "'":
            return i + 1, ''
        return i + 1, mode
    if mode == 'string':
        if ch == '\\':
            return i + 2, mode
        if ch == '"':
            return i + 1, ''
        return i + 1, mode
    # mode == 'raw'
    if ch == '`':
        return i + 1, ''
    return i + 1, mode


def _strip_strings_and_comments(line: str) -> str:
    result = []
    i = 0
    mode = ''
    while i < len(line):
        ch = line[i]
        if mode:
            i, mode = _advance_in_literal(ch, line, i, mode)
            continue
        if ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
            break
        if ch == "'":
            mode = 'rune'
        elif ch == '"':
            mode = 'string'
        elif ch == '`':
            mode = 'raw'
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _handle_raw_string(in_raw_string: bool, line: str) -> tuple[bool, str | None]:
    """Handle multiline raw string state. Returns (new_state, remaining_line_or_None)."""
    if not in_raw_string:
        return False, None
    if '`' not in line:
        return True, None
    return False, line[line.index('`') + 1:]


def _handle_block_comment(in_block_comment: bool, line: str) -> tuple[bool, str | None]:
    """Handle block comment state. Returns (new_state, remaining_line_or_None)."""
    if not in_block_comment:
        return False, None
    if '*/' not in line:
        return True, None
    return False, line[line.index('*/') + 2:]


def _strip_block_comment(line: str) -> tuple[str, bool]:
    """Remove inline block comment. Returns (cleaned_line, entered_block_comment)."""
    if '/*' not in line:
        return line, False
    before = line[:line.index('/*')]
    after = line[line.index('/*') + 2:]
    if '*/' in after:
        return before + after[after.index('*/') + 2:], False
    return before, True


def _record_violation(
    violations: list[Violation], lineno: int, depth: int, func_name: str,
) -> None:
    violations.append(Violation(line_number=lineno, depth=depth, function_name=func_name))


def _count_keyword_nesting(
    stripped: str, violations: list[Violation], lineno: int,
    nesting_depth: int, func_name: str, max_depth: int,
) -> tuple[int, int]:
    """Process nesting keywords and func literals. Returns (new_nesting_depth, remaining_open_braces)."""
    else_if_count = len(_ELSE_IF.findall(stripped))
    else_count = len(_ELSE.findall(stripped))
    effective_keywords = len(_NESTING_KEYWORD.findall(stripped)) - else_if_count
    func_literals = len(_FUNC_LITERAL.findall(stripped))
    effective_close = stripped.count('}') - else_count - else_if_count
    effective_open = stripped.count('{') - else_count - else_if_count

    remaining_open = effective_open
    for _ in range(effective_keywords + func_literals):
        if remaining_open <= 0:
            break
        nesting_depth += 1
        if nesting_depth > max_depth:
            _record_violation(violations, lineno, nesting_depth, func_name)
        remaining_open -= 1

    for _ in range(effective_close):
        if nesting_depth > 0:
            nesting_depth -= 1

    return nesting_depth, effective_open - effective_close


class GoChecker:
    EXTENSIONS: set[str] = {'.go'}
    SHEBANG_RE: re.Pattern[bytes] | None = None
    DEFAULT_MAX_DEPTH: int = 2

    def check_nesting(self, lines: list[str], max_depth: int) -> list[Violation]:
        violations: list[Violation] = []
        func_name = ''
        nesting_depth = 0
        brace_depth = 0
        in_block_comment = False
        in_raw_string = False

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.rstrip('\n')

            in_raw_string, remaining = _handle_raw_string(in_raw_string, line)
            if remaining is not None:
                line = remaining
            elif in_raw_string:
                continue

            in_block_comment, remaining = _handle_block_comment(in_block_comment, line)
            if remaining is not None:
                line = remaining
            elif in_block_comment:
                continue

            line, in_block_comment = _strip_block_comment(line)
            stripped = _strip_strings_and_comments(line)

            line_no_comment = _LINE_COMMENT.sub('', line)
            if line_no_comment.count('`') % 2 == 1:
                in_raw_string = True

            fm = _FUNC_DECL.match(stripped)
            if fm:
                func_name = fm.group(1)
                nesting_depth = 0
                brace_depth = stripped.count('{') - stripped.count('}')
                continue

            depth_delta, brace_delta = _count_keyword_nesting(
                stripped, violations, lineno, nesting_depth, func_name, max_depth,
            )
            nesting_depth = depth_delta
            brace_depth += brace_delta

            if func_name and brace_depth <= 0:
                func_name = ''
                nesting_depth = 0
                brace_depth = 0

        return violations
