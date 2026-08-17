import re


def strip_strings_and_comments(line: str) -> str:
    line = re.sub(r"'[^']*'", '', line)
    line = re.sub(r'"[^"]*"', '', line)
    line = re.sub(r'#.*', '', line)
    return line


def strip_shell_line(line: str, in_squote: bool) -> tuple[str, bool]:
    """The code on one shell line, plus whether a single quote is still open.

    A single-quoted string is the one shell construct that carries across
    lines, and an embedded awk or sed program is where that matters: its
    `if` and `while` are data, but a line-at-a-time strip reads them as bash
    and counts nesting that is not there. The quote state comes back out so
    the caller can hand it to the next line.

    Comments and double-quoted spans are skipped in the same pass rather than
    by a separate regex, so neither an apostrophe in prose (`# don't`, `"it's"`)
    can open a span nor a `#` inside a string (`sed 's/#.*//'`) can eat the
    quote that closes one.
    """
    out: list[str] = []
    in_dquote = False
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
    return ''.join(out), in_squote
