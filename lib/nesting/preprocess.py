import re


def strip_strings_and_comments(line: str) -> str:
    line = re.sub(r"'[^']*'", '', line)
    line = re.sub(r'"[^"]*"', '', line)
    line = re.sub(r'#.*', '', line)
    return line


def strip_shell_line(
    line: str, in_squote: bool, in_dquote: bool,
) -> tuple[str, bool, bool]:
    """The code on one shell line, plus which quote is still open after it.

    Both kinds of quote carry across lines in bash, and a line-at-a-time strip
    reads what they hold as code: an embedded awk or sed program's `if` and
    `while` are data, but they get counted as nesting that is not there. Both
    states come back out so the caller can hand them to the next line.

    Comments are skipped in the same pass rather than by a separate regex, so
    neither an apostrophe in prose (`# don't`, `"it's"`) can open a span nor a
    `#` inside a string (`sed 's/#.*//'`) can eat the quote that closes one.
    """
    out: list[str] = []
    i = 0
    while i < len(line):
        char = line[i]
        # A backslash escapes the next character everywhere but inside single
        # quotes, where bash gives it no meaning. `\"` must not close a span.
        step = 2 if char == '\\' and not in_squote else 1
        if in_squote:
            in_squote = char != "'"
        elif char == '\\':
            pass
        elif in_dquote:
            in_dquote = char != '"'
        elif char == "'":
            in_squote = True
        elif char == '"':
            in_dquote = True
        elif char == '#':
            break
        else:
            out.append(char)
        i += step
    return ''.join(out), in_squote, in_dquote
