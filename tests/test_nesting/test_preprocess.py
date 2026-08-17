from nesting.preprocess import strip_shell_line, strip_strings_and_comments


def test_removes_single_quoted_strings():
    assert strip_strings_and_comments("echo 'hello world'") == "echo "


def test_removes_double_quoted_strings():
    assert strip_strings_and_comments('echo "hello"') == "echo "


def test_removes_comments():
    assert strip_strings_and_comments("code # comment") == "code "


def test_keywords_inside_strings_stripped():
    result = strip_strings_and_comments("echo 'if then fi'")
    assert "if" not in result
    assert "fi" not in result


def test_line_without_strings_or_comments_unchanged():
    line = "for x in y; do echo; done"
    assert strip_strings_and_comments(line) == line


def test_shell_line_strips_strings_and_comments():
    assert strip_shell_line("echo 'hello world'", False) == ("echo ", False)
    assert strip_shell_line('echo "hello"', False) == ("echo ", False)
    assert strip_shell_line("code # comment", False) == ("code ", False)
    assert strip_shell_line("for x in y; do echo; done", False) == (
        "for x in y; do echo; done", False,
    )


def test_shell_line_reports_a_quote_left_open():
    assert strip_shell_line("  rows=$(awk '", False) == ("  rows=$(awk ", True)


def test_shell_line_inside_an_open_quote_is_all_data():
    assert strip_shell_line("      if (n == 0) next", True) == ("", True)


def test_shell_line_closing_a_quote_keeps_the_code_after_it():
    assert strip_shell_line("  ' \"$1\"; fi", True) == (" ; fi", False)


def test_an_apostrophe_in_prose_does_not_open_a_quote():
    """A `#` or `"` span is skipped in the same pass, so its quotes are inert.

    Otherwise one contraction would swallow every line after it, and the
    nesting under those lines would go uncounted.
    """
    assert strip_shell_line("code # don't count this", False) == ("code ", False)
    assert strip_shell_line('err "it isn\'t here"', False) == ("err ", False)


def test_a_hash_inside_a_quote_does_not_hide_the_closing_quote():
    assert strip_shell_line("sed 's/#.*//' file", False) == ("sed  file", False)


def test_an_escaped_quote_does_not_close_the_string_holding_it():
    """`\\"` is one character of the string, not its end.

    Ending the string there hands the words after it back to the keyword scan,
    and a phrase as ordinary as `--until HEAD` then reads as a `until` loop.
    """
    line = 'echo "a \\"quoted\\" --until HEAD"; exit 0'
    assert strip_shell_line(line, False) == ("echo ; exit 0", False)
