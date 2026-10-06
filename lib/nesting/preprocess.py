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
    same scan. The size counter and ``nesting/bash.py`` both call it so they
    agree on where a heredoc starts.

    Neither the raw line nor the quote-stripped one can answer this alone, and
    each is wrong in the opposite direction. ``strip_shell_line`` erases the
    inside of a quoted span, so a quoted delimiter (``cat <<'EOF'``) comes back
    as ``cat <<`` and the heredoc is never seen to start — its body then gets
    scanned as ordinary shell, silently dropping the blank and ``#``-led lines
    a caller may promise to count. Searching the raw line instead finds a
    ``<<`` that is only being talked about, in a comment (``# unquoted <<EOF,
    not <<'EOF'``) or inside a string, and opens a
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

    Nested quoting inside ``$( )`` is tracked only in the returned flags.
    Same-line stripped output is still the flat scan: a quoted substitution
    such as ``foo="$(bar)"`` must stay ``foo=``, not ``foo=$(bar)``.
    """
    stripped, _, _ = _flat_strip(line, in_squote, in_dquote)
    _, in_squote, in_dquote = _nested_state(line, in_squote, in_dquote)
    return stripped, in_squote, in_dquote


def _flat_strip(
    line: str, in_squote: bool, in_dquote: bool,
) -> tuple[str, bool, bool]:
    """Today's scan, moved verbatim so stripped output cannot move."""
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


def _nested_state(
    line: str, in_squote: bool, in_dquote: bool,
) -> tuple[str, bool, bool]:
    """Quote flags at end of *line*, with ``$( )`` as a nested quoting frame.

    Stripped output is not produced here. Nested state must not drive
    ``out.append``: re-enabling append inside a quoted ``$( )`` is what
    turned ``foo="$(bar)"`` into ``foo=$(bar)`` across the tree.
    """
    _, in_squote, in_dquote, _closed = _scan_quote_frame(
        line, 0, in_squote, in_dquote, close_paren=False,
    )
    return '', in_squote, in_dquote


def _scan_quote_frame(
    line: str,
    i: int,
    in_squote: bool,
    in_dquote: bool,
    *,
    close_paren: bool,
) -> tuple[int, bool, bool, bool]:
    """Walk *line* from *i* tracking quotes; do not build stripped text.

    When *close_paren* is set this is a ``$(`` body: quotes belong to this
    frame, and the matching unquoted ``)`` returns so the parent frame resumes
    unchanged. Nested ``$(`` recurses. ``$((`` is arithmetic, not a frame.
    Single quotes suppress everything, including ``$(``. A backslash skips
    the next character, so ``\\$(`` is not a substitution.

    Returns ``(next_index, in_squote, in_dquote, closed)``.
    """
    depth = 0
    n = len(line)
    while i < n:
        char = line[i]
        if in_squote:
            in_squote = char != "'"
            i += 1
            continue
        if char == '\\':
            i += 2
            continue
        sub = _consume_command_sub(line, i)
        if sub is not None and sub[3]:
            i = sub[0]
            continue
        if sub is not None:
            return _unclosed_inner(sub[0], sub[1], sub[2])
        if in_dquote:
            in_dquote = char != '"'
            i += 1
            continue
        if char == "'":
            in_squote = True
        elif char == '"':
            in_dquote = True
        elif char == '#':
            break
        elif close_paren and char == '(':
            depth += 1
        elif close_paren and char == ')' and depth == 0:
            return i + 1, in_squote, in_dquote, True
        elif close_paren and char == ')':
            depth -= 1
        i += 1
    return i, in_squote, in_dquote, not close_paren


def _consume_command_sub(
    line: str, i: int,
) -> tuple[int, bool, bool, bool] | None:
    """Scan a ``$(`` body starting at *i*, or None if *i* is not one."""
    if not line.startswith('$(', i) or line.startswith('$((', i):
        return None
    return _scan_quote_frame(line, i + 2, False, False, close_paren=True)


def _unclosed_inner(
    i: int, in_squote: bool, in_dquote: bool,
) -> tuple[int, bool, bool, bool]:
    # ceiling: the API returns two bools, so an unclosed $( inside an
    # outer " cannot represent both frames — inner flags are returned
    # and a later line's << is then read as code rather than a heredoc.
    # Upgrade trigger: once a tracked shell file leaves $( unclosed at
    # end of line inside a double-quoted span that a following heredoc
    # opener depends on.
    return i, in_squote, in_dquote, False
