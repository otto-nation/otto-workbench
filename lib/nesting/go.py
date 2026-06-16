import re

from nesting.types import Violation

_FUNC_DECL = re.compile(
    r'^\s*func\s+'
    r'(?:\([^)]*\)\s+)?'   # optional receiver
    r'(\w+)\s*\('           # function name
)
_NESTING_KEYWORD = re.compile(r'\b(if|for|switch|select)\b')
# Matches `} else if ...` — the `if` here is a continuation, not new nesting
_ELSE_IF = re.compile(r'\}\s*else\s+if\b')
# Matches `} else {` — a continuation block, not new nesting
_ELSE = re.compile(r'\}\s*else\s*\{')
# Matches func literals (closures): func( or func () but NOT named func declarations
_FUNC_LITERAL = re.compile(r'\bfunc\s*\(')


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

            # Count `} else if` and `} else {` occurrences so we can handle
            # them correctly: these are continuations, not new nesting levels.
            else_if_count = len(_ELSE_IF.findall(stripped))
            else_count = len(_ELSE.findall(stripped))

            keywords = _NESTING_KEYWORD.findall(stripped)
            open_braces = stripped.count('{')
            close_braces = stripped.count('}')

            # Subtract `else if` keyword matches — they are not new nesting.
            # Each `} else if` contributes one spurious `if` to the keyword list.
            effective_keywords = len(keywords) - else_if_count

            # Count func literals (closures) as nesting — they open a new scope.
            # Named func declarations are already handled above, so _FUNC_LITERAL
            # here only fires for anonymous functions/closures.
            func_literals = len(_FUNC_LITERAL.findall(stripped))

            # `} else {` lines: the closing `}` is a continuation brace, not a
            # block-end for nesting purposes. The opening `{` after `else` is
            # also a continuation, not a new nesting level. Adjust close/open
            # brace counts so they cancel each other out for `else` branches.
            effective_close_braces = close_braces - else_count
            effective_open_braces = open_braces - else_count

            # `} else if x {`: the `}` closes the previous `if` block and the
            # `{` opens the `else if` body. Both are continuations — subtract them.
            # Each `else if` has one `}` and one `{` that are continuations.
            effective_close_braces -= else_if_count
            effective_open_braces -= else_if_count

            # `case` in switch/select bodies: Go case clauses don't use braces
            # but they do represent a new nesting level inside the switch body.
            case_keywords = len(re.findall(r'\bcase\b', stripped))

            # Process nesting keywords (if/for/switch/select), each consuming
            # one open brace from the line.
            remaining_open = effective_open_braces
            for _ in range(effective_keywords):
                if remaining_open > 0:
                    nesting_depth += 1
                    if nesting_depth > max_depth:
                        violations.append(Violation(
                            line_number=lineno,
                            depth=nesting_depth,
                            function_name=func_name,
                        ))
                    remaining_open -= 1

            # Func literals consume one open brace and count as nesting.
            for _ in range(func_literals):
                if remaining_open > 0:
                    nesting_depth += 1
                    if nesting_depth > max_depth:
                        violations.append(Violation(
                            line_number=lineno,
                            depth=nesting_depth,
                            function_name=func_name,
                        ))
                    remaining_open -= 1

            # `case` clauses add nesting depth without braces.
            for _ in range(case_keywords):
                nesting_depth += 1
                if nesting_depth > max_depth:
                    violations.append(Violation(
                        line_number=lineno,
                        depth=nesting_depth,
                        function_name=func_name,
                    ))

            # Update brace depth using effective counts (excluding else continuations).
            brace_depth += effective_open_braces - effective_close_braces

            # Decrement nesting depth for closing braces (excluding else continuations).
            for _ in range(effective_close_braces):
                if nesting_depth > 0:
                    nesting_depth -= 1

            if func_name and brace_depth <= 0:
                func_name = ''
                nesting_depth = 0
                brace_depth = 0

        return violations
