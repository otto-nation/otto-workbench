from nesting.types import Violation


def test_violation_fields():
    v = Violation(line_number=10, depth=3, function_name="process")
    assert v.line_number == 10
    assert v.depth == 3
    assert v.function_name == "process"


def test_violation_empty_function_name():
    v = Violation(line_number=1, depth=1, function_name="")
    assert v.function_name == ""
