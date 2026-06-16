from nesting.preprocess import strip_strings_and_comments


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
