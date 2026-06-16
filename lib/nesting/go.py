import re

from nesting.types import Violation

_FUNC_DECL = re.compile(
    r'^\s*func\s+'
    r'(?:\([^)]*\)\s+)?'   # optional receiver
    r'(\w+)\s*\('           # function name
)
_NESTING_KEYWORD = re.compile(r'\b(if|for|switch|select)\b')


def _strip_strings_and_comments(line: str) -> str:
    result = []
    i = 0
    in_rune = False
    in_string = False
    in_raw = False
    while i < len(line):
        ch = line[i]
        if in_rune:
            if ch == '\\':
                i += 1
            elif ch == "'":
                in_rune = False
            i += 1
            continue
        if in_string:
            if ch == '\\':
                i += 1
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if in_raw:
            if ch == '`':
                in_raw = False
            i += 1
            continue
        if ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
            break
        if ch == "'":
            in_rune = True
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == '`':
            in_raw = True
            i += 1
            continue
        result.append(ch)
        i += 1
    return ''.join(result)


class GoChecker:
    EXTENSIONS: set[str] = {'.go'}
    SHEBANGS: set[str] = set()
    DEFAULT_MAX_DEPTH: int = 3

    def check_nesting(self, lines: list[str], max_depth: int) -> list[Violation]:
        violations: list[Violation] = []
        func_name = ''
        nesting_depth = 0
        brace_depth = 0
        in_block_comment = False

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.rstrip('\n')

            if in_block_comment:
                if '*/' in line:
                    in_block_comment = False
                    line = line[line.index('*/') + 2:]
                else:
                    continue

            if '/*' in line:
                before = line[:line.index('/*')]
                after = line[line.index('/*') + 2:]
                if '*/' in after:
                    line = before + after[after.index('*/') + 2:]
                else:
                    in_block_comment = True
                    line = before

            stripped = _strip_strings_and_comments(line)

            fm = _FUNC_DECL.match(stripped)
            if fm:
                func_name = fm.group(1)
                nesting_depth = 0
                brace_depth = 0
                open_count = stripped.count('{')
                close_count = stripped.count('}')
                brace_depth += open_count - close_count
                continue

            keywords = _NESTING_KEYWORD.findall(stripped)
            open_braces = stripped.count('{')
            close_braces = stripped.count('}')

            for _ in keywords:
                if open_braces > 0:
                    nesting_depth += 1
                    if nesting_depth > max_depth:
                        violations.append(Violation(
                            line_number=lineno,
                            depth=nesting_depth,
                            function_name=func_name,
                        ))
                    open_braces -= 1

            brace_depth += stripped.count('{') - close_braces

            for _ in range(close_braces):
                if nesting_depth > 0:
                    nesting_depth -= 1

            if func_name and brace_depth <= 0:
                func_name = ''
                nesting_depth = 0
                brace_depth = 0

        return violations
