import re

_HEREDOC_START = re.compile(r"<<-?\s*[\"']?([A-Za-z_]\w*)[\"']?")


def strip_strings_and_comments(line: str) -> str:
    line = re.sub(r"'[^']*'", '', line)
    line = re.sub(r'"[^"]*"', '', line)
    line = re.sub(r'#.*', '', line)
    return line


def heredoc_delimiter(line: str, in_squote: bool, in_dquote: bool) -> str | None:
    """The delimiter a heredoc opened on *line* will end at, or None.

    Lives here beside ``strip_shell_line`` because it is the other half of the
    same scan, but only the size counter uses it today. ``nesting/bash.py``
    keeps its own regex deliberately: it reads the delimiter off the raw line
    and so opens a heredoc on a ``<<`` that is merely mentioned, but its state
    also survives ``strip_shell_line`` leaking an unclosed single-quote span on
    a line like ``"$(printf "the remote's branch")"`` — nested quoting inside a
    command substitution that the flat scan cannot model. Switching it to this
    helper makes it trust those leaked flags and suppress the next real
    heredoc, which newly fails `tests/generate_doc_reference.bats`. Unifying
    the two needs `strip_shell_line` fixed first; see #1471.

    Neither the raw line nor the quote-stripped one can answer this alone, and
    each is wrong in the opposite direction. ``strip_shell_line`` erases the
    inside of a quoted span, so a quoted delimiter (``cat <<'EOF'``) comes back
    as ``cat <<`` and the heredoc is never seen to start — its body then gets
    scanned as ordinary shell, silently dropping the blank and ``#``-led lines
    a caller may promise to count. Searching the raw line instead finds a
    ``<<`` that is only being talked about, in a comment (``# unquoted <<EOF,
    not <<'EOF'``, in lib/ai/commit.sh) or inside a string, and opens a
    heredoc that never existed — swallowing the rest of the file up to a
    delimiter that never arrives.

    So the scan walks the line itself: a heredoc opens where the ``<<``
    operator sits in code, outside both kinds of quote and before any
    comment, and the delimiter that follows it is read from the raw text with
    its quotes intact.
    """
    i = 0
    while i < len(line):
        char = line[i]
        if in_squote:
            in_squote = char != "'"
        elif char == "\\":
            i += 2
            continue
        elif in_dquote:
            in_dquote = char != '"'
        elif char == "'":
            in_squote = True
        elif char == '"':
            in_dquote = True
        elif char == "#":
            # A comment runs to end of line, so nothing past it opens anything.
            return None
        elif line.startswith("<<<", i):
            # A here-string, not a heredoc: it takes a word rather than a body,
            # so there is no delimiter and no following lines to swallow.
            i += 2
        elif line.startswith("<<", i):
            match = _HEREDOC_START.match(line, i)
            return match.group(1) if match else None
        i += 1
    return None


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
